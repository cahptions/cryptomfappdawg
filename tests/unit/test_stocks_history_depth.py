"""History backfills that reach as far back as the measures reading them, a bounded part per pass: insider history
from the SEC's quarterly data sets, the filings feed catching up after downtime, FINRA short interest and short
volume, and fails to deliver. Every source answer here is a fake; nothing touches the network."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from desk.core.config import load_config
from desk.core.errors import DeskError
from desk.core.paths import DeskPaths
from desk.stocks import calendar as cal
from desk.stocks import collect, runner
from desk.stocks.contracts import Fail, IndexEntry, InsiderTrade, Listing, ShortInterest, ShortVolume
from desk.stocks.repo import StocksDatabases, StocksMarket
from tests.conftest import REPO

NOW = datetime(2026, 11, 5, 22, 0, tzinfo=UTC)          # a Thursday evening, New York


@pytest.fixture
def market(tmp_path: Path):
    dbs = StocksDatabases.open(DeskPaths(tmp_path / "home", REPO / "config", tmp_path / "secrets.env"))
    m = StocksMarket(dbs.market)
    m.save_listings([Listing("AAA", "A Common Stock", "NYSE", False, False, "common"),
                     Listing("BBB", "B Common Stock", "NYSE", False, False, "common")], {"AAA": 11, "BBB": 22}, NOW)
    yield m
    dbs.close()


def trade(accession: str, cik: int, day: date, shares: float = 100.0) -> InsiderTrade:
    return InsiderTrade(accession, cik, {11: "AAA", 22: "BBB"}.get(cik, "ZZZ"), "Owner", 5, True, False, None, False,
                        day, "P", shares, 10.0, True, None, True, False)


# ── insider history ──────────────────────────────────────────────────────────────────────────────
class Sec:
    def __init__(self, quarters: list[str], broken: set[str] | None = None) -> None:
        self.quarters, self.broken, self.downloads = quarters, broken or set(), []
        self.contact_configured = True

    async def insider_dataset_paths(self) -> list[str]:
        older = [f"/files/old-folder/{q}_form345.zip" for q in self.quarters if q < "2025"]
        return sorted([f"/files/insider/{q}_form345.zip" for q in self.quarters if q >= "2025"], reverse=True) + older

    async def insider_dataset(self, path: str) -> list[tuple[InsiderTrade, date]]:
        self.downloads.append(path)
        if path in self.broken:
            raise DeskError("insider_dataset: HTTP 503")
        code = path.rsplit("/", 1)[-1][:6]
        filed = collect.quarter_end(code) - timedelta(days=20)
        return [(trade(f"{code}-1", 11, filed), filed), (trade(f"{code}-2", 99, filed), filed),   # 99: not in universe
                (trade("feed-stored", 11, filed), filed)]


QUARTERS = [f"{y}q{q}" for y in (2023, 2024, 2025, 2026) for q in (1, 2, 3, 4)][:15]      # through 2026q3


async def test_nine_newest_quarters_arrive_one_per_pass_newest_first_and_each_only_once(market: StocksMarket) -> None:
    sec = Sec(QUARTERS)
    suite = SimpleNamespace(sec=sec)
    filed = collect.quarter_end("2026q3") - timedelta(days=20)
    market.save_insider_trades([(trade("feed-stored", 11, filed, shares=7.0), filed)])     # the daily feed's copy
    first = await collect.insider_history(suite, market, NOW, per_pass=1)
    assert first["ok"] and first["this_pass"] == ["/files/insider/2026q3_form345.zip"] and first["remaining"] == 8
    rows = market.insider_trades(date(2026, 1, 1))
    assert {(r["accession"], r["shares"]) for r in rows} == {("2026q3-1", 100.0), ("feed-stored", 7.0)}
    for _ in range(8):
        await collect.insider_history(suite, market, NOW, per_pass=1)
    done = await collect.insider_history(suite, market, NOW, per_pass=1)
    assert done["remaining"] == 0 and done["this_pass"] == [] and done["newest_quarter"] == "2026q3"
    assert [p.rsplit("/", 1)[-1][:6] for p in sec.downloads] == sorted(QUARTERS, reverse=True)[:9]   # by quarter, not
    assert len(set(sec.downloads)) == 9                                                              # by folder


async def test_a_failed_quarter_keeps_the_ones_already_loaded(market: StocksMarket) -> None:
    sec = Sec(QUARTERS, broken={"/files/insider/2026q2_form345.zip"})
    suite = SimpleNamespace(sec=sec)
    failed = await collect.insider_history(suite, market, NOW, per_pass=3)
    assert not failed["ok"] and "503" in failed["error"] and failed["remaining"] == 8
    assert market.jobs()["insider_history"]["detail"]["loaded"] == ["/files/insider/2026q3_form345.zip"]
    sec.broken.clear()
    resumed = await collect.insider_history(suite, market, NOW, per_pass=1)
    assert resumed["this_pass"] == ["/files/insider/2026q2_form345.zip"]
    assert sec.downloads.count("/files/insider/2026q3_form345.zip") == 1


def test_a_backfill_runs_every_pass_while_parts_remain_and_waits_after_a_failure(market: StocksMarket) -> None:
    assert runner._backfill_due(market, "insider_history", NOW, 24 * 7)                  # never run
    market.job("insider_history", NOW, detail={"remaining": 3})
    assert runner._backfill_due(market, "insider_history", NOW + timedelta(minutes=5), 24 * 7)
    market.job("insider_history", NOW, detail={"remaining": 0})
    assert not runner._backfill_due(market, "insider_history", NOW + timedelta(days=6), 24 * 7)
    assert runner._backfill_due(market, "insider_history", NOW + timedelta(days=7), 24 * 7)   # a new quarter, weekly
    market.job("insider_history", NOW, error="HTTP 503", detail={"remaining": 3})
    assert not runner._backfill_due(market, "insider_history", NOW + timedelta(hours=5), 24 * 7)
    assert runner._backfill_due(market, "insider_history", NOW + timedelta(hours=6), 24 * 7)


# ── filings feed catch-up ─────────────────────────────────────────────────────────────────────────
class Feed:
    def __init__(self) -> None:
        self.days: list[date] = []
        self.contact_configured = True

    async def daily_index(self, day: date) -> list[IndexEntry]:
        self.days.append(day)
        return [IndexEntry("10-Q", "B", 22, day, "edgar/data/22/x.txt")] if day == date(2026, 10, 20) else []

    async def submissions(self, cik: int) -> Any:
        raise DeskError("submissions: not found")


async def test_the_filings_feed_catches_up_ten_days_a_pass_and_says_it_is_behind(market: StocksMarket) -> None:
    sec = Feed()
    suite = SimpleNamespace(sec=sec)
    first = await collect.filings_feed(suite, market, NOW, start=date(2026, 10, 1), max_days=10)
    assert first["ok"] and len(first["days"]) == 10 and first["behind"] and first["through"] == "2026-10-14"
    second = await collect.filings_feed(suite, market, NOW, start=date(2026, 10, 1), max_days=10)
    assert second["days"][0] == "2026-10-15" and second["behind"] and second["companies"]["missing"] == [22]
    last = await collect.filings_feed(suite, market, NOW, start=date(2026, 10, 1), max_days=10)
    assert not last["behind"] and last["through"] == "2026-11-04"                        # today is not published yet
    assert len(sec.days) == len(set(sec.days)) == 25                                       # every trading day, once


async def test_the_filings_feed_starts_no_new_day_after_its_time_budget(market: StocksMarket) -> None:
    ticks = iter(range(0, 10_000, 200))                                                   # each day takes 200 s
    report = await collect.filings_feed(SimpleNamespace(sec=Feed()), market, NOW, start=date(2026, 10, 1),
                                        max_days=10, budget_seconds=300, clock=lambda: float(next(ticks)))
    assert len(report["days"]) == 2 and report["behind"]


# ── what the collector schedules ─────────────────────────────────────────────────────────────────
async def test_the_collector_schedules_the_backfills(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from desk.stocks import market_events, news_flow

    paths = DeskPaths(tmp_path / "home", REPO / "config", tmp_path / "secrets.env")
    for name in ("MASSIVE_API_KEY", "TRADIER_TOKEN", "SCHWAB_APP_KEY", "SCHWAB_APP_SECRET"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("SEC_CONTACT_EMAIL", "me@example.org")
    monkeypatch.setattr(runner, "utc_now", lambda: datetime(2026, 11, 1, 15, 0, tzinfo=UTC))   # a Sunday
    calls: dict[str, dict[str, Any]] = {}

    def fake(name: str) -> Any:
        async def job(*args: Any, **kwargs: Any) -> dict[str, Any]:
            calls[name] = kwargs
            return {"ok": True}
        return job

    for name in ("listings", "mentions", "rates", "short_interest", "short_volume", "fails", "companies",
                 "filings_feed", "insider_history"):
        monkeypatch.setattr(collect, name, fake(name))
    async def no_news(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {}

    monkeypatch.setattr(market_events, "job", fake("market_events"))
    monkeypatch.setattr(news_flow, "job", no_news)
    await runner.cycle(paths, load_config(REPO / "config"), research_only=True)
    assert calls["insider_history"] == {"quarters": 9, "per_pass": 1}
    assert calls["filings_feed"]["max_days"] == 10 and calls["filings_feed"]["budget_seconds"] == 300
    assert calls["short_interest"] == calls["short_volume"] == calls["fails"] == {}       # full depth, not 5 days
    assert (collect.SHORT_INTEREST_SETTLEMENTS, collect.SHORT_VOLUME_DAYS, collect.FAILS_MONTHS) == (48, 45, 24)


# ── short interest, short volume, fails ──────────────────────────────────────────────────────────
def finra_settlements(months: int = 30) -> list[date]:
    """Mid-month and month-end, moved back to a weekday; 2026-05-15 is moved one more day, as for a holiday."""
    out, year, month = [], 2026, 10
    for _ in range(months):
        end = date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1)
        for anchor in (date(year, month, 15), end):
            while anchor.weekday() >= 5:
                anchor -= timedelta(days=1)
            out.append(date(2026, 5, 14) if anchor == date(2026, 5, 15) else anchor)
        year, month = (year - 1, 12) if month == 1 else (year, month - 1)
    return sorted(out, reverse=True)


class Finra:
    def __init__(self) -> None:
        self.published = set(finra_settlements())
        self.asked: list[date] = []
        self.files: list[date] = []
        self.unpublished_files: set[date] = set()

    async def latest_short_interest(self, today: date) -> tuple[date, list[ShortInterest]]:
        newest = max(d for d in self.published if d <= today)
        return newest, [ShortInterest("AAA", newest, 100.0, None, None, None)]

    async def short_interest(self, settlement: date) -> list[ShortInterest]:
        self.asked.append(settlement)
        return [ShortInterest("AAA", settlement, 100.0, None, None, None)] if settlement in self.published else []

    async def reg_sho(self, day: date) -> list[ShortVolume]:
        self.files.append(day)
        if day in self.unpublished_files:
            raise DeskError("reg_sho_daily: not found")
        return [ShortVolume("AAA", day, 40.0, 0.0, 100.0)]


async def test_short_interest_backfills_two_years_four_settlements_a_pass_asking_each_date_once(
        market: StocksMarket) -> None:
    finra = Finra()
    suite = SimpleNamespace(feeds=finra)
    first = await collect.short_interest(suite, market, NOW)
    assert first["ok"] and first["settlement"] == "2026-10-30" and len(first["backfilled"]) == 4
    assert first["remaining"] == 48 - 5
    calls = 1
    while (report := await collect.short_interest(suite, market, NOW))["remaining"]:
        calls += 1
        assert len(report["backfilled"]) == 4 and calls < 20
    stored = market.short_interest_dates()
    assert len(stored) == collect.SHORT_INTEREST_SETTLEMENTS == 48 and calls + 1 == 12       # 5 + 4 × 10 + 3
    assert stored == sorted(finra_settlements()[:48])                    # two years, none skipped
    assert len(finra.asked) == len(set(finra.asked))                     # every date asked about once
    assert date(2026, 5, 15) in finra.asked and date(2026, 5, 14) in stored   # the holiday twin, found the next day
    asked = len(finra.asked)
    done = await collect.short_interest(suite, market, NOW + timedelta(days=1))
    assert done["remaining"] == 0 and len(finra.asked) == asked           # complete: only the newest is checked


async def test_short_volume_fills_any_downtime_up_to_45_days_and_skips_old_unpublished_files(
        market: StocksMarket) -> None:
    finra = Finra()
    suite = SimpleNamespace(feeds=finra)
    last = cal.last_closed_session(NOW, timedelta(hours=3))
    before = last - timedelta(days=30)
    market.save_short_volume([ShortVolume("AAA", before, 1.0, 0.0, 2.0)])           # stopped 30 days ago
    finra.unpublished_files = {cal.trading_days(before, last)[3]}
    first = await collect.short_volume(suite, market, NOW)
    gap = [d for d in cal.trading_days(before, last) if d != before]
    assert first["ok"] and first["days"] == 19 and first["remaining"] == len(gap) - 20 > 0
    second = await collect.short_volume(suite, market, NOW)                          # the same window, finished
    assert second["remaining"] == 0 and second["from"] == first["from"]
    assert market.short_volume_days() == set(gap) - finra.unpublished_files | {before}
    assert min(finra.files) > before                                                 # nothing older is asked for
    assert {d for d in finra.files if finra.files.count(d) > 1} == finra.unpublished_files   # once more next pass
    asked = len(finra.files)
    later = await collect.short_volume(suite, market, NOW + timedelta(days=1))
    assert later["days"] == 1 and finra.files[asked:] == [date(2026, 11, 5)]        # the old missing file is left


async def test_short_volume_first_load_reaches_back_45_days(market: StocksMarket) -> None:
    finra = Finra()
    suite = SimpleNamespace(feeds=finra)
    while (await collect.short_volume(suite, market, NOW))["remaining"]:
        pass
    last = cal.last_closed_session(NOW, timedelta(hours=3))
    assert market.short_volume_days() == set(cal.trading_days(last - timedelta(days=45), last))


class Fails:
    def __init__(self) -> None:
        self.downloads: list[str] = []
        self.gone = "cnsfails202501a.zip"             # never published
        self.late = "cnsfails202610b.zip"             # the second half of October: published mid-November

    async def fails(self, name: str) -> list[Fail]:
        self.downloads.append(name)
        if name in (self.gone, self.late):
            raise DeskError("fails_to_deliver: not found")
        day = date(int(name[8:12]), int(name[12:14]), 5 if name[14] == "a" else 20)
        return [Fail(day, "000000000", "AAA", 1000.0, 10.0)]


async def test_fails_backfill_two_years_of_files_four_a_pass_each_once(market: StocksMarket) -> None:
    sec = Fails()
    suite = SimpleNamespace(sec=sec)
    first = await collect.fails(suite, market, NOW)
    assert first["ok"] and first["files"] == ["cnsfails202610a.zip", "cnsfails202609b.zip", "cnsfails202609a.zip",
                                              "cnsfails202608b.zip"] and first["unpublished"] == [sec.late]
    calls = 1
    while (await collect.fails(suite, market, NOW))["remaining"]:
        calls += 1
    detail = market.jobs()["fails"]["detail"]
    assert len(detail["loaded"]) == 46 and detail["missing"] == [sec.gone] and calls + 1 == 12      # 46 files, 4 a pass
    assert {n for n in sec.downloads if sec.downloads.count(n) > 1} == {sec.late}   # only the recent one is retried
    assert min(detail["loaded"]) == "cnsfails202411a.zip"                           # 24 months back from October
