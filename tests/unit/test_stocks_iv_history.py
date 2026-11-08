"""Implied-volatility history from Massive's real daily option closes: monthly at-the-money calls and puts listed nine
weeks before expiry, their IVs solved from closing trades and interpolated to a constant 30 days, then merged with the
collected chains' own IV30 for IV rank, labelled with where each day came from. Every Massive download shares one
queue and one budget."""

import asyncio
from datetime import UTC, date, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from typing import Any

import pytest

from desk.core.errors import ProviderError, ProviderErrorKind
from desk.stocks import calendar as cal
from desk.stocks import iv_history as ivh
from desk.stocks import massive_options as mo
from desk.stocks import options as opt
from desk.stocks.repo import day_of
from desk.stocks.signals import options as option_signals
from tests.stocks_world import ASOF, make_world

NOW = datetime(2026, 10, 24, 15, 0, tzinfo=UTC)          # a Saturday: the market is closed
IV = 0.35


def test_monthly_expiries_already_past_newest_first_with_a_friday_holiday_moved_back() -> None:
    days = ivh.expiries(date(2026, 11, 4))
    assert days[0] == date(2026, 10, 16) and all(b < a for a, b in pairwise(days))
    assert date(2026, 6, 18) in days and date(2026, 6, 19) not in days      # Juneteenth: Thursday's expiry
    assert all(cal.is_trading_day(d) for d in days) and 21 <= len(days) <= 23


def test_the_pair_is_the_listed_strike_nearest_the_price_with_a_call_and_a_put() -> None:
    e = date(2026, 9, 18)
    listed = [{"ticker": f"X{k}{kind[0]}", "kind": kind, "strike": k, "expiration": e}
              for k in (95.0, 100.0, 105.0) for kind in ("call", "put") if not (k == 100.0 and kind == "put")]
    chosen = ivh.choose(listed, 100.4, e)
    assert [(c["kind"], c["strike"]) for c in chosen] == [("call", 95.0), ("put", 95.0)] or \
        [(c["kind"], c["strike"]) for c in chosen] == [("call", 105.0), ("put", 105.0)]
    assert ivh.choose(listed, 100.0, date(2026, 10, 16)) == []                    # another expiry's contracts


def test_constant_maturity_interpolates_total_variance_or_takes_a_near_expiry() -> None:
    got = ivh.constant_maturity([(10, 0.30), (38, 0.40)])
    assert got == pytest.approx(opt.interpolate_iv([{"days": 10, "atm_iv": 0.30}, {"days": 38, "atm_iv": 0.40}], 30))
    assert 0.30 < got < 0.40
    assert ivh.constant_maturity([(36, 0.42)]) == 0.42
    assert ivh.constant_maturity([(50, 0.42)]) is None


class FakeMassive:
    """Listings 1% apart around the price; each contract's bars priced at a 35% IV on the stored closes."""
    configured = True

    def __init__(self, closes: dict[date, float], refuse: int | None = None) -> None:
        self.closes, self.refuse = closes, refuse
        self.calls: list[tuple[str, str]] = []

    async def option_contracts(self, underlying: str, as_of: date, first: date, last: date, *, kind: Any = None,
                               strikes: Any = None) -> list[dict[str, Any]]:
        self.calls.append(("list", underlying))
        if self.refuse:
            raise ProviderError("massive", ProviderErrorKind.HTTP_ERROR, "refused", status_code=self.refuse)
        spot = (strikes[0] + strikes[1]) / 2
        return [{"ticker": f"{underlying}{first:%y%m%d}{k[0].upper()}{round(spot * (1 + s / 100) * 1000):08d}",
                 "kind": k, "strike": round(spot * (1 + s / 100), 3), "expiration": first, "underlying": underlying}
                for s in range(-9, 10) for k in ("call", "put")]

    async def option_bars(self, ticker: str, first: date, last: date) -> list[dict[str, Any]]:
        self.calls.append(("bars", ticker))
        kind = "call" if ticker[-9] == "C" else "put"
        strike = int(ticker[-8:]) / 1000
        expiry = date(2000 + int(ticker[-15:-13]), int(ticker[-13:-11]), int(ticker[-11:-9]))
        out = []
        for d, s in sorted(self.closes.items()):
            if first <= d <= last:
                price = opt.bs_price(kind, s, strike, opt.year_fraction(d, expiry), IV, 0.04)
                out.append({"day": d, "open": price, "high": price, "low": price, "close": price, "volume": 25.0})
        return out


def optioned(w: Any, symbol: str = "SPY") -> None:
    w.market.save_option_daily(symbol, ASOF, NOW, {"price": 400.0, "iv30": 0.2, "expiries": []})


def test_the_download_lists_then_prices_and_the_series_reproduces_the_contracts_volatility(tmp_path: Path,
                                                                                        monkeypatch: Any) -> None:
    w = make_world(tmp_path, n=12)
    try:
        optioned(w)
        assert ivh.names(w.market, ASOF)[0] == "SPY" and len(ivh.names(w.market, ASOF)) == 5   # the core ETF first
        monkeypatch.setattr(ivh, "names", lambda market, today: ["SPY"])
        closes = {b.day: b.close for b in w.market.bars("SPY")}
        fake = FakeMassive(closes)
        got = asyncio.run(ivh.task(fake, w.market, w.paths.home, NOW, 9))
        assert got["ok"] and got["requests"] == 9 == len(fake.calls)
        assert [c[0] for c in fake.calls[:3]] == ["list", "bars", "bars"]       # a listing, then its call and put
        rows = w.market.conn.execute("SELECT * FROM stk_iv_daily WHERE symbol='SPY'").fetchall()
        assert len(rows) >= 40 and {r["source"] for r in rows} == {ivh.SOURCE}
        assert all(r["iv30"] == pytest.approx(IV, abs=0.003) for r in rows)        # solved back from real closes
        again = asyncio.run(ivh.task(fake, w.market, w.paths.home, NOW, 3))
        assert again["name_months_done"] > got["name_months_done"]                # the next share carries on
        assert "IV history:" in ivh.describe(w.market, ASOF)
        refused = asyncio.run(ivh.task(FakeMassive(closes, refuse=403), w.market, w.paths.home, NOW, 3))
        assert not refused["ok"] and "Options Basic" in refused["error"]
    finally:
        w.close()


def test_iv_rank_reads_the_collected_days_first_and_massive_on_the_others(tmp_path: Path) -> None:
    w = make_world(tmp_path, n=12)
    try:
        days = [d for d in cal.trading_days(ASOF - timedelta(days=300), ASOF - timedelta(days=60))]
        later = datetime(2026, 10, 23, tzinfo=UTC)                               # downloaded after the analysis
        w.market.conn.executemany("INSERT INTO stk_iv_daily VALUES (?,?,?,?,?,?)",
                                  [("CHEAP", day_of(d), 0.2 + 0.4 * i / len(days), ivh.SOURCE, 2, later.isoformat())
                                   for i, d in enumerate(days)])
        summary = option_signals(w.market, ASOF)["CHEAP"]
        basis = summary["iv_basis"]
        assert basis["collected"] == 30 and basis["massive"] == len(days) and basis["days"] == 30 + len(days)
        assert len(summary["iv_history"]) == basis["days"] and basis["first"] == days[0].isoformat()
        assert "from Massive's closing trades" in ivh.basis_text(basis)
        before = option_signals(w.market, ASOF, observed_by=later - timedelta(days=1))["CHEAP"]["iv_basis"]
        assert before["massive"] == 0                                              # downloaded later: not yet known
        from desk.stocks.analysis import Analyzer
        from desk.stocks.prices import load_panel

        panel = load_panel(w.market, ASOF - timedelta(days=500), ASOF)
        day = Analyzer(w.market, w.research, _settings(w)).build(panel, ASOF, now=NOW)
        result = day.results["options"]["CHEAP"]
        reasons = " ".join(result.reasons) + " ".join(text for _, text in result.signals) + str(result.facts)
        assert "IV rank" in reasons and "Massive's closing trades" in reasons
    finally:
        w.close()


def _settings(w: Any) -> Any:
    from desk.core.config import load_config

    return load_config(w.paths.config_dir).settings.stocks


def test_the_queue_shares_one_budget_fairly_and_a_refusal_stops_every_task(tmp_path: Path, monkeypatch: Any) -> None:
    w = make_world(tmp_path, n=8)
    try:
        asked: list[tuple[str, int]] = []

        def fake(name: str, need: int, error: str | None = None) -> Any:
            async def task(massive: Any, market: Any, home: Path, now: datetime, budget: int) -> dict[str, Any]:
                asked.append((name, budget))
                return {"requests": min(need, budget), **({"error": error} if error else {})}
            return task

        monkeypatch.setattr(mo, "tasks", lambda: [("a", fake("a", 1)), ("b", fake("b", 99)), ("c", fake("c", 99))])
        got = asyncio.run(mo.job(type("M", (), {"configured": True})(), w.market, w.paths.home, NOW))
        assert got["budget"] == mo.PER_PASS_CLOSED and got["requests"] == mo.PER_PASS_CLOSED
        assert asked == [("a", 13), ("b", 19), ("c", 20)]                         # what one leaves, the next gets
        open_hours = asyncio.run(mo.job(type("M", (), {"configured": True})(), w.market, w.paths.home,
                                        datetime(2026, 10, 22, 15, 0, tzinfo=UTC)))
        assert open_hours["requests"] <= mo.PER_PASS_OPEN
        asked.clear()
        monkeypatch.setattr(mo, "tasks", lambda: [("a", fake("a", 1, "Massive refused option data (HTTP 403)")),
                                                  ("b", fake("b", 9))])
        stopped = asyncio.run(mo.job(type("M", (), {"configured": True})(), w.market, w.paths.home, NOW))
        assert not stopped["ok"] and [n for n, _ in asked] == ["a"]
        assert w.market.jobs()["massive_options"]["last_error"]
    finally:
        w.close()
