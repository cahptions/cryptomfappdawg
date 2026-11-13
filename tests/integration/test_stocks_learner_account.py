"""The Learner PAPER account (desk.stocks.learner.policy) on a synthetic market: it waits until a model beat random on
held-out data, then decides each closed session by the documented rule within its fixed limits, records every decision,
fills at the next session's real quotes through the PAPER engine, leaves by the exit rule it chose and settles at expiry.
Synthetic chains only: the numbers check the machinery, never that anything trades well."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

import pytest

from desk.core.config import load_config
from desk.stocks import calendar as cal
from desk.stocks import plays as pl
from desk.stocks.learner import experience as ex
from desk.stocks.learner import labels as lb
from desk.stocks.learner import model as md
from desk.stocks.learner import policy
from desk.stocks.paper import fills
from desk.stocks.paper.book import PaperBook
from desk.stocks.paper.engine import PaperEngine, liquidation_return, sold_max_loss
from tests.conftest import REPO
from tests.stocks_world import add_chains, make_world

SETTINGS = load_config(REPO / "config").settings.stocks
SYMBOLS = ["SPY", "CHEAP", "SQZ", "S10", "S11", "S12"]


def closing(d: date) -> datetime:
    return cal.session_close(d) + timedelta(minutes=30)


def stepped(d: date) -> datetime:
    return datetime.combine(d, time(22, 30), UTC)


def passing(real: Any) -> Any:
    """The real gate, except that the synthetic model is let through (its file, hash and models still checked)."""
    def gate(row: Any, home: Any = None, *, load: bool = True) -> policy.Gate:
        g = real(row, home, load=load)
        if row is None:
            return g
        fitted = md.load_models(home, row) if home is not None else None
        return policy.Gate(fitted is not None, g.why if fitted is None else "synthetic test: gate let through",
                           g.model_id, g.model_code, g.detail, fitted, g.metrics)
    return gate


@pytest.fixture(scope="module")
def run(tmp_path_factory: pytest.TempPathFactory) -> Any:
    mp = pytest.MonkeyPatch()
    mp.setattr(md, "PARAMS", {**md.PARAMS, "max_iter": 10})
    mp.setattr(md, "EXIT_PARAMS", {**md.EXIT_PARAMS, "max_iter": 4})
    w = make_world(tmp_path_factory.mktemp("learner-account"), n=14)
    add_chains(w, SYMBOLS, w.days[-75:], max_days=120)
    train_days, trade_days = w.days[-75:-30], w.days[-30:]
    for d in train_days:
        pl.build(w.market, w.research, closing(d))
        ex.record_forward(w.market, w.research, d, closing(d))
    lb.run(w.research.conn, w.market.conn, lb.ForwardPrices(w.market.conn, w.research.conn),
           closing(train_days[-1]) + timedelta(hours=4), limit=10_000)
    # Before any model: the first session waits and says why.
    first = trade_days[0]
    pl.build(w.market, w.research, closing(first))
    ex.record_forward(w.market, w.research, first, closing(first))
    waited = policy.decide(w.research.conn, w.dbs.ledger, w.market, w.paths.home, first, closing(first))
    trained = md.train(w.research.conn, w.paths.home, closing(train_days[-1]) + timedelta(hours=5))
    real_gate = md.latest(w.research.conn)
    honest = policy.gate(real_gate, w.paths.home)
    mp.setattr(policy, "gate", passing(policy.gate))
    book = PaperBook(w.dbs.ledger)
    engine = PaperEngine(w.market, book, SETTINGS, policy.STRATEGIES)
    decisions = []
    for d in trade_days[1:]:
        rep = engine.step(d, stepped(d), None)
        assert rep.skipped is None, rep.skipped
        if d != trade_days[-1]:
            pl.build(w.market, w.research, closing(d))
            ex.record_forward(w.market, w.research, d, closing(d))
            decisions.append(policy.decide(w.research.conn, w.dbs.ledger, w.market, w.paths.home, d, closing(d)))
    try:
        yield w, book, waited, trained, honest, decisions, trade_days
    finally:
        mp.undo()
        w.close()


def test_it_waits_without_a_model_that_beat_random(run: Any) -> None:
    w, _book, waited, trained, honest, *_ = run
    assert waited["status"] == "waiting" and "No model" in waited["why"]
    day = w.dbs.ledger.execute("SELECT * FROM stk_learner_days ORDER BY session LIMIT 2").fetchall()
    assert {r["account"] for r in day} == {"learner", "learner_random"} and {r["status"] for r in day} == {"waiting"}
    assert "Waits with the Learner" in next(r["why"] for r in day if r["account"] == "learner_random")
    assert trained["trained"]
    # The synthetic model is judged by the real gate: whatever it says, it says it plainly with the threshold.
    assert ("beat random" in honest.why or "held-out days" in honest.why or "rank correlation" in honest.why)
    assert policy.gate(None).ok is False


def test_every_decision_is_recorded_with_its_model_scores_and_reason(run: Any) -> None:
    w, _book, _, trained, _, decisions, _ = run
    rows = w.dbs.ledger.execute("SELECT * FROM stk_learner_decisions").fetchall()
    assert rows, decisions
    for r in rows:
        scores = json.loads(r["scores_json"])
        if r["account"] == "learner":
            assert r["model_id"] == trained["id"] and r["mode"] in ("exploit", "explore")
            assert {"expected", "q10", "q90", "sigma", "evidence", "se", "lcb", "ucb", "draw"} <= set(scores)
            if r["mode"] == "exploit":
                assert scores["lcb"] > 0 and r["reason"].startswith("Exploit")
            else:
                assert scores["lcb"] <= 0 < scores["ucb"] and scores["draw"] > 0 and r["reason"].startswith("Explore")
            json.loads(r["alternatives_json"])
        else:
            assert r["mode"] == "random" and r["model_id"] is None and r["reason"].startswith("Random")
        assert r["risk_usd"] <= policy.TRADE_RISK * r["equity_usd"] + 0.01
        assert r["policy_version"] == policy.version_hash() and r["source"] == "forward"
        paper = w.dbs.ledger.execute("SELECT * FROM stk_decisions WHERE id=?", (r["paper_decision_id"],)).fetchone()
        assert paper["account"] == r["account"] and paper["action"] == "ENTER"
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        w.dbs.ledger.execute("UPDATE stk_learner_decisions SET reason='x'")


def test_the_session_budget_and_the_exploration_share_hold(run: Any) -> None:
    w, *_ = run
    by: dict[tuple[int, str], list[sqlite3.Row]] = {}
    for r in w.dbs.ledger.execute("SELECT * FROM stk_learner_decisions"):
        by.setdefault((r["session"], r["account"]), []).append(r)
    for rows in by.values():
        equity = rows[0]["equity_usd"]
        total = sum(r["risk_usd"] for r in rows)
        assert total <= policy.SESSION_RISK * equity + 0.01 and len(rows) <= policy.MAX_NEW
        assert len({r["symbol"] for r in rows}) == len(rows)                  # one new position a stock a session
        explore = sum(r["risk_usd"] for r in rows if r["mode"] == "explore")
        assert explore <= policy.SESSION_RISK * policy.EXPLORE_SHARE * equity + 0.01
    days = {(r["session"], r["account"]): r["status"] for r in w.dbs.ledger.execute("SELECT * FROM stk_learner_days")}
    sessions = {s for s, _ in days}
    assert all((s, "learner") in days and (s, "learner_random") in days for s in sessions)


def test_entries_fill_at_the_next_sessions_quotes_and_never_risk_more_than_allowed(run: Any) -> None:
    w, book, *_ = run
    opened = [p for a in policy.ACCOUNTS for p in book.positions(a, state=None)]
    assert opened
    for p in opened:
        order = json.loads(w.dbs.ledger.execute("SELECT order_json FROM stk_pending WHERE decision_id=?",
                                                (p.decision_id,)).fetchone()[0])
        decided = date.fromisoformat(order["plan"]["decided"])
        assert p.entry_day > decided                                          # never at the decision's own quotes
        _, chains = w.market.option_chain(p.symbol, p.entry_day)
        quotes = fills.index_chain(chains)
        for leg in p.legs:
            q = quotes[leg["occ"]]
            assert leg["entry_price"] == (q.ask if leg["quantity"] > 0 else q.bid)
        assert p.max_loss is not None and p.max_loss > 0
        assert p.max_loss <= order["risk_usd"] + 0.01                         # the fill never risks more than decided
        if p.state == "CLOSED":
            assert p.pnl is not None and p.pnl >= -p.max_loss - 0.01          # P/L never beyond what was at risk


def test_positions_leave_by_their_exit_rule_on_real_quotes(run: Any) -> None:
    w, book, *_ = run
    closed = [p for a in policy.ACCOUNTS for p in book.positions(a, state="CLOSED")]
    reasons = " | ".join(p.exit_reason or "" for p in closed)
    assert closed, "some positions closed within the run"
    for p in closed:
        assert p.plan["exit_rule"]
        evidence = json.loads(w.dbs.ledger.execute("SELECT exit_evidence_json FROM stk_positions WHERE id=?",
                                                   (p.id,)).fetchone()[0])
        assert not evidence.get("modeled_exit"), reasons                     # never left at a modelled price
    exits = w.dbs.ledger.execute("SELECT evidence_json FROM stk_decisions WHERE action='EXIT' AND account IN "
                                 "('learner','learner_random')").fetchall()
    assert all(json.loads(e[0]).get("exit_rule") for e in exits)


def test_sold_structures_are_bounded_and_collateralised() -> None:
    put_spread = [{"strike": 95.0, "kind": "put", "quantity": -1}, {"strike": 90.0, "kind": "put", "quantity": 1}]
    assert sold_max_loss(put_spread, -150.0 + 1.3) == pytest.approx(500 - 150 + 1.3)
    csp = [{"strike": 20.0, "kind": "put", "quantity": -1}]
    assert sold_max_loss(csp, -80.0 + 0.65) == pytest.approx(2000 - 80 + 0.65)
    naked_call = [{"strike": 20.0, "kind": "call", "quantity": -1}]
    assert sold_max_loss(naked_call, -50.0) is None


def test_the_return_a_rule_reads_is_floored_like_the_labels() -> None:
    from desk.stocks.contracts import OptionQuote
    from desk.stocks.paper.book import Position

    when = datetime(2026, 11, 2, 21, tzinfo=UTC)
    legs = [{"occ": "X1", "kind": "call", "strike": 10.0, "expiration": "2026-12-18", "quantity": 2}]
    p = Position("p", "learner", "", "option", "X", "long_call", legs, 1, when.isoformat(), date(2026, 11, 2), 200.0,
                 1.3, 201.3, None, date(2026, 12, 18), {}, "OPEN", 200.0, 200.0)
    q = {"X1": OptionQuote("X1", "X", "call", 10.0, date(2026, 12, 18), 2.0, 2.2, 2.1, 10, 10, 0, when)}
    assert liquidation_return(p, q) == pytest.approx((400 - 1.3 - 200 - 1.3) / 201.3)
    q0 = {"X1": OptionQuote("X1", "X", "call", 10.0, date(2026, 12, 18), 0.0, 0.01, 0.005, 10, 10, 0, when)}
    assert liquidation_return(p, q0) == -1.0
    assert liquidation_return(p, {}) is None


def test_the_learner_page_shows_each_trade_with_its_decision(run: Any) -> None:
    from desk.stocks.learner import account

    w, *_ = run
    view = account.build(w.dbs.ledger, w.research.conn, w.market, closing(w.days[-1]))
    assert set(view["accounts"]) == {"learner", "learner_random"}
    me = view["accounts"]["learner"]
    assert me["curve"] and me["grade"]["grade"] in ("early", "working", "promising", "not_working", "backwards")
    assert view["accounts"]["learner_random"]["grade"]["grade"] == "info"
    assert me["explore_share"] is None or 0 <= me["explore_share"] <= 1
    assert view["trades"] and all(t["decision"] for t in view["trades"])
    for t in view["trades"]:
        assert t["decision"]["reason"] and t["decision"]["exit_title"]
        if t["state"] == "CLOSED" and t["return"] is not None:
            assert t["return"] >= -1.0 - 1e-6
    assert view["baselines"]["spy"] and view["days"] and view["models"]
    assert view["gate"]["why"] and view["promotion"]
    current = next(v for v in view["promotion"] if v["current"])
    assert current["stage"] in ("provisional", "not traded yet")
    json.dumps(view, allow_nan=False)
    out = account.refresh(w.paths, w.dbs, closing(w.days[-1]))
    saved = account.saved(w.paths.home)
    assert out["refreshed"] and saved is not None and saved["accounts"]
    assert account.refresh(w.paths, w.dbs, closing(w.days[-1]))["refreshed"] is False      # unchanged: not rebuilt


def test_kestrel_check_covers_the_learner_account_and_findings(run: Any) -> None:
    from desk.stocks import accuracy as acc

    w, *_ = run
    src = acc.Sources(w.paths, closing(w.days[-1]), w.market.conn, w.research.conn, w.dbs.ledger)
    account, found = acc.check_learner_account(src), acc.check_learner_findings(src)
    assert account.skipped is None and account.checked > 0 and account.violations == 0, account.examples
    assert found.skipped is None and found.violations == 0, found.examples
    for name in ("check_book_cash", "check_book_positions", "check_book_fills"):
        result = getattr(acc, name)(src)
        assert result.violations == 0, (name, result.examples)
    # A decision over the per-trade limit is caught.
    row = next(r for r in w.dbs.ledger.execute("SELECT * FROM stk_learner_decisions")
               if w.dbs.ledger.execute("SELECT 1 FROM stk_positions WHERE decision_id=?",
                                       (r["paper_decision_id"],)).fetchone())
    fake = dict(row)
    fake["risk_usd"] = 0.01
    pos = w.dbs.ledger.execute("SELECT * FROM stk_positions WHERE decision_id=?", (row["paper_decision_id"],)).fetchone()
    if pos is not None:
        assert any("more than" in x for x in acc.learner_position_problems(pos, fake))   # type: ignore[arg-type]
        assert any("no recorded" in x for x in acc.learner_position_problems(pos, None))


def test_the_learning_report_has_the_learner_account(run: Any) -> None:
    from desk.stocks import learning_report as lr
    from desk.stocks.accuracy import report_problems
    from desk.stocks.learner import account

    w, *_ = run
    account.refresh(w.paths, w.dbs, closing(w.days[-1]), force=True)
    report = lr.build(w.paths, closing(w.days[-1]))
    section = next(s for s in report["sections"] if s["id"] == "learner")
    headlines = " ".join(i["headline"] for i in section["items"])
    assert "PAPER account" in headlines
    assert not report_problems(report)
    assert "The Learner" in lr.render_markdown(report)


class _Fake:
    """A book of closed positions for the promotion arithmetic."""

    def __init__(self, positions: list[Any]) -> None:
        self.items = positions
        self.conn = self

    def execute(self, *_: Any) -> Any:
        return self

    def fetchone(self) -> None:
        return None

    def positions(self, account: str, state: Any = "OPEN") -> list[Any]:
        return [p for p in self.items if p.account == account and (state is None or p.state == state)]


def _closed(account: str, day: date, ret: float, decision: int | None = None) -> Any:
    from types import SimpleNamespace

    plan = {"decided": day.isoformat(), "learner_decision": decision} if decision is not None else {}
    return SimpleNamespace(id=f"{account}{day}{decision}", account=account, plan=plan, entry_day=day, state="CLOSED",
                           pnl=100 * ret, max_loss=100.0, entry_cost=100.0, entry_fees=0.0, exit_reason="expired")


def test_a_version_is_confirmed_only_after_beating_random_and_the_rules_forward() -> None:
    from desk.stocks.learner import promotion as pr

    days = [date(2026, 6, 1) + timedelta(days=i) for i in range(25)]
    decisions = {i: d.toordinal() for i, d in enumerate(days)}
    few = _Fake([_closed("learner", d, 0.2, i) for i, d in enumerate(days[:5])])
    assert pr.judge(few, decisions)["verdict"] is None and "5 of 20" in pr.judge(few, decisions)["why"]
    rule = pr.rule_strategies()[0]
    good = _Fake([*(_closed("learner", d, 0.3 + 0.01 * (i % 3), i) for i, d in enumerate(days)),
                  *(_closed("learner_random", d, -0.2 + 0.01 * (i % 4)) for i, d in enumerate(days)),
                  *(_closed(rule, d, -0.1 + 0.02 * (i % 2)) for i, d in enumerate(days[:10]))])
    verdict = pr.judge(good, decisions)
    assert verdict["verdict"] == "confirmed" and verdict["vs_random"]["low"] > 0 and verdict["vs_rules"]["low"] > 0
    assert verdict["paired_days"] == pr.FORWARD_DAYS
    bad = _Fake([*(_closed("learner", d, -0.3 + 0.01 * (i % 3), i) for i, d in enumerate(days)),
                 *(_closed("learner_random", d, -0.2 + 0.01 * (i % 4)) for i, d in enumerate(days)),
                 *(_closed(rule, d, -0.1 + 0.02 * (i % 2)) for i, d in enumerate(days[:10]))])
    assert pr.judge(bad, decisions)["verdict"] == "failed"
    no_rules = _Fake([p for p in good.items if p.account != rule])
    assert pr.judge(no_rules, decisions)["verdict"] is None                  # can't compare with the rules: waits
