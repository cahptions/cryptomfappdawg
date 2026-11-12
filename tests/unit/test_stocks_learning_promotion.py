"""The 2026-11-13 Learning promotion rules: frozen manifests, Holm across recipes, untouched confirmation days, failed
confirmations kept, and earlier promotions relabelled. Synthetic outcomes only."""

from __future__ import annotations

import json
import random
import sqlite3
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from desk.core.paths import DeskPaths
from desk.stocks import calendar as cal
from desk.stocks import learning
from desk.stocks import learning_promotion as promo
from tests.conftest import REPO

AT = datetime(2026, 11, 13, 21, 0, tzinfo=UTC)
DAYS = cal.trading_days(date(2026, 1, 5), date(2026, 11, 12))


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    c = learning.open_book(DeskPaths(tmp_path / "home", REPO / "config", tmp_path / "secrets.env"))
    learning.initialize(c, AT - timedelta(days=400))
    yield c
    c.close()


def recipes(conn: sqlite3.Connection) -> list[str]:
    gone = learning.retired(conn)
    return [r[0] for r in conn.execute("SELECT id FROM stk_learning_experiments ORDER BY id") if r[0] not in gone]


def rows(ident: str, days: list[date], draw) -> list[dict]:
    out = []
    for d in days:
        entered = datetime(d.year, d.month, d.day, 15, tzinfo=UTC)
        for symbol in ("AAA", "BBB"):
            out.append({"experiment_id": ident, "entered_at": entered.isoformat(),
                        "matured_at": (entered + timedelta(days=1)).isoformat(),
                        "block": f"{d.isoformat()}:{symbol}", "net_return": draw()})
    return out


def test_student_t_tail_and_holm() -> None:
    assert promo.t_sf(2.093, 19) == pytest.approx(0.025, abs=2e-4)
    assert promo.t_sf(1.729, 19) == pytest.approx(0.05, abs=3e-4)
    assert promo.t_sf(-1.729, 19) == pytest.approx(0.95, abs=3e-4)
    assert promo.t_sf(0.0, 5) == pytest.approx(0.5)
    # Holm: the smallest must clear alpha/m, the next alpha/(m-1), and it stops at the first failure.
    assert promo.holm({"a": 0.001, "b": 0.02, "c": 0.04}) == {"a": pytest.approx(0.05 / 3),
                                                              "b": pytest.approx(0.025), "c": pytest.approx(0.05)}
    assert promo.holm({"a": 0.02, "b": 0.03, "c": 0.04}) == {}


def test_manifests_are_frozen_at_registration(conn: sqlite3.Connection) -> None:
    ident = learning.add_lifecycle(conn, AT, "long_call", "3m", 45)
    row = conn.execute("SELECT * FROM stk_learning_manifests WHERE experiment_id = ?", (ident,)).fetchone()
    value = json.loads(row["manifest_json"])
    assert row["backfilled"] == 0 and value["config"]["hold_minutes"] == 45 * 1440 and value["rules"] == promo.RULES
    assert "Holm" in value["test"] and "next 20 entry days" in value["confirmation"]
    assert len(row["manifest_sha256"]) == 64
    with pytest.raises(sqlite3.IntegrityError, match="frozen"):
        conn.execute("UPDATE stk_learning_manifests SET manifest_json = '{}'")
    # Every registered recipe has one; recipes that predate the rules are marked as frozen later.
    assert conn.execute("SELECT COUNT(*) FROM stk_learning_experiments WHERE id NOT IN "
                        "(SELECT experiment_id FROM stk_learning_manifests)").fetchone()[0] == 0


def test_backfilled_manifests_say_so(tmp_path: Path) -> None:
    c = learning.open_book(DeskPaths(tmp_path / "home", REPO / "config", tmp_path / "secrets.env"))
    try:
        c.execute("INSERT INTO stk_learning_experiments VALUES ('old0000000000000000', '2026-10-01T00:00:00+00:00', "
                  "'an earlier recipe', 'x', ?)", (json.dumps({"structure": "long_call"}),))
        assert promo.backfill(c, AT) == 1
        assert c.execute("SELECT backfilled FROM stk_learning_manifests").fetchone()[0] == 1
    finally:
        c.close()


def test_many_noise_recipes_are_never_promoted(conn: sqlite3.Connection) -> None:
    active = recipes(conn)
    assert len(active) == 15
    rng = random.Random(11)
    data = [r for ident in active for r in rows(ident, DAYS[:60], lambda: rng.gauss(0.0, 0.3))]
    # Re-checked as the sample grows, every check-in: still nothing, because each test is Holm-corrected across all 15.
    promoted = []
    for n in range(20, 61, 5):
        cut = DAYS[n - 1].isoformat()
        seen = [r for r in data if r["block"][:10] <= cut]
        promo.decide(conn, seen, DAYS[0].isoformat(), AT, active)
        promoted += [r[0] for r in conn.execute("SELECT experiment_id FROM stk_learning_promotions")]
    assert promoted == []
    # The old rule (mean minus three standard errors, uncorrected) is reported for comparison only, never applied.
    assert promo.champion(conn) is None


def test_a_real_edge_is_provisional_then_confirmed_on_untouched_days(conn: sqlite3.Connection) -> None:
    active = recipes(conn)
    edge = active[3]
    rng = random.Random(5)
    first = [r for ident in active for r in rows(ident, DAYS[:30],
             (lambda: rng.gauss(0.06, 0.08)) if ident == edge else (lambda: rng.gauss(0.0, 0.3)))]
    changed = promo.decide(conn, first, DAYS[0].isoformat(), AT, active)
    assert changed["provisional"] == [edge] and promo.champion(conn) is None
    detail = json.loads(conn.execute("SELECT detail_json FROM stk_learning_promotions").fetchone()[0])
    assert detail["recipes_compared"] == 15 and detail["holm_threshold"] == pytest.approx(0.05 / 15)
    assert detail["confirmation"] == {"entry_days": 20, "after": DAYS[29].isoformat()}
    # Fewer than 20 confirmation days: nothing is decided yet.
    later = rows(edge, DAYS[30:45], lambda: rng.gauss(0.06, 0.08))
    assert promo.decide(conn, first + later, DAYS[0].isoformat(), AT, active)["confirmed"] == []
    view = promo.overview(conn, AT)
    assert view["provisional"][0]["experiment_id"] == edge and view["confirmed"] == []
    later += rows(edge, DAYS[45:52], lambda: rng.gauss(0.06, 0.08))
    changed = promo.decide(conn, first + later, DAYS[0].isoformat(), AT, active)
    assert changed["confirmed"] == [edge]
    structure = json.loads(conn.execute("SELECT config_json FROM stk_learning_experiments WHERE id = ?",
                                        (edge,)).fetchone()[0])["structure"]
    assert promo.champion(conn) == structure
    confirmed = promo.overview(conn, AT)["confirmed"][0]
    # The confirmation used exactly the 20 entry days after the promotion day, none of the promotion's own.
    assert confirmed["window"] == [DAYS[30].isoformat(), DAYS[49].isoformat()] and confirmed["days"] == 20


def test_a_failed_confirmation_is_kept_and_needs_fresh_days(conn: sqlite3.Connection) -> None:
    active = recipes(conn)
    lucky = active[0]
    rng = random.Random(8)
    first = [r for ident in active for r in rows(ident, DAYS[:25],
             (lambda: rng.gauss(0.08, 0.05)) if ident == lucky else (lambda: rng.gauss(0.0, 0.3)))]
    assert promo.decide(conn, first, DAYS[0].isoformat(), AT, active)["provisional"] == [lucky]
    after = rows(lucky, DAYS[25:45], lambda: rng.gauss(-0.02, 0.1))
    assert promo.decide(conn, first + after, DAYS[0].isoformat(), AT, active)["failed"] == [lucky]
    view = promo.overview(conn, AT)
    assert [f["experiment_id"] for f in view["failed"]] == [lucky] and view["provisional"] == []
    assert view["failed"][0]["fresh_after"] == DAYS[44].isoformat() and promo.champion(conn) is None
    stages = [r[0] for r in conn.execute("SELECT stage FROM stk_learning_promotions ORDER BY id")]
    assert stages == ["provisional", "failed"]                     # the promotion that failed stays in the record
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("DELETE FROM stk_learning_promotions")
    # The same days cannot promote it again: only entry days after the failed window count.
    assert promo.decide(conn, first + after, DAYS[0].isoformat(), AT, active)["provisional"] == []


def test_an_earlier_promotion_is_kept_and_labelled_provisional(conn: sqlite3.Connection) -> None:
    old = {"version": 7, "champion": "long_call", "promoted": True, "test_net_return": 0.04, "test_blocks": 30}
    assert promo.relabel_legacy(conn, old, "2026-10-20", AT)
    assert not promo.relabel_legacy(conn, old, "2026-10-20", AT)       # once
    assert not promo.relabel_legacy(conn, {**old, "rules": promo.RULES}, None, AT)
    legacy = promo.overview(conn, AT)["legacy"]
    assert len(legacy) == 1 and legacy[0]["label"] == "provisional (before 2026-11-13 rules)"
    assert legacy[0]["rules"] == "before 2026-11-13" and promo.champion(conn) is None
