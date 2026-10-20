"""Synthetic software checks only: causality, cohort coverage, exact quantities and zero trading influence."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from desk.contracts.discovery import DiscoveryReason, Outcome, StageResult
from desk.contracts.market import WindowMetrics
from desk.core.config import load_config
from desk.core.paths import REPO_ROOT, DeskPaths
from desk.ops.opportunities import pass_once
from desk.research.opportunities import OpportunityLab, features, readonly_market
from desk.research.rh_opportunities import RhOpportunityLab, participation
from desk.rh.contracts import BidAsk, Candle, Estimate, Pair
from desk.rh.repo import RhDatabases, RhMarket
from desk.storage.databases import Databases
from desk.storage.db import DbKind, connect, migrate
from desk.storage.discovery_repo import DiscoveryRepository
from desk.storage.market_repo import MarketRepository
from tests.unit.test_counterfactual import snap
from tests.unit.test_market_context import address

D = Decimal
T0 = datetime(2026, 10, 20, 12, 0, 30, tzinfo=UTC)


@pytest.fixture
def paths(tmp_path: Path) -> DeskPaths:
    return DeskPaths(home=tmp_path / "home", config_dir=REPO_ROOT / "config", secrets_file=tmp_path / "not-needed.env")


def research(paths: DeskPaths) -> sqlite3.Connection:
    conn = connect(paths.db_dir / "research.db", DbKind.RESEARCH)
    migrate(conn, DbKind.RESEARCH)
    return conn


def seed_rh(paths: DeskPaths, *, btc_available_late: bool = False) -> RhDatabases:
    dbs = RhDatabases.open(paths)
    market = RhMarket(dbs.market)
    pairs = [Pair(s, s.split("-")[0], "USD", D("0.01"), D("0.01"), D(1000), D(1), "tradable", True)
             for s in ("BTC-USD", "ALT-USD", "MISSING-USD")]
    market.save_pairs(pairs, T0 - timedelta(hours=2))
    for symbol in ("BTC-USD", "ALT-USD"):
        end = T0 - timedelta(seconds=30)
        bars = [Candle(symbol, 900, end - timedelta(minutes=15 * (5 - i)), D(100), D(106), D(99),
                       D(104) if symbol == "ALT-USD" and i == 4 else D(100), D(4000) if i == 4 else D(1000))
                for i in range(5)]
        received = T0 + timedelta(seconds=1) if btc_available_late and symbol == "BTC-USD" else T0
        market.save_candles("coinbase", bars, received)
    market.save_quotes([BidAsk("ALT-USD", D("103.9"), D("104.1"), T0 - timedelta(seconds=1), "q"),
                        BidAsk("BTC-USD", D("99.9"), D("100.1"), T0 - timedelta(seconds=1), "q")])
    return dbs


def sol_series(dbs: Databases, *, include_end: bool = True) -> tuple[str, str]:
    mint, pool = address(), address()
    repo = MarketRepository(dbs.market)
    rows = []
    for i in range(6):
        row = snap(pool, mint, T0 + timedelta(minutes=i), "1.04" if i == 5 else "1")
        rows.append(row.model_copy(update={"windows": {"m5": WindowMetrics(
            volume_usd=D(6000) if i == 5 else D(2000), buys=60, sells=20)}}))
    if include_end:
        rows.append(snap(pool, mint, T0 + timedelta(minutes=65), "2", "0"))
    repo.save_pool_snapshots(rows)
    return mint, pool


def test_rh_uses_five_closed_bars_and_retains_missing_pair_in_coverage(paths: DeskPaths) -> None:
    dbs = seed_rh(paths)
    source_before = tuple(dbs.market.iterdump())
    ledger_before = tuple(dbs.ledger.iterdump())
    result = pass_once(paths, "robinhood", now=T0)
    assert result["points"] == 2                       # not 5 fictitious historical entries per asset
    report = result["report"]
    assert report["cohorts"] == 3 and report["cohorts_without_comparable_points"] == 1
    conn = research(paths)
    row = conn.execute("SELECT p.* FROM opportunity_points p JOIN opportunity_cohorts c ON c.id = p.cohort_id"
                       " WHERE c.asset = 'ALT-USD'").fetchone()
    assert row["flow_watch"] and row["price_watch"] and row["stance"] == "WATCH"
    payload = json.loads(row["features_json"])
    assert len(payload["bars"]) == 5 and payload["authorization"] == "NONE"
    assert payload["availability_delay_seconds"] == "30"
    assert payload["experiment_usd"] == "50.00" and payload["asset"] == "ALT-USD"
    assert tuple(dbs.market.iterdump()) == source_before and tuple(dbs.ledger.iterdump()) == ledger_before
    assert report["promotion"] == "DISABLED" and report["influence"] == "NONE"
    conn.close()
    dbs.close()


def test_rh_does_not_use_benchmark_received_after_its_decision(paths: DeskPaths) -> None:
    dbs = seed_rh(paths, btc_available_late=True)
    pass_once(paths, "robinhood", now=T0 + timedelta(seconds=2))
    conn = research(paths)
    row = conn.execute("SELECT p.* FROM opportunity_points p JOIN opportunity_cohorts c ON c.id=p.cohort_id"
                       " WHERE c.asset='ALT-USD'").fetchone()
    assert row["stance"] == "ABSTAIN" and not row["flow_watch"]
    assert "NO_VOLUME_BASELINE_OR_BTC" in json.loads(row["features_json"])["reasons"]
    conn.close()
    dbs.close()


def test_rh_stale_history_cannot_produce_historical_trades(paths: DeskPaths) -> None:
    dbs = seed_rh(paths)
    # A much older backfill is first available now: it must not create any earlier decision.
    market = RhMarket(dbs.market)
    market.save_candles("coinbase", [Candle("ALT-USD", 900, T0 - timedelta(days=5), D(1), D(2), D(1), D(2), D(9000))], T0)
    pass_once(paths, "robinhood", now=T0)
    conn = research(paths)
    assert conn.execute("SELECT MIN(decided_ms) FROM opportunity_points").fetchone()[0] == int(T0.timestamp() * 1000)
    assert conn.execute("SELECT COUNT(*) FROM opportunity_points").fetchone()[0] == 2
    conn.close()
    dbs.close()


def test_rh_outcomes_require_original_quantity_and_keep_fees_separate_from_chart_returns(paths: DeskPaths) -> None:
    dbs = seed_rh(paths)
    market = RhMarket(dbs.market)
    buy = Estimate("ALT-USD", "EX", "BUY", D("0.45"), D(100), D("0.475"), D("45.475"), D("0.0095"), None, T0, "b")
    market.save_estimates([buy], "PROBE")
    pass_once(paths, "robinhood", now=T0)
    end = T0 + timedelta(hours=1)
    market.save_quotes([BidAsk("ALT-USD", D(155), D(157), end, "q1")])
    # A same-dollar fresh ticket is NOT the quantity our original hypothetical entry acquired.
    wrong = Estimate("ALT-USD", "MM", "SELL", D("0.06"), D(155), D(0), D("9.30"), None, None, end, "s")
    market.save_estimates([wrong], "PROBE")
    conn = research(paths)
    lab = RhOpportunityLab(dbs.market, conn, ticket_lamports=100_000_000)
    assert lab.label(end + timedelta(seconds=89)) >= 0
    assert conn.execute("SELECT 1 FROM opportunity_outcomes WHERE horizon_seconds=3600").fetchone() is None
    lab.label(end + timedelta(seconds=90))
    row = conn.execute("SELECT o.* FROM opportunity_outcomes o JOIN opportunity_points p ON p.id=o.point_id"
                       " JOIN opportunity_cohorts c ON c.id=p.cohort_id WHERE c.asset='ALT-USD'"
                       " AND o.horizon_seconds=3600").fetchone()
    assert D(row["chart_return"]) == D("0.5")
    quote = json.loads(row["payload_json"])["quote"]
    assert quote["status"] == "NO_EXACT_QUANTITY_EXIT_ESTIMATE" and quote["net_return"] is None
    assert not quote["confirmed_fill"]
    conn.close()
    dbs.close()


def test_rh_exact_estimated_totals_include_reported_fees(paths: DeskPaths) -> None:
    dbs = seed_rh(paths)
    market = RhMarket(dbs.market)
    market.save_estimates([Estimate("ALT-USD", "EX", "BUY", D("0.45"), D(100), D("0.475"), D("45.475"),
                                     None, None, T0, "buy")], "PROBE")
    pass_once(paths, "robinhood", now=T0)
    end = T0 + timedelta(hours=1)
    market.save_quotes([BidAsk("ALT-USD", D(155), D(157), end, "q")])
    market.save_estimates([Estimate("ALT-USD", "EX", "SELL", D("0.450"), D(150), D("0.5"), D("67.0"),
                                     None, None, end, "sell")], "EXIT")
    conn = research(paths)
    lab = RhOpportunityLab(dbs.market, conn, ticket_lamports=100_000_000)
    lab.label(end + timedelta(seconds=90))
    quotes = [json.loads(r[0]).get("quote", {}) for r in conn.execute(
        "SELECT payload_json FROM opportunity_outcomes WHERE horizon_seconds=3600")]
    quote = next(q for q in quotes if q.get("status") == "HYPOTHETICAL_QUOTE_MARK")
    assert D(quote["estimated_return_after_reported_fees"]) == D("67.0") / D("45.475") - 1
    assert quote["net_return"] is None and not quote["confirmed_fill"]
    conn.close()
    dbs.close()


def test_solana_records_deferred_rejected_unassessed_and_untracked_pools(paths: DeskPaths) -> None:
    dbs = Databases.open(paths)
    mint, pool = sol_series(dbs)
    discovery = DiscoveryRepository(dbs.market, load_config(REPO_ROOT / "config").settings.discovery)
    candidate = discovery.observe(mint, pool, "fixture", T0, reason=DiscoveryReason.NEW_POOL)
    discovery.save_results([StageResult(candidate_id=candidate, stage=1, outcome=Outcome.REJECT, rule_id="FUTURE_REJECT",
                                        observed_values={}, thresholds={}, config_hash="x", decided_at=T0+timedelta(minutes=10))])
    other = snap(address(), address(), T0, "1").model_copy(update={"source": "geckoterminal.new_pools"})
    MarketRepository(dbs.market).save_pool_snapshots([other])
    lab = OpportunityLab(dbs.market, dbs.research, ticket_lamports=100_000_000)
    lab.ingest(T0 + timedelta(minutes=5))
    point = dbs.research.execute("SELECT * FROM opportunity_points WHERE flow_watch=1").fetchone()
    assert point and point["primary_state"] == "UNASSESSED"    # future rejection never leaks into features
    lab.ingest(T0 + timedelta(minutes=70))
    report = lab.report(T0 + timedelta(minutes=70))
    assert report["cohorts"] == 2 and report["cohorts_without_comparable_points"] == 1
    lab.label(T0 + timedelta(minutes=70))
    outcome = dbs.research.execute("SELECT * FROM opportunity_outcomes WHERE point_id=? AND horizon_seconds=3600",
                                  (point["id"],)).fetchone()
    assert outcome["status"] == "ZERO_LIQUIDITY" and D(outcome["chart_return"]) > 0
    assert json.loads(outcome["payload_json"])["net_return"] is None
    dbs.close()


def test_restart_is_idempotent_and_research_records_are_immutable(paths: DeskPaths) -> None:
    dbs = seed_rh(paths)
    first = pass_once(paths, "robinhood", now=T0)
    again = pass_once(paths, "robinhood", now=T0 + timedelta(seconds=5))
    assert first["points"] == 2 and again["points"] == 0
    conn = research(paths)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE opportunity_points SET flow_watch=0")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("DELETE FROM opportunity_cohorts")
    conn.close()
    source = readonly_market(paths.db_dir / "robinhood-market.db")
    with pytest.raises(sqlite3.OperationalError):
        source.execute("DELETE FROM candles")
    source.close()
    dbs.close()


def test_venue_records_do_not_mix_or_create_missing_source_files(paths: DeskPaths) -> None:
    assert not pass_once(paths, "robinhood", now=T0)["available"]
    assert not (paths.db_dir / "robinhood-market.db").exists()
    rh = seed_rh(paths)
    sol = Databases.open(paths)
    sol_series(sol, include_end=False)
    r1 = pass_once(paths, "robinhood", now=T0 + timedelta(minutes=5))
    r2 = pass_once(paths, "solana", now=T0 + timedelta(minutes=5))
    assert r1["report"]["venue"] == "robinhood" and r2["report"]["venue"] == "solana"
    assert r1["report"]["cohorts"] == 3 and r2["report"]["cohorts"] == 1
    assert r1["report"]["spec_sha256"] != r2["report"]["spec_sha256"]
    rh.close()
    sol.close()


def test_invalid_or_gapped_features_abstain() -> None:
    assert features([])[0] == "ABSTAIN"
    assert participation([], D(0))[0] == "ABSTAIN"
    bars = [{"start_ms": i*900_000, "close": "100", "volume": "1000"} for i in range(5)]
    bars[-1]["start_ms"] = 900_000 * 6
    assert participation(bars, D(0))[0] == "ABSTAIN"


def test_future_source_timestamp_does_not_get_skipped_by_cursor(paths: DeskPaths) -> None:
    dbs = Databases.open(paths)
    market = MarketRepository(dbs.market)
    market.save_pool_snapshots([snap(address(), address(), T0 + timedelta(hours=1), "1")])
    lab = OpportunityLab(dbs.market, dbs.research, ticket_lamports=100_000_000)
    result = lab.ingest(T0)
    assert result["future_timestamp_block"] == 1 and result["snapshots"] == 0
    assert lab.ingest(T0 + timedelta(hours=1))["snapshots"] == 1
    dbs.close()


def test_short_horizon_backlog_cannot_starve_day_outcomes(paths: DeskPaths) -> None:
    dbs = Databases.open(paths)
    sol_series(dbs)
    lab = OpportunityLab(dbs.market, dbs.research, ticket_lamports=100_000_000)
    lab.ingest(T0 + timedelta(hours=2))
    assert lab.label(T0 + timedelta(days=1, hours=2), limit=4) == 4
    assert {r[0] for r in dbs.research.execute("SELECT horizon_seconds FROM opportunity_outcomes")} == {
        900, 3600, 21600, 86400}
    dbs.close()


def test_dashboard_report_reads_do_not_run_research_or_create_sources(paths: DeskPaths,
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient

    from desk.ops.opportunities import read_report
    from desk.rh.web import create_rh_app

    assert read_report(paths, "robinhood")["available"] is False
    assert not paths.db_dir.exists()
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("a dashboard must not execute research")
    monkeypatch.setattr("desk.ops.opportunities.pass_once", forbidden)
    with TestClient(create_rh_app(paths, load_config(paths.config_dir)), base_url="http://127.0.0.1:8766") as client:
        response = client.get("/api/rh/opportunities")
        assert response.status_code == 200 and response.json()["available"] is False
    assert not (paths.db_dir / "market.db").exists() and not (paths.db_dir / "research.db").exists()
