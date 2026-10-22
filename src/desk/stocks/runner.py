"""Bounded, read-only collection and forward PAPER orchestration. No brokerage order client exists here."""

from __future__ import annotations

import json
import os
from collections.abc import Awaitable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from desk.core.clock import utc_now
from desk.core.config import LoadedConfig
from desk.core.errors import DeskError
from desk.core.hashing import canonical_json, sha256_hex
from desk.core.paths import DeskPaths
from desk.core.secrets import load_secrets
from desk.stocks import calendar as cal
from desk.stocks import collect, daily, validation
from desk.stocks.analysis import MIN_BARS, Analyzer, stock_detail
from desk.stocks.connection import _atomic_private
from desk.stocks.lists import LISTS, build_lists
from desk.stocks.paper import lab
from desk.stocks.paper.book import PaperBook
from desk.stocks.paper.engine import PaperEngine
from desk.stocks.paper.report import report
from desk.stocks.paper.strategies import STRATEGIES
from desk.stocks.prices import load_panel
from desk.stocks.repo import StocksDatabases, StocksMarket, StocksResearch
from desk.stocks.suite import build_stocks_suite


class MemoryPressure(DeskError):
    pass


def saved_path(paths: DeskPaths, name: str) -> Path:
    if name not in {"worker", "paper", "research", "progress"}:
        raise ValueError("unknown Stocks record")
    return paths.home / f"stocks-{name}.json"


def saved(paths: DeskPaths, name: str) -> dict[str, Any] | None:
    path = saved_path(paths, name)
    if not path.exists():
        return None
    try:
        if path.stat().st_size > 4_000_000:
            raise ValueError
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError
        if name == "research" and (not isinstance(value.get("lists"), list) or any(
                not isinstance(item, dict) or not isinstance(item.get("id"), str)
                or not isinstance(item.get("rows"), list) for item in value["lists"])):
            raise ValueError
        return dict(value)
    except (ValueError, OSError) as exc:
        raise DeskError("Stored Stocks report is unreadable; run desk stocks refresh again") from exc


def _memory_guard() -> None:
    """Avoid starting a broad-universe panel while this laptop is already under memory pressure."""
    info = Path("/proc/meminfo")
    if info.exists():
        fields = {line.split(":")[0]: line.split(":")[1].strip().split()[0] for line in info.read_text().splitlines()}
        if int(fields.get("MemAvailable", "999999999")) < 1_500_000:
            raise MemoryPressure("Analysis postponed: less than 1.5 GB available RAM; close heavy browser tabs and retry")


def _due(market: StocksMarket, name: str, now: datetime, hours: float) -> bool:
    at = market.jobs().get(name, {}).get("last_ok_at")
    return not at or now - datetime.fromisoformat(at) >= timedelta(hours=hours)


def research_preview(paths: DeskPaths, loaded: LoadedConfig, dbs: StocksDatabases, now: datetime) -> dict[str, Any]:
    """Refresh a mutable research view during backfill; never step PAPER or rewrite frozen evidence."""
    settings = loaded.settings.stocks
    market, research = StocksMarket(dbs.market), StocksResearch(dbs.research)
    expected = cal.last_closed_session(now, timedelta(minutes=settings.refresh_after_close_minutes))
    days = [d for d in market.bar_days() if expected - timedelta(days=settings.history_days) <= d <= expected]
    if not days:
        return {"waiting": f"Waiting for published prices through {expected.isoformat()}", "paper": None}
    # Research can describe the latest published history, dated honestly. PAPER below still requires today's target.
    target = days[-1]
    if len(days) < MIN_BARS:
        return {"waiting": f"Collecting history: {len(days)}/{MIN_BARS} sessions minimum for research eligibility. "
                           "Charts update as prices arrive; longer-lookback measures need more history.", "paper": None}
    # Receipts change as history arrives; successful source jobs can add new fundamentals/options/crowd data.
    receipts = [tuple(r) for r in market.conn.execute(
        "SELECT day, rows, received_at FROM stk_bar_days WHERE day<=? ORDER BY day", (target.toordinal(),))]
    jobs = {k: v.get("last_ok_at") for k, v in market.jobs().items() if k != "prices"}
    signature = sha256_hex(canonical_json({"target": target.isoformat(), "expected": expected.isoformat(),
                                         "receipts": receipts, "jobs": jobs,
                                         "config": loaded.snapshot_hash, "preview_version": 3}))
    try:
        previous = saved(paths, "research")
    except DeskError:
        previous = None  # derived preview can be rebuilt from authoritative observations
    if previous and previous.get("input_hash") == signature:
        return {"asof": target.isoformat(), "waiting": "Research is current; watching for new data", "paper": None}
    _memory_guard()
    panel = load_panel(market, target - timedelta(days=settings.history_days), target)
    analyzer = Analyzer(market, research, settings)
    day = analyzer.build(panel, target, now=now)  # no persistent fundamentals cache during an incomplete backfill
    research.save_scores(target, {s: stock_detail(day, s) for s in day.ctx.symbols}, {}, now, record_history=False)
    selected = build_lists(day)
    from desk.stocks.activity import candidates

    _, option_coverage = candidates(day)
    value = {"asof": target.isoformat(), "computed_at": now.isoformat(), "input_hash": signature,
             "universe": day.universe_size, "basis": "Latest published research preview, not a frozen forward record",
             "expected_asof": expected.isoformat(), "awaiting_newer_close": target < expected,
             "lists": [{"id": spec.id, "title": spec.title, "blurb": spec.blurb, "asof": target.isoformat(),
                        "rows": [{**row, "rank": rank} for rank, row in enumerate(selected[spec.id][:25], 1)],
                        "preview": True, "coverage": option_coverage if spec.id in
                        ("market_activity", "options_activity", "unusual_options") else None} for spec in LISTS]}
    _atomic_private(saved_path(paths, "research"), json.dumps(value, allow_nan=False))
    return {"asof": target.isoformat(), "universe": day.universe_size, "research_only": True, "paper": None}


def process(paths: DeskPaths, loaded: LoadedConfig, dbs: StocksDatabases, now: datetime, *,
            research_only: bool = False) -> dict[str, Any]:
    """Only today's actually available closed-session analysis creates forward orders; no historical entry replay."""
    if research_only:
        return research_preview(paths, loaded, dbs, now)
    settings = loaded.settings.stocks
    market, research, book = StocksMarket(dbs.market), StocksResearch(dbs.research), PaperBook(dbs.ledger)
    if paths.stop_file.exists():
        with book.atomic():
            for order in book.pending():
                if order.order["action"] == "ENTER":
                    book.resolve(order.id, "CANCELLED", now, "STOP: new entries disabled")
    target = cal.last_closed_session(now, timedelta(minutes=settings.refresh_after_close_minutes))
    available = [d for d in market.bar_days() if d <= target]
    if not available or available[-1] != target:
        return {"waiting": f"Waiting for published {target.isoformat()} closing prices", "paper": None}
    history = [d for d in available if d >= target - timedelta(days=settings.history_days)]
    if book.last_step() is None and len(history) < MIN_BARS:
        return {"waiting": f"PAPER is running; downloading history ({len(history)}/{MIN_BARS} sessions minimum). "
                           "Eligible strategies will simulate trades automatically when data is ready.", "paper": None}
    if book.last_step() == target:
        return {"asof": target.isoformat(), "waiting": "Session already processed; collection/backfill continues"}
    _memory_guard()
    panel = load_panel(market, target - timedelta(days=settings.history_days), target)
    analyzer = Analyzer(market, research, settings)
    learned, retired = lab.strategies(research)
    strategies = (*STRATEGIES, *learned)
    engine = PaperEngine(market, book, settings, strategies, retired)
    # Missed sessions manage existing exposure only; no retrospectively discovered entries or fabricated bars.
    last = book.last_step()
    missed = [d for d in available if last is not None and last < d < target]
    gaps = ([d.isoformat() for d in cal.trading_days(last, target) if last < d < target]
            if last is not None else [])
    for session in missed:
        engine.step(session, now, None, allow_entries=False)
    refreshed = daily.refresh(analyzer, panel, target, now)
    stepped = engine.step(target, now, refreshed.day, allow_entries=not paths.stop_file.exists())
    research.record_run("paper_step", now, now, target, stepped.skipped is None,
                        {"session": target.isoformat(), "intervening_sessions": gaps,
                         "recovery_simulation": bool(gaps), "managed_stored_sessions": [d.isoformat() for d in missed],
                         "computed_at": now.isoformat(), "stop": paths.stop_file.exists()})
    result = report(book, market, strategies)
    result.update(generated_at=now.isoformat(), mode="PAPER", orders_enabled=False,
                  caveats=["Synthetic/offline tests do not establish profitability.",
                           "Daily option snapshots and modeled marks are research approximations; see STOCKS_PAPER.md."])
    _atomic_private(saved_path(paths, "paper"), json.dumps(result, allow_nan=False))
    return {"refresh": refreshed.as_json(), "paper": stepped.as_json(), "missed_sessions_managed": len(missed)}


async def cycle(paths: DeskPaths, loaded: LoadedConfig, *, price_days: int = 5, company_limit: int = 20,
                option_limit: int = 5, research_only: bool = False, managed: bool = False) -> dict[str, Any]:
    now = utc_now()
    dbs = StocksDatabases.open(paths)
    try:
        suite = build_stocks_suite(paths, loaded, load_secrets(paths.secrets_file), dbs.runtime)
    except BaseException:
        dbs.close()
        raise
    market, research = StocksMarket(dbs.market), StocksResearch(dbs.research)
    settings = loaded.settings.stocks
    jobs: dict[str, Any] = {}

    def progress(phase: str) -> None:
        value = {"phase": phase, "pid": os.getpid(), "started_at": now.isoformat(),
                 "updated_at": utc_now().isoformat(), "mode": "PAPER", "research_only": research_only, "managed": managed}
        _atomic_private(saved_path(paths, "progress"), json.dumps(value))

    async def run_job(name: str, work: Awaitable[dict[str, Any]]) -> dict[str, Any]:
        try:
            progress(name)
        except BaseException:
            import inspect

            if inspect.iscoroutine(work):
                work.close()
            raise
        return await work

    def preview_now() -> dict[str, Any]:
        progress('research_preview')
        try:
            return process(paths, loaded, dbs, utc_now(), research_only=True)
        except MemoryPressure as exc:
            return {'waiting': str(exc), 'paper': None}

    try:
        progress("starting")
        # Publish usable stored research before lengthy network work, then again after a price batch.
        # The input signature skips unchanged analysis. Neither preview may step the PAPER book.
        preview_now()
        if suite.options.configured and cal.is_trading_day(cal.new_york(now).date()) and _due(market, "options", now, 4):
            held = PaperBook(dbs.ledger).held_expirations()
            # Rotate bounded batches, with held contracts always included. Never fetch the whole universe on a tick.
            ranked = [r[0] for r in research.conn.execute(
                "SELECT symbol FROM stk_scores_latest ORDER BY json_extract(detail_json, '$.price.dollar_volume_20d') DESC "
                "LIMIT ?", (settings.options_focus_symbols,))]
            candidates = list(dict.fromkeys([*research.watchlist(), *ranked])) or ["SPY", "QQQ"]
            job = market.jobs().get("options", {}).get("detail", {})
            offset = int(job.get("next_offset", 0)) % len(candidates)
            # Core ETF chains are refreshed every options pass; broad rotation cannot starve SPY.
            core = ["SPY", "QQQ"][:option_limit]
            rotating = [s for s in candidates if s not in core]
            offset = offset % max(1, len(rotating))
            remainder = max(0, option_limit - len(core))
            wanted = [*core, *(rotating + rotating)[offset:offset + remainder]]
            jobs["options"] = await run_job("options", collect.option_chains(suite, market, now, wanted, rate=0.04,
                                                         last_prices={}, held=held))
            if jobs["options"].get("ok"):
                detail = {**jobs["options"], "next_offset": (offset + remainder) % max(1, len(rotating))}
                market.job("options", now, detail=detail)
        directory = market.jobs().get("listings", {}).get("detail", {})
        needs_mapping = suite.sec.contact_configured and (not directory.get("with_cik") or directory.get("warning"))
        if _due(market, "listings", now, 24) or needs_mapping:
            jobs["listings"] = await run_job("listings", collect.listings(suite, market, now))
        if suite.massive.configured:
            jobs["prices"] = await run_job("prices", collect.prices(suite, market, now, history_days=settings.history_days,
                                                  after_close=timedelta(minutes=settings.refresh_after_close_minutes),
                                                  max_days=price_days))
            preview_now()
            if _due(market, "corporate_actions", now, 24):
                jobs["corporate_actions"] = await run_job("corporate_actions", collect.corporate_actions(suite, market, now,
                                                                            history_days=settings.history_days))
        if suite.sec.contact_configured:
            jobs["companies"] = await run_job("companies", collect.companies(suite, market, now,
                                                        collect.stale_companies(market, now, limit=company_limit)))
            if _due(market, "filings_feed", now, 4):
                jobs["filings"] = await run_job("filings", collect.filings_feed(suite, market, now,
                                                            start=now.date() - timedelta(days=7), max_days=1))
        for name, work, hours in (("mentions", collect.mentions, 1), ("rates", collect.rates, 24),
                                   ("short_interest", collect.short_interest, 24)):
            if _due(market, name, now, hours):
                jobs[name] = await run_job(name, work(suite, market, now))
        if suite.sec.contact_configured and _due(market, "fails", now, 24):
            jobs["fails"] = await run_job("fails", collect.fails(suite, market, now))
        if _due(market, "short_volume", now, 24):
            jobs["short_volume"] = await run_job("short_volume", collect.short_volume(suite, market, now, days=5))
        progress("analysis")
        try:
            preview = preview_now()
            if research_only:
                analysis = preview
            else:
                progress('analysis')
                analysis = process(paths, loaded, dbs, utc_now())
        except MemoryPressure as exc:
            analysis = {"waiting": str(exc), "paper": None}
        result = {"started_at": now.isoformat(), "finished_at": utc_now().isoformat(), "mode": "PAPER",
                  "research_only": research_only, "options_provider": suite.option_source,
                  "problems": suite.problems, "jobs": jobs,
                  "analysis": analysis}
        _atomic_private(saved_path(paths, "worker"), json.dumps(result, allow_nan=False))
        progress("idle")
        return result
    finally:
        await suite.aclose()
        dbs.close()


def evaluate(paths: DeskPaths, loaded: LoadedConfig, *, learning: bool = False) -> dict[str, Any]:
    """Explicit offline work, never run by a dashboard request or every collection poll."""
    _memory_guard()
    dbs = StocksDatabases.open(paths)
    try:
        now = utc_now()
        market, research = StocksMarket(dbs.market), StocksResearch(dbs.research)
        days = market.bar_days()
        if not days:
            raise DeskError("No stored price history yet; run desk stocks refresh first")
        panel = load_panel(market, days[-1] - timedelta(days=loaded.settings.stocks.history_days), days[-1])
        validation.rebuild(Analyzer(market, research, loaded.settings.stocks), panel, now=now)
        if learning:
            return lab.run(research, panel, now=now, mode=loaded.settings.stocks.learning)
        return validation.run(market, research, panel, today=days[-1], now=now)
    finally:
        dbs.close()
