"""Plays history, step two: valuing every structure, daily marks, gap repair, settlement, quarantine and twins."""

from __future__ import annotations

import json
import math
import sqlite3
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from desk.core.errors import DeskError
from desk.core.paths import DeskPaths
from desk.stocks import calendar as cal
from desk.stocks import odds as od
from desk.stocks import options as opt
from desk.stocks import play_marks as pm
from desk.stocks import plays as pl
from desk.stocks.contracts import Bar, IntradayBar, OptionQuote
from desk.stocks.paper.fills import OPTION_FEE_PER_CONTRACT as FEE
from desk.stocks.repo import StocksDatabases, day_of
from tests.conftest import REPO

D = cal.trading_days(date(2026, 11, 16), date(2026, 12, 4))     # Mon 16 Nov … ; Thanksgiving (26 Nov) is not there
D0, D1, D2, D3, D4, D5, D6, D7, D8, D9 = D[:10]                 # D8 is Fri 27 Nov, D9 is Mon 30 Nov
EXP = date(2026, 12, 18)
RATE = 0.04


def rate(_: date) -> float:
    return RATE


def ny(day: date, hour: int = 17) -> datetime:
    return datetime(day.year, day.month, day.day, hour, tzinfo=cal.NEW_YORK).astimezone(UTC)


def occ(symbol: str, kind: str, strike: float, expiration: date = EXP) -> str:
    return f"{symbol}{expiration:%y%m%d}{kind[0].upper()}{round(strike * 1000):08d}"


def leg(symbol: str, kind: str, strike: float, side: str = "buy", expiration: date = EXP, bid: float = 1.0,
        ask: float = 1.1, iv: float = 0.5, delta: float = 0.5) -> dict[str, Any]:
    return {"occ": occ(symbol, kind, strike, expiration), "kind": kind, "side": side, "strike": strike,
            "expiration": expiration.isoformat(), "bid": bid, "ask": ask, "mid": (bid + ask) / 2, "iv": iv,
            "delta": delta}


def position(legs: list[dict[str, Any]], action: str = "BUY", structure: str = "long_call", entry: float = 1.1,
             max_loss: float | None = None, expiration: date = EXP) -> pm.Position:
    loss = max_loss if max_loss is not None else entry * 100 + FEE * len(legs)
    return pm.Position(D0, "edge", 1, 0, legs[0]["occ"][:-15], action, structure, expiration, legs, entry, loss, 12.0)


# ── valuing every structure ──────────────────────────────────────────────────────────────────────
def quotes(**by_occ: tuple[float, float]) -> dict[str, pm.LegQuote]:
    return {k: pm.LegQuote(b, a, "quoted") for k, (b, a) in by_occ.items()}


def test_long_calls_and_puts_are_worth_the_bid_and_pay_fees_both_ways() -> None:
    call = leg("AAL", "call", 12)
    p = position([call])
    value, mid = pm.liquidation(p.legs, quotes(**{call["occ"]: (1.50, 1.60)}))
    assert value == 1.50 and mid == pytest.approx(1.55)
    pnl, pct = pm.profit("BUY", 1.10, p.max_loss, value, 1, settled=False)
    assert pnl == pytest.approx((1.50 - 1.10) * 100 - 2 * FEE)
    assert pct == pytest.approx(pnl / (110 + FEE))
    put = leg("AAL", "put", 11)
    value, _ = pm.liquidation([put], quotes(**{put["occ"]: (0.20, 0.25)}))
    assert pm.profit("BUY", 1.10, 110.65, value, 1, settled=False)[0] == pytest.approx(-90 - 2 * FEE)


def test_straddles_and_credit_spreads() -> None:
    c, p = leg("SPY", "call", 500), leg("SPY", "put", 500)
    value, _ = pm.liquidation([c, p], quotes(**{c["occ"]: (6.0, 6.2), p["occ"]: (4.0, 4.2)}))
    assert value == pytest.approx(10.0)
    assert pm.profit("BUY", 9.0, 900 + 2 * FEE, value, 2, settled=False)[0] == pytest.approx(100 - 4 * FEE)
    short, long_ = leg("SPY", "put", 480, "sell"), leg("SPY", "put", 470, "buy")   # bull put credit, $1.50 credit
    loss = (10 - 1.5) * 100 + 2 * FEE
    value, mid = pm.liquidation([short, long_], quotes(**{short["occ"]: (0.90, 1.00), long_["occ"]: (0.40, 0.45)}))
    assert value == pytest.approx(-1.00 + 0.40) and mid == pytest.approx(-0.95 + 0.425)   # buy back short at the ask
    pnl, pct = pm.profit("SELL", 1.50, loss, value, 2, settled=False)
    assert pnl == pytest.approx((1.50 - 0.60) * 100 - 4 * FEE) and pct == pytest.approx(pnl / loss)
    call_short, call_long = leg("XOM", "call", 120, "sell"), leg("XOM", "call", 125, "buy")  # bear call credit
    value, _ = pm.liquidation([call_short, call_long], quotes(**{call_short["occ"]: (3.0, 3.2),
                                                                 call_long["occ"]: (1.0, 1.1)}))
    assert value == pytest.approx(-2.2)


def test_settlement_matches_the_existing_grade() -> None:
    short, long_ = leg("SPY", "put", 480, "sell"), leg("SPY", "put", 470, "buy")
    assert pm.settlement([short, long_], 475) == pytest.approx(-5.0)
    assert pm.settlement([short, long_], 460) == pytest.approx(-10.0)
    assert pm.settlement([short, long_], 490) == 0.0
    assert pm.settlement([short, long_], 475) == pl.settle_value([short, long_], 475)
    entry, loss = 1.5, (10 - 1.5) * 100 + 2 * FEE
    _, pct = pm.profit("SELL", entry, loss, pm.settlement([short, long_], 475), 2, settled=True)
    grade = (entry + -5.0 - FEE * 2 / 100) / (loss / 100)                         # plays.grade's net return
    assert pct == pytest.approx(grade)
    call = leg("AAL", "call", 12)
    _, pct = pm.profit("BUY", 1.1, 110 + FEE, pm.settlement([call], 14), 1, settled=True)
    assert pct == pytest.approx((2.0 - 1.1 - FEE / 100) / (1.1 + FEE / 100))


def test_assignment_risk_is_flagged_on_deep_short_legs() -> None:
    short, long_ = leg("SPY", "put", 480, "sell"), leg("SPY", "put", 470, "buy")
    p = position([short, long_], "SELL", "bull_put_credit", 1.5, 851.3)
    deep = {short["occ"]: pm.LegQuote(20.0, 20.04, "quoted"), long_["occ"]: pm.LegQuote(10.0, 10.1, "quoted")}
    assert "Early-assignment" in (pm.assignment_risk(p, 460.0, deep) or "")
    assert pm.assignment_risk(p, 490.0, deep) is None


# ── storage helpers ──────────────────────────────────────────────────────────────────────────────
@pytest.fixture
def dbs(tmp_path: Path) -> Any:
    out = StocksDatabases.open(DeskPaths(tmp_path / "home", REPO / "config", tmp_path / "secrets.env"))
    yield out
    out.close()


def record(conn: sqlite3.Connection, p: pm.Position) -> None:
    conn.execute(
        "INSERT INTO stk_plays(session, category, rank, symbol, action, structure, expiration, event, event_kind,"
        " event_strike, event_width, entry, max_loss, legs_json, underlying, itm_market, itm_history, itm_kestrel,"
        " itm_kestrel_raw, profit_kestrel, play_json, method, recorded_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'itm', ?, ?, 0,"
        " ?, ?, ?, ?, 0.4, 0.4, 0.4, 0.4, 0.3, ?, 'm', 'x')",
        (day_of(p.session), p.category, p.rank, p.symbol, p.action, p.structure, day_of(p.expiration),
         p.legs[0]["kind"], p.legs[0]["strike"], p.entry, p.max_loss, json.dumps(p.legs), p.underlying,
         json.dumps({"earnings": p.earnings.isoformat() if p.earnings else None})))


def quoted(conn: sqlite3.Connection, occ_: str, day: date, bid: float, ask: float, iv: float, s: float) -> None:
    conn.execute("INSERT INTO stk_track_days(occ, session, status, bid, ask, mark, underlying, iv, source, recorded_at)"
                 " VALUES (?, ?, 'quoted', ?, ?, ?, ?, ?, 'schwab_quotes', 'x')",
                 (occ_, day_of(day), bid, ask, (bid + ask) / 2, s, iv))


def bars(conn: sqlite3.Connection, symbol: str, closes: dict[date, float]) -> None:
    conn.executemany("INSERT INTO stk_bars(symbol, day, open, high, low, close, volume) VALUES (?, ?, ?, ?, ?, ?, 1e6)",
                     [(symbol, day_of(d), c, c * 1.01, c * 0.99, c) for d, c in closes.items()])


class FakeHistory:
    configured = True

    def __init__(self, closes: dict[str, dict[date, float]], minutes: dict[date, float] | None = None) -> None:
        self.closes, self.minutes = closes, minutes or {}
        self.daily: list[tuple[str, date]] = []
        self.intraday: list[str] = []

    async def daily_since(self, symbol: str, since: date, today: date) -> list[Bar]:
        self.daily.append((symbol, since))
        return [Bar(symbol, d, c, c, c, c, 1e6) for d, c in sorted(self.closes.get(symbol, {}).items())]

    async def intraday_history(self, symbol: str) -> list[IntradayBar]:
        self.intraday.append(symbol)
        out: list[IntradayBar] = []
        for d, s in self.minutes.items():
            start = datetime(d.year, d.month, d.day, 9, 30, tzinfo=cal.NEW_YORK).astimezone(UTC)
            out.extend(IntradayBar(symbol, start + timedelta(minutes=i), s, s, s, s, 100) for i in range(390))
        return out


def marks(conn: sqlite3.Connection, table: str = "stk_play_marks") -> dict[tuple[date, int], sqlite3.Row]:
    return {(date.fromordinal(r["day"]), r["rank"]): r for r in conn.execute(f"SELECT * FROM {table}")}


def calm(conn: sqlite3.Connection, symbol: str, start: float = 12.0) -> None:
    """Forty quiet sessions before the pick (for the volatility the jump flag measures against)."""
    days = cal.trading_days(D0 - timedelta(days=70), cal.previous_trading_day(D0))[-40:]
    bars(conn, symbol, {d: start * (1 + 0.003 * (-1) ** i) for i, d in enumerate(days)})


# ── gap repair ───────────────────────────────────────────────────────────────────────────────────
async def test_a_laptop_off_gap_is_estimated_from_the_real_stock_path_and_flagged(dbs: Any) -> None:
    research, market = dbs.research, dbs.market
    call = leg("AAL", "call", 12, iv=0.5)
    p = position([call])
    record(research, p)
    calm(market, "AAL")
    bars(market, "AAL", {D0: 12.0, D1: 12.2, D5: 12.5, D6: 12.6})
    quoted(research, call["occ"], D1, 1.15, 1.25, 0.50, 12.2)
    quoted(research, call["occ"], D5, 1.60, 1.70, 0.60, 12.5)
    quoted(research, call["occ"], D6, 1.70, 1.80, 0.60, 12.6)
    history = FakeHistory({"AAL": {D2: 12.3, D3: 15.0, D4: 12.4}}, minutes={D4: 12.4})
    report = await pm.update(market, research, ny(D6), history=history, rate=rate, budget=20)
    assert history.daily == [("AAL", D2)] and report["pending"] == 0
    rows = {date.fromordinal(r["session"]): r for r in research.execute("SELECT * FROM stk_track_days")}
    assert [rows[d]["status"] for d in (D0, D1, D2, D3, D4, D5, D6)] == [
        "quoted", "quoted", "estimated", "estimated", "estimated", "quoted", "quoted"]
    assert rows[D0]["source"] == "recorded play" and rows[D0]["bid"] == 1.0
    for d, iv in ((D2, 0.525), (D3, 0.55), (D4, 0.575)):                       # interpolated between D1 and D5
        assert rows[d]["iv"] == pytest.approx(iv)
        price = opt.bs_price("call", rows[d]["underlying"], 12, max((EXP - d).days, 0.25) / 365, iv, RATE)
        assert rows[d]["bid"] == pytest.approx(price) and "laptop was off" in rows[d]["note"]
    got = marks(research)
    assert {d: got[(d, 1)]["status"] for d in (D0, D2, D5)} == {D0: "quoted", D2: "estimated", D5: "quoted"}
    assert "unreliable" in got[(D3, 1)]["note"] and "standard deviations" in got[(D3, 1)]["note"]
    assert got[(D5, 1)]["pnl"] == pytest.approx((1.60 - 1.10) * 100 - 2 * FEE)
    # recent gap sessions also get an estimated intraday path from the stock's minute bars
    intraday = research.execute("SELECT session, status, points FROM stk_track_intraday").fetchall()
    assert [(date.fromordinal(r[0]), r[1]) for r in intraday] == [(D4, "estimated")] and intraday[0][2] == 78
    # point in time: a late quote for a marked session changes nothing, and running again is a no-op
    research.execute("DELETE FROM stk_track_days WHERE 0")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        research.execute("UPDATE stk_play_marks SET pnl = 0")
    again = await pm.update(market, research, ny(D6, 18), history=history, rate=rate, budget=20)
    assert again["open"] == 0 and len(history.daily) == 1 and marks(research) == got


async def test_an_expiry_inside_the_gap_is_settled_exactly_on_the_close(dbs: Any) -> None:
    research, market = dbs.research, dbs.market
    put = leg("AAL", "put", 13, expiration=D4, bid=1.0, ask=1.1, iv=0.5)
    p = position([put], structure="long_put", expiration=D4)
    record(research, p)
    bars(market, "AAL", {D0: 12.0, D1: 12.2})
    quoted(research, put["occ"], D1, 0.9, 1.0, 0.5, 12.2)
    history = FakeHistory({"AAL": {D2: 12.3, D3: 12.1, D4: 11.4}})
    await pm.update(market, research, ny(D6), history=history, rate=rate, budget=20)
    got = marks(research)
    settled = got[(D4, 1)]
    assert settled["status"] == "settled" and settled["underlying"] == 11.4 and settled["value"] == pytest.approx(1.6)
    assert settled["pnl"] == pytest.approx((1.6 - 1.1) * 100 - FEE)            # opening fee only: it expired
    assert max(d for d, _ in got) == D4 and got[(D3, 1)]["status"] == "estimated"
    assert "held at the last quote" in research.execute(
        "SELECT note FROM stk_track_days WHERE session = ?", (day_of(D3),)).fetchone()[0]


async def test_a_trailing_gap_is_provisional_until_a_later_quote_brackets_it(dbs: Any) -> None:
    research, market = dbs.research, dbs.market
    call = leg("AAL", "call", 12, iv=0.5)
    record(research, position([call]))
    bars(market, "AAL", {D0: 12.0, D1: 12.2})
    quoted(research, call["occ"], D1, 1.15, 1.25, 0.50, 12.2)
    history = FakeHistory({"AAL": {D2: 12.3, D3: 12.4}})
    report = await pm.update(market, research, ny(D3), history=history, rate=rate, budget=20)
    assert report["provisional"] == 2 and set(marks(research)) == {(D0, 1), (D1, 1)}
    provisional = marks(research, "stk_play_marks_provisional")
    assert set(provisional) == {(D2, 1), (D3, 1)} and "provisional" in provisional[(D2, 1)]["note"]
    assert research.execute("SELECT COUNT(*) FROM stk_track_days WHERE session >= ?", (day_of(D2),)).fetchone()[0] == 0
    quoted(research, call["occ"], D4, 1.40, 1.50, 0.70, 12.6)
    bars(market, "AAL", {D4: 12.6})
    await pm.update(market, research, ny(D4), history=history, rate=rate, budget=20)
    got = marks(research)
    assert got[(D2, 1)]["status"] == "estimated" and got[(D4, 1)]["status"] == "quoted"
    assert research.execute("SELECT COUNT(*) FROM stk_play_marks_provisional").fetchone()[0] == 0
    assert research.execute("SELECT iv FROM stk_track_days WHERE session = ?", (day_of(D2),)).fetchone()[0] == \
        pytest.approx(0.5 + (0.7 - 0.5) / 3)


async def test_holidays_are_not_gaps_and_a_missing_quote_while_tracking_is_modelled(dbs: Any) -> None:
    research, market = dbs.research, dbs.market
    a, b = leg("AAL", "call", 12, iv=0.5), leg("AAL", "call", 13, iv=0.45)
    record(research, position([a]))
    record(research, pm.Position(D0, "edge", 2, 0, "AAL", "BUY", "long_call", EXP, [b], 0.6, 60 + FEE, 12.0))
    closes = {d: 12.0 + 0.1 * i for i, d in enumerate(D[:10])}
    bars(market, "AAL", closes)
    for d in D[1:10]:
        quoted(research, a["occ"], d, 1.2, 1.3, 0.5, closes[d])                  # tracking ran every session
    await pm.update(market, research, ny(D9), history=None, rate=rate, budget=0)
    got = marks(research)
    assert date(2026, 11, 26) not in {d for d, _ in got}                          # Thanksgiving is not a session
    assert all(got[(d, 2)]["status"] == "modelled" for d in D[1:10])             # tracked sessions without its quote
    row, first = (research.execute("SELECT * FROM stk_track_days WHERE occ = ? AND session = ?",
                                   (b["occ"], day_of(d))).fetchone() for d in (D9, D0))
    assert first["source"] == "recorded play" and first["iv"] > 0                 # solved from the recorded quote
    assert row["iv"] == pytest.approx(first["iv"]) and row["bid"] == pytest.approx(
        opt.bs_price("call", closes[D9], 13, (EXP - D9).days / 365, first["iv"], RATE))


async def test_a_split_ends_the_record_and_quarantines_the_play(dbs: Any) -> None:
    research, market = dbs.research, dbs.market
    call = leg("XYZ", "call", 50)
    record(research, pm.Position(D0, "edge", 1, 0, "XYZ", "BUY", "long_call", EXP, [call], 2.0, 200 + FEE, 50.0))
    bars(market, "XYZ", {d: 50.0 for d in D[:6]})
    for d in D[1:6]:
        quoted(research, call["occ"], d, 2.0, 2.1, 0.4, 50.0)
    market.executemany("INSERT INTO stk_splits VALUES (?, ?, ?, ?, 'x')", [("XYZ", day_of(D3), 1, 2)])
    await pm.update(market, research, ny(D5), history=None, rate=rate, budget=0)
    assert max(d for d, _ in marks(research)) == D2
    reason = research.execute("SELECT reason FROM stk_play_quarantine").fetchone()[0]
    assert reason.startswith(f"Split on {D3.isoformat()}")


async def test_history_is_not_refetched_within_six_hours(dbs: Any) -> None:
    research, market = dbs.research, dbs.market
    call = leg("AAL", "call", 12)
    record(research, position([call]))
    history = FakeHistory({})                                                    # Schwab has nothing either
    await pm.update(market, research, ny(D3), history=history, rate=rate, budget=20)
    await pm.update(market, research, ny(D3, 19), history=history, rate=rate, budget=20)
    assert len(history.daily) == 1
    state = json.loads(research.execute("SELECT value_json FROM stk_track_state WHERE key = 'repair'").fetchone()[0])
    assert state["pending"] == 1 and state["note"]


async def test_a_refused_history_request_is_asked_again_on_the_next_pass(dbs: Any) -> None:
    """A Schwab sign-in that lapsed mid-gap must not delay the repair by six hours once it works again."""
    research, market = dbs.research, dbs.market
    record(research, position([leg("AAL", "call", 12)]))

    class SignedOut(FakeHistory):
        async def daily_since(self, symbol: str, since: date, today: date) -> list[Bar]:
            self.daily.append((symbol, since))
            raise DeskError("Schwab sign-in has expired")

    refused, working = SignedOut({}), FakeHistory({})
    await pm.update(market, research, ny(D3), history=refused, rate=rate, budget=20)
    await pm.update(market, research, ny(D3, 19), history=working, rate=rate, budget=20)
    assert len(refused.daily) == 1 and len(working.daily) == 1


# ── twins ────────────────────────────────────────────────────────────────────────────────────────
NOW = datetime(2026, 11, 16, 15, tzinfo=UTC)


def chain_table(symbol: str, price: float, expirations: list[date]) -> pl.Table:
    quotes = []
    for e in expirations:
        t = (e - D0).days / 365
        for kind in ("call", "put"):
            for k in [round(price * (0.7 + 0.05 * i), 2) for i in range(13)]:
                mid = opt.bs_price(kind, price, k, t, 0.4, RATE)
                if mid >= 0.1:
                    quotes.append(OptionQuote(occ(symbol, kind, k, e), symbol, kind, k, e, round(mid * 0.97, 2),
                                              round(mid * 1.03, 2), mid, 100, 1000, None, NOW))
    u = pl.Underlying(symbol, price, None, "schwab", {e: [q for q in quotes if q.expiration == e] for e in expirations})
    ctx = pl.Context(D0, NOW, RATE, None, 0.0, None, {"itm": od.Calibration(), "move": od.Calibration()}, [])
    tb = pl.table(u, ctx)
    assert tb is not None
    return tb


def test_twins_match_structure_expiry_and_delta_on_another_stock_reproducibly() -> None:
    tables = [chain_table("AAL", 12, [EXP]), chain_table("KO", 70, [EXP, date(2027, 1, 15)]),
              chain_table("XOM", 110, [date(2026, 12, 24)]), chain_table("F", 10, [date(2027, 3, 19)])]
    play = {"symbol": "AAL", "category": "edge", "rank": 3, "expiration": EXP.isoformat(), "structure": "long_call",
            "action": "BUY", "underlying_price": 12.0, "legs": [{**leg("AAL", "call", 12), "delta": 0.35}]}
    t = pl.twin(play, tables, D0)
    assert t is not None and t["symbol"] == "KO" and t["expiration"] == EXP.isoformat()   # same expiry beats nearby
    assert t["legs"][0]["kind"] == "call" and abs(t["legs"][0]["delta"] - 0.35) < 0.15
    assert pl.twin(play, tables, D0) == t                                          # the same draw every time
    straddle = {**play, "structure": "long_straddle", "legs": [leg("AAL", "call", 12), leg("AAL", "put", 12)]}
    s = pl.twin(straddle, tables, D0)
    assert s is not None and {x["kind"] for x in s["legs"]} == {"call", "put"}
    assert s["legs"][0]["strike"] == s["legs"][1]["strike"] and s["entry"] > 0
    spread = {**play, "action": "SELL", "structure": "bull_put_credit",
              "legs": [{**leg("AAL", "put", 11.4, "sell"), "delta": -0.25}, leg("AAL", "put", 10.8, "buy")]}
    c = pl.twin(spread, tables, D0)
    assert c is not None and [x["side"] for x in c["legs"]] == ["sell", "buy"]
    assert c["legs"][1]["strike"] < c["legs"][0]["strike"] and c["entry"] > 0
    width = c["legs"][0]["strike"] - c["legs"][1]["strike"]
    assert c["max_loss"] == pytest.approx((width - c["entry"]) * 100 + 2 * FEE, abs=0.01)
    lonely = {**play, "expiration": date(2028, 1, 21).isoformat()}
    assert pl.twin(lonely, tables, D0) is None                                     # nothing within a week: no twin
    assert math.isfinite(t["entry"])
