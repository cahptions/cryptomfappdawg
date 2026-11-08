"""Schwab's earlier daily history joins the stored prices only where it provably reconciles with Massive's unadjusted
bars and the split record: the basis is detected per stock, adjusted candles are un-adjusted, Massive's rows and days
are never touched, every stored day says its source, and the earlier years count only once every stock is checked.
Synthetic prices only; Schwab's real responses are not testable here."""

from __future__ import annotations

import asyncio
import math
import random
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np

from desk.core.config import load_config
from desk.core.errors import ProviderError, ProviderErrorKind
from desk.core.paths import DeskPaths
from desk.stocks import calendar as cal
from desk.stocks import collect
from desk.stocks import schwab_history as sh
from desk.stocks import validation as val
from desk.stocks.backtest import PANEL_LOOKBACK, historical_panel
from desk.stocks.contracts import Bar, Listing, Split
from desk.stocks.features import compute
from desk.stocks.prices import Panel, load_panel
from desk.stocks.repo import StocksDatabases, StocksMarket
from tests.conftest import REPO

NOW = datetime(2026, 11, 6, 23, 0, tzinfo=UTC)
TODAY = date(2026, 11, 6)
EDGE = date(2026, 5, 1)                       # the first stored Massive day in these tests
DAYS = cal.trading_days(date(2023, 1, 3), TODAY)
SETTINGS = load_config(REPO / "config").settings.stocks.model_copy(update={"history_days": 180})


def adjusted(seed: int) -> dict[date, tuple[float, float]]:
    """A continuous (fully split-adjusted) price path: day → (open, close)."""
    rng, px, out = random.Random(seed), 40.0, {}
    for d in DAYS:
        opened = px * (1 + rng.gauss(0, 0.002))
        px = opened * (1 + rng.gauss(0, 0.01))
        out[d] = (opened, px)
    return out


def factor(splits: list[Split], d: date) -> float:
    return math.prod(s.ratio for s in splits if s.execution_date > d)


def candles(symbol: str, path: dict[date, tuple[float, float]], splits: list[Split], *, basis: str) -> list[Bar]:
    """What Schwab would send: the adjusted path, or the raw prices (adjusted × later splits)."""
    out = []
    for d, (o, c) in path.items():
        f = factor(splits, d) if basis == "unadjusted" else 1.0
        out.append(Bar(symbol, d, o * f, max(o, c) * f * 1.002, min(o, c) * f * 0.998, c * f, 1e6 / f))
    return out


def official(symbol: str, path: dict[date, tuple[float, float]], splits: list[Split]) -> dict[date, Bar]:
    """Massive's unadjusted bars from the boundary on."""
    return {b.day: b for b in candles(symbol, path, splits, basis="unadjusted") if b.day >= EDGE}


def check(symbol: str, path: dict[date, tuple[float, float]], splits: list[Split], basis: str,
          record_from: date = DAYS[0]) -> sh.Checked:
    return sh.reconcile(symbol, candles(symbol, path, splits, basis=basis), official(symbol, path, splits), splits,
                        edge=EDGE, record_from=record_from, today=TODAY)


def test_adjusted_candles_are_proved_on_a_split_in_the_overlap_and_un_adjusted_with_the_split_record() -> None:
    path = adjusted(1)
    splits = [Split("X", date(2024, 3, 1), 1, 3), Split("X", date(2026, 7, 1), 1, 2)]
    got = check("X", path, splits, "split-adjusted")
    assert got.status == "stored" and got.basis == "split-adjusted" and "shared sessions" in got.note
    truth = {b.day: b for b in candles("X", path, splits, basis="unadjusted")}
    assert got.bars and all(b.day < EDGE for b in got.bars)
    for b in got.bars:
        assert math.isclose(b.close, truth[b.day].close, rel_tol=1e-9)
        assert math.isclose(b.volume, truth[b.day].volume, rel_tol=1e-9)
    assert math.isclose(got.bars[0].close / path[got.bars[0].day][1], 6.0)        # both splits came after it


def test_without_a_split_in_the_overlap_each_earlier_split_shows_the_basis_in_schwabs_own_candles() -> None:
    path, splits = adjusted(2), [Split("Y", date(2024, 3, 1), 1, 3)]
    adjusted_basis = check("Y", path, splits, "split-adjusted")
    assert adjusted_basis.status == "stored" and adjusted_basis.basis == "split-adjusted"
    raw = check("Y", path, splits, "unadjusted")
    assert raw.status == "stored" and raw.basis == "unadjusted"
    truth = {b.day: b.close for b in candles("Y", path, splits, basis="unadjusted")}
    assert all(math.isclose(b.close, truth[b.day], rel_tol=1e-9) for b in adjusted_basis.bars + raw.bars)
    plain = check("Z", adjusted(3), [], "split-adjusted")
    assert plain.status == "stored" and plain.basis == "no splits"


def test_stocks_that_dont_reconcile_are_refused_with_a_note() -> None:
    path = adjusted(4)
    small = check("S", path, [Split("S", date(2024, 3, 1), 4, 5)], "split-adjusted")        # 5-for-4 can't show
    assert small.status == "refused" and not small.bars and "unknown" in small.note
    off = sh.reconcile("V", [Bar("V", b.day, b.open * 1.1, b.high * 1.1, b.low * 1.1, b.close * 1.1, b.volume)
                             for b in candles("V", path, [], basis="unadjusted")],
                       official("V", path, []), [], edge=EDGE, record_from=DAYS[0], today=TODAY)
    assert off.status == "refused" and "match" in off.note
    volume = sh.reconcile("W", [Bar("W", b.day, b.open, b.high, b.low, b.close, b.volume * 100)
                                for b in candles("W", path, [], basis="unadjusted")],
                          official("W", path, []), [], edge=EDGE, record_from=DAYS[0], today=TODAY)
    assert volume.status == "refused" and "volumes" in volume.note
    short = sh.reconcile("T", candles("T", path, [], basis="unadjusted")[:-140], official("T", path, []), [],
                         edge=EDGE, record_from=DAYS[0], today=TODAY)
    assert short.status == "refused" and "overlap" in short.note
    wrong = check("U", path, [Split("U", date(2026, 7, 1), 1, 2), Split("U", date(2024, 3, 1), 1, 3)], "unadjusted")
    assert wrong.status == "stored" and wrong.basis == "unadjusted"         # raw prices: the gap matches the record


def test_candles_older_than_the_split_record_are_not_stored() -> None:
    got = check("X", adjusted(1), [], "split-adjusted", record_from=date(2025, 1, 2))
    assert got.status == "stored" and got.bars[0].day >= date(2025, 1, 2)


# ── the job ──────────────────────────────────────────────────────────────────────────────────────
class Schwab:
    configured = True

    def __init__(self, data: dict[str, list[Bar]], fail: set[str] = frozenset()) -> None:  # type: ignore[assignment]
        self.data, self.fail, self.asked = data, fail, []

    async def price_history(self, symbol: str, now: datetime | None = None) -> list[Bar]:
        self.asked.append(symbol)
        if symbol in self.fail:
            raise ProviderError("schwab", ProviderErrorKind.HTTP_ERROR, "pricehistory: HTTP 500", 500)
        return self.data.get(symbol, [])


def market(tmp_path: Path) -> tuple[StocksDatabases, StocksMarket, dict[str, Any]]:
    paths = DeskPaths(tmp_path / "home", REPO / "config", tmp_path / "secrets.env")
    dbs = StocksDatabases.open(paths)
    m = StocksMarket(dbs.market)
    m.save_listings([Listing("X", "X Corp", "NYSE", False, False, "common"),
                     Listing("Y", "Y Corp", "NYSE", False, False, "common"),
                     Listing("SPY", "SPDR S&P 500", "NYSE Arca", True, False, "etf")], {}, NOW)
    world = {"X": (adjusted(1), [Split("X", date(2024, 3, 1), 1, 3), Split("X", date(2026, 7, 1), 1, 2)]),
             "Y": (adjusted(2), []), "SPY": (adjusted(5), [])}
    m.save_splits([s for _, splits in world.values() for s in splits], NOW)
    for d in cal.trading_days(EDGE, cal.last_closed_session(NOW, timedelta(minutes=75))):
        m.save_bars(d, [official(s, path, splits)[d] for s, (path, splits) in world.items()], "massive", NOW)
    m.job("corporate_history", NOW, detail={"from": "2023-06-01", "complete": True})
    return dbs, m, world


def test_the_job_stores_earlier_days_with_their_source_and_never_touches_massives(tmp_path: Path) -> None:
    dbs, m, world = market(tmp_path)
    try:
        before = list(m.conn.execute("SELECT * FROM stk_bars WHERE day >= ? ORDER BY symbol, day", (EDGE.toordinal(),)))
        massive_days = list(m.conn.execute("SELECT * FROM stk_bar_days ORDER BY day"))
        schwab = Schwab({s: candles(s, path, splits, basis="split-adjusted") for s, (path, splits) in world.items()},
                        fail={"Y"})
        suite = SimpleNamespace(schwab=schwab)
        first = asyncio.run(sh.job(suite, m, NOW, SETTINGS, per_pass=1))     # type: ignore[arg-type]
        assert first["ok"] and schwab.asked == ["SPY"] and first["remaining"] == len(sh.queue(m)) - 1
        assert not first["complete_at"] and sh.queue(m)[-2:] == ["X", "Y"]
        # Schwab's years stay out of evaluations while stocks remain unchecked.
        assert val.evaluation_window(m, SETTINGS, available_kb=64_000_000)[0] == EDGE
        done = asyncio.run(sh.job(suite, m, NOW, SETTINGS))                  # type: ignore[arg-type]
        assert done["remaining"] == 0 and done["complete_at"] and done["stored"] == 2 and done["failed"] == 1
        assert list(m.conn.execute("SELECT * FROM stk_bars WHERE day >= ? ORDER BY symbol, day",
                                   (EDGE.toordinal(),))) == before
        assert [tuple(r) for r in m.conn.execute("SELECT * FROM stk_bar_days WHERE day >= ? ORDER BY day",
                                                  (EDGE.toordinal(),))] == [tuple(r) for r in massive_days]
        sources = {r[0] for r in m.conn.execute("SELECT DISTINCT source FROM stk_bar_days WHERE day < ?",
                                               (EDGE.toordinal(),))}
        assert sources == {sh.SOURCE}
        ledger = {r["symbol"]: r for r in m.conn.execute("SELECT * FROM stk_history_symbols")}
        assert ledger["X"]["status"] == "stored" and ledger["X"]["basis"] == "split-adjusted"
        assert ledger["Y"]["status"] == "failed" and "500" in ledger["Y"]["note"]
        assert m.conn.execute("SELECT MIN(day) FROM stk_bar_days").fetchone()[0] >= date(2023, 6, 1).toordinal()
        start, _ = val.evaluation_window(m, SETTINGS, available_kb=64_000_000)
        assert start < EDGE and start >= date(2023, 6, 1)
        # Read back adjusted, the stored history is one continuous series with Massive's.
        panel = load_panel(m, start, TODAY)
        path, _ = world["X"]
        x = panel.close[panel.index["X"]]
        assert all(math.isclose(x[j], path[d][1], rel_tol=1e-9) for j, d in enumerate(panel.days) if not np.isnan(x[j]))
        rows = sum(r[0] for r in m.conn.execute("SELECT rows FROM stk_bar_days WHERE source = ?", (sh.SOURCE,)))
        assert rows == m.conn.execute("SELECT COUNT(*) FROM stk_bars WHERE day < ?", (EDGE.toordinal(),)).fetchone()[0]
        again = asyncio.run(sh.job(suite, m, NOW, SETTINGS))                 # resumable: nothing asked twice
        assert again["asked"] == 0
        stored = m.conn.execute("SELECT COUNT(*) FROM stk_bars").fetchone()[0]
        longer = asyncio.run(sh.job(suite, m, NOW, SETTINGS.model_copy(update={"price_history_years": 12})))  # type: ignore[arg-type]
        assert longer["asked"] == len(sh.queue(m)) - 1                            # a longer span checks again (not Y)
        assert m.conn.execute("SELECT COUNT(*) FROM stk_bars").fetchone()[0] == stored     # never a row twice
        cover = sh.coverage(m.conn)
        assert cover["massive_from"] == EDGE.isoformat() and cover["complete"] and cover["schwab_from"] < EDGE.isoformat()
    finally:
        dbs.close()


def test_the_job_waits_for_massives_window_and_the_split_record(tmp_path: Path) -> None:
    dbs, m, _ = market(tmp_path)
    try:
        m.job("corporate_history", NOW, detail={"from": "2026-01-01", "complete": False})
        assert "split" in (sh.waiting(m, NOW, SETTINGS) or "")
        long = SETTINGS.model_copy(update={"history_days": 400})
        assert "Massive" in (sh.waiting(m, NOW, long) or "")
    finally:
        dbs.close()


def test_corporate_history_walks_back_a_month_a_pass_and_stops_at_a_refusal(tmp_path: Path) -> None:
    dbs, m, _ = market(tmp_path)
    try:
        asked: list[tuple[date, date | None]] = []

        class Massive:
            refuse = False

            async def splits(self, since: date, until: date | None = None) -> list[Split]:
                asked.append((since, until))
                if self.refuse:
                    raise ProviderError("massive", ProviderErrorKind.HTTP_ERROR, "splits: HTTP 403", 403)
                return [Split("X", since, 1, 2), Split("OTHER", since, 1, 2)]

            async def dividends(self, since: date, until: date | None = None) -> list[Any]:
                return []

        massive = Massive()
        suite = SimpleNamespace(massive=massive)
        m.conn.execute("DELETE FROM stk_jobs WHERE name = 'corporate_history'")
        first = asyncio.run(collect.corporate_history(suite, m, NOW, history_days=180))   # type: ignore[arg-type]
        start = NOW.date() - timedelta(days=210)
        assert asked[0] == (start.replace(day=1), start - timedelta(days=1)) or asked[0][1] == start - timedelta(days=1)
        assert first["remaining"] == 1 and first["splits"] == 1 and first["from"] == asked[0][0].isoformat()
        asyncio.run(collect.corporate_history(suite, m, NOW, history_days=180))           # type: ignore[arg-type]
        assert asked[1][1] == asked[0][0] - timedelta(days=1)
        massive.refuse = True
        stopped = asyncio.run(collect.corporate_history(suite, m, NOW, history_days=180))  # type: ignore[arg-type]
        assert stopped["complete"] and "refused" in stopped["refused"] and stopped["from"] == asked[1][0].isoformat()
    finally:
        dbs.close()


# ── long panels cost no more per day ─────────────────────────────────────────────────────────────
def test_features_and_replay_panels_read_only_their_lookback() -> None:
    rng = np.random.default_rng(0)
    days = cal.trading_days(date(2020, 1, 2), date(2026, 10, 30))
    shape = (6, len(days))
    close = 50 * np.exp(np.cumsum(rng.normal(0, 0.01, shape), axis=1))
    close[2, :900] = np.nan
    arrays = [close * 0.99, close * 1.01, close * 0.98, close, rng.uniform(1e5, 1e6, shape)]
    full = Panel(["A", "B", "C", "D", "E", "SPY"], days, *arrays)
    t = len(days) - 1
    short = Panel(full.symbols, days[-400:], *(a[:, -400:] for a in arrays))
    long_f, short_f = compute(full, t), compute(short, 399)
    for key, values in long_f.values.items():
        assert np.allclose(values, short_f.values[key], equal_nan=True), key
    window = historical_panel(full, days[-10], [], lookback=PANEL_LOOKBACK)
    assert len(window.days) == PANEL_LOOKBACK and window.days[-1] == days[-10]
    assert np.array_equal(window.close, historical_panel(full, days[-10], []).close[:, -PANEL_LOOKBACK:], equal_nan=True)
