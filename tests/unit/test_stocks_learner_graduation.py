"""The Learner's graduation to Plays (desk.stocks.learner.graduation): graduated once its version is confirmed forward,
demoted when its latest 20 closed entry days fall below the same bar, graduated again only on a later window."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from desk.stocks.learner import graduation as gr
from desk.stocks.learner import promotion as pr
from desk.storage.db import DbKind, connect, migrate

NOW = datetime(2026, 11, 2, 23, tzinfo=UTC)
RULE = pr.rule_strategies()[0]


class Book:
    def __init__(self, items: list[Any]) -> None:
        self.items = items
        self.conn = self

    def execute(self, *_: Any) -> Any:
        return self

    def fetchone(self) -> None:
        return None

    def positions(self, account: str, state: Any = "OPEN") -> list[Any]:
        return [p for p in self.items if p.account == account and (state is None or p.state == state)]


def closed(account: str, day: date, ret: float, decision: int | None = None, state: str = "CLOSED") -> Any:
    plan = {"decided": day.isoformat(), "learner_decision": decision} if decision is not None else {}
    return SimpleNamespace(id=f"{account}{day}{decision}", account=account, plan=plan, entry_day=day, state=state,
                           pnl=100 * ret, max_loss=100.0, entry_cost=100.0, entry_fees=0.0, exit_reason="expired")


def record(days: list[date], learner: list[float], first: int = 0) -> list[Any]:
    out = []
    for i, d in enumerate(days):
        out += [closed("learner", d, learner[i] + 0.01 * (i % 3), first + i),
                closed("learner_random", d, -0.2 + 0.01 * (i % 4)), closed(RULE, d, -0.1 + 0.02 * (i % 2))]
    return out


def test_graduates_on_confirmation_demotes_below_the_bar_and_graduates_again_only_later(tmp_path: Path) -> None:
    conn = connect(tmp_path / "research.db", DbKind.STOCKS_RESEARCH)
    migrate(conn, DbKind.STOCKS_RESEARCH)
    code, pv = gr.current()
    assert gr.status(conn)["graduated"] is False and "Preview" not in gr.status(conn)["why"]
    assert "shadow preview" in gr.status(conn)["why"]
    days = [date(2026, 6, 1) + timedelta(days=i) for i in range(60)]
    decisions = {i: d.toordinal() for i, d in enumerate(days)}
    good = record(days[:20], [0.3] * 20)
    assert gr.step(conn, Book(good), decisions, NOW) == {"graduated": False, "why": "not confirmed"}
    conn.execute("INSERT INTO stk_learn_promotions(model_code, policy_version, stage, at, detail_json) VALUES "
                 "(?,?,?,?,?)", (code, pv, "confirmed", NOW.isoformat(), "{}"))
    assert gr.step(conn, Book(good), decisions, NOW)["changed"] == "graduated"
    assert gr.status(conn)["graduated"] and "Graduated" in gr.status(conn)["why"]
    assert gr.step(conn, Book(good), decisions, NOW + timedelta(days=1))["changed"] is None      # still passes
    # The next 20 entry days lose to random: the latest closed window fails the bar → demoted.
    worse = good + record(days[20:40], [-0.4] * 20, 20)
    out = gr.step(conn, Book(worse), decisions, NOW + timedelta(days=2))
    assert out["changed"] == "demoted" and gr.status(conn)["graduated"] is False
    assert "Demoted" in gr.status(conn)["why"]
    # An open position keeps its entry day out of the window: the window is the latest closed days only.
    window = gr.latest_window(Book([*worse, closed("learner", days[40], 0.0, 40, state="OPEN")]),
                              {**decisions})
    assert max(window.values()) == days[39].toordinal() and len(set(window.values())) == gr.WINDOW
    # A window overlapping the failed one does not graduate it again; a later window that passes does.
    mixed = worse + record(days[40:50], [0.5] * 10, 40)
    assert gr.step(conn, Book(mixed), decisions, NOW + timedelta(days=3))["changed"] is None
    better = worse + record(days[40:60], [0.5] * 20, 40)
    assert gr.step(conn, Book(better), decisions, NOW + timedelta(days=4))["changed"] == "graduated again"
    assert [s["stage"] for s in gr.stages(conn)] == ["graduated", "demoted", "graduated"]
    conn.close()
