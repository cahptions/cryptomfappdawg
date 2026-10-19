"""M8-06 in production: the signer's real per-intent authorizer refuses a BUY when any single LIVE gate is missing.

A fixture world satisfies all seven gates (PLAN §16.5); each case removes exactly one and the real ``Signer`` with
the real ``LiveAuthorizer`` must refuse before any order, simulation or signature. With every gate present the same
intent is ordered, inspected, simulated, signed once and confirmed against fake transports. No key is real, no
network is touched and no transaction leaves the process.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from solders.keypair import Keypair

from desk.contracts.discovery import Outcome
from desk.contracts.observed import DataStatus
from desk.contracts.strategy import DecisionContext
from desk.core.clock import to_epoch_millis, to_iso
from desk.core.config import Mode, load_config
from desk.core.hashing import canonical_json
from desk.core.paths import REPO_ROOT, DeskPaths
from desk.live.authorize import LiveAuthorizer
from desk.live.gates import ELIGIBLE, LIVE_GUARDIAN
from desk.paper.ledger import PaperLedger
from desk.signer.journal import Journal
from desk.signer.main import capital_ceiling
from desk.signer.service import Signer
from desk.storage.databases import Databases, open_ledger
from desk.storage.shadow_repo import ShadowRepository
from desk.strategies import builtin_specs
from desk.strategies.specs import paper_specs
from tests.unit.test_signer import AMOUNT, MIN_OUT, FakeRpc, FakeSwap, jupiter_buy, order_for
from tests.unit.test_strategies import NOW as CONTEXT_AT
from tests.unit.test_strategies import _context

AT = CONTEXT_AT + timedelta(seconds=30)
H1 = next(spec for spec in paper_specs() if spec.strategy_id == "H1")
LIVE_CONFIG = load_config(REPO_ROOT / "config", {"desk.mode": "LIVE", "live.max_capital_sol": "1"})
QUOTE = "quote-sha-1"
CANDIDATE = "cand-0001"


class World:
    """Every gate holds until a test removes one."""

    def __init__(self, tmp_path: Path, mint: str) -> None:
        self.paths = DeskPaths(tmp_path / "data", REPO_ROOT / "config", tmp_path / "none.env")
        self.mint = mint
        dbs = Databases.open(self.paths)
        repo = ShadowRepository(dbs.market)
        repo.register_specs(builtin_specs(), AT)
        repo.register_specs(paper_specs(), AT)
        dbs.research.execute("INSERT INTO approvals(id, created_at, kind, subject, confirmation, payload_json) "
                             "VALUES ('appr-1', ?, 'LIVE_STRATEGY', ?, ?, '{}')",
                             (to_iso(AT), H1.version_id, f"APPROVE LIVE {H1.version_id}"))
        self.report(ELIGIBLE, AT - timedelta(days=1), dbs)
        self.beat(AT - timedelta(seconds=3), dbs)
        dbs.market.execute("INSERT INTO market_context(observed_at, observed_at_ms, version, coverage_json) "
                           "VALUES (?, ?, 'fixture', '{}')", (to_iso(AT), to_epoch_millis(AT) - 10_000))
        dbs.market.execute("INSERT INTO quotes(source, outcome, input_mint, output_mint, in_amount_raw, out_amount_raw, "
                           "received_at, received_at_ms, payload_json, raw_sha256) VALUES ('fixture', 'quote', ?, ?, ?, ?, "
                           "?, ?, '{}', ?)", ("So11111111111111111111111111111111111111112", mint, str(AMOUNT),
                                               str(MIN_OUT), to_iso(AT), to_epoch_millis(AT) - 5_000, QUOTE))
        dbs.close()
        open_ledger(self.paths.db_dir, Mode.LIVE).close()
        self.env = {"DESK_LIVE_CONFIRM": "yes"}
        self.loaded = LIVE_CONFIG
        self.ceiling = capital_ceiling(self.paths)
        self.context = _context().model_copy(update={"features": _context().features.model_copy(update={"mint": mint})})

    def report(self, readiness: str, at: datetime, dbs: Databases | None = None) -> None:
        own = dbs or Databases.open(self.paths)
        payload = {"kind": "CONFIDENCE_MATRIX", "matrices": [{"strategy_id": "H1", "strategy_version": H1.version_id,
                                                              "readiness": readiness, "cells": []}]}
        own.research.execute("INSERT INTO experiments(id, created_at, kind, payload_json) VALUES (?, ?, 'CONFIDENCE_MATRIX', ?)",
                             (f"conf-{to_epoch_millis(at)}-{readiness[:4]}", to_iso(at), canonical_json(payload)))
        if dbs is None:
            own.close()

    def beat(self, at: datetime, dbs: Databases | None = None) -> None:
        own = dbs or Databases.open(self.paths)
        own.runtime.execute("INSERT INTO heartbeats(process, pid, at) VALUES (?, 1, ?) ON CONFLICT(process) "
                            "DO UPDATE SET at = excluded.at", (LIVE_GUARDIAN, to_iso(at)))
        if dbs is None:
            own.close()

    def authorizer(self) -> LiveAuthorizer:
        context = self.context
        return LiveAuthorizer(self.paths, self.loaded, self.ceiling, env=self.env,
                              context=lambda _conn, candidate, _now: context)


def _intent(mint: str, **changes: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"intent_id": "intent-live-1", "side": "BUY", "mint": mint, "amount_raw": AMOUNT,
                            "max_slippage_bps": 50, "min_out_raw": MIN_OUT, "quote_ref": QUOTE,
                            "expires_at": (AT + timedelta(seconds=20)).isoformat(),
                            "strategy_version": H1.version_id, "candidate_id": CANDIDATE}
    return base | changes


def _run(world: World, tmp_path: Path, **intent_changes: Any) -> tuple[dict[str, Any], FakeSwap, FakeRpc]:
    wallet = Keypair()
    swap = FakeSwap(order_for(wallet.pubkey(), world.mint, jupiter_buy(wallet.pubkey(), world.mint)))
    rpc = FakeRpc(wallet.pubkey())

    async def no_sleep(_: float) -> None:
        return None

    signer = Signer(wallet, Journal(tmp_path / "journal.db"), swap, rpc, world.loaded.settings.live,
                    world.loaded.settings.limits, world.loaded.settings.sizing, clock=lambda: AT, sleep=no_sleep,
                    buy_authorizer=world.authorizer())
    result = asyncio.run(signer.handle({"op": "intent", "intent": _intent(world.mint, **intent_changes)}))
    return result, swap, rpc


@pytest.fixture
def world(tmp_path: Path) -> World:
    from tests.unit.test_signer import MINT

    return World(tmp_path, MINT)


def test_all_seven_gates_present_lets_the_signer_buy_once(world: World, tmp_path: Path) -> None:
    result, swap, rpc = _run(world, tmp_path)
    assert result["ok"] and result["state"] == "CONFIRMED", result
    assert len(swap.orders) == 1 and len(swap.executions) == 1 and len(rpc.simulations) == 1


def _no_env(w: World) -> None:
    w.env = {}


def _paper_mode(w: World) -> None:
    w.loaded = load_config(REPO_ROOT / "config", {"live.max_capital_sol": "1"})


def _no_approval(w: World) -> None:
    other = next(spec for spec in paper_specs() if spec.strategy_id == "H3")
    dbs = Databases.open(w.paths)
    try:
        dbs.research.execute("DROP TRIGGER approvals_no_delete")       # test-only: build the world without it
        dbs.research.execute("DELETE FROM approvals")
        dbs.research.execute("INSERT INTO approvals(id, created_at, kind, subject, confirmation, payload_json) "
                             "VALUES ('appr-2', ?, 'LIVE_STRATEGY', ?, 'x', '{}')", (to_iso(AT), other.version_id))
    finally:
        dbs.close()


def _not_eligible(w: World) -> None:
    w.report("CONTINUE PAPER TESTING", AT - timedelta(hours=1))


def _stale_report(w: World) -> None:
    dbs = Databases.open(w.paths)
    try:
        dbs.research.execute("DROP TRIGGER experiments_no_delete")
        dbs.research.execute("DELETE FROM experiments")
    finally:
        dbs.close()
    w.report(ELIGIBLE, AT - timedelta(days=8))


def _capital_above_ceiling(w: World) -> None:
    w.ceiling = Decimal("0.5")


def _stop(w: World) -> None:
    w.paths.stop_file.write_text("stop\n")


def _halted(w: World) -> None:
    conn = open_ledger(w.paths.db_dir, Mode.LIVE)
    try:
        PaperLedger(conn).halt("DAILY_LOSS_LIMIT", AT)
    finally:
        conn.close()


def _guardian_stale(w: World) -> None:
    w.beat(AT - timedelta(minutes=2))


def _stale_context(w: World) -> None:
    dbs = Databases.open(w.paths)
    try:
        dbs.market.execute("DROP TRIGGER market_context_no_delete")
        dbs.market.execute("DELETE FROM market_context")
    finally:
        dbs.close()


def _position_limit(w: World) -> None:
    conn = open_ledger(w.paths.db_dir, Mode.LIVE)
    ledger = PaperLedger(conn)
    try:
        ledger.init_book(AT, Decimal(100), Decimal(100), Decimal(100))
        for i in range(LIVE_CONFIG.settings.limits.max_open_positions):
            ledger.open_position(position_id=f"p{i}", intent_id=f"i{i}", candidate_id="c", mint=f"Mint{i}",
                                 pool_address="pool", strategy_id="H1", strategy_version=H1.version_id,
                                 decision_hash="d", regime_label="RISK_ON", at=AT, ticket_lamports=10 ** 8,
                                 tokens_raw=1, entry_fees_lamports=0, failed_fees_lamports=0, rent_lamports=0, entry={})
    finally:
        conn.close()


def _security_deferred(w: World) -> None:
    w.context = w.context.model_copy(update={"security_outcome": Outcome.DEFER})


def _regime_uncalibrated(w: World) -> None:
    w.context = w.context.model_copy(update={"regime_status": DataStatus.MISSING, "regime_label": None})


def _regime_not_eligible(w: World) -> None:
    w.context = w.context.model_copy(update={"regime_label": "RISK_OFF"})


def _decision_gone(w: World) -> None:
    from tests.unit.test_strategies import _feature, _replace_feature

    w.context = _replace_feature(w.context, "distinct_buyers_1h", _feature("12"))       # H1 needs >= 100


CASES: list[tuple[str, Callable[[World], None], str]] = [
    ("1 mode: no DESK_LIVE_CONFIRM", _no_env, "GATE_1_MODE"),
    ("1 mode: config is not LIVE", _paper_mode, "GATE_1_MODE"),
    ("2 approval for another version only", _no_approval, "GATE_2_NOT_APPROVED"),
    ("3 readiness not eligible", _not_eligible, "GATE_3_READINESS"),
    ("3 readiness report older than 7 days", _stale_report, "GATE_3_READINESS"),
    ("4 capital above the ceiling", _capital_above_ceiling, "GATE_4_CAPITAL"),
    ("6 STOP file", _stop, "GATE_6_STOP"),
    ("6 LIVE halt", _halted, "GATE_6_HALTED:DAILY_LOSS_LIMIT"),
    ("6 LIVE guardian not supervising", _guardian_stale, "GATE_6_GUARDIAN"),
    ("6 market data stale", _stale_context, "GATE_6_STALE_DATA"),
    ("6 LIVE position limit", _position_limit, "GATE_6_POSITION_LIMIT"),
    ("6 security not a fresh PASS", _security_deferred, "GATE_6_SECURITY"),
    ("7 regime uncalibrated", _regime_uncalibrated, "GATE_7_REGIME_UNCALIBRATED"),
    ("7 regime not eligible", _regime_not_eligible, "GATE_7_REGIME_NOT_ELIGIBLE"),
    ("decision no longer holds on current data", _decision_gone, "DECISION_NO_LONGER_HOLDS"),
]


@pytest.mark.parametrize("label,remove,problem", CASES, ids=[c[0] for c in CASES])
def test_m8_06_each_single_missing_gate_refuses_before_order_or_signature(
    world: World, tmp_path: Path, label: str, remove: Callable[[World], None], problem: str,
) -> None:
    remove(world)
    result, swap, rpc = _run(world, tmp_path)
    assert result["error"] == "REFUSED" and problem in result["problems"], (label, result)
    assert not swap.orders and not swap.executions and not rpc.simulations


def test_quote_must_be_the_one_just_recorded(world: World, tmp_path: Path) -> None:
    result, swap, _ = _run(world, tmp_path, quote_ref="unknown-quote")
    assert "GATE_6_QUOTE_STALE" in result["problems"] and not swap.orders


def test_disarmed_signer_and_zero_capital_refuse_first(world: World, tmp_path: Path) -> None:
    world.loaded = load_config(REPO_ROOT / "config", {"desk.mode": "LIVE"})          # max_capital_sol = 0
    result, swap, _ = _run(world, tmp_path)
    assert "NO_CAPITAL_ALLOCATION" in result["problems"] and "GATE_4_CAPITAL" in result["problems"] and not swap.orders


def test_missing_live_ledger_or_unknown_version_is_a_refusal_not_a_pass(world: World, tmp_path: Path) -> None:
    (world.paths.db_dir / "ledger-live.db").unlink()
    result, _, _ = _run(world, tmp_path)
    assert result["problems"] == ["EVIDENCE_UNAVAILABLE:ledger-live.db"]
    open_ledger(world.paths.db_dir, Mode.LIVE).close()
    unknown = "H1:9:" + "b" * 64
    result, _, _ = _run(world, tmp_path / "second", strategy_version=unknown)
    assert "STRATEGY_VERSION_UNKNOWN" in result["problems"]


def test_a_buy_must_name_its_decision() -> None:
    from desk.signer.intents import Intent

    with pytest.raises(ValueError, match="strategy_version and candidate_id"):
        Intent.model_validate({**_intent("So11111111111111111111111111111111111111112"), "strategy_version": None})
    sell = Intent.model_validate({**_intent("So11111111111111111111111111111111111111112"), "side": "SELL",
                                  "strategy_version": None, "candidate_id": None})
    assert sell.side == "SELL"


def test_override_record_satisfies_readiness(world: World, tmp_path: Path) -> None:
    _not_eligible(world)
    dbs = Databases.open(world.paths)
    try:
        dbs.research.execute("INSERT INTO approvals(id, created_at, kind, subject, confirmation, payload_json) VALUES "
                             "('ovr-1', ?, 'READINESS_OVERRIDE', ?, 'OVERRIDE', ?)",
                             (to_iso(AT), H1.version_id, json.dumps({"reason": "operator decision"})))
    finally:
        dbs.close()
    result, swap, _ = _run(world, tmp_path)
    assert result["ok"] and len(swap.executions) == 1


def test_context_is_recomputed_for_the_intents_candidate(world: World, tmp_path: Path) -> None:
    seen: list[str] = []
    context = world.context

    def provider(_conn: Any, candidate: str, _now: datetime) -> DecisionContext:
        seen.append(candidate)
        return context

    world_authorizer = LiveAuthorizer(world.paths, world.loaded, world.ceiling, env=world.env, context=provider)
    from desk.signer.intents import Intent

    assert world_authorizer(Intent.model_validate(_intent(world.mint)), AT) == []
    assert seen == [CANDIDATE]
    other = context.model_copy(update={"features": context.features.model_copy(
        update={"mint": "So11111111111111111111111111111111111111112"})})
    mismatch = LiveAuthorizer(world.paths, world.loaded, world.ceiling, env=world.env,
                              context=lambda *_: other)
    assert "CANDIDATE_MINT_DIFFERS" in mismatch(Intent.model_validate(_intent(world.mint)), AT)
    assert datetime(2026, 1, 1, tzinfo=UTC) < AT                                # fixture sanity


def test_arm_wires_the_production_authorizer_into_the_signer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The gap found in review: `desk live arm` used to build the signer without any BUY authorizer."""
    import getpass

    from desk.core.secrets import Secrets
    from desk.signer import main

    paths = DeskPaths(tmp_path / "data", REPO_ROOT / "config", tmp_path / "none.env")
    paths.ensure()
    built: list[Any] = []

    class Recording(Signer):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            built.append(kwargs.get("buy_authorizer"))
            super().__init__(*args, **kwargs)

    async def serve_once(*_: Any) -> None:
        return None

    monkeypatch.setattr(main.keystore, "unlock", lambda *_: Keypair())
    monkeypatch.setattr(getpass, "getpass", lambda *_: "not a real passphrase")
    monkeypatch.setattr(main, "Signer", Recording)
    monkeypatch.setattr(main, "serve", serve_once)
    assert main.arm(paths, LIVE_CONFIG, Secrets()) == 0
    assert len(built) == 1 and isinstance(built[0], LiveAuthorizer)
    assert built[0].capital_ceiling_sol == capital_ceiling(paths) and built[0].loaded is LIVE_CONFIG
