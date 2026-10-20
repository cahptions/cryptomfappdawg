"""Evidence council end to end on real SQLite: recorded primary decision → two advisories → report.

Covers reproducibility (same evidence, same advisory hash, even when later data has arrived), abstention when the
desk could not have known, the daily cohort, isolation (no trading path imports the council; it writes nothing
outside its own records; a failure is contained) and the zero-request benchmark capture. Synthetic prices test
semantics only.
"""

from __future__ import annotations

import ast
import asyncio
import json
import random
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
from solders.pubkey import Pubkey

from desk.contracts.advisory import Advisory
from desk.contracts.discovery import DiscoveryReason, Outcome
from desk.contracts.market import OhlcvBar
from desk.core.clock import to_epoch_millis, to_iso
from desk.core.config import load_config
from desk.core.hashing import fingerprint
from desk.core.paths import REPO_ROOT, DeskPaths
from desk.council.evaluate import Advisor, PrimaryDecision
from desk.ops.council import council_report, run_council
from desk.paper.ledger import PaperLedger
from desk.security.policy import SOL_MINT
from desk.storage.council_repo import CouncilRepository
from desk.storage.databases import Databases
from desk.storage.discovery_repo import DiscoveryRepository
from desk.storage.market_repo import MarketRepository
from tests.unit.test_discovery import pool_snapshot
from tests.unit.test_security import MINT, POOL
from tests.unit.test_strategies import _context

LOADED = load_config(REPO_ROOT / "config")
SOL_POOL = LOADED.settings.context.sol_reference_pool
END = datetime(2026, 10, 20, 12, tzinfo=UTC)          # end of the latest closed 15-minute bar
DECIDED = END + timedelta(seconds=120)   # the last SOL minute is usable 90 s after its close
BARS = 306


def seed_history(dbs: Databases, mint: str, pool: str, *, bars: int = BARS, signal_extra: float = 0.0,
                 seed: int = 1, end: datetime = END, benchmark: bool = True) -> None:
    """``bars`` consecutive 15-minute bars ending at ``end``: token 5-minute bars and SOL/USD minutes."""
    MarketRepository(dbs.market).save_pool_snapshots(
        [pool_snapshot().model_copy(update={"pool_address": pool, "base_mint": mint})])
    rng = random.Random(seed)
    token, sol = [1.0], [100.0]
    for j in range(1, bars):
        rb = rng.gauss(0, 0.004)
        extra = signal_extra if bars - 17 <= j <= bars - 2 else (0.03 if j == bars - 1 else 0.0)
        token.append(token[-1] * float(np.exp(0.0002 + 1.2 * rb + rng.gauss(0, 0.006) + extra)))
        sol.append(sol[-1] * float(np.exp(rb)))
    start = end - timedelta(minutes=15 * bars)
    token_rows, sol_rows = [], []
    for i in range(bars):
        bar_start = start + timedelta(minutes=15 * i)
        before_t, before_s = (token[i - 1], sol[i - 1]) if i else (token[0], sol[0])
        for k in range(3):
            at = bar_start + timedelta(minutes=5 * k)
            close = before_t * (token[i] / before_t) ** ((k + 1) / 3)
            token_rows.append((pool, mint, to_iso(at), to_epoch_millis(at), repr(close), repr(close), repr(close),
                               repr(close), "1000", to_iso(at + timedelta(minutes=5, seconds=20)), "a" * 64))
        for m in range(15):
            at = bar_start + timedelta(minutes=m)
            close = before_s * (sol[i] / before_s) ** ((m + 1) / 15)
            sol_rows.append((SOL_POOL, to_iso(at), to_epoch_millis(at), repr(close), repr(close), repr(close),
                             repr(close), "50", to_iso(at + timedelta(seconds=150)),
                             to_epoch_millis(at + timedelta(seconds=150)), "b" * 64))
    dbs.market.executemany("INSERT INTO ohlcv_bars(pool_address, mint, timeframe, bar_at, bar_at_ms, open, high, low,"
                           " close, volume_usd, observed_at, raw_sha256, source) VALUES (?, ?, '5m', ?, ?, ?, ?, ?, ?, ?,"
                           " ?, ?, 'geckoterminal.ohlcv')", token_rows)
    if benchmark:
        dbs.market.executemany("INSERT OR IGNORE INTO benchmark_bars(pool_address, instrument, source, timeframe,"
                               " bar_start, bar_start_ms, open, high, low, close, volume_usd, first_received_at,"
                               " first_received_at_ms, raw_sha256) VALUES (?, 'SOL/USD', 'geckoterminal.ohlcv', '1m', ?,"
                               " ?, ?, ?, ?, ?, ?, ?, ?, ?)", sol_rows)


class World:
    def __init__(self, tmp_path: Path) -> None:
        self.paths = DeskPaths(tmp_path / "data", REPO_ROOT / "config", tmp_path / "none.env")
        self.dbs = Databases.open(self.paths)
        self.count = 0

    def token(self, *, mint: str = MINT, pool: str = POOL, first_seen: datetime = DECIDED - timedelta(days=5),
              quotes_age: float = 3.0, back: int = 98_000_000) -> str:
        MarketRepository(self.dbs.market).save_pool_snapshots(
            [pool_snapshot().model_copy(update={"pool_address": pool, "base_mint": mint})])
        candidate = DiscoveryRepository(self.dbs.market, LOADED.settings.discovery).observe(
            mint, pool, "test", first_seen, reason=DiscoveryReason.NEW_POOL)
        received = DECIDED - timedelta(seconds=quotes_age)
        buy = {"input_mint": SOL_MINT, "output_mint": mint, "in_amount_raw": 100_000_000,
               "out_amount_raw": 1_000_000_000, "received_at": to_iso(received - timedelta(seconds=1)),
               "raw_sha256": "c" * 64}
        sell = {"input_mint": mint, "output_mint": SOL_MINT, "in_amount_raw": 1_000_000_000, "out_amount_raw": back,
                "received_at": to_iso(received), "raw_sha256": "d" * 64}
        self.dbs.market.execute("INSERT INTO security_assessments(candidate_id, assessed_at, config_hash, payload_json)"
                                " VALUES (?, ?, 'cfg', ?)", (candidate, to_iso(received),
                                                             json.dumps({"buy_quote": buy, "sell_quote": sell})))
        return candidate

    def decide(self, candidate: str, *, mint: str = MINT, pool: str = POOL, at: datetime = DECIDED,
               chosen: str = "H3", security: Outcome = Outcome.PASS) -> str:
        base = _context()
        features = base.features.model_copy(update={"mint": mint, "pool_address": pool, "as_of": at})
        context = base.model_copy(update={"features": features, "security_outcome": security,
                                          "security_assessed_at": at - timedelta(seconds=2)})
        self.count += 1
        digest = fingerprint({"decision": self.count, "candidate": candidate})
        PaperLedger(self.dbs.paper_ledger).event(at, "DECISION", {"decision_hash": digest, "chosen": chosen,
                                                                  "context": context.model_dump(mode="json")},
                                                 candidate_id=candidate)
        return digest

    def advisories(self, digest: str) -> dict[str, dict[str, Any]]:
        return {row["advisor_id"]: json.loads(row["payload_json"]) for row in self.dbs.research.execute(
            "SELECT advisor_id, payload_json FROM council_advisories WHERE primary_decision_hash = ?", (digest,))}

    def run(self) -> dict[str, Any]:
        return run_council(self.paths, LOADED, DECIDED + timedelta(minutes=10))


@pytest.fixture
def world(tmp_path: Path):
    w = World(tmp_path)
    try:
        yield w
    finally:
        w.dbs.close()


def test_a_recorded_decision_gets_two_reproducible_advisories(world: World) -> None:
    seed_history(world.dbs, MINT, POOL, signal_extra=0.012)
    digest = world.decide(world.token())
    stats = world.run()
    assert stats["decisions"] == 1 and stats["advisories"] == 2
    records = world.advisories(digest)
    c1, c2 = Advisory.model_validate(records["C1_BRC"]), Advisory.model_validate(records["C2_ECDR"])
    assert c1.stance == "SUPPORT" and c1.rationale_codes == ("SUPPORT_SETUP",)
    assert Decimal(c1.candidate_signal["residual_z"] or "0") >= 2 and c1.evidence.training_bars == 288
    assert c1.economics.status == "CURRENT_COST_QUALIFIED_ONLY" and c1.economics.roundtrip_cost_fraction == "0.02315"
    assert c1.economics.expected_net_return is None and c1.influence == "NONE" and c1.primary_action == "LONG_CANDIDATE"
    assert c2.stance == "CAUTION" and c2.rationale_codes == ("NO_REVERSAL_THESIS",)
    assert c1.expires_at <= (DECIDED + timedelta(seconds=10)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")  # ends with the quote
    assert world.run()["advisories"] == 0                                          # idempotent
    # the same decision advised later, after more data arrived, gives the same evidence and the same view
    seed_history(world.dbs, MINT, POOL, bars=8, end=END + timedelta(hours=2), seed=9)
    repo = CouncilRepository(world.dbs.research)
    decision = pending_decisions_from(world, digest)
    again = Advisor(world.dbs.market, LOADED, benchmark_pool=SOL_POOL, member=lambda *_: True,
                    now=DECIDED + timedelta(days=3)).advise(decision)
    assert [a.input_sha256 for a in again] == [c1.input_sha256, c2.input_sha256]
    assert [a.stance for a in again] == ["SUPPORT", "CAUTION"] and repo.cursor("PAPER") == 1


def pending_decisions_from(world: World, digest: str) -> PrimaryDecision:
    row = world.dbs.paper_ledger.execute("SELECT id, at, candidate_id, payload_json FROM paper_events WHERE kind ="
                                         " 'DECISION' AND payload_json LIKE ?", (f"%{digest}%",)).fetchone()
    payload = json.loads(row["payload_json"])
    from desk.contracts.strategy import DecisionContext
    from desk.core.clock import parse_iso8601

    return PrimaryDecision("PAPER", row["id"], digest, payload["chosen"], parse_iso8601(row["at"]),
                           row["candidate_id"], DecisionContext.model_validate(payload["context"]))


@pytest.mark.parametrize("case,c1,c2", [
    ("young", "UNIVERSE_AGE", "UNIVERSE_AGE"),
    ("security", "PRIMARY_SECURITY_BLOCK", "PRIMARY_SECURITY_BLOCK"),
    ("stale_quotes", "ECONOMICS_INPUT_STALE", "NO_REVERSAL_THESIS"),
    ("costly", "ROUNDTRIP_COST_ABOVE_CEILING", "NO_REVERSAL_THESIS"),
    ("short_history", "INPUT_MISSING:history 120/306 consecutive 15m bars", "NO_REVERSAL_THESIS"),
    ("no_benchmark", "INPUT_MISSING:aligned_15m_bars", "INPUT_MISSING:aligned_15m_bars"),
])
def test_what_the_desk_could_not_know_is_abstain_and_what_it_measured_is_said(world: World, case: str, c1: str,
                                                                               c2: str) -> None:
    seed_history(world.dbs, MINT, POOL, signal_extra=0.012, bars=120 if case == "short_history" else BARS,
                 benchmark=case != "no_benchmark")
    candidate = world.token(first_seen=DECIDED - timedelta(hours=95) if case == "young" else DECIDED - timedelta(days=5),
                            quotes_age=30 if case == "stale_quotes" else 3.0,
                            back=95_000_000 if case == "costly" else 98_000_000)
    digest = world.decide(candidate, security=Outcome.REJECT if case == "security" else Outcome.PASS)
    world.run()
    records = world.advisories(digest)
    assert c1 in records["C1_BRC"]["rationale_codes"] and c2 in records["C2_ECDR"]["rationale_codes"]
    expected_c1 = "OPPOSE" if case == "costly" else "ABSTAIN"
    assert records["C1_BRC"]["stance"] == expected_c1


def test_the_daily_cohort_holds_twenty_members(world: World) -> None:
    digests = []
    for _ in range(21):
        mint, pool = str(Pubkey.new_unique()), str(Pubkey.new_unique())
        digests.append(world.decide(world.token(mint=mint, pool=pool), mint=mint, pool=pool))
    world.run()
    reasons = [world.advisories(d)["C1_BRC"]["rationale_codes"] for d in digests]
    assert all("UNIVERSE_FULL" not in r for r in reasons[:20]) and reasons[20] == ["UNIVERSE_FULL"]
    assert world.dbs.research.execute("SELECT COUNT(*) FROM council_members").fetchone()[0] == 20


def _imports(path: Path) -> set[str]:
    names = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            names |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            names |= {f"{node.module}.{a.name}" for a in node.names}
    return names


def test_no_trading_path_imports_the_council_and_the_council_imports_no_trading_path() -> None:
    root = REPO_ROOT / "src" / "desk"
    for package in ("paper", "live", "signer", "risk", "strategies", "discovery", "security", "features"):
        for path in (root / package).rglob("*.py"):
            assert not any(n.startswith(("desk.council", "desk.ops.council")) for n in _imports(path)), path
    allowed = {"desk.paper.fees"}             # reads market fee observations; computes no trade
    for path in [*(root / "council").rglob("*.py"), root / "ops" / "council.py"]:
        for name in _imports(path):
            if name.startswith(("desk.paper", "desk.live", "desk.signer", "desk.risk")):
                assert name in allowed, f"{path.name} imports {name}"


def test_the_council_writes_nothing_but_its_own_records(world: World) -> None:
    seed_history(world.dbs, MINT, POOL, signal_extra=-0.012)
    world.decide(world.token())
    ledger_before = list(world.dbs.paper_ledger.iterdump())
    market_before = {t: world.dbs.market.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                     for t in ("ohlcv_bars", "benchmark_bars", "quotes", "security_assessments", "discoveries")}
    world.run()
    assert list(world.dbs.paper_ledger.iterdump()) == ledger_before
    assert {t: world.dbs.market.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for t in market_before} == market_before
    kinds = {r[0] for r in world.dbs.research.execute("SELECT kind FROM experiments")}
    assert kinds == {"COUNCIL_REGISTRATION", "COUNCIL_BENCHMARK_REBUILD"}


def test_a_failing_council_is_contained_inside_its_job(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    from desk.ops import collector as collector_module
    from desk.ops import council as council_module
    from desk.ops.collector import Collector, TaskStats

    def boom(*_: Any, **__: Any) -> dict[str, Any]:
        raise RuntimeError("council crashed")

    monkeypatch.setattr(council_module, "run_council", boom)
    secrets = SimpleNamespace(all_secret_values=lambda: [])
    suite: Any = SimpleNamespace(rpc=SimpleNamespace(http=SimpleNamespace(secrets=secrets)))
    collector = Collector(suite, world.dbs, LOADED, discovery=True)
    collector.stats.tasks["council"] = TaskStats()
    asyncio.run(collector.council())
    assert collector.stats.tasks["council"].item_errors == 1 and collector.stats.unhandled_exceptions == 0
    assert collector_module.COUNCIL_INTERVAL_SECONDS == 900


def test_the_report_states_what_the_records_can_and_cannot_show(world: World, monkeypatch: pytest.MonkeyPatch,
                                                                  capsys: pytest.CaptureFixture[str]) -> None:
    seed_history(world.dbs, MINT, POOL, signal_extra=-0.012)
    digest = world.decide(world.token())
    world.run()
    assert world.advisories(digest)["C1_BRC"]["stance"] == "OPPOSE"
    report = council_report(world.dbs, DECIDED + timedelta(hours=1))
    assert report["filter_diagnostic"]["verdict"].startswith("BLOCKED: PRIMARY BASELINE DEGENERATE")
    ledger = PaperLedger(world.dbs.paper_ledger)
    ledger.open_position(position_id="pos1", intent_id="intent-1", candidate_id="c", mint=MINT, pool_address=POOL,
                         strategy_id="H3", strategy_version="H3:2:" + "a" * 64, decision_hash=digest,
                         regime_label=None, at=DECIDED, ticket_lamports=10 ** 8, tokens_raw=10 ** 9,
                         entry_fees_lamports=5000, failed_fees_lamports=0, rent_lamports=0, entry={})
    ledger.close_position("pos1", DECIDED + timedelta(hours=1), reason="HARD_STOP", exit_lamports=8 * 10 ** 7,
                          exit_fees_lamports=5000, profiles={})
    report = council_report(world.dbs, DECIDED + timedelta(hours=2))
    arms = report["filter_diagnostic"]["arms"]
    assert arms["P"]["trades"] == 1 and arms["P+A"]["trades"] == 0 and arms["P+A"]["removed_losers"] == 1
    assert arms["P+B"] == arms["P"] | {"removed": 0, "removed_winners": 0, "removed_losers": 0}
    assert report["filter_diagnostic"]["verdict"].startswith("INSUFFICIENT EVIDENCE")
    assert report["coverage"]["c1_with_full_history"] == 1 and report["coverage"]["benchmark_minutes"] == BARS * 15
    from desk import cli

    monkeypatch.setattr(cli, "_bootstrap", lambda _: (world.paths, []))
    args = cli.build_parser().parse_args(["council", "report"])
    assert args.func(args) == 0
    out = capsys.readouterr().out
    assert "influence NONE" in out and "INSUFFICIENT EVIDENCE" in out


def test_market_context_stores_benchmark_minutes_from_the_payload_it_already_fetched(world: World) -> None:
    from desk.ops.collector import Collector, TaskStats

    now = datetime.now(UTC).replace(second=0, microsecond=0)

    class Gecko:
        calls = 0

        async def ohlcv(self, pool: str, timeframe: str, aggregate: int, limit: int) -> Any:
            Gecko.calls += 1
            bars = [OhlcvBar(pool_address=pool, timeframe="minute:1", ts=now - timedelta(minutes=m), open=Decimal(100),
                             high=Decimal(100), low=Decimal(100), close=Decimal(100), volume_usd=Decimal(5),
                             source="geckoterminal.ohlcv", observed_at=now + timedelta(seconds=5)) for m in range(70)]
            return SimpleNamespace(items=bars, errors=[], raw_sha256="e" * 64)

    secrets = SimpleNamespace(all_secret_values=lambda: [])
    suite: Any = SimpleNamespace(geckoterminal=Gecko(), rpc=SimpleNamespace(http=SimpleNamespace(secrets=secrets)))
    collector = Collector(suite, world.dbs, LOADED)
    collector.stats.tasks["market_context"] = TaskStats()
    asyncio.run(collector.market_context())
    stored = world.dbs.market.execute("SELECT COUNT(*) FROM benchmark_bars").fetchone()[0]
    assert Gecko.calls == 1 and stored == 69 - 1          # every minute closed > 60 s before receipt (cache rule)


def test_the_dashboard_shows_the_latest_records_read_only(world: World) -> None:
    from desk.web.data import DashboardData

    seed_history(world.dbs, MINT, POOL, signal_extra=0.012)
    world.decide(world.token())
    world.run()
    council = DashboardData(world.paths, LOADED).snapshot()["council"]
    assert council["enabled"] and council["influence"] == "NONE" and council["benchmark_minutes"] == BARS * 15
    assert {row["advisor"]: row["stance"] for row in council["latest"]} == {"C1_BRC": "SUPPORT", "C2_ECDR": "CAUTION"}
    assert council["latest"][0]["signal"] and "residual_z" in str(council["latest"])
