"""M8 LIVE executor end to end against fakes: decision → authorized BUY → booked from measured balances → exit
through the signer → token account closed → booked at what the wallet received.

The real LIVE book, engine, guardian, limits and liquidation run against a fake signer socket (answers, a journal
for lookups, lost answers on demand), a fake chain (the wallet's token accounts) and fake Jupiter quotes. No key
exists, nothing is signed and nothing leaves the process. The signer's own checks have their own tests.
"""

from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import Callable
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from solders.pubkey import Pubkey

from desk.contracts.quotes import QuoteObservation, QuoteUnavailable
from desk.contracts.strategy import DecisionContext
from desk.core.clock import to_iso
from desk.core.config import Mode, load_config
from desk.core.paths import REPO_ROOT, DeskPaths
from desk.live import engine as engine_module
from desk.live.book import LiveBook
from desk.live.engine import LiveEngine
from desk.live.guardian import LiveGuardian
from desk.live.link import SignerLink
from desk.live.liquidate import liquidate_all
from desk.regime.context import INPUTS, ContextRecord, save_context
from desk.security.policy import SOL_MINT, USDC_MINT
from desk.signer import programs
from desk.signer.inspect import associated_token_address
from desk.storage.databases import Databases, open_ledger
from desk.storage.market_repo import MarketRepository
from desk.strategies.specs import paper_specs
from tests.integration.test_paper_lifecycle import Clock, FakeJupiter, no_sleep
from tests.unit.test_strategies import NOW, _context

LOADED = load_config(REPO_ROOT / "config", {"desk.mode": "LIVE", "live.max_capital_sol": "1"})
H1 = next(spec for spec in paper_specs() if spec.strategy_id == "H1")
B1 = next(spec for spec in paper_specs() if spec.strategy_id == "B1")
RENT = 2_039_280
BUY_FEE = 7_000
CLOSE_FEE = 5_000


def token_account(amount: int) -> dict[str, Any]:
    data = base64.b64encode(bytes(64) + amount.to_bytes(8, "little") + bytes(93)).decode()
    return {"data": [data, "base64"], "lamports": RENT, "owner": programs.TOKEN}


class Quotes(FakeJupiter):
    """FakeJupiter prices, received at the test clock's time, each with its own raw reference; no route for ``dead``."""

    def __init__(self, clock: Clock) -> None:
        super().__init__()
        self.clock, self.dead, self.count = clock, set[str](), 0

    async def order(self, input_mint: str, output_mint: str, amount: int) -> QuoteObservation | QuoteUnavailable:
        self.count += 1
        if input_mint in self.dead or output_mint in self.dead:
            return QuoteUnavailable(source="fake", input_mint=input_mint, output_mint=output_mint, in_amount_raw=amount,
                                    error_code="NO_ROUTE", error_message=None, http_status=200, received_at=self.clock())
        quote = await super().order(input_mint, output_mint, amount)
        assert isinstance(quote, QuoteObservation)
        return quote.model_copy(update={"requested_at": self.clock(), "received_at": self.clock(),
                                        "raw_sha256": f"quote-{self.count}"})


class Chain:
    """The wallet's token accounts, as getMultipleAccounts(base64) reports them."""

    def __init__(self) -> None:
        self.wallet = str(Pubkey.new_unique())
        self.holdings: dict[str, int] = {}

    async def get_multiple_accounts(self, addresses: list[str], *, encoding: str = "jsonParsed") -> tuple[Any, None]:
        assert encoding == "base64"
        mints = {associated_token_address(self.wallet, mint, programs.TOKEN): mint for mint in self.holdings}
        return {"value": [token_account(self.holdings[mints[a]]) if a in mints else None for a in addresses]}, None


class Signer:
    """The signer socket as the desk sees it. ``respond`` may script an answer; ``lose``/``drop`` lose one answer
    after (or the request before) the signer journals it."""

    def __init__(self, chain: Chain) -> None:
        self.chain = chain
        self.journal: dict[str, dict[str, Any]] = {}
        self.requests: list[dict[str, Any]] = []
        self.lose: set[str] = set()
        self.drop: set[str] = set()
        self.respond: Callable[[str, dict[str, Any]], dict[str, Any] | None] = lambda op, request: None

    def ops(self, op: str, side: str | None = None) -> list[dict[str, Any]]:
        return [r for r in self.requests if r["op"] == op and (side is None or r["intent"]["side"] == side)]

    async def send(self, payload: dict[str, Any]) -> dict[str, Any]:
        op = payload["op"]
        if op in self.drop:
            self.drop.discard(op)
            raise ConnectionResetError
        self.requests.append(payload)
        if op == "status":
            return {"ok": True, "wallet": self.chain.wallet, "unlocked": True}
        if op == "lookup":
            row = self.journal.get(payload["intent_id"])
            return {"ok": True, "found": False} if row is None else {"ok": True, "found": True, **row}
        request = payload["intent"] if op == "intent" else payload["close"]
        answer = self.respond(op, request) or self.default(op, request)
        self.journal[request["intent_id"]] = {
            "state": answer.get("state", "REFUSED"), "signature": answer.get("signature"),
            "kind": "SWAP" if op == "intent" else "CLOSE",
            "detail": {"balances": answer.get("balances"), "problems": answer.get("problems")}}
        if answer.get("state") == "CONFIRMED":
            self.land(op, request, answer["balances"])
        if op in self.lose:
            self.lose.discard(op)
            raise TimeoutError
        return answer

    def default(self, op: str, request: dict[str, Any]) -> dict[str, Any]:
        if op == "close":
            return {"ok": True, "state": "CONFIRMED", "signature": "sig-close", "balances": {"sol_lamports": RENT - CLOSE_FEE}}
        if request["side"] == "BUY":
            return {"ok": True, "state": "CONFIRMED", "signature": "sig-buy", "balances": {
                "sol_lamports": -(request["amount_raw"] + BUY_FEE + RENT), "input_token_raw": 0,
                "output_token_raw": request["min_out_raw"] + 1_000, "output_account_created": True,
                "output_account_lamports": RENT}}
        return {"ok": True, "state": "CONFIRMED", "signature": "sig-sell", "balances": {
            "sol_lamports": request["min_out_raw"] + 2_000, "input_token_raw": -request["amount_raw"],
            "output_token_raw": 0, "output_account_created": False, "output_account_lamports": 0}}

    def land(self, op: str, request: dict[str, Any], balances: dict[str, Any] | None) -> None:
        mint = request["mint"]
        if op == "close":
            self.chain.holdings.pop(mint, None)
        elif request["side"] == "BUY":
            received = balances["output_token_raw"] if balances else request["min_out_raw"] + 1_000
            self.chain.holdings[mint] = self.chain.holdings.get(mint, 0) + received
        else:
            self.chain.holdings[mint] -= -balances["input_token_raw"] if balances else request["amount_raw"]


def refused(*problems: str) -> dict[str, Any]:
    return {"ok": False, "error": "REFUSED", "problems": list(problems)}


class World:
    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, approve: tuple[str, ...] = (H1.version_id,)) -> None:
        self.paths = DeskPaths(tmp_path / "data", REPO_ROOT / "config", tmp_path / "none.env")
        self.dbs = Databases.open(self.paths)
        self.conn = open_ledger(self.paths.db_dir, Mode.LIVE)
        self.book = LiveBook(self.conn)
        self.clock = Clock()
        self.jupiter = Quotes(self.clock)
        self.chain = Chain()
        self.signer = Signer(self.chain)
        self.problems: list[str] = []
        self.authorized: list[Any] = []
        self.mints: dict[str, str] = {}
        suite: Any = SimpleNamespace(jupiter=self.jupiter, rpc=self.chain)

        def authorize(intent: Any, _now: Any) -> list[str]:
            self.authorized.append(intent)
            return list(self.problems)

        self.engine = LiveEngine(suite, self.dbs, LOADED, self.book, SignerLink(self.signer.send), authorize,
                                 clock=self.clock)
        self.guardian = LiveGuardian(suite, self.dbs, LOADED, self.book, SignerLink(self.signer.send),
                                     clock=self.clock, sleep=no_sleep)
        for version in approve:
            self.dbs.research.execute("INSERT INTO approvals(id, created_at, kind, subject, confirmation, payload_json)"
                                      " VALUES (?, ?, 'LIVE_STRATEGY', ?, ?, '{}')",
                                      (f"appr-{version[:2]}", to_iso(NOW), version, f"APPROVE LIVE {version}"))
        monkeypatch.setattr(engine_module, "decision_context", self.context)
        self.engine_market = MarketRepository(self.dbs.market)
        self.beat()
        asyncio.run(self.engine.start())

    def context(self, _conn: Any, candidate_id: str, now: Any, _references: Any = None) -> DecisionContext:
        base, delta = _context(), now - NOW
        values = {n: v.model_copy(update={"observed_at": v.observed_at + delta}) for n, v in base.features.values.items()}
        features = base.features.model_copy(update={"mint": self.mints[candidate_id], "as_of": now, "values": values})
        shifted = {name: getattr(base, name) + delta for name in ("security_assessed_at", "discovery_observed_at",
                                                                  "migration_occurred_at")}
        return base.model_copy(update={"features": features, **shifted})

    def beat(self) -> None:
        """What the running desk keeps fresh: market context, the LIVE guardian's heartbeat, discovery's SOL/USD."""
        save_context(self.dbs.market, ContextRecord(observed_at=self.clock.now, reasons=dict.fromkeys(INPUTS, "TEST")))
        self.guardian.heartbeat()
        self.engine_market.save_quote(asyncio.run(self.jupiter.order(SOL_MINT, USDC_MINT, 10 ** 8)))

    def candidate(self) -> str:
        candidate_id = f"cand-{len(self.mints) + 1}"
        self.mints[candidate_id] = str(Pubkey.new_unique())
        return candidate_id

    def consider(self) -> str:
        return asyncio.run(self.engine.consider(self.candidate()))

    def later(self, seconds: float) -> None:
        self.clock.now += timedelta(seconds=seconds)
        self.beat()

    def events(self, kind: str) -> list[dict[str, Any]]:
        return [json.loads(r["payload_json"]) for r in self.conn.execute(
            "SELECT payload_json FROM paper_events WHERE kind = ? ORDER BY id", (kind,))]

    def close(self) -> None:
        self.conn.close()
        self.dbs.close()


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    w = World(tmp_path, monkeypatch)
    try:
        yield w
    finally:
        w.close()


ENTRY_COST = 10 ** 8 + BUY_FEE + RENT


# ── entries ──────────────────────────────────────────────────────────────────────────────────────────
def test_a_buy_is_sized_quoted_authorized_sent_once_and_booked_from_what_left_the_wallet(world: World) -> None:
    assert world.book.deposits() == 10 ** 9                              # live.max_capital_sol = 1
    assert world.consider() == "OPENED"
    (request,) = world.signer.ops("intent")
    intent = request["intent"]
    assert intent["side"] == "BUY" and intent["amount_raw"] == 10 ** 8   # 10% of min(equity, allocation)
    assert intent["strategy_version"] == H1.version_id and intent["candidate_id"] == "cand-1"
    assert intent["min_out_raw"] == 990_000_000                          # its exact fresh quote less 1% slippage
    quote = world.dbs.market.execute("SELECT in_amount_raw FROM quotes WHERE raw_sha256 = ?", (intent["quote_ref"],))
    assert quote.fetchone()["in_amount_raw"] == str(10 ** 8)
    assert [a.intent_id for a in world.authorized] == [intent["intent_id"]]       # pre-checked like the signer will
    (position,) = world.book.open_positions()
    assert (position["ticket_lamports"], position["tokens_raw"]) == (10 ** 8, 990_001_000)
    assert (position["entry_fees_lamports"], position["rent_lamports"]) == (BUY_FEE, RENT)
    assert world.book.cash_lamports() == 10 ** 9 - ENTRY_COST            # exactly what the wallet paid
    assert world.book.reconcile()["difference_lamports"] == 0 and world.book.pending() == []
    decision = world.events("DECISION")[0]
    assert decision["mode"] == "LIVE" and decision["chosen"] == "H1" and decision["context"]["regime_label"] == "RISK_ON"


def test_an_intent_the_signer_would_refuse_is_never_sent(world: World) -> None:
    world.problems = ["GATE_7_REGIME_UNCALIBRATED"]
    assert world.consider() == "BLOCKED"
    assert world.signer.ops("intent") == [] and world.book.pending() == []
    blocked = world.events("ENTRY_BLOCKED")[0]
    assert blocked["reasons"] == ["GATE_7_REGIME_UNCALIBRATED"] and blocked["stage"] == "authorization"


def test_only_approved_hypotheses_trade_live(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for approve in ((), (B1.version_id,)):                               # nothing approved; a baseline approved
        w = World(tmp_path / str(len(approve)), monkeypatch, approve=approve)
        try:
            assert w.consider() == "NO_APPROVED_STRATEGY"
            assert w.signer.requests == [] and w.events("DECISION") == []
        finally:
            w.close()


def test_a_lost_buy_answer_is_looked_up_and_booked_once_never_resent(world: World) -> None:
    world.signer.lose.add("intent")                                      # the BUY lands; its answer never arrives
    assert world.consider() == "INTENT_UNRESOLVED"
    assert world.book.open_positions() == [] and len(world.book.pending(("BUY",))) == 1
    assert world.consider() == "OPENED"                                  # the journal first, then the next decision
    first, second = world.signer.ops("intent")
    (lookup,) = world.signer.ops("lookup")
    assert lookup["intent_id"] == first["intent"]["intent_id"] != second["intent"]["intent_id"]
    assert len(world.book.open_positions()) == 2 and world.book.pending() == []
    paid = sum(r["intent"]["amount_raw"] + BUY_FEE + RENT for r in (first, second))   # the second is 10% of equity
    assert world.book.cash_lamports() == 10 ** 9 - paid and world.book.reconcile()["difference_lamports"] == 0


def test_a_request_that_never_arrived_waits_for_its_expiry_before_trading_resumes(world: World) -> None:
    world.signer.drop.add("intent")
    assert world.consider() == "INTENT_UNRESOLVED"
    assert world.consider() == "INTENT_UNRESOLVED"                       # not found yet, not expired: could arrive
    world.later(60)
    assert world.consider() == "OPENED"
    resolved = world.events("INTENT_RESOLVED")
    assert resolved[0]["state"] == "NOT_RECEIVED" and resolved[1]["state"] == "CONFIRMED"


def test_a_failed_buy_books_its_fee_and_an_expired_one_books_nothing(world: World) -> None:
    world.signer.respond = lambda op, request: {"ok": False, "state": "FAILED", "signature": "sig-f",
                                                "balances": {"sol_lamports": -6_000, "output_token_raw": 0}}
    assert world.consider() == "ENTRY_FAILED"
    assert world.book.cash_lamports() == 10 ** 9 - 6_000 and world.book.open_positions() == []
    world.signer.respond = lambda op, request: {"ok": False, "state": "EXPIRED", "signature": "sig-e"}
    assert world.consider() == "ENTRY_EXPIRED"
    assert world.book.cash_lamports() == 10 ** 9 - 6_000 and world.book.reconcile()["difference_lamports"] == 0


def test_without_measured_balances_the_buy_is_booked_at_its_guaranteed_bounds(world: World) -> None:
    world.signer.respond = lambda op, request: {"ok": True, "state": "CONFIRMED", "signature": "sig-b", "balances": None}
    assert world.consider() == "OPENED"
    (position,) = world.book.open_positions()
    assert position["tokens_raw"] == 990_000_000 and position["rent_lamports"] == RENT
    assert json.loads(position["entry_json"])["booking"]["source"] == "INTENT_BOUNDS_RECONCILE_ON_CHAIN"


# ── account limits (PLAN §13.2) ──────────────────────────────────────────────────────────────────────
def test_two_positions_at_most(world: World) -> None:
    assert [world.consider() for _ in range(3)] == ["OPENED", "OPENED", "BLOCKED"]
    assert "POSITION_LIMIT" in world.events("ENTRY_BLOCKED")[0]["reasons"] and len(world.signer.ops("intent")) == 2


def test_three_failed_transactions_halt_entries_until_resumed(world: World) -> None:
    world.signer.respond = lambda op, request: {"ok": False, "state": "FAILED", "signature": "f", "balances": None}
    assert [world.consider() for _ in range(4)] == ["ENTRY_FAILED", "ENTRY_FAILED", "ENTRY_FAILED", "BLOCKED"]
    assert world.book.halted() == "CONSECUTIVE_FAILED_TX" and len(world.signer.ops("intent")) == 3
    world.book.resume(world.clock.now)                                   # `desk live resume`, typed
    world.signer.respond = lambda op, request: None
    assert world.consider() == "OPENED"


def test_a_daily_realized_loss_of_five_percent_stops_entries_until_the_next_utc_day(world: World) -> None:
    assert world.consider() == "OPENED"
    world.jupiter.price = Decimal("0.4")                                # a 60% loss on a 10% ticket: 6% of equity
    world.later(30)
    asyncio.run(world.guardian.tick())
    assert world.book.open_positions() == []
    world.later(30)
    assert world.consider() == "BLOCKED"
    assert "DAILY_LOSS_LIMIT" in world.events("ENTRY_BLOCKED")[-1]["reasons"]
    world.jupiter.price = Decimal(1)
    world.later(86_400)
    assert world.consider() == "OPENED"


def test_a_quarter_lost_from_the_peak_halts_until_a_manual_reset_which_restarts_the_peak(world: World) -> None:
    past = world.clock.now + timedelta(minutes=1)
    for i in range(2):                                                   # -0.26 SOL realized on the first day
        world.book.open_position(position_id=f"loss{i}", intent_id=f"loss-buy-{i}", candidate_id="c", mint=f"mint{i}",
                                 pool_address="p", strategy_id="H1", strategy_version=H1.version_id,
                                 decision_hash=f"d{i}", regime_label="RISK_ON", at=past, ticket_lamports=2 * 10 ** 8,
                                 tokens_raw=1, entry_fees_lamports=0, failed_fees_lamports=0, rent_lamports=0, entry={})
        world.book.close_position(f"loss{i}", past, reason="HARD_STOP", exit_lamports=7 * 10 ** 7, exit_fees_lamports=0,
                                  profiles={})
    world.later(2 * 86_400)                                              # past the daily limits: only the peak remains
    assert world.consider() == "BLOCKED" and world.book.halted() == "CUMULATIVE_DRAWDOWN"
    assert world.consider() == "BLOCKED"                                 # persistent: no new day lifts it
    world.book.resume(world.clock.now)                                   # `desk live resume`, typed
    assert world.consider() == "OPENED"                                  # the same losses cannot re-halt at once
    assert world.signer.ops("intent")[0]["intent"]["amount_raw"] == 74 * 10 ** 6   # 10% of what is left


# ── exits (LIVE guardian) ────────────────────────────────────────────────────────────────────────────
def _stop_out(world: World, price: str = "0.8") -> dict[str, Any]:
    assert world.consider() == "OPENED"
    world.jupiter.price = Decimal(price)
    world.later(30)
    asyncio.run(world.guardian.tick())
    return dict(world.conn.execute("SELECT * FROM positions").fetchone())


def test_an_exit_sells_the_wallets_balance_closes_the_account_and_books_what_the_wallet_received(world: World) -> None:
    position = _stop_out(world)
    (sell,) = world.signer.ops("intent", "SELL")
    (close,) = world.signer.ops("close")
    assert sell["intent"]["amount_raw"] == 990_001_000                   # read from the chain, not from a quote
    assert close["close"]["mint"] == position["mint"] and world.chain.holdings == {}
    assert position["state"] == "CLOSED" and position["exit_reason"] == "HARD_STOP"
    received = sell["intent"]["min_out_raw"] + 2_000
    assert position["exit_lamports"] == received + (RENT - CLOSE_FEE) - RENT
    assert position["realized_pnl_lamports"] == position["exit_lamports"] - 10 ** 8 - BUY_FEE
    # the book's cash moved exactly as the wallet did: entry, sale, rent back less the close fee
    assert world.book.cash_lamports() == 10 ** 9 - ENTRY_COST + received + RENT - CLOSE_FEE
    assert world.book.reconcile()["difference_lamports"] == 0 and world.book.pending() == []


def test_tokens_left_after_a_sale_are_sold_before_the_account_is_closed(world: World) -> None:
    first = {"done": False}

    def partial(op: str, request: dict[str, Any]) -> dict[str, Any] | None:
        if op == "intent" and request["side"] == "SELL" and not first["done"]:
            first["done"] = True
            return {"ok": True, "state": "CONFIRMED", "signature": "sig-1", "balances": {
                "sol_lamports": 70_000_000, "input_token_raw": -(request["amount_raw"] - 50_000)}}
        return None

    world.signer.respond = partial
    position = _stop_out(world)
    sells = world.signer.ops("intent", "SELL")
    assert [s["intent"]["amount_raw"] for s in sells] == [990_001_000, 50_000] and len(world.signer.ops("close")) == 1
    second = sells[1]["intent"]["min_out_raw"] + 2_000
    assert position["state"] == "CLOSED"
    assert position["exit_lamports"] == 70_000_000 + second + (RENT - CLOSE_FEE) - RENT
    assert [e["reason"] for e in world.events("SOLD")] == ["HARD_STOP", "SELL_REMAINDER"]


def test_an_account_the_sale_already_closed_books_the_sale_alone(world: World) -> None:
    world.signer.respond = lambda op, request: refused("NO_TOKEN_ACCOUNT") if op == "close" else None
    position = _stop_out(world)
    received = world.signer.ops("intent", "SELL")[0]["intent"]["min_out_raw"] + 2_000
    assert position["state"] == "CLOSED" and position["exit_lamports"] == received - RENT
    assert world.book.reconcile()["difference_lamports"] == 0


def test_refused_exits_are_retried_then_blocked_but_stay_supervised_and_a_failed_one_pays_its_fee(world: World) -> None:
    world.signer.respond = lambda op, request: refused("SIMULATION_FAILED:slippage") if op == "intent" \
        and request["side"] == "SELL" else None
    _stop_out(world)
    for _ in range(2):
        world.later(30)
        asyncio.run(world.guardian.tick())
    (row,) = world.book.open_positions()
    assert row["state"] == "EXIT_BLOCKED" and row["exit_attempts"] == 3 and len(world.signer.ops("intent", "SELL")) == 3
    world.signer.respond = lambda op, request: {"ok": False, "state": "FAILED", "signature": "f",
                                                "balances": {"sol_lamports": -6_000}} if op == "intent" else None
    world.later(61)                                                      # the blocked schedule: once a minute
    asyncio.run(world.guardian.tick())
    (row,) = world.book.open_positions()
    assert row["exit_failed_fees_lamports"] == 6_000 and world.book.reconcile()["difference_lamports"] == 0
    world.signer.respond = lambda op, request: None
    world.later(61)
    asyncio.run(world.guardian.tick())
    assert world.book.open_positions() == []


def test_a_lost_sell_answer_is_looked_up_before_the_position_is_touched_again(world: World) -> None:
    assert world.consider() == "OPENED"
    world.signer.lose.add("intent")
    world.jupiter.price = Decimal("0.8")
    world.later(30)
    asyncio.run(world.guardian.tick())
    assert len(world.book.open_positions()) == 1 and world.signer.ops("close") == []
    world.later(30)
    asyncio.run(world.guardian.tick())
    assert world.book.open_positions() == [] and len(world.signer.ops("intent", "SELL")) == 1


def test_tokens_missing_from_the_wallet_stop_live_entries_for_reconciliation(world: World) -> None:
    assert world.consider() == "OPENED"
    mint = world.book.open_positions()[0]["mint"]
    world.chain.holdings.clear()                                         # e.g. moved by hand outside the desk
    world.signer.respond = lambda op, request: refused("SIMULATION_FAILED:insufficient funds") \
        if op == "intent" and request["side"] == "SELL" else None
    world.jupiter.price = Decimal("0.8")
    world.later(30)
    asyncio.run(world.guardian.tick())                                   # too soon to tell from a lagging node
    (row,) = world.book.open_positions()
    assert row["state"] == "EXITING" and world.book.halted() is None
    (sell,) = world.signer.ops("intent", "SELL")
    assert sell["intent"]["amount_raw"] == row["tokens_raw"] and sell["intent"]["mint"] == mint
    world.later(120)
    asyncio.run(world.guardian.tick())
    (row,) = world.book.open_positions()
    assert row["state"] == "EXIT_BLOCKED" and world.book.halted() == "RECONCILE_REQUIRED"
    assert len(world.signer.ops("intent", "SELL")) == 1                  # nothing more is sent


# ── M8-10 emergency liquidation ──────────────────────────────────────────────────────────────────────
def test_m8_10_liquidation_attempts_every_exit_and_reports_truthfully(world: World) -> None:
    assert [world.consider() for _ in range(2)] == ["OPENED", "OPENED"]
    first, second = (row["mint"] for row in world.book.open_positions())
    world.signer.respond = lambda op, request: refused("SIMULATION_FAILED:slippage") if op == "intent" \
        and request["side"] == "SELL" and request["mint"] == second else None
    stopped: list[int] = []
    report = asyncio.run(liquidate_all(world.guardian, stop_entries=lambda: stopped.append(1)))
    assert stopped == [1]
    assert [(r["mint"], r["result"]) for r in report] == [(first, "CLOSED"), (second, "FAILED")]
    assert report[1]["reason"] == "REFUSED" and "SIMULATION_FAILED:slippage" in report[1]["problems"]
    (left,) = world.book.open_positions()
    assert left["mint"] == second and left["exit_reason"] == "EMERGENCY_LIQUIDATION"   # still supervised
    world.jupiter.dead.add(second)
    world.signer.respond = lambda op, request: None
    (again,) = asyncio.run(liquidate_all(world.guardian, stop_entries=lambda: None))
    assert again["result"] == "FAILED" and again["reason"] == "NO_SELL_ROUTE"
    assert world.book.reconcile()["difference_lamports"] == 0


# ── wiring ───────────────────────────────────────────────────────────────────────────────────────────
def test_live_run_refuses_unless_gates_1_and_4_hold_and_live_resume_is_typed(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    from desk import cli
    from desk.core.secrets import Secrets

    paths = DeskPaths(tmp_path / "data", REPO_ROOT / "config", tmp_path / "private.env")
    monkeypatch.setattr(cli, "_bootstrap", lambda _: (paths, []))
    monkeypatch.setattr(cli, "load_secrets", lambda _: Secrets())
    monkeypatch.delenv("DESK_LIVE_CONFIRM", raising=False)
    started: list[dict[str, Any]] = []
    monkeypatch.setattr(cli, "_run_collector", lambda *_, **kwargs: started.append(kwargs) or 0)
    run = cli.build_parser().parse_args(["live", "run"])
    assert run.func(run) == 2 and started == []                         # default config: PAPER mode, no capital
    out = capsys.readouterr().out
    assert "gate 1" in out and "gate 4" in out
    monkeypatch.setattr(cli, "load_config", lambda _: LOADED)
    monkeypatch.setenv("DESK_LIVE_CONFIRM", "yes")
    assert run.func(run) == 0 and started == [{"live": True}] and run.paper and run.discovery and run.minutes == 1440
    conn = open_ledger(paths.db_dir, Mode.LIVE)
    LiveBook(conn).halt("CONSECUTIVE_FAILED_TX", NOW)
    conn.close()
    monkeypatch.setattr("builtins.input", lambda _: "resume live")
    resume = cli.build_parser().parse_args(["live", "resume"])
    assert resume.func(resume) == 1                                      # the exact words, or nothing changes
    monkeypatch.setattr("builtins.input", lambda _: "RESUME LIVE")
    assert resume.func(resume) == 0
    conn = open_ledger(paths.db_dir, Mode.LIVE)
    assert LiveBook(conn).halted() is None
    conn.close()


def test_paper_and_live_decide_on_the_same_pass_and_one_failure_never_stops_the_other(tmp_path: Path) -> None:
    from desk.ops.collector import Collector

    calls: list[tuple[str, str]] = []

    class Engine:
        def __init__(self, name: str, fail: bool = False) -> None:
            self.name, self.fail = name, fail

        async def consider(self, candidate_id: str) -> str:
            calls.append((self.name, candidate_id))
            if self.fail:
                raise RuntimeError("boom")
            return "NO_TRADE"

    hooks: Any = SimpleNamespace(paper_engine=Engine("paper"), live_engine=Engine("live", fail=True))
    asyncio.run(Collector._paper_and_live(hooks, "cand-9"))
    assert sorted(calls) == [("live", "cand-9"), ("paper", "cand-9")]
    dbs = Databases.open(DeskPaths(tmp_path / "data", REPO_ROOT / "config", tmp_path / "none.env"))
    try:
        secrets = SimpleNamespace(all_secret_values=lambda: [])
        suite: Any = SimpleNamespace(rpc=SimpleNamespace(http=SimpleNamespace(secrets=secrets)))
        collector = Collector(suite, dbs, LOADED, discovery=True, paper=True, live_engine=Engine("live"))
        assert collector.discovery_engine is not None
        assert collector.discovery_engine.on_pass == collector._paper_and_live
        with pytest.raises(ValueError, match="alongside PAPER"):
            Collector(suite, dbs, LOADED, discovery=True, live_engine=Engine("live"))
    finally:
        dbs.close()


def test_the_dashboard_shows_the_live_book_read_only(world: World) -> None:
    from desk.web.data import DashboardData

    data = DashboardData(world.paths, LOADED)
    book = data.snapshot()["live"]["book"]
    assert book["started"] is True and book["open"] == [] and book["deposits_sol"] == "1.000000"
    assert world.consider() == "OPENED"
    live = data.snapshot()["live"]
    (row,) = live["book"]["open"]
    assert row["strategy"] == "H1" and row["ticket_sol"] == "0.100000" and row["value_sol"] is None
    assert live["book"]["pending_requests"] == 0 and "desk live run" in live["reason"]
