"""Every Plays section replayed on real past option prices: listings and ladders downloaded through the one Massive
queue at a modest share, each section's live selector run on each past week's chain using only what was known then,
graded at expiry like a forward play, and kept apart from the forward record (its own append-only tables, its own
calibration). Network-free: a fake Massive prices a skewed smile on the stored closes."""

import asyncio
import json
import math
import sqlite3
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from desk.stocks import calendar as cal
from desk.stocks import iv_history as ivh
from desk.stocks import lotto_history as lh
from desk.stocks import massive_options as mo
from desk.stocks import options as opt
from desk.stocks import play_replay as pr
from desk.stocks import plays as pl
from tests.stocks_world import make_world, occ

NOW = datetime(2026, 10, 24, 15, 0, tzinfo=UTC)          # a Saturday: the market is closed


def smile(k: float, s: float) -> float:
    return max(0.12, min(1.2, 0.32 - 0.45 * math.log(k / s)))


class FakeMassive:
    """Listings every 1% of the price; each contract's daily bars priced on a skewed smile at the stored closes."""
    configured = True

    def __init__(self, closes: dict[str, dict[date, float]]) -> None:
        self.closes = closes
        self.calls: list[tuple[str, str]] = []
        self.known: dict[str, tuple[str, str, float, date]] = {}

    async def option_contracts(self, underlying: str, as_of: date, first: date, last: date, *, kind: Any = None,
                               strikes: Any = None) -> list[dict[str, Any]]:
        self.calls.append(("list", underlying))
        spot = max(v for d, v in self.closes[underlying].items() if d <= as_of and d >= as_of - timedelta(days=7))
        out = []
        for step in range(-40, 61):
            strike = round(spot * (1 + step / 100), 2)
            if strikes and not strikes[0] <= strike <= strikes[1]:
                continue
            for k in ("call", "put") if kind is None else (kind,):
                ticker = occ(underlying, first, k, strike)
                self.known[ticker] = (underlying, k, strike, first)
                out.append({"ticker": ticker, "kind": k, "strike": strike, "expiration": first,
                            "underlying": underlying})
        return out

    async def option_bars(self, ticker: str, first: date, last: date) -> list[dict[str, Any]]:
        self.calls.append(("bars", ticker))
        symbol, kind, strike, expiry = self.known[ticker]
        out = []
        for d, s in sorted(self.closes[symbol].items()):
            if first <= d <= last:
                price = opt.bs_price(kind, s, strike, opt.year_fraction(d, expiry), smile(strike, s), 0.04)
                if price >= 0.01:
                    out.append({"day": d, "open": price, "high": price, "low": price, "close": round(price, 2),
                                "volume": 400.0})
        return out


@pytest.fixture()
def world(tmp_path: Path, monkeypatch: Any) -> Any:
    w = make_world(tmp_path, n=12)
    stock = w.specs[0].symbol
    names = ["SPY", stock]
    for symbol in names:
        w.market.save_option_daily(symbol, w.days[-1], NOW, {"price": 100.0, "iv30": 0.3, "expiries": []})
    monthly, weekly = ivh.expiries, lh.expiries
    monkeypatch.setattr(ivh, "expiries", lambda today: monthly(today)[:3])
    monkeypatch.setattr(ivh, "names", lambda market, today, n=60: names)
    monkeypatch.setattr(lh, "expiries", lambda today: weekly(today)[:4])
    monkeypatch.setattr(lh, "NAMES", tuple(names))
    fake = FakeMassive({s: {b.day: b.close for b in w.market.bars(s)} for s in names})
    try:
        yield w, fake, stock
    finally:
        w.close()


def download_all(w: Any, fake: FakeMassive) -> None:
    asyncio.run(ivh.task(fake, w.market, w.paths.home, NOW, 1000))
    asyncio.run(lh.download(fake, w.market, NOW, 1000))
    asyncio.run(pr.download(fake, w.market, NOW, 1000))


def test_the_ladder_is_the_money_pair_and_out_of_the_money_steps() -> None:
    expiry = date(2026, 9, 18)
    listed = [{"ticker": occ("X", expiry, k, s), "kind": k, "strike": s, "expiration": expiry}
              for s in range(60, 161) for k in ("call", "put")]
    chosen = pr.choose(listed, 100.4, 0.30, expiry)
    calls = sorted(c["strike"] for c in chosen if c["kind"] == "call")
    puts = sorted(c["strike"] for c in chosen if c["kind"] == "put")
    assert 100 in calls and 100 in puts                                   # the at-the-money pair
    assert len(calls) == len(puts) == 1 + len(pr.LADDER) and max(calls) > 120 and min(puts) < 85
    assert pr.PER_NAME_MONTH == 13


def test_nothing_is_replayed_until_its_inputs_are_downloaded_then_every_section_is(world: Any) -> None:
    w, fake, _ = world
    assert pr.record(w.market, NOW) == {"days": 0, "plays": 0}             # no listing yet: wait, never guess
    asyncio.run(pr.download(fake, w.market, NOW, 1000))
    assert pr.record(w.market, NOW)["days"] == 0                           # the IV rank's year is not downloaded yet
    download_all(w, fake)
    done = pr.record(w.market, NOW)
    assert done["days"] >= 6 and done["plays"] > 0
    rows = w.market.conn.execute("SELECT category, COUNT(*) n FROM stk_replay_days GROUP BY category").fetchall()
    assert {r["category"] for r in rows} == set(pr.REPLAYED)
    plays = w.market.conn.execute("SELECT * FROM stk_replay_plays").fetchall()
    assert {p["source"] for p in plays} == {pr.SOURCE}
    by = {p["category"] for p in plays}
    assert by & {"edge", "strangles", "cheap_vol"} and by & set(pr.ETF_ONLY)
    assert all(p["symbol"] == "SPY" for p in plays if p["category"] in pr.ETF_ONLY)     # no committee view live
    assert {p["basis"] for p in plays} <= {"chain", "lognormal"}
    for p in plays:                                    # bought at the close plus half the assumed spread
        legs = json.loads(p["legs_json"])
        for leg in legs:
            close = (leg["bid"] + leg["ask"]) / 2
            assert leg["ask"] - close == pytest.approx(max(pr.MIN_HALF, pr.HALF_SPREAD * close), abs=0.002)
    assert pr.record(w.market, NOW) == {"days": 0, "plays": 0}             # each day once
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        w.market.conn.execute("UPDATE stk_replay_plays SET entry = 0")


def test_a_replayed_day_sees_only_what_was_known_then(world: Any) -> None:
    w, fake, _ = world
    download_all(w, fake)
    inputs = pr.Inputs(w.market, NOW.date())
    d = next(day for day in pr.decision_days(w.days[-200], w.days[-1]) if inputs.ready(day))
    ctx = pr._context(w.market, d)
    items = {s: {e: inputs.lists[(s, e)] for e in inputs.monthly(d)} for s in inputs.names}
    tables = pr.tables(w.market, d, ctx, items)
    assert tables
    for tb in tables:
        close = w.market.conn.execute("SELECT close FROM stk_bars WHERE symbol=? AND day=?",
                                      (tb.u.symbol, d.toordinal())).fetchone()[0]
        assert tb.u.price == close and tb.u.closes[-1] == pytest.approx(close)
        assert tb.u.committee is None and all(q.expiration > d for q in tb.quotes)
        assert all((q.expiration - d).days >= pr.WINDOW[0] for q in tb.quotes)
        if tb.u.iv_basis:
            assert tb.u.iv_basis["first"] is None or tb.u.iv_basis["first"] < d.isoformat()


def test_picks_are_graded_like_forward_plays_and_kept_apart(world: Any) -> None:
    w, fake, _ = world
    download_all(w, fake)
    pr.record(w.market, NOW)
    graded = pr.grade(w.market, NOW)
    assert graded > 0 and pr.grade(w.market, NOW) == 0
    row = w.market.conn.execute("SELECT p.*, o.* FROM stk_replay_plays p JOIN stk_replay_outcomes o USING "
                                "(method, session, category, rank) LIMIT 1").fetchone()
    legs = json.loads(row["legs_json"])
    close = w.market.conn.execute("SELECT close FROM stk_bars WHERE symbol=? AND day=?",
                                  (row["symbol"], row["expiration"])).fetchone()[0]
    value = pl.settle_value(legs, close)
    fees = pl.FEE * len(legs) / 100
    want = ((value - row["entry"] - fees) / (row["entry"] + fees) if row["action"] == "BUY"
            else (row["entry"] + value - fees) / (row["max_loss"] / 100))
    assert row["net_return"] == pytest.approx(want) and row["underlying_close"] == close
    v = pr.view(w.market.conn, NOW)
    assert v["label"].startswith("Replay") and v["resolved"] == graded
    assert len(v["skipped"]) == 8 and {s["id"] for s in v["sections"]} == set(pr.REPLAYED)
    edge = next(s for s in v["sections"] if s["resolved"])
    assert edge["hit_rate"] is not None and edge["twins"]["resolved"] >= 0
    assert v["calibration"]["itm_kestrel_raw"]["resolved"] > 0 and "profit" in v["calibration"]["events"]
    # the forward record and its calibration are untouched
    assert w.research.conn.execute("SELECT COUNT(*) FROM stk_plays").fetchone()[0] == 0
    assert not any(c.buckets for c in pl.calibration(w.research).values())


def test_the_queue_gives_the_replay_a_modest_share_and_reports_what_is_left(world: Any) -> None:
    w, fake, _ = world
    assert ("plays_replay", pr.task) in mo.tasks()
    got = asyncio.run(pr.task(fake, w.market, w.paths.home, NOW, 40))
    assert got["ok"] and 0 < got["requests"] <= pr.PER_PASS_CLOSED
    c = got["coverage"]
    assert c["requests_planned"] == 2 * 3 * pr.PER_NAME_MONTH and c["requests_left"] > 0
    assert c["days_left_at_share"] == pytest.approx(c["requests_left"] / (pr.PER_PASS_CLOSED * pr.PASSES_PER_DAY),
                                                    abs=0.1)
    opened = datetime(2026, 10, 22, 15, 0, tzinfo=UTC)                     # a weekday, market open
    assert cal.market_session(opened)["state"] == "open"
    assert asyncio.run(pr.task(fake, w.market, w.paths.home, opened, 8))["requests"] <= pr.PER_PASS_OPEN


def test_performance_and_the_learning_report_show_the_replay_apart(world: Any) -> None:
    from desk.stocks import learning_report as lr
    from desk.stocks import play_performance as pp

    w, fake, _ = world
    assert pp.build(w.market.conn, w.research.conn, NOW)["replay"] is None      # nothing replayed: nothing shown
    download_all(w, fake)
    pr.record(w.market, NOW)
    pr.grade(w.market, NOW)
    perf = pp.build(w.market.conn, w.research.conn, NOW)
    assert perf["replay"]["resolved"] > 0 and perf["replay"]["label"].startswith("Replay")
    assert perf["plays"] == 0 and perf["calibration"]["itm_kestrel"]["resolved"] == 0      # forward: untouched
    report = lr.Report(NOW)
    lr.replays(lr.Sources(views={"plays_replay": pr.view(w.market.conn)}), report)
    items = [i for i in report.items if i["key"].startswith("plays-replay:")]
    assert items and all(i["section"] == "replay" and "replayed" in i["detail"] for i in items)
