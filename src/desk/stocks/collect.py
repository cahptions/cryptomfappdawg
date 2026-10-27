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
from desk.stocks.contracts import InsiderTrade
from desk.stocks.fundamentals import CONCEPTS
from desk.stocks.repo import FILING_FORMS, StocksMarket
from desk.stocks.sectors import sector
from desk.stocks.sources.feeds import TREASURY_COLUMNS, settlement_candidates
from desk.stocks.sources.sec import ftd_file_names, parse_companyfacts, parse_form4, parse_submission_text
from desk.stocks.suite import StocksSuite
from desk.stocks.universe import research_listings, stored_symbols

log = logging.getLogger(__name__)
COMPANY_FORMS = frozenset({"10-K", "10-Q", "10-K/A", "10-Q/A", "8-K", "8-K/A", "20-F", "6-K"})
FRED_SERIES = ("BAMLH0A0HYM2", "VIXCLS")
MENTION_SOURCES = ("wallstreetbets", "all-stocks", "options")
FACT_YEARS = 8           # company facts kept: enough for five-year growth and two years of point-in-time history


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
                facts_changed += market.save_facts(cik, parsed, sha256_hex(raw), now)
            done += 1
        return {"companies": done, "new_filings": new_filings, "facts_changed": facts_changed, "missing": missing[:20]}
    return await _job(market, "companies", now, work)


def stale_companies(market: StocksMarket, now: datetime, *, max_age: timedelta = timedelta(days=45),
                    limit: int = 400) -> list[int]:
    """Universe companies never fetched (first) or not refreshed for ``max_age``."""
    ages = market.facts_age()
    ciks = sorted({int(r["cik"]) for r in research_listings(market) if r["cik"]})
    never = [c for c in ciks if not ages.get(c)]
    old = sorted((c for c in ciks if ages.get(c) and datetime.fromisoformat(str(ages[c])) < now - max_age),
                 key=lambda c: str(ages[c]))
    return (never + old)[:limit]


async def filings_feed(suite: StocksSuite, market: StocksMarket, now: datetime, *, start: date,
                       max_days: int = 10) -> dict[str, Any]:
    """The SEC daily index: Form 4s of universe companies are parsed (one request each), and companies with new
    periodic or current reports are refreshed. Resumes from the last processed day."""
    async def work() -> dict[str, Any]:
        state = market.jobs().get("filings_feed", {}).get("detail", {})
        first = date.fromisoformat(state["through"]) + timedelta(days=1) if state.get("through") else start
        universe = {int(r["cik"]): r["symbol"] for r in research_listings(market) if r["cik"]}
        today = cal.new_york(now).date()
        processed: list[str] = []
        trades, refresh = 0, set()
        through = state.get("through")
        d = first
        while d < today and len(processed) < max_days:
            if not cal.is_trading_day(d):
                d += timedelta(days=1)
                through = (d - timedelta(days=1)).isoformat()
                continue
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
        report: dict[str, Any] = {"days": processed, "insider_rows": trades, "through": through}
        if refresh:
            report["companies"] = await companies(suite, market, now, sorted(refresh))
        return report
    return await _job(market, "filings_feed", now, work)


async def insider_history(suite: StocksSuite, market: StocksMarket, now: datetime, *, quarters: int = 4) -> dict[str, Any]:
    """The SEC's quarterly insider data sets (one small download per quarter) for history before the daily feed."""
    async def work() -> dict[str, Any]:
        state = market.jobs().get("insider_history", {}).get("detail", {})
        loaded = set(state.get("loaded", []))
        universe = {int(r["cik"]) for r in research_listings(market) if r["cik"]}
        rows = 0
        for path in (await suite.sec.insider_dataset_paths())[:quarters]:
            if path in loaded:
                continue
            data = await suite.sec.insider_dataset(path)
            rows += market.save_insider_trades([(t, f) for t, f in data if t.issuer_cik in universe])
            loaded.add(path)
        newest = max((p.rsplit("/", 1)[-1][:6] for p in loaded), default=None)
        return {"loaded": sorted(loaded), "rows": rows, "newest_quarter": newest}
    return await _job(market, "insider_history", now, work)


def quarter_end(code: str) -> date:
    """'2026q3' → 2026-09-30."""
    year, q = int(code[:4]), int(code[5])
    return date(year + (q == 4), (q * 3) % 12 + 1, 1) - timedelta(days=1)


# ── short interest, short volume, fails ──────────────────────────────────────────────────────────
async def short_interest(suite: StocksSuite, market: StocksMarket, now: datetime, *, history: int = 6) -> dict[str, Any]:
    async def work() -> dict[str, Any]:
        keep = stored_symbols(market)
        have = set(market.short_interest_dates())
        settlement, rows = await suite.feeds.latest_short_interest(now.date())
        stored = market.save_short_interest([r for r in rows if r.symbol in keep], now) if settlement not in have else 0
        backfilled: list[str] = []
        if settlement is not None and len(have) < history:
            for older in settlement_candidates(settlement - timedelta(days=1), months=history // 2 + 1):
                if older in have or len(have) + len(backfilled) >= history:
                    continue
                old_rows = await suite.feeds.short_interest(older)
                if old_rows:
                    stored += market.save_short_interest([r for r in old_rows if r.symbol in keep], now)
                    backfilled.append(older.isoformat())
        return {"settlement": None if settlement is None else settlement.isoformat(), "rows": stored, "backfilled": backfilled}
    return await _job(market, "short_interest", now, work)


async def short_volume(suite: StocksSuite, market: StocksMarket, now: datetime, *, days: int = 45) -> dict[str, Any]:
    async def work() -> dict[str, Any]:
        keep = stored_symbols(market)
        have = market.short_volume_days()
        last = cal.last_closed_session(now, timedelta(hours=3))
        stored, fetched = 0, []
        for d in reversed(cal.trading_days(last - timedelta(days=days), last)):
            if d in have:
                continue
            try:
                rows = await suite.feeds.reg_sho(d)
            except DeskError as exc:
                if "not found" in str(exc) or "HTTP 403" in str(exc):
                    continue
                raise
            stored += market.save_short_volume(rows, keep)
            fetched.append(d.isoformat())
        return {"days": len(fetched), "rows": stored}
    return await _job(market, "short_volume", now, work)


async def fails(suite: StocksSuite, market: StocksMarket, now: datetime) -> dict[str, Any]:
    async def work() -> dict[str, Any]:
        keep = stored_symbols(market)
        month = now.date().replace(day=1)
        names = ftd_file_names(month - timedelta(days=1)) + ftd_file_names(month - timedelta(days=40))
        stored, files = 0, []
        for name in names:
            try:
                rows = await suite.sec.fails(name)
            except DeskError as exc:
                if "not found" in str(exc):
                    continue
                raise
            stored += market.save_fails([r for r in rows if r.symbol in keep])
            files.append(name)
        return {"files": files, "rows": stored}
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
        years = [now.year] if market.rates("UST10Y") else [now.year - 2, now.year - 1, now.year]
        for year in years:
            stored += market.save_rates(await suite.feeds.treasury_curve(year))
        fred_errors = {}
        for series in FRED_SERIES:          # FRED is optional: unreliable from some hosts, never blocks the refresh
            have = market.rates(series)
            since = have[-1].day - timedelta(days=10) if have else now.date() - timedelta(days=800)
            try:
                stored += market.save_rates(await suite.feeds.fred_series(series, since))
            except DeskError as exc:
                fred_errors[series] = str(exc)[:120]
        return {"rows": stored, "series": sorted(set(TREASURY_COLUMNS.values()) | set(FRED_SERIES)), "fred_errors": fred_errors}
    return await _job(market, "rates", now, work)


# ── options ──────────────────────────────────────────────────────────────────────────────────────
def pick_expirations(expirations: Sequence[date], today: date, earnings: date | None = None) -> list[date]:
    """The nearest weekly (2+ days out), the ones closest to 30, 60 and 90 days, the one nearest a year out (180+ days),
    and the first after earnings."""
    future = [e for e in sorted(expirations) if (e - today).days >= 2]
    if not future:
        return []
    picked = {future[0]}
    for target in (30, 60, 90):
        picked.add(min(future, key=lambda e: abs((e - today).days - target)))
    long_dated = [e for e in future if (e - today).days >= 180]
    if long_dated:                               # one LEAPS expiry, nearest a year out (Plays → LEAPS)
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
                chains = {}
                received_at = clock()
                for e in chosen:
                    chains[e] = await suite.tradier.chain(symbol, e)
                    received_at = clock()
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
                price = last_prices.get(symbol)
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
