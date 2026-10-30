"""Plays history, step one: live tracking. Schwab responses here are documentation-shaped synthetic examples, not
captured broker responses; ``desk stocks doctor --online`` verifies the real endpoint on the operator's laptop."""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from desk.core.config import load_config
from desk.core.errors import ProviderError, ProviderErrorKind
from desk.core.paths import DeskPaths
from desk.core.secrets import Secrets
from desk.stocks import calendar as cal
from desk.stocks import play_tracking as pt
from desk.stocks.connection import save_connection
from desk.stocks.contracts import OptionQuote, OptionTick, StockQuote
from desk.stocks.repo import StocksDatabases, day_of
from desk.stocks.schwab_auth import SchwabTokens, token_path
from desk.stocks.sources.schwab import parse_option_quotes, schwab_option_symbol
from desk.stocks.suite import build_stocks_suite
from tests.conftest import REPO

SESSION = date(2026, 10, 22)
EXP = date(2026, 11, 20)
QUOTES = "https://api.schwabapi.com/marketdata/v1/quotes"


def ny(hour: int, minute: int = 0, day: date = SESSION) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=cal.NEW_YORK).astimezone(UTC)


def option_row(occ: str, **quote: Any) -> dict[str, Any]:
    root, tail = occ[:-15], occ[-15:]
    return {"symbol": f"{root:<6}{tail}", "assetMainType": "OPTION",
            "reference": {"strikePrice": int(tail[-8:]) / 1000, "contractType": tail[6], "multiplier": 100,
                          "underlying": root, "expirationYear": 2000 + int(tail[:2]), "expirationMonth": int(tail[2:4]),
                          "expirationDay": int(tail[4:6])},
            "quote": {"bidPrice": 5.0, "askPrice": 5.2, "lastPrice": 5.1, "mark": 5.1, "totalVolume": 120,
                      "openInterest": 400, "volatility": 23.5, "delta": 0.52, "gamma": 0.02, "theta": -0.08,
                      "vega": 0.31, "underlyingPrice": 501.0, "quoteTime": 1792602000000, **quote}}


def occ_of(symbol: str, kind: str, strike: float, expiration: date = EXP) -> str:
    return f"{symbol}{expiration:%y%m%d}{kind[0].upper()}{round(strike * 1000):08d}"


# ── the parser ───────────────────────────────────────────────────────────────────────────────────
def test_option_quotes_keep_identity_units_and_signs() -> None:
    call, put = occ_of("SPY", "call", 500), occ_of("SPY", "put", 495)
    bad_strike, adjusted, other_root, unwanted = (occ_of("SPY", "call", 505), occ_of("SPY", "call", 510),
                                                  occ_of("SPY", "put", 490), occ_of("SPY", "put", 480))
    rows = {call: option_row(call),
            put: option_row(put, delta=-0.48, theta=-0.07, gamma=-999, volatility="NaN"),
            bad_strike: {**option_row(bad_strike), "reference": {**option_row(bad_strike)["reference"],
                                                                  "strikePrice": 506}},
            adjusted: {**option_row(adjusted), "reference": {**option_row(adjusted)["reference"], "multiplier": 10}},
            other_root: {**option_row(other_root), "reference": {**option_row(other_root)["reference"],
                                                                  "underlying": "SPY1"}},
            unwanted: option_row(unwanted)}
    ticks = {t.occ: t for t in parse_option_quotes(json.dumps(rows).encode(),
                                                     {call, put, bad_strike, adjusted, other_root})}
    assert set(ticks) == {call, put}
    assert ticks[call].iv == pytest.approx(0.235) and ticks[call].delta == 0.52 and ticks[call].theta == -0.08
    assert ticks[call].underlying_price == 501.0 and ticks[call].quoted_at is not None
    assert ticks[call].mid == pytest.approx(5.1)
    assert ticks[put].delta == -0.48 and ticks[put].gamma is None and ticks[put].iv is None


def test_option_symbols_convert_both_ways() -> None:
    assert schwab_option_symbol("SPY261120C00500000") == "SPY   261120C00500000"
    assert schwab_option_symbol("GOOGL261120P00150500") == "GOOGL 261120P00150500"
    assert pt.parse_occ("AAL261120C00012000") == ("AAL", "call", 12.0, EXP)
    with pytest.raises(ValueError):
        schwab_option_symbol("SPY 261120C00500000")


# ── the fake Schwab endpoint: batches, drops, token refresh, budget ─────────────────────────────
@pytest.fixture
def schwab_suite(tmp_path: Path) -> Any:
    paths = DeskPaths(tmp_path / "home", REPO / "config", tmp_path / "secrets.env")
    save_connection(paths, app_key="public-key", app_secret="private-value",
                    callback="https://127.0.0.1:8182/oauth/callback", provider="schwab")
    credentials = Secrets(values={"SCHWAB_APP_KEY": "public-key", "SCHWAB_APP_SECRET": "private-value"})
    tokens = SchwabTokens(token_path(paths), credentials)
    tokens.save({"format": 1, "access_token": "first-bearer", "refresh_token": "private-refresh",
                 "expires_at": time.time() + 1800, "authorized_at": time.time()})
    dbs = StocksDatabases.open(paths)
    suite = build_stocks_suite(paths, load_config(paths.config_dir), credentials, dbs.runtime, client=httpx.AsyncClient())

    async def no_sleep(_: float) -> None:
        return None

    assert suite.schwab is not None
    suite.schwab.http._sleep = no_sleep
    yield suite, tokens, dbs
    dbs.close()


def answer(request: httpx.Request) -> httpx.Response:
    wanted = request.url.params["symbols"].split(",")
    assert request.url.params["fields"] == "quote,reference"
    return httpx.Response(200, json={s: option_row(s.replace(" ", "")) for s in wanted})


def tracked(n: int, priority: int = 0) -> list[pt.Tracked]:
    return [pt.Tracked(occ_of("SPY", "call", 400 + i), "SPY", "call", 400.0 + i, EXP, priority) for i in range(n)]


@respx.mock
async def test_quotes_go_100_a_request_padded_and_keep_schwab_greeks(schwab_suite: Any) -> None:
    suite, _, _ = schwab_suite
    route = respx.get(QUOTES).mock(side_effect=answer)
    saved: list[tuple[list[OptionTick], str]] = []
    try:
        result = await pt.quote_pass(tracked(250), lambda t, s: saved.append((t, s)), schwab=suite.schwab, chains=None)
    finally:
        await suite.aclose()
    assert route.call_count == 3 and result.requests == 3 and result.quoted == 250 and not result.missing
    assert [len(c.request.url.params["symbols"].split(",")) for c in route.calls] == [100, 100, 50]
    assert "SPY   261120C00400000" in route.calls[0].request.url.params["symbols"]
    assert result.endpoint == "working" and saved[0][1] == "schwab_quotes"
    assert saved[0][0][0].delta == 0.52 and saved[0][0][0].iv == pytest.approx(0.235)


@respx.mock
async def test_a_dropped_connection_is_retried_and_an_expired_token_is_refreshed(schwab_suite: Any) -> None:
    suite, tokens, _ = schwab_suite
    tokens.save({**tokens.read(), "expires_at": time.time() - 5})          # expired: refreshed before the request
    refresh = respx.post("https://api.schwabapi.com/v1/oauth/token").respond(
        200, json={"access_token": "second-bearer", "expires_in": 1800, "token_type": "Bearer"})
    calls = {"n": 0}

    def flaky(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("dropped")
        return answer(request)

    route = respx.get(QUOTES).mock(side_effect=flaky)
    try:
        result = await pt.quote_pass(tracked(10), lambda *_: None, schwab=suite.schwab, chains=None)
    finally:
        await suite.aclose()
    assert refresh.call_count == 1 and result.quoted == 10
    assert route.calls[-1].request.headers["Authorization"] == "Bearer second-bearer"
    assert "private-value" not in str(route.calls[-1].request.url)


class FakeChains:
    configured = True

    def __init__(self) -> None:
        self.calls: list[tuple[str, date]] = []

    async def chain(self, symbol: str, expiration: date) -> list[OptionQuote]:
        self.calls.append((symbol, expiration))
        return [OptionQuote(occ_of(symbol, "call", 400 + i, expiration), symbol, "call", 400.0 + i, expiration, 1.0, 1.1,
                            1.05, 10, 100, None, ny(11)) for i in range(300)]

    async def quotes(self, symbols: list[str]) -> list[StockQuote]:
        return [StockQuote(s, 450.0, 449.9, 450.1, 448.0, 1e6, ny(11)) for s in symbols]


@respx.mock
async def test_budget_is_respected_and_what_schwab_misses_falls_back_to_chains(schwab_suite: Any) -> None:
    suite, _, _ = schwab_suite

    def partial(request: httpx.Request) -> httpx.Response:
        wanted = request.url.params["symbols"].split(",")[:-5]            # five contracts not returned per batch
        return httpx.Response(200, json={s: option_row(s.replace(" ", "")) for s in wanted})

    route = respx.get(QUOTES).mock(side_effect=partial)
    chains = FakeChains()
    sources: list[str] = []
    try:
        result = await pt.quote_pass(tracked(250), lambda t, s: sources.append(s), schwab=suite.schwab, chains=chains,
                                     budget=2)
    finally:
        await suite.aclose()
    # two quote requests spent the whole budget: the rest waits for the next pass, nothing is over-spent
    assert route.call_count == 2 and result.requests == 2 and not chains.calls
    assert result.quoted == 190 and len(result.missing) == 60
    chains2 = FakeChains()
    result2 = await pt.quote_pass(tracked(30), lambda t, s: sources.append(s), schwab=None, chains=chains2, budget=5)
    assert chains2.calls == [("SPY", EXP)] and result2.quoted == 30 and result2.requests == 2   # one chain, one price
    assert sources[-1] == "chains"


class Refusing:
    configured = True

    def __init__(self, status: int | None) -> None:
        self.status = status

    async def option_quotes(self, occs: list[str]) -> list[OptionTick]:
        if self.status is None:
            return []                       # answered without any option quote
        raise ProviderError("schwab", ProviderErrorKind.HTTP_ERROR, f"quotes: HTTP {self.status}", self.status)


@pytest.mark.parametrize(("status", "state"), [(400, "refused"), (None, None), (503, None)])
async def test_an_endpoint_without_option_quotes_falls_back_to_chains(status: int | None, state: str | None) -> None:
    chains = FakeChains()
    result = await pt.quote_pass(tracked(20), lambda *_: None, schwab=Refusing(status), chains=chains)
    assert result.endpoint == state and result.quoted == 20 and chains.calls


# ── what is due, and when ────────────────────────────────────────────────────────────────────────
def test_schedules_and_the_closing_quote() -> None:
    a, b, c = (pt.Tracked(occ_of("SPY", "call", k), "SPY", "call", k, EXP, p) for k, p in ((500, 0), (501, 1), (502, 2)))
    contracts = {t.occ: t for t in (a, b, c)}
    now = ny(11)
    last = {a.occ: now - timedelta(minutes=5), b.occ: now - timedelta(minutes=10), c.occ: now - timedelta(minutes=30)}
    assert [t.occ for t in pt.due(contracts, last, now)] == [a.occ]
    assert [t.occ for t in pt.due(contracts, {}, now)] == [a.occ, b.occ, c.occ]
    late = ny(15, 52)
    recent = {t.occ: ny(15, 45) for t in (a, b, c)}
    assert len(pt.due(contracts, recent, late)) == 3           # everything once more for its closing quote
    assert pt.market_open(ny(9, 30)) and pt.market_open(ny(16, 0)) and not pt.market_open(ny(16, 5))
    assert not pt.market_open(ny(12, day=date(2026, 10, 24)))                # Saturday
    assert not pt.market_open(ny(12, day=date(2026, 11, 26)))                # Thanksgiving


def research_db(tmp_path: Path) -> StocksDatabases:
    return StocksDatabases.open(DeskPaths(tmp_path / "home", REPO / "config", tmp_path / "secrets.env"))


def record_play(conn: sqlite3.Connection, session: date, category: str, rank: int, legs: list[dict[str, Any]],
                expiration: date = EXP) -> None:
    conn.execute(
        "INSERT INTO stk_plays(session, category, rank, symbol, action, structure, expiration, event, event_kind,"
        " event_strike, event_width, entry, max_loss, legs_json, underlying, itm_market, itm_history, itm_kestrel,"
        " itm_kestrel_raw, profit_kestrel, play_json, method, recorded_at) VALUES (?, ?, ?, ?, 'BUY', 'long_call', ?,"
        " 'itm', 'call', ?, 0, 1.0, 101, ?, 100, 0.4, 0.4, 0.4, 0.4, 0.3, '{}', 'm', ?)",
        (day_of(session), category, rank, legs[0]["occ"][:-15], day_of(expiration), legs[0]["strike"], json.dumps(legs),
         ny(16, 20, session).isoformat()))


def test_the_tracked_set_covers_recorded_plays_twins_and_the_live_board(tmp_path: Path) -> None:
    dbs = research_db(tmp_path)
    try:
        today = date(2026, 10, 27)
        recent, old = occ_of("AAL", "call", 12), occ_of("XOM", "put", 100, date(2027, 1, 15))
        soon, expired = occ_of("XOM", "call", 120, date(2026, 10, 30)), occ_of("AAL", "call", 13, date(2026, 10, 23))
        record_play(dbs.research, date(2026, 10, 26), "lottos", 1, [{"occ": recent, "side": "buy", "strike": 12}])
        record_play(dbs.research, date(2026, 9, 1), "bear_puts", 1, [{"occ": old, "side": "buy", "strike": 100}],
                    date(2027, 1, 15))
        record_play(dbs.research, date(2026, 9, 1), "bear_puts", 2, [{"occ": soon, "side": "buy", "strike": 120}],
                    date(2026, 10, 30))
        record_play(dbs.research, date(2026, 9, 1), "lottos", 3, [{"occ": expired, "side": "buy", "strike": 13}],
                    date(2026, 10, 23))
        twin = occ_of("KO", "call", 70)
        dbs.research.execute("INSERT INTO stk_play_twins VALUES (?, 'lottos', 1, 'KO', 'BUY', 'long_call', ?, ?, 0.2,"
                             " 21, 'x')", (day_of(date(2026, 10, 26)), day_of(EXP), json.dumps([{"occ": twin}])))
        board = {"categories": [{"id": "edge", "plays": [{"legs": [{"occ": old}, {"occ": occ_of("F", "put", 9)}]}]}]}
        got = pt.tracked_set(dbs.research, today, board)
        assert {o: t.priority for o, t in got.items()} == {recent: 0, old: 0, soon: 0, twin: 2,
                                                          occ_of("F", "put", 9): 0}
        got = pt.tracked_set(dbs.research, today, None)
        assert got[old].priority == 1 and expired not in got
    finally:
        dbs.close()


# ── the fold at the close ────────────────────────────────────────────────────────────────────────
def tick(occ: str, at: datetime, bid: float, ask: float, underlying: float = 100.0) -> tuple[Any, ...]:
    return pt.tick_rows([OptionTick(occ, bid, ask, None, (bid + ask) / 2, 50, 200, 0.4, 0.5, 0.03, -0.05, 0.1,
                                    underlying, at)], at, at.astimezone(cal.NEW_YORK).date(), "schwab_quotes")[0]


def test_a_session_folds_into_intraday_and_one_permanent_close(tmp_path: Path) -> None:
    dbs = research_db(tmp_path)
    try:
        conn = dbs.research
        live, stale = occ_of("AAL", "call", 100), occ_of("AAL", "put", 90)
        rows = [tick(live, ny(9, 35) + timedelta(minutes=5 * i), 2 + 0.1 * i, 2.2 + 0.1 * i) for i in range(77)]
        rows.append(tick(live, ny(16, 30), 9.0, 9.5))                      # after the close: not part of the session
        rows.append(tick(stale, ny(10, 0), 1.0, 1.2))                       # nothing in the last hour
        pt.save_ticks(conn, rows)
        assert pt.fold_due(conn, ny(16, 2), lambda _: 0.04) == []           # not yet: five minutes after the close
        assert pt.fold_due(conn, ny(16, 6), lambda _: 0.04) == [SESSION]
        assert conn.execute("SELECT COUNT(*) FROM stk_track_ticks").fetchone()[0] == 0
        packed = conn.execute("SELECT * FROM stk_track_intraday WHERE occ = ?", (live,)).fetchone()
        points = pt.unpack(packed["packed"])
        assert packed["points"] == 77 and points[0][0] == 9 * 60 + 35 and points[-1][0] == 16 * 60 - 5
        day = conn.execute("SELECT * FROM stk_track_days WHERE occ = ?", (live,)).fetchone()
        assert day["status"] == "quoted" and day["bid"] == pytest.approx(2 + 7.6) and day["schwab_delta"] == 0.5
        assert day["iv"] is not None and day["delta"] is not None and day["schwab_iv"] == 0.4
        assert day["low_mid"] == pytest.approx(2.1) and day["high_mid"] == pytest.approx(9.7)
        assert conn.execute("SELECT COUNT(*) FROM stk_track_days WHERE occ = ?", (stale,)).fetchone()[0] == 0
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute("UPDATE stk_track_days SET bid = 0 WHERE occ = ?", (live,))
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute("DELETE FROM stk_track_days WHERE occ = ?", (live,))
        # a later quote for the same session cannot change the folded close
        pt.save_ticks(conn, [tick(live, ny(15, 59), 50.0, 51.0)])
        pt.fold_due(conn, ny(16, 30), lambda _: 0.04)
        assert conn.execute("SELECT bid FROM stk_track_days WHERE occ = ?", (live,)).fetchone()[0] == pytest.approx(9.6)
    finally:
        dbs.close()


def test_intraday_detail_is_kept_30_days_then_compacted(tmp_path: Path) -> None:
    dbs = research_db(tmp_path)
    try:
        conn, today = dbs.research, date.today()
        for session in (today - timedelta(days=60), today - timedelta(days=3)):
            conn.execute("INSERT INTO stk_track_intraday VALUES ('AAL261120C00012000', ?, 'quoted', 1, ?, 'x')",
                         (day_of(session), pt.pack([[600, 1, 1.1, 10, None]])))
        assert pt.compact(conn, today) == 1
        with pytest.raises(sqlite3.IntegrityError, match="30 days"):
            conn.execute("DELETE FROM stk_track_intraday")
    finally:
        dbs.close()


def test_first_sightings_are_recorded_during_the_session_only_and_never_change(tmp_path: Path) -> None:
    dbs = research_db(tmp_path)
    try:
        conn = dbs.research
        legs = [{"occ": occ_of("AAL", "call", 12), "side": "buy"}]

        def board(at: datetime, entry: float) -> dict[str, Any]:
            return {"asof": SESSION.isoformat(), "generated_at": at.isoformat(),
                    "categories": [{"id": "lottos", "plays": [{"symbol": "AAL", "entry": entry, "legs": legs}]}]}

        assert pt.first_seen(conn, board(ny(10, 15), 0.18), ny(10, 15)) == 1
        assert pt.first_seen(conn, board(ny(14, 0), 0.31), ny(14, 0)) == 0
        row = conn.execute("SELECT * FROM stk_play_first_seen").fetchone()
        assert row["entry"] == 0.18 and row["seen_at"].startswith("2026-10-22T14:15")
        other = {**board(ny(16, 20), 0.2), "categories": [{"id": "edge", "plays": [
            {"symbol": "AAL", "entry": 0.2, "legs": legs}]}]}
        assert pt.first_seen(conn, other, ny(16, 20)) == 0                    # built after the close: no sighting
    finally:
        dbs.close()


async def test_a_pass_quotes_due_contracts_and_reports_freshness(tmp_path: Path) -> None:
    dbs = research_db(tmp_path)
    try:
        legs = [{"occ": occ_of("SPY", "call", 400 + i), "side": "buy", "strike": 400 + i} for i in range(3)]
        record_play(dbs.research, SESSION, "edge", 1, legs[:1])
        board = {"asof": SESSION.isoformat(), "generated_at": ny(10).isoformat(),
                 "categories": [{"id": "edge", "plays": [{"symbol": "SPY", "entry": 1.1, "legs": legs[1:]}]}]}

        class Quotes:
            configured = True

            def __init__(self) -> None:
                self.asked: list[list[str]] = []

            async def option_quotes(self, occs: list[str]) -> list[OptionTick]:
                self.asked.append(occs)
                return [OptionTick(o, 1.0, 1.1, None, None, 5, 50, 0.3, 0.4, None, None, None, 450.0, ny(11))
                        for o in occs]

        quotes = Quotes()
        report = await pt.run_pass(dbs.market, dbs.research, ny(11), board=board, schwab=quotes, chains=None,
                                   rate=lambda _: 0.04, clock=lambda: ny(11))
        assert report["tracked"] == 3 and report["quoted"] == 3 and report["requests"] == 1
        assert report["freshness"]["buckets"] == {"≤ 5 min": 3} and report["first_seen"] == 1
        again = await pt.run_pass(dbs.market, dbs.research, ny(11, 2), board=board, schwab=quotes, chains=None,
                                  rate=lambda _: 0.04, clock=lambda: ny(11, 2))
        assert again["due"] == 0 and again["requests"] == 0 and len(quotes.asked) == 1
        status = pt.status(dbs.research, ny(11, 3))
        assert status["today"]["contracts"] == 3 and status["endpoint"]["state"] == "working"
        assert status["storage"]["rows"]["stk_track_ticks"] == 3
        closed = await pt.run_pass(dbs.market, dbs.research, ny(17), board=board, schwab=quotes, chains=None,
                                   rate=lambda _: 0.04, clock=lambda: ny(17))
        assert closed["market"] == "closed" and closed["folded"] == [SESSION.isoformat()]
        # the 11:00 quotes were not in the last hour, so the close was asked for once; 11:00 again is not a close
        assert len(quotes.asked) == 2 and closed["close_catch_up"]["closing_quotes"] == 0
    finally:
        dbs.close()


async def test_a_missed_close_is_taken_from_the_quote_schwab_still_serves_after_hours(tmp_path: Path) -> None:
    """The sign-in lapsed all session: that evening Schwab still returns the 3:59 PM bid/ask, which is the real
    closing quote of that session; an older quote time is never relabelled as the close."""
    dbs = research_db(tmp_path)
    try:
        fresh, old = occ_of("GOOGL", "call", 325), occ_of("GOOGL", "put", 300)
        record_play(dbs.research, SESSION - timedelta(days=1), "edge", 1, [{"occ": fresh, "side": "buy", "strike": 325},
                                                                          {"occ": old, "side": "buy", "strike": 300}])

        class AfterHours:
            configured = True

            def __init__(self) -> None:
                self.asked: list[list[str]] = []

            async def option_quotes(self, occs: list[str]) -> list[OptionTick]:
                self.asked.append(occs)
                return [OptionTick(o, 16.6, 17.0, 16.8, 16.8, 900, 4000, 0.31, 0.55, 0.01, -0.09, 0.4, 298.5,
                                   ny(15, 59) if o == fresh else ny(13, 40)) for o in occs]

        schwab = AfterHours()
        evening = ny(20, 57)
        report = await pt.run_pass(dbs.market, dbs.research, evening, board=None, schwab=schwab, chains=None,
                                   rate=lambda _: 0.04, clock=lambda: evening)
        assert report["close_catch_up"]["closing_quotes"] == 1 and report["folded"] == [SESSION.isoformat()]
        day = dbs.research.execute("SELECT * FROM stk_track_days WHERE occ = ?", (fresh,)).fetchone()
        assert day["status"] == "quoted" and day["bid"] == 16.6 and day["source"] == "schwab_quotes_after_close"
        assert dbs.research.execute("SELECT 1 FROM stk_track_days WHERE occ = ?", (old,)).fetchone() is None
        later = evening + timedelta(minutes=10)
        await pt.run_pass(dbs.market, dbs.research, later, board=None, schwab=schwab, chains=None,
                          rate=lambda _: 0.04, clock=lambda: later)
        assert len(schwab.asked) == 1                          # asked again only after half an hour
        morning = ny(8, 0, SESSION + timedelta(days=1))
        await pt.run_pass(dbs.market, dbs.research, morning, board=None, schwab=schwab, chains=None,
                          rate=lambda _: 0.04, clock=lambda: morning)
        assert schwab.asked[-1] == [old]                       # only the contract still without a close
    finally:
        dbs.close()
