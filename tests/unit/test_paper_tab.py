"""The Paper trading tab: plain-English activity, exit levels, fees, funnel and moonshot status from the ledger."""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from desk.core.config import load_config
from desk.core.paths import REPO_ROOT, DeskPaths
from desk.paper.ledger import PaperLedger
from desk.web.app import create_app
from desk.web.paper_tab import paper_tab, plain
from tests.integration.test_paper_lifecycle import _setup, dbs  # noqa: F401  (fixture)

LOADED = load_config(REPO_ROOT / "config")


@pytest.mark.parametrize("code,text", [
    ("SECURITY_DEFER", "safety checks not finished"), ("GUARDIAN_NOT_SUPERVISING", "the exit guardian is not running"),
    ("FEATURE_MISSING:distinct_buyers_1h", "no distinct buyers 1h data"),
    ("FEATURE_STALE:roundtrip_cost_fraction", "roundtrip cost fraction data is stale"),
    ("ENTRY_CONDITION_FALSE:log_return_1h:gte:0.405", "needs log return 1h ≥ 0.405"),
    ("HALTED:STOP", "halted (stop is set)"), ("TRAILING_STOP", "trailing stop"),
    ("SOMETHING_NEW", "something new"),
])
def test_reason_codes_read_as_plain_english(code: str, text: str) -> None:
    assert plain(code) == text


def test_tab_explains_what_the_book_did(dbs: Any, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    engine, guardian, jupiter, clock, candidate = _setup(dbs, monkeypatch)
    assert asyncio.run(engine.consider(candidate)) == "OPENED"
    clock.now += timedelta(seconds=30)
    asyncio.run(guardian.tick())                                         # an executable mark
    tab = paper_tab(dbs, LOADED, clock.now)
    assert tab["status"]["headline"] == "Paper trading active" or tab["status"]["tone"] in ("neutral", "warning")
    opened = tab["open"][0]
    assert opened["strategy"] == "H1" and opened["mark_kind"] == "EXECUTABLE"   # hypotheses outrank baselines
    assert Decimal(opened["hard_stop_sol"]) == Decimal("0.09")            # H1 stop: -10% of a 0.1 SOL ticket
    assert opened["trailing_armed"] is False and opened["hold_remaining_seconds"] > 0
    titles = [a["title"] for a in tab["activity"]]
    assert any(t.startswith("Bought") and "0.100000 SOL" in t for t in titles)
    assert any(t.startswith("Signal on") for t in titles)
    groups = {a["kind"]: a["group"] for a in tab["activity"]}
    assert groups == {"DECISION": "signal", "ENTRY_ATTEMPT": "trade", "OPENED": "trade"}
    assert tab["funnel"]["all_time"] == {"decisions": 1, "no_trade": 0, "signals": 1, "blocked": 0, "skipped_size": 0,
                                          "skipped_cost": 0, "attempts": 1, "failed": 0, "opened": 1, "closed": 0}

    jupiter.price = Decimal("0.8")                                       # hard stop
    clock.now += timedelta(seconds=30)
    asyncio.run(guardian.tick())
    tab = paper_tab(dbs, LOADED, clock.now)
    assert not tab["open"] and len(tab["closed"]) == 1
    trade = tab["closed"][0]
    assert trade["exit_reason"] == "hard stop" and Decimal(trade["return"]) < 0
    assert Decimal(trade["fees_sol"]) > 0 and trade["breakdown"]["close_account_tx_lamports"] == 105_000
    assert any(a["title"].startswith("Sold") and "−" in a["title"] for a in tab["activity"])
    book = tab["book"]
    assert book["closed_trades"] == 1 and book["open_positions"] == 0 and Decimal(book["cash_sol"]) > 0
    fees = tab["fees"]
    assert fees["current_estimate"]["source"] == "FALLBACK_MODEL" and Decimal(fees["average_round_trip_pct"]) > 0
    assert tab["funnel"]["all_time"]["opened"] == 1 and tab["funnel"]["all_time"]["closed"] == 1
    assert tab["exit_reasons"] == {"hard stop": 1} and len(tab["curve"]) == 1
    moonshot = tab["moonshot"]
    assert moonshot["strategy"] == "H8" and moonshot["conviction_evidence"] == "NO_REPORT"
    # a loss never shrinks the $100 base (1 SOL here): 10% normally, 15% for an evidence-backed moonshot
    assert (moonshot["normal_ticket_sol"], moonshot["sized_up_ticket_sol"]) == ("0.100000", "0.150000")
    assert book["next_ticket_sol"] == "0.100000" and book["next_ticket_blockers"] == []
    assert PaperLedger(dbs.paper_ledger).reconcile()["difference_lamports"] == 0


def test_feed_keeps_trades_visible_beside_a_flood_of_decisions(dbs: Any, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    engine, _, _, clock, candidate = _setup(dbs, monkeypatch)
    assert asyncio.run(engine.consider(candidate)) == "OPENED"
    ledger = PaperLedger(dbs.paper_ledger)
    ranking = [{"id": "H3", "eligible": False, "score": "0", "reasons": ["FEATURE_MISSING:distinct_buyers_1h"]}]
    for i in range(120):                                   # far more looks than the feed keeps
        ledger.event(clock.now + timedelta(seconds=i), "DECISION",
                     {"chosen": "NO_TRADE", "regime_label": None, "ranking": ranking, "context": {"big": "x" * 500}},
                     candidate_id=candidate)
    tab = paper_tab(dbs, LOADED, clock.now + timedelta(minutes=5))
    kinds = [a["kind"] for a in tab["activity"]]
    assert kinds.count("DECISION") == 50 and "OPENED" in kinds and "ENTRY_ATTEMPT" in kinds
    assert [a["id"] for a in tab["activity"]] == sorted((a["id"] for a in tab["activity"]), reverse=True)
    look = next(a for a in tab["activity"] if a["group"] == "look")
    assert look["title"].startswith("Looked at") and look["detail"].startswith("no distinct buyers 1h data")
    assert "context" not in json.dumps(tab["activity"])     # decision contexts never reach the browser
    funnel = tab["funnel"]["all_time"]
    assert (funnel["decisions"], funnel["no_trade"], funnel["signals"]) == (121, 120, 1)


def test_paper_endpoint_is_read_only_and_reports_an_empty_book(desk_home: Path, config_dir: Path,
                                                               tmp_path: Path) -> None:
    paths = DeskPaths(desk_home, config_dir, tmp_path / "secrets.env")
    with TestClient(create_app(paths), base_url="http://127.0.0.1") as client:
        response = client.get("/api/paper")
        assert response.status_code == 200
        body = response.json()
        assert body["initialized"] is False and body["status"]["headline"]
        assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
