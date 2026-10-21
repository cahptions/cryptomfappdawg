"""Bounded, read-only collection and forward PAPER orchestration. No brokerage order client exists here."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from desk.core.clock import utc_now
from desk.core.config import LoadedConfig
from desk.core.errors import DeskError
from desk.core.paths import DeskPaths
from desk.core.secrets import load_secrets
from desk.stocks import calendar as cal
from desk.stocks import collect, daily, validation
from desk.stocks.analysis import Analyzer
from desk.stocks.connection import _atomic_private
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
    if name not in {"worker", "paper"}:
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


def process(paths: DeskPaths, loaded: LoadedConfig, dbs: StocksDatabases, now: datetime) -> dict[str, Any]:
    """Only today's actually available closed-session analysis creates forward orders; no historical entry replay."""
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
                option_limit: int = 5) -> dict[str, Any]:
    now = utc_now()
    dbs = StocksDatabases.open(paths)
    suite = build_stocks_suite(paths, loaded, load_secrets(paths.secrets_file), dbs.runtime)
    market, research = StocksMarket(dbs.market), StocksResearch(dbs.research)
    settings = loaded.settings.stocks
    jobs: dict[str, Any] = {}
    try:
        if _due(market, "listings", now, 24):
            jobs["listings"] = await collect.listings(suite, market, now)
        if suite.massive.configured:
            jobs["prices"] = await collect.prices(suite, market, now, history_days=settings.history_days,
                                                  after_close=timedelta(minutes=settings.refresh_after_close_minutes),
                                                  max_days=price_days)
            if _due(market, "corporate_actions", now, 24):
                jobs["corporate_actions"] = await collect.corporate_actions(suite, market, now,
                                                                            history_days=settings.history_days)
        if suite.sec.contact_configured:
            jobs["companies"] = await collect.companies(suite, market, now,
                                                        collect.stale_companies(market, now, limit=company_limit))
            if _due(market, "filings_feed", now, 4):
                jobs["filings"] = await collect.filings_feed(suite, market, now,
                                                            start=now.date() - timedelta(days=7), max_days=1)
        for name, work, hours in (("mentions", collect.mentions, 1), ("rates", collect.rates, 24),
                                   ("short_interest", collect.short_interest, 24), ("fails", collect.fails, 24)):
            if _due(market, name, now, hours):
                jobs[name] = await work(suite, market, now)
        if _due(market, "short_volume", now, 24):
            jobs["short_volume"] = await collect.short_volume(suite, market, now, days=5)
        if suite.options.configured and cal.is_trading_day(cal.new_york(now).date()) and _due(market, "options", now, 4):
            held = PaperBook(dbs.ledger).held_expirations()
            # Rotate bounded batches, with held contracts always included. Never fetch the whole universe on a tick.
            ranked = [r[0] for r in research.conn.execute(
                "SELECT symbol FROM stk_scores_latest ORDER BY json_extract(detail_json, '$.committee.score') DESC "
                "LIMIT ?", (settings.options_focus_symbols,))]
            candidates = list(dict.fromkeys([*research.watchlist(), "SPY", "QQQ", *ranked]))
            job = market.jobs().get("options", {}).get("detail", {})
            offset = int(job.get("next_offset", 0)) % len(candidates)
            wanted = (candidates + candidates)[offset:offset + option_limit]
            jobs["options"] = await collect.option_chains(suite, market, now, wanted, rate=0.04,
                                                         last_prices={}, held=held)
            if jobs["options"].get("ok"):
                detail = {**jobs["options"], "next_offset": (offset + option_limit) % len(candidates)}
                market.job("options", now, detail=detail)
        try:
            analysis = process(paths, loaded, dbs, utc_now())
        except MemoryPressure as exc:
            analysis = {"waiting": str(exc), "paper": None}
        result = {"started_at": now.isoformat(), "finished_at": utc_now().isoformat(), "mode": "PAPER",
                  "options_provider": suite.option_source, "problems": suite.problems, "jobs": jobs,
                  "analysis": analysis}
        _atomic_private(saved_path(paths, "worker"), json.dumps(result, allow_nan=False))
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
