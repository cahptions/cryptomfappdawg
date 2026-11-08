"""Collection jobs for KESTREL Stocks. Each fetches what is missing, stores it insert-once, records what it did in
stk_jobs, and raises nothing a later run cannot retry (a failure is recorded and reported, never filled)."""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable, Iterable, Sequence
from datetime import date, datetime, timedelta
from typing import Any

from desk.core.clock import utc_now
from desk.core.errors import DeskError, ProviderError
from desk.core.hashing import sha256_hex
from desk.stocks import calendar as cal
from desk.stocks import options as opt
from desk.stocks.activity import VERSION as OPTIONS_VERSION
from desk.stocks.activity import chain_activity, qualified_quotes, quote_session
from desk.stocks.contracts import InsiderTrade, OptionQuote, RatePoint
from desk.stocks.fundamentals import CONCEPTS
from desk.stocks.repo import FILING_FORMS, SCHWAB_RATE, StocksMarket
from desk.stocks.sectors import sector
from desk.stocks.sources.feeds import TREASURY_COLUMNS, settlement_candidates
from desk.stocks.sources.sec import ftd_file_names, parse_companyfacts, parse_form4, parse_submission_text
from desk.stocks.suite import StocksSuite
from desk.stocks.universe import research_listings, stored_symbols

log = logging.getLogger(__name__)
COMPANY_FORMS = frozenset({"10-K", "10-Q", "10-K/A", "10-Q/A", "8-K", "8-K/A", "20-F", "6-K"})
FRED_SERIES = ("BAMLH0A0HYM2", "VIXCLS")
MENTION_SOURCES = ("wallstreetbets", "all-stocks", "options")
FACT_YEARS = 16          # company facts kept: ten years of rebuilt history (price_history_years) plus five-year growth


class IncompleteJob(DeskError):
    def __init__(self, message: str, detail: dict[str, Any]) -> None:
        super().__init__(message)
        self.detail = detail


async def _job(market: StocksMarket, name: str, now: datetime, work: Callable[[], Awaitable[dict[str, Any]]]) -> dict[str, Any]:
    started = time.monotonic()
    try:
        detail = await work()
    except DeskError as exc:
        detail = exc.detail if isinstance(exc, IncompleteJob) else {}
        market.job(name, now, error=str(exc)[:300], detail=detail)
        log.warning("stocks job failed", extra={"job": name, "error": str(exc)[:200]})
        return {"ok": False, "error": str(exc)[:300], **detail}
    detail["seconds"] = round(time.monotonic() - started, 1)
    market.job(name, now, detail=detail)
    return {"ok": True, **detail}


# ── listings ─────────────────────────────────────────────────────────────────────────────────────
async def listings(suite: StocksSuite, market: StocksMarket, now: datetime) -> dict[str, Any]:
    async def work() -> dict[str, Any]:
        rows = await suite.feeds.listings()
        ciks = {r["symbol"]: int(r["cik"]) for r in market.listings() if r["cik"]}
        warning = None
        if suite.sec.contact_configured:
            try:
                ciks.update({symbol: cik for cik, _, symbol, _ in await suite.sec.tickers()})
            except DeskError:
                # Optional enrichment must not discard a successfully fetched exchange directory.
                warning = "SEC company mapping unavailable; listings saved and existing company IDs preserved"
        counts = market.save_listings(rows, ciks, now)
        research = research_listings(market)
        return {**counts, "listings": len(rows), "research": len(research), "with_cik": sum(1 for r in research if r["cik"]),
                "warning": warning}
    return await _job(market, "listings", now, work)


# ── prices ───────────────────────────────────────────────────────────────────────────────────────
def missing_price_days(market: StocksMarket, now: datetime, history_days: int, after_close: timedelta) -> list[date]:
    """Trading days in the window without a stored day, newest first (so today arrives before the backfill)."""
    last = cal.last_closed_session(now, after_close)
    checked = market.checked_days()
    return [d for d in reversed(cal.trading_days(last - timedelta(days=history_days), last)) if d not in checked]


async def prices(suite: StocksSuite, market: StocksMarket, now: datetime, *, history_days: int, after_close: timedelta,
                 max_days: int | None = None, clock: Callable[[], datetime] = utc_now) -> dict[str, Any]:
    async def work() -> dict[str, Any]:
        keep = stored_symbols(market)
        days = missing_price_days(market, now, history_days, after_close)
        fetched, stored, empty = [], 0, []
        deferred = []
        for d in days[:max_days] if max_days else days:
            try:
                bars = await suite.massive.grouped_daily(d)
            except ProviderError as exc:
                # A Basic subscription can deny an as-yet unavailable recent close. That must
                # not starve all older history, nor count the denied day as downloaded/a holiday.
                if exc.status_code == 403 and (cal.new_york(now).date() - d).days <= 3:
                    deferred.append(d.isoformat())
                    continue
                raise
            received_at = clock()
            if not bars:
                empty.append(d.isoformat())        # not published yet, or a closure the calendar does not know
                if (now.date() - d).days >= 4:
                    market.save_bars(d, [], "massive", received_at)
                continue
            wrong_day = sum(1 for b in bars if b.day != d)
            if wrong_day:
                raise DeskError(f"massive grouped daily for {d}: {wrong_day} bars stamped another day")
            stored += market.save_bars(d, [b for b in bars if b.symbol in keep], "massive", received_at)
            fetched.append(d.isoformat())
        if deferred and not fetched:
            raise DeskError("Massive denied recent closing prices (HTTP 403); publication delay or plan entitlement. "
                            "Stored history is preserved; this is not proof the API key is wrong.")
        return {"days": len(fetched), "rows": stored, "empty": empty, "remaining": max(0, len(days) - len(fetched) - len(empty)),
                "newest": fetched[0] if fetched else None, "deferred_403": deferred,
                "warning": "Recent closes denied; retrying later. Check publication/plan entitlement in Settings."
                           if deferred else None}
    return await _job(market, "prices", now, work)


async def corporate_actions(suite: StocksSuite, market: StocksMarket, now: datetime, *, history_days: int) -> dict[str, Any]:
    async def work() -> dict[str, Any]:
        state = market.jobs().get("corporate_actions", {}).get("detail", {})
        since = date.fromisoformat(state["through"]) - timedelta(days=7) if state.get("through") else (
            now.date() - timedelta(days=history_days + 30))
        splits = await suite.massive.splits(since)
        dividends = await suite.massive.dividends(since)
        keep = stored_symbols(market)
        return {"splits": market.save_splits([s for s in splits if s.symbol in keep], now),
                "dividends": market.save_dividends([d for d in dividends if d.symbol in keep], now),
                "through": now.date().isoformat()}
    return await _job(market, "corporate_actions", now, work)


CORPORATE_YEARS = 10     # splits and dividends reach as far back as Schwab's daily history (price_history_years)


async def corporate_history(suite: StocksSuite, market: StocksMarket, now: datetime, *, history_days: int,
                            years: int = CORPORATE_YEARS) -> dict[str, Any]:
    """Splits and dividends before the daily job's window, one calendar month a pass, newest first, back ``years``.

    The split record un-adjusts Schwab's older daily candles and the dividends pay replayed PAPER accounts. ``from`` is
    the first day the stored record covers. If Massive refuses older reference data (a plan limit), the backfill
    stops there and says so: earlier candles are then never stored, because they could not be un-adjusted."""
    async def work() -> dict[str, Any]:
        state = market.jobs().get("corporate_history", {}).get("detail", {})
        oldest = (now.date() - timedelta(days=365 * years + 5)).replace(day=1)
        cover = date.fromisoformat(state["from"]) if state.get("from") else now.date() - timedelta(days=history_days + 30)
        if state.get("refused") or cover <= oldest:
            return {**state, "remaining": 0, "complete": True}
        end = cover - timedelta(days=1)
        start = max(oldest, end.replace(day=1))
        try:
            splits = await suite.massive.splits(start, end)
            dividends = await suite.massive.dividends(start, end)
        except ProviderError as exc:
            if exc.status_code not in (401, 403):
                raise
            return {"from": cover.isoformat(), "remaining": 0, "complete": True,
                    "refused": f"Massive refused splits and dividends before {cover.isoformat()} (HTTP "
                               f"{exc.status_code}); earlier prices can't be un-adjusted, so none are stored"}
        keep = stored_symbols(market)
        return {"from": start.isoformat(), "remaining": int(start > oldest), "complete": start <= oldest,
                "splits": market.save_splits([s for s in splits if s.symbol in keep], now),
                "dividends": market.save_dividends([d for d in dividends if d.symbol in keep], now)}
    return await _job(market, "corporate_history", now, work)


# ── SEC companies, filings feed and insiders ─────────────────────────────────────────────────────
async def companies(suite: StocksSuite, market: StocksMarket, now: datetime, ciks: Iterable[int], *,
                    facts: bool = True) -> dict[str, Any]:
    """Refresh submissions (filings, SIC) and XBRL company facts for these companies."""
    async def work() -> dict[str, Any]:
        done, new_filings, facts_changed, missing = 0, 0, 0, []
        for cik in ciks:
            try:
                company = await suite.sec.submissions(cik)
            except DeskError as exc:
                if "not found" in str(exc):
                    missing.append(cik)
                    continue
                raise
            new_filings += market.save_company(company, sector(company.sic), now)
            if facts:
                try:
                    raw = await suite.sec.companyfacts_raw(cik)
                except DeskError as exc:
                    if "not found" not in str(exc):
                        raise
                    raw = b'{"facts": {}}'                     # no XBRL (many foreign filers): an honest empty set
                horizon = now.date() - timedelta(days=FACT_YEARS * 366)
                parsed = {k: [f for f in v if f.end >= horizon] for k, v in parse_companyfacts(raw, CONCEPTS).items()}
                # the kept span is part of the revision, so a longer span re-reads facts already stored
                facts_changed += market.save_facts(cik, parsed, f"{sha256_hex(raw)}:{FACT_YEARS}y", now)
            done += 1
        return {"companies": done, "new_filings": new_filings, "facts_changed": facts_changed, "missing": missing[:20]}
    return await _job(market, "companies", now, work)


def stale_companies(market: StocksMarket, now: datetime, *, max_age: timedelta = timedelta(days=45),
                    limit: int = 400) -> list[int]:
    """Universe companies never fetched (first) or not refreshed for ``max_age``."""
    ages = market.facts_age()
    ciks = sorted({int(r["cik"]) for r in research_listings(market) if r["cik"]})
    # companies delisted during Massive's window, once (their filings and facts serve the years they traded)
    gone = sorted({int(r[0]) for r in market.conn.execute("SELECT cik FROM stk_delisted WHERE cik IS NOT NULL")})
    never = [c for c in ciks if not ages.get(c)]
    old = sorted((c for c in ciks if ages.get(c) and datetime.fromisoformat(str(ages[c])) < now - max_age),
                 key=lambda c: str(ages[c]))
    return (never + old + [c for c in gone if not ages.get(c) and c not in set(ciks)])[:limit]


async def filings_feed(suite: StocksSuite, market: StocksMarket, now: datetime, *, start: date,
                       max_days: int = 10, budget_seconds: float | None = None,
                       clock: Callable[[], float] = time.monotonic) -> dict[str, Any]:
    """The SEC daily index: Form 4s of universe companies are parsed (one request each), and companies with new
    periodic or current reports are refreshed. Resumes from the last processed day.

    A pass handles at most ``max_days`` trading days and starts no new day once ``budget_seconds`` have gone by (a
    busy day is a few hundred Form 4s, minutes at the SEC's polite rate), so catching up after downtime never holds
    the collector for long; ``behind`` says days remain, and the runner then comes back on its next pass."""
    async def work() -> dict[str, Any]:
        state = market.jobs().get("filings_feed", {}).get("detail", {})
        first = date.fromisoformat(state["through"]) + timedelta(days=1) if state.get("through") else start
        universe = {int(r["cik"]): r["symbol"] for r in research_listings(market) if r["cik"]}
        today = cal.new_york(now).date()
        processed: list[str] = []
        trades, refresh = 0, set()
        through = state.get("through")
        d, began, behind = first, clock(), False
        while d < today:
            if not cal.is_trading_day(d):
                d += timedelta(days=1)
                through = (d - timedelta(days=1)).isoformat()
                continue
            if len(processed) >= max_days or (budget_seconds is not None and processed
                                              and clock() - began >= budget_seconds):
                behind = True
                break
            try:
                entries = await suite.sec.daily_index(d)
            except DeskError as exc:
                if "not found" in str(exc):           # not published yet: try again later
                    break
                raise
            rows: list[tuple[InsiderTrade, date]] = []
            for e in entries:
                if e.cik not in universe:
                    continue
                if e.form in ("4", "4/A"):
                    _, xml = parse_submission_text(await suite.sec.submission_text(e.path))
                    if xml:
                        rows += [(t, e.filed) for t in parse_form4(xml, e.accession) if t.issuer_cik == e.cik]
                elif e.form in COMPANY_FORMS or e.form in FILING_FORMS:
                    refresh.add(e.cik)
            trades += market.save_insider_trades(rows)
            processed.append(d.isoformat())
            through = d.isoformat()
            d += timedelta(days=1)
        report: dict[str, Any] = {"days": processed, "insider_rows": trades, "through": through, "behind": behind}
        if refresh:
            report["companies"] = await companies(suite, market, now, sorted(refresh))
        return report
    return await _job(market, "filings_feed", now, work)


INSIDER_QUARTERS = 9     # two years and a quarter: a year-on-year comparison of insider buying, with a quarter to spare


async def insider_history(suite: StocksSuite, market: StocksMarket, now: datetime, *, quarters: int = INSIDER_QUARTERS,
                          per_pass: int | None = None) -> dict[str, Any]:
    """The SEC's quarterly insider data sets (one ~9 MB download per quarter) for the history before the daily feed.

    The newest ``quarters`` published are loaded, each once, at most ``per_pass`` new ones per call (each is parsed in
    memory, so a pass stays small); ``remaining`` says how many are still to come. Filings the daily feed already
    stored are skipped, so one filing never holds rows from two parsers. A failure keeps the quarters already loaded,
    so the next run resumes instead of downloading them again."""
    async def work() -> dict[str, Any]:
        state = market.jobs().get("insider_history", {}).get("detail", {})
        loaded = set(state.get("loaded", []))
        universe = {int(r["cik"]) for r in research_listings(market) if r["cik"]}
        rows, fetched = 0, list[str]()

        def detail(remaining: int) -> dict[str, Any]:
            newest = max((p.rsplit("/", 1)[-1][:6] for p in loaded), default=None)
            return {"loaded": sorted(loaded), "rows": rows, "newest_quarter": newest, "this_pass": fetched,
                    "remaining": remaining}

        todo: list[str] = []
        try:
            paths = sorted(await suite.sec.insider_dataset_paths(), key=lambda p: p.rsplit("/", 1)[-1], reverse=True)
            todo = [p for p in paths[:quarters] if p not in loaded]
            for path in todo[:per_pass] if per_pass else todo:
                data = await suite.sec.insider_dataset(path)
                stored = market.insider_accessions(min(f for _, f in data)) if data else set()
                rows += market.save_insider_trades([(t, f) for t, f in data
                                                    if t.issuer_cik in universe and t.accession not in stored])
                loaded.add(path)
                fetched.append(path)
        except DeskError as exc:
            raise IncompleteJob(str(exc), detail(len(todo) - len(fetched))) from exc
        return detail(len(todo) - len(fetched))
    return await _job(market, "insider_history", now, work)


def quarter_end(code: str) -> date:
    """'2026q3' → 2026-09-30."""
    year, q = int(code[:4]), int(code[5])
    return date(year + (q == 4), (q * 3) % 12 + 1, 1) - timedelta(days=1)


# ── short interest, short volume, fails ──────────────────────────────────────────────────────────
SHORT_INTEREST_SETTLEMENTS = 48     # two years of twice-monthly settlements: as deep as the price history
SHORT_VOLUME_DAYS = 45              # the short-volume measures read about a month and a half of daily files
FAILS_MONTHS = 24                   # two years of fails-to-deliver files, two a month


def _months_back(first: date, k: int) -> date:
    year, month = divmod(first.year * 12 + first.month - 1 - k, 12)
    return date(year, month + 1, 1)


async def short_interest(suite: StocksSuite, market: StocksMarket, now: datetime, *,
                         history: int = SHORT_INTEREST_SETTLEMENTS, per_pass: int = 4) -> dict[str, Any]:
    """FINRA's newest published settlement, then older ones until ``history`` settlements are stored, at most
    ``per_pass`` a call (each is about five 5,000-row requests); ``remaining`` says how many are still to come.

    FINRA moves a settlement back to a weekday (one more day for a holiday), so each half-month has two candidate
    dates. A candidate within three days of a stored settlement is its twin and is skipped; one FINRA answered empty is
    older than a published settlement, so it never will be: it is remembered and not asked about again."""
    async def work() -> dict[str, Any]:
        keep = stored_symbols(market)
        state = market.jobs().get("short_interest", {}).get("detail", {})
        empty = set(state.get("empty", []))
        have = set(market.short_interest_dates())
        settlement, rows = await suite.feeds.latest_short_interest(now.date())
        stored = market.save_short_interest([r for r in rows if r.symbol in keep], now) if settlement not in have else 0
        backfilled: list[str] = []
        left = False

        def detail() -> dict[str, Any]:
            return {"settlement": None if settlement is None else settlement.isoformat(), "rows": stored,
                    "backfilled": backfilled, "stored_settlements": len(have), "empty": sorted(empty),
                    "remaining": max(0, history - len(have)) if left else 0}

        if settlement is None:
            return detail()
        have.add(settlement)
        try:
            for older in settlement_candidates(settlement - timedelta(days=1), months=history // 2 + 1):
                if len(have) >= history:
                    break
                if older.isoformat() in empty or any(abs((older - s).days) <= 3 for s in have):
                    continue
                if len(backfilled) >= per_pass:
                    left = True
                    break
                old_rows = await suite.feeds.short_interest(older)
                if old_rows:
                    stored += market.save_short_interest([r for r in old_rows if r.symbol in keep], now)
                    backfilled.append(older.isoformat())
                    have.add(older)
                else:
                    empty.add(older.isoformat())
        except DeskError as exc:
            left = True
            raise IncompleteJob(str(exc), detail()) from exc
        return detail()
    return await _job(market, "short_interest", now, work)


async def short_volume(suite: StocksSuite, market: StocksMarket, now: datetime, *, days: int = SHORT_VOLUME_DAYS,
                       recent: int = 5, per_pass: int = 20) -> dict[str, Any]:
    """FINRA's daily short-sale volume files, newest first, at most ``per_pass`` a call. It looks back to the newest
    stored day (at least ``recent`` days, for a file published late; at most ``days``), so a downtime of any length
    is filled as far as the measures read, without asking again every day for an old file FINRA never published.
    While a backfill is unfinished the next call keeps the same starting day."""
    async def work() -> dict[str, Any]:
        keep = stored_symbols(market)
        state = market.jobs().get("short_volume", {}).get("detail", {})
        have = market.short_volume_days()
        last = cal.last_closed_session(now, timedelta(hours=3))
        start = last - timedelta(days=days)
        if state.get("remaining") and state.get("from"):
            start = max(start, date.fromisoformat(state["from"]))
        elif have:
            start = max(start, min(max(have), last - timedelta(days=recent)))
        wanted = [d for d in reversed(cal.trading_days(start, last)) if d not in have]
        stored, fetched, tried = 0, list[str](), 0
        for d in wanted[:per_pass]:
            tried += 1
            try:
                rows = await suite.feeds.reg_sho(d)
            except DeskError as exc:
                if "not found" in str(exc) or "HTTP 403" in str(exc):
                    continue
                raise IncompleteJob(str(exc), {"from": start.isoformat(), "remaining": len(wanted) - tried + 1,
                                               "days": len(fetched), "rows": stored}) from exc
            stored += market.save_short_volume(rows, keep)
            fetched.append(d.isoformat())
        return {"days": len(fetched), "rows": stored, "from": start.isoformat(), "remaining": len(wanted) - tried}
    return await _job(market, "short_volume", now, work)


async def fails(suite: StocksSuite, market: StocksMarket, now: datetime, *, months: int = FAILS_MONTHS,
                per_pass: int = 4) -> dict[str, Any]:
    """SEC fails-to-deliver, two files a month (each half-month, published about two weeks after it ends), for the
    last ``months`` months: newest first, each file once, at most ``per_pass`` downloads a call. A recent file not
    published yet is asked for again at the next run; one still missing two months on is recorded and left alone."""
    async def work() -> dict[str, Any]:
        keep = stored_symbols(market)
        state = market.jobs().get("fails", {}).get("detail", {})
        month = now.date().replace(day=1)
        names = [(k, name) for k in range(1, months + 1) for name in reversed(ftd_file_names(_months_back(month, k)))]
        window = {name for _, name in names}
        loaded = set(state.get("loaded", [])) & window
        missing = set(state.get("missing", [])) & window
        todo = [(k, name) for k, name in names if name not in loaded and name not in missing]
        stored, files, unpublished = 0, list[str](), list[str]()

        def detail(left: int) -> dict[str, Any]:
            return {"files": files, "rows": stored, "loaded": sorted(loaded), "missing": sorted(missing),
                    "unpublished": unpublished, "remaining": left}

        tried = 0
        try:
            for k, name in todo:
                if len(files) >= per_pass:
                    break
                tried += 1
                try:
                    rows = await suite.sec.fails(name)
                except DeskError as exc:
                    if "not found" not in str(exc):
                        raise
                    if k >= 3:
                        missing.add(name)
                    else:
                        unpublished.append(name)
                    continue
                stored += market.save_fails([r for r in rows if r.symbol in keep])
                files.append(name)
                loaded.add(name)
        except DeskError as exc:
            raise IncompleteJob(str(exc), detail(len(todo) - tried + 1)) from exc
        return detail(len(todo) - tried)
    return await _job(market, "fails", now, work)


# ── crowd and rates ──────────────────────────────────────────────────────────────────────────────
async def mentions(suite: StocksSuite, market: StocksMarket, now: datetime) -> dict[str, Any]:
    async def work() -> dict[str, Any]:
        hour = int(now.timestamp() // 3600)
        counts = {}
        for source in MENTION_SOURCES:
            rows = await suite.feeds.mentions(source)
            counts[source] = market.save_mentions(rows, hour)
        return {"hour": hour, "rows": counts}
    return await _job(market, "mentions", now, work)


async def rates(suite: StocksSuite, market: StocksMarket, now: datetime) -> dict[str, Any]:
    async def work() -> dict[str, Any]:
        stored = 0
        years = [now.year] if market.rates("UST10Y", merged=False) else [now.year - 2, now.year - 1, now.year]
        for year in years:
            stored += market.save_rates(await suite.feeds.treasury_curve(year))
        fred_errors = {}
        for series in FRED_SERIES:          # FRED is optional: unreliable from some hosts, never blocks the refresh
            have = market.rates(series, merged=False)
            since = have[-1].day - timedelta(days=10) if have else now.date() - timedelta(days=800)
            try:
                stored += market.save_rates(await suite.feeds.fred_series(series, since))
            except DeskError as exc:
                fred_errors[series] = str(exc)[:120]
        return {"rows": stored, "series": sorted(set(TREASURY_COLUMNS.values()) | set(FRED_SERIES)), "fred_errors": fred_errors}
    return await _job(market, "rates", now, work)


# Schwab index quotes for the series the macro desk, PAPER's option pricing and Plays read: $VIX for FRED's VIX close,
# $TNX and $IRX (Cboe's 10-year and 13-week Treasury yield indexes) for the Treasury curve's 10-year and 3-month.
SCHWAB_INDEXES = {"$VIX": "VIXCLS", "$TNX": "UST10Y", "$IRX": "UST3M"}
INDEX_SETTLE = timedelta(minutes=5)


def index_close(row: dict[str, Any], series: str, now: datetime) -> RatePoint | None:
    """A finished session's closing value from Schwab's quote, or None while the session is open or the quote is
    stale, missing or after hours. Cboe's yield indexes are quoted as ten times the yield (42.5 = 4.25%) when above 20."""
    value = row.get("regular_last") or row.get("last")
    stamp = row.get("regular_trade_time") or row.get("trade_time") or row.get("quote_time")
    if not isinstance(value, (int, float)) or value <= 0 or not isinstance(stamp, str):
        return None
    day = cal.new_york(datetime.fromisoformat(stamp)).date()
    close = cal.session_close(day) if cal.is_trading_day(day) else None
    # the closing print only: a later (extended-hours) value is not the session's close
    if close is None or now < close + INDEX_SETTLE or datetime.fromisoformat(stamp) > close + timedelta(minutes=15):
        return None
    value = float(value) / 10 if series.startswith("UST") and value > 20 else float(value)
    return RatePoint(f"{series}{SCHWAB_RATE}", day, value)


async def index_closes(suite: StocksSuite, market: StocksMarket, now: datetime) -> dict[str, Any]:
    """Same-evening closes of $VIX, $TNX and $IRX from Schwab (one request), stored as their own source; the free
    Treasury and FRED series stay the fallback on every day Schwab has none (``StocksMarket.rates``)."""
    async def work() -> dict[str, Any]:
        assert suite.schwab is not None
        rows = await suite.schwab.closing_quotes(list(SCHWAB_INDEXES))
        points = [p for symbol, series in SCHWAB_INDEXES.items()
                  if (p := index_close(rows.get(symbol) or {}, series, now)) is not None]
        return {"stored": market.save_rates(points), "days": sorted({p.day.isoformat() for p in points}),
                "missing": sorted(set(SCHWAB_INDEXES) - set(rows))}
    return await _job(market, "index_closes", now, work)


# ── options ──────────────────────────────────────────────────────────────────────────────────────
SPAN = 12          # listed expirations one Schwab chains request may span (names with daily expiries need several)


def spans(listed: Sequence[date], chosen: Sequence[date], most: int = SPAN) -> list[list[date]]:
    """The chosen expirations grouped so each group spans at most ``most`` listed ones: one request a group, so a
    stock with weekly and monthly expiries needs one, and SPY's daily expiries a few rather than one huge answer."""
    order = {e: i for i, e in enumerate(sorted(set(listed) | set(chosen)))}
    groups: list[list[date]] = []
    for e in sorted(chosen):
        if groups and order[e] - order[groups[-1][0]] < most:
            groups[-1].append(e)
        else:
            groups.append([e])
    return groups


def pick_expirations(expirations: Sequence[date], today: date, earnings: date | None = None) -> list[date]:
    """The nearest weekly (2+ days out), the ones closest to 30, 60, 90 and 180 days, the one nearest a year out (180+
    days), and the first after earnings: every bucket Learning's lifecycle recipes trade."""
    future = [e for e in sorted(expirations) if (e - today).days >= 2]
    if not future:
        return []
    picked = {future[0]}
    for target in (30, 60, 90, 180):
        picked.add(min(future, key=lambda e: abs((e - today).days - target)))
    long_dated = [e for e in future if (e - today).days >= 180]
    if long_dated:                               # one LEAPS expiry, nearest a year out (Plays → Expiry: LEAPS)
        picked.add(min(long_dated, key=lambda e: abs((e - today).days - 365)))
    if earnings is not None:
        after = [e for e in future if e > earnings]
        if after:
            picked.add(after[0])
    return sorted(picked)


async def option_chains(suite: StocksSuite, market: StocksMarket, now: datetime, symbols: Sequence[str], *, rate: float,
                        last_prices: dict[str, float], earnings: dict[str, date] | None = None,
                        held: dict[str, set[date]] | None = None,
                        clock: Callable[[], datetime] = utc_now) -> dict[str, Any]:
    """Chains for ``symbols`` (and every underlying in ``held``): the picked expirations plus any expiration a PAPER
    position holds, so a held contract is quoted every day until it closes."""
    async def work() -> dict[str, Any]:
        today = quote_session(now)
        done, contracts, skipped = 0, 0, []
        errors = {}
        coverage = {}
        wanted = list(dict.fromkeys([*symbols, *(held or {})]))
        for symbol in wanted:
            try:
                expirations = await suite.tradier.expirations(symbol)
                chosen = sorted(set(pick_expirations(expirations, today, (earnings or {}).get(symbol)))
                                | {e for e in (held or {}).get(symbol, set()) if e in expirations and e >= today})
                if not chosen:
                    skipped.append(symbol)
                    coverage[symbol] = {"status": "no_eligible_expirations", "contracts": 0}
                    continue
                chains: dict[date, list[OptionQuote]] = {}
                received_at = clock()
                price = last_prices.get(symbol)
                ranged = getattr(suite.tradier, "chain_range", None)
                # Schwab: one chains request covering every chosen expiry (a few for daily-expiry names), with the
                # underlying's price in the same answer. Tradier: one request an expiry.
                for group in (spans(expirations, chosen) if ranged else [[e] for e in chosen]):
                    if ranged:
                        got, quoted = await ranged(symbol, group)
                        price = price or quoted
                    else:
                        got = {group[0]: await suite.tradier.chain(symbol, group[0])}
                    received_at = clock()
                    for e in group:
                        chains[e] = got.get(e, [])
                        # Persist the actual chain even when a subsequent underlying quote fails.
                        # Never replace a useful stored expiry with an empty parser/provider response.
                        if chains[e]:
                            market.save_option_chain(symbol, quote_session(received_at), e, received_at,
                                                     last_prices.get(symbol), chains[e],
                                                     source=suite.option_source)
                            contracts += len(chains[e])
                if not any(chains.values()):
                    errors[symbol] = "No supported contracts returned; check provider response/standard deliverables"
                    coverage[symbol] = {"status": "empty_chain", "contracts": 0}
                    continue
                if price is None:
                    stock = await suite.tradier.quotes([symbol])
                    price = stock[0].last if stock else None
                if price is None or price <= 0:
                    errors[symbol] = "Chains saved; underlying price unavailable for IV/expected moves"
                    coverage[symbol] = {"status": "missing_underlying_price", "contracts": sum(map(len, chains.values()))}
                    continue
                received_at = clock()
                capture_day = quote_session(received_at)
                for e, quotes in chains.items():
                    if quotes:
                        market.save_option_chain(symbol, capture_day, e, received_at, price, quotes, source=suite.option_source)
                valid, _ = qualified_quotes([q for qs in chains.values() for q in qs], received_at)
                summary = opt.daily_summary({e: [q for q in valid if q.expiration == e] for e in chains},
                                            price, capture_day, rate)
                summary["activity"] = chain_activity([q for qs in chains.values() for q in qs], received_at)
                summary["analytics_version"] = OPTIONS_VERSION
                summary["source"] = suite.option_source
                summary["provider_diagnostics"] = dict(getattr(suite.tradier, "chain_diagnostics", {}).get(symbol, {}))
                market.save_option_daily(symbol, capture_day, received_at, summary)
                coverage[symbol] = {"status": "collected", **summary["activity"]}
                done += 1
            except DeskError as exc:
                errors[symbol] = str(exc)[:200]
                continue
        pruned = market.prune_option_chains(today - timedelta(days=45))
        detail = {"symbols": done, "contracts": contracts, "skipped": skipped[:20], "pruned": pruned,
                "source": suite.option_source, "errors": errors, "partial": bool(errors), "session": today.isoformat(),
                "coverage": coverage, "parser_version": OPTIONS_VERSION}
        if errors and not done:
            first_error = next(iter(errors.values()))
            raise IncompleteJob(f"All {len(errors)} option requests failed; {first_error}", detail)
        return detail
    return await _job(market, "options", now, work)


__all__ = ["COMPANY_FORMS", "FRED_SERIES", "MENTION_SOURCES", "companies", "corporate_actions", "fails", "filings_feed",
           "insider_history", "listings", "mentions", "missing_price_days", "option_chains", "pick_expirations", "prices",
           "quarter_end", "rates", "short_interest", "short_volume", "stale_companies"]
