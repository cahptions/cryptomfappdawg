"""KESTREL Stocks Plays on a synthetic market: every category is built from stored chains with consistent prices,
odds and greeks; a planted burst of call volume is caught as unusual and confirmed by open interest; a closed
session's plays are recorded once and graded at expiration; the page's endpoint serves them."""

from __future__ import annotations

import json
import math
from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from desk.core.config import load_config
from desk.stocks import calendar as cal
from desk.stocks import collect
from desk.stocks import odds as od
from desk.stocks import plays as pl
from desk.stocks.analysis import Analyzer
from desk.stocks.contracts import Bar
from desk.stocks.daily import refresh
from desk.stocks.paper.book import PaperBook
from desk.stocks.paper.fills import OPTION_FEE_PER_CONTRACT
from desk.stocks.prices import load_panel
from desk.storage.db import DbKind, connect, migrate
from tests.conftest import REPO
from tests.stocks_world import ASOF, NOW, StocksWorld, add_chains, make_world

SETTINGS = load_config(REPO / "config").settings.stocks
OPTIONED = ["CHEAP", "SQZ", "S10", "S11", "S12", "PRICEY", "TRAP"]
OPEN = datetime.combine(ASOF, time(14, 0), cal.NEW_YORK).astimezone(UTC)
CLOSED = datetime.combine(ASOF, time(16, 30), cal.NEW_YORK).astimezone(UTC)


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory) -> StocksWorld:
    w = make_world(tmp_path_factory.mktemp("plays"))
    add_chains(w, OPTIONED, w.days[-12:])
    add_chains(w, ["SPY"], w.days[-3:], max_days=420)
    # a planted burst: one out-of-the-money CHEAP call trades 20,000 contracts today (it usually trades ~800)
    _, chains = w.market.option_chain("CHEAP", ASOF)
    expiration = min(e for e in chains if (e - ASOF).days >= 7)
    price = w.market.chain_underlying_price("CHEAP", ASOF)
    quotes = chains[expiration]
    target = min((q for q in quotes if q.kind == "call" and q.strike > price * 1.04), key=lambda q: q.strike)
    burst = [replace(q, volume=20_000, last=q.ask) if q.occ == target.occ else q for q in quotes]
    w.market.conn.execute("DELETE FROM stk_option_chains WHERE underlying = 'CHEAP' AND day = ? AND expiration = ?",
                          (ASOF.toordinal(), expiration.toordinal()))
    w.market.save_option_chain("CHEAP", ASOF, expiration, datetime.combine(ASOF, time(21, 30), UTC), price, burst)
    w.market.conn.execute("UPDATE stk_option_daily SET summary_json = json_set(summary_json, '$.iv30', 0.55) "
                          "WHERE underlying IN ('S10', 'S11') AND day = ?", (ASOF.toordinal(),))
    analyzer = Analyzer(w.market, w.research, SETTINGS)
    refresh(analyzer, load_panel(w.market, w.days[0], ASOF), ASOF, NOW)
    w.research.watch("S12", NOW, add=True)
    w.burst = target.occ  # type: ignore[attr-defined]
    yield w
    w.close()


def ledger(tmp_path: Path) -> PaperBook:
    conn = connect(tmp_path / "ledger.db", DbKind.STOCKS_LEDGER)
    migrate(conn, DbKind.STOCKS_LEDGER)
    return PaperBook(conn)


def test_every_category_is_built_with_consistent_prices_odds_and_greeks(world: StocksWorld, tmp_path: Path) -> None:
    out = pl.build(world.market, world.research, OPEN, ledger=ledger(tmp_path))
    json.dumps(out, allow_nan=False)                                       # what the page receives is valid JSON
    assert out["asof"] == ASOF.isoformat() and out["coverage"]["underlyings"] == len(OPTIONED) + 1
    assert [c["id"] for c in out["categories"]] == [c.id for c in pl.CATEGORIES]
    assert not out["recorded"] and not out["session_closed"]              # an open session is never recorded
    by = {c["id"]: c for c in out["categories"]}
    for cat in out["categories"]:
        assert len({(p["symbol"], p["legs"][0]["occ"]) for p in cat["plays"]}) == len(cat["plays"])
        for p in cat["plays"]:
            assert p["rank"] >= 1 and p["source"] and p["reasons"]
            assert 0 <= p["itm_market"] <= 1 and 0 <= p["itm_kestrel"] <= 1 and 0 <= p["profit_kestrel"] <= 1
            legs = p["legs"]
            fees = OPTION_FEE_PER_CONTRACT * len(legs)
            if p["action"] == "BUY":
                debit = sum(leg["ask"] if leg["side"] == "buy" else -leg["bid"] for leg in legs)
                assert p["cost"] == pytest.approx(debit * 100 + fees, abs=0.01) and p["max_loss"] == p["cost"]
            else:
                credit = sum(leg["bid"] if leg["side"] == "sell" else -leg["ask"] for leg in legs)
                width = pl.spread_width(legs)
                assert p["credit"] == pytest.approx(credit * 100 - fees, abs=0.01)
                assert p["max_loss"] == pytest.approx((width - credit) * 100 + fees, abs=0.01)
                assert p["max_gain"] == p["credit"]
            if p["event"] == "itm" and p["action"] == "BUY":
                leg = legs[0]
                t = (date.fromisoformat(leg["expiration"]) - ASOF).days / 365
                market = od.finishes_beyond(leg["kind"], p["underlying_price"], leg["strike"], t, leg["iv"], out["model"]["rate"])
                assert p["itm_market"] == pytest.approx(market, abs=2e-3)   # vectorized and scalar math agree
            assert p["delta"] == pytest.approx(sum((1 if leg["side"] == "buy" else -1) * leg["delta"] for leg in legs),
                                               abs=2e-4)                   # a spread's delta is its legs' net
    likely = by["likely_itm"]["plays"]
    assert likely and all(p["action"] == "BUY" and 0.25 <= abs(p["delta"]) <= 0.56 and 7 <= p["dte"] <= 60 for p in likely)
    assert [p["itm_kestrel"] for p in likely] == sorted((p["itm_kestrel"] for p in likely), reverse=True)
    sells = by["sell_premium"]["plays"]
    assert sells and all(p["type"].endswith("CREDIT SPREAD") and p["chance_label"] == "Chance it expires worthless"
                         for p in sells)                                  # rich IV (S10, S11): defined-risk sells only
    assert all(p["action"] == "SELL" for p in sells) and all(p["action"] == "BUY" for p in by["lottos"]["plays"])
    debits = by["debit_spreads"]["plays"]
    assert debits                                                         # committee leans in the world have spreads
    for p in debits:
        bought, sold = p["legs"]
        debit = bought["ask"] - sold["bid"]
        width = abs(sold["strike"] - bought["strike"])
        fees = OPTION_FEE_PER_CONTRACT * 2
        assert p["structure"] in ("bull_call_debit", "bear_put_debit") and p["action"] == "BUY"
        assert (bought["side"], sold["side"]) == ("buy", "sell") and bought["kind"] == sold["kind"]
        assert bought["expiration"] == sold["expiration"] and 21 <= p["dte"] <= 60
        assert (sold["strike"] > bought["strike"]) == (p["structure"] == "bull_call_debit")   # sold leg further out
        assert 0 < debit <= 0.6 * width + 1e-9 and p["type"].endswith("DEBIT SPREAD")
        assert p["max_gain"] == pytest.approx((width - debit) * 100 - fees, abs=0.01)
        assert p["breakeven"] == pytest.approx(bought["strike"] + debit if bought["kind"] == "call"
                                               else bought["strike"] - debit, abs=0.01)
        assert 0 < p["probability_targets"]["max_gain_probability"] <= p["probability_targets"]["returns"]["0"]
    assert [p["score"] for p in debits] == sorted((p["score"] for p in debits), reverse=True)
    for p in (p for cat in out["categories"] for p in cat["plays"]):
        assert pl.structure_of(p["legs"]) == p["structure"]                # every play names its own shape
        assert len({leg["expiration"] for leg in p["legs"]}) == 1
        if p["breakevens"]:
            lower, upper = p["breakevens"]
            assert lower < upper and p["event"] == "move"
            assert p["event_strike"] == pytest.approx((lower + upper) / 2, abs=0.01)
    built = {p["structure"] for cat in out["categories"] for p in cat["plays"]}
    assert {"long_strangle", "iron_condor", "iron_butterfly", "cash_secured_put"} <= built
    for p in by["iron_condors"]["plays"] + by["iron_butterflies"]["plays"]:
        lp, sp, sc, lc = sorted(p["legs"], key=lambda leg: (leg["kind"] == "call", leg["strike"]))
        assert [lp["side"], sp["side"], sc["side"], lc["side"]] == ["buy", "sell", "sell", "buy"]
        assert (sp["strike"] == sc["strike"]) == (p["structure"] == "iron_butterfly")
        credit = sp["bid"] + sc["bid"] - lp["ask"] - lc["ask"]
        assert p["breakevens"] == pytest.approx([sp["strike"] - credit, sc["strike"] + credit], abs=0.01)
        assert p["chance_label"] == "Chance it finishes between the break-evens"
        assert p["profit_kestrel"] == pytest.approx(1 - p["itm_kestrel"], abs=1e-3)
        top = p["probability_targets"]["max_gain_probability"]
        assert (top == 0) if p["structure"] == "iron_butterfly" else 0 < top <= p["profit_kestrel"] + 1e-3
    for p in by["strangles"]["plays"]:
        call, put = sorted(p["legs"], key=lambda leg: leg["kind"])
        assert call["strike"] > p["underlying_price"] > put["strike"] and p["max_gain"] is None
        assert p["profit_kestrel"] == pytest.approx(p["itm_kestrel"], abs=1e-3)
    for p in by["cash_secured_puts"]["plays"]:
        [leg] = p["legs"]
        assert leg["side"] == "sell" and leg["kind"] == "put" and leg["strike"] < p["underlying_price"]
        assert p["max_loss"] == pytest.approx((leg["strike"] - leg["bid"]) * 100 + OPTION_FEE_PER_CONTRACT, abs=0.01)
        assert p["chance_label"] == "Chance it expires worthless" and 21 <= p["dte"] <= 45
    leaps = by["leaps"]["plays"]
    assert leaps and leaps[0]["symbol"] == "SPY" and leaps[0]["dte"] >= 180 and leaps[0]["delta"] >= 0.6
    assert [p["symbol"] for p in by["watchlist"]["plays"]] == ["S12"]
    for p in by["lottos"]["plays"]:
        assert p["dte"] <= 14 and 0.05 <= p["ask"] <= 0.50 and any("worthless" in f for f in p["flags"])


def test_a_planted_burst_is_unusual_and_explained(world: StocksWorld, tmp_path: Path) -> None:
    out = pl.build(world.market, world.research, OPEN)
    unusual = next(c for c in out["categories"] if c["id"] == "unusual")["plays"]
    hit = next(p for p in unusual if p["legs"][0]["occ"] == world.burst)   # type: ignore[attr-defined]
    u = hit["unusual"]
    assert u["volume"] == 20_000 and u["vol_oi"] == pytest.approx(4.0) and u["vol_avg"] > 20
    assert u["lean"] == "bullish lean" and "last trade at or above the ask" in u["factors"]
    assert "next session's open interest" in u["confirmation"]            # confirmed (or not) by tomorrow's chain
    assert hit["unusual"]["score"] == max(p["unusual"]["score"] for p in unusual)
    assert "not an exchange print" in hit["reasons"][0]
    plain = [p for p in unusual if p["legs"][0]["occ"] != world.burst]    # type: ignore[attr-defined]
    assert all(p["unusual"]["volume"] >= 250 for p in plain)


def test_a_closed_session_is_recorded_once_and_graded_at_expiration(world: StocksWorld) -> None:
    first = pl.build(world.market, world.research, CLOSED)
    assert first["session_closed"] and set(first["recorded"]) == {c.id for c in pl.CATEGORIES}
    again = pl.build(world.market, world.research, CLOSED + timedelta(minutes=5))
    assert again["recorded"] == []                                        # never recorded twice
    stored = world.research.conn.execute("SELECT COUNT(*) FROM stk_plays").fetchone()[0]
    assert stored == sum(len(c["plays"]) for c in first["categories"])
    with pytest.raises(Exception, match="append-only"):
        world.research.conn.execute("UPDATE stk_plays SET itm_kestrel = 1")
    # the earliest expiry arrives: every play expiring then is graded on that day's close
    expiry = date.fromordinal(world.research.conn.execute("SELECT MIN(expiration) FROM stk_plays").fetchone()[0])
    symbols = [r[0] for r in world.research.conn.execute("SELECT DISTINCT symbol FROM stk_plays WHERE expiration = ?",
                                                         (expiry.toordinal(),))]
    closes = {s: world.market.chain_underlying_price(s, ASOF) * 1.03 for s in symbols}
    world.market.save_bars(expiry, [Bar(s, expiry, c, c, c, c, 1e6) for s, c in closes.items()], "synthetic", NOW)
    graded = pl.grade(world.market, world.research, NOW)
    rows = world.research.conn.execute(
        "SELECT p.*, o.hit, o.payoff, o.net_return, o.underlying_close FROM stk_plays p JOIN stk_play_outcomes o "
        "USING (session, category, rank)").fetchall()
    assert graded == len(rows) > 0 and all(date.fromordinal(r["expiration"]) == expiry for r in rows)
    for r in rows:
        legs = json.loads(r["legs_json"])
        assert r["payoff"] == pytest.approx(pl.settle_value(legs, r["underlying_close"]))
        if r["event"] == "itm":
            above = r["underlying_close"] > r["event_strike"]
            assert bool(r["hit"]) == (above if r["event_kind"] == "call" else not above)
        if r["action"] == "BUY" and r["hit"] == 0 and r["event"] == "itm":
            assert r["net_return"] == pytest.approx(-1.0)                  # expired worthless: all of it lost
    assert pl.grade(world.market, world.research, NOW) == 0               # graded once
    track = pl.records(world.research)
    assert sum(v["resolved"] for v in track.values()) == len(rows)


def test_the_next_session_confirms_whether_flagged_volume_opened(world: StocksWorld) -> None:
    flagged = pl.flagged_before(world.research, ASOF + timedelta(days=1))
    assert world.burst in flagged                                          # type: ignore[attr-defined]
    nxt = cal.next_trading_day(ASOF)
    world.market.save_bars(nxt, [Bar(b.symbol, nxt, b.close, b.close, b.close, b.close, 1e6)
                                 for s in OPTIONED for b in world.market.bars(s, ASOF)[:1]], "synthetic", NOW)
    add_chains(world, OPTIONED, [nxt])
    _, chains = world.market.option_chain("CHEAP", nxt)
    expiration = date.fromisoformat(flagged[world.burst]["expiration"])  # type: ignore[attr-defined]
    quotes = [replace(q, open_interest=q.open_interest + 15_000) if q.occ == world.burst else q  # type: ignore[attr-defined]
              for q in chains[expiration]]
    world.market.conn.execute("DELETE FROM stk_option_chains WHERE underlying = 'CHEAP' AND day = ? AND expiration = ?",
                              (nxt.toordinal(), expiration.toordinal()))
    world.market.save_option_chain("CHEAP", nxt, expiration, datetime.combine(nxt, time(21, 30), UTC),
                                   world.market.bars("CHEAP", nxt)[0].close, quotes)
    out = pl.build(world.market, world.research, datetime.combine(nxt, time(14, 0), cal.NEW_YORK).astimezone(UTC))
    assert out["asof"] == nxt.isoformat()
    unusual = next(c for c in out["categories"] if c["id"] == "unusual")
    follow = {r["occ"]: r for r in unusual["followups"]}
    burst = follow[world.burst]                                            # type: ignore[attr-defined]
    assert burst["open_interest_now"] - burst["open_interest"] == 15_000
    assert burst["opened"] == pytest.approx(0.75) and burst["verdict"].startswith("Opened")
    assert burst["session"] == ASOF.isoformat()


def test_collection_adds_one_long_dated_expiry() -> None:
    today = date(2026, 10, 21)
    listed = [date(2026, 10, 23), date(2026, 11, 20), date(2026, 12, 18), date(2027, 1, 15), date(2027, 6, 18),
              date(2027, 10, 15), date(2028, 1, 21)]
    picked = collect.pick_expirations(listed, today)
    assert date(2027, 10, 15) in picked and date(2028, 1, 21) not in picked and date(2027, 6, 18) not in picked


def test_the_page_endpoint_serves_the_saved_plays(world: StocksWorld) -> None:
    from desk.stocks import runner, web
    runner.publish_plays(world.paths, world.dbs, OPEN)
    saved = runner.saved(world.paths, "plays")
    assert saved and saved["categories"]
    with TestClient(web.create_app(world.paths, auto_start=False), base_url="http://127.0.0.1:8767") as client:
        body = client.get("/api/stocks/plays").json()
        assert body["asof"] == ASOF.isoformat() and len(body["categories"]) == len(pl.CATEGORIES)
        assert client.get("/assets/plays.js").status_code == 200 and client.get("/assets/plays.css").status_code == 200
        assert "#plays" in client.get("/").text
    assert math.isfinite(saved["coverage"]["contracts_scored"])
