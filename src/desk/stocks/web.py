"""Small loopback-only Stocks dashboard. Reads stored research; never runs a collector or broker order on a GET."""

from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import re
import secrets
import sqlite3
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager, suppress
from dataclasses import asdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field

from desk.core.clock import utc_now
from desk.core.config import LoadedConfig, load_config
from desk.core.errors import DeskError
from desk.core.paths import REPO_ROOT, DeskPaths
from desk.core.secrets import load_secrets
from desk.core.worker_lock import WorkerLock
from desk.stocks import play_history, play_tracking
from desk.stocks.backtest import position_view
from desk.stocks.backtest_job import BacktestWorker
from desk.stocks.calculator import ScenarioInput
from desk.stocks.cli import new_login
from desk.stocks.connection import LoginAttempt, connection_status, save_connection, save_sources
from desk.stocks.desks import DESK_LABELS
from desk.stocks.evidence_job import EvidenceWorker
from desk.stocks.evidence_job import document as evidence_document
from desk.stocks.hub import adjusted_bars, list_detail, option_table, research_lists
from desk.stocks.hub import home as hub_home
from desk.stocks.paper.book import PaperBook
from desk.stocks.repo import LEDGER_FILE, MARKET_FILE, RESEARCH_FILE, StocksDatabases, StocksMarket, StocksResearch
from desk.stocks.research_worker import ResearchWorker
from desk.stocks.runner import saved
from desk.stocks.runtime import UI_PROTOCOL, source_revision
from desk.storage.db import DbKind
from desk.storage.db import connect as open_database
from desk.web.app import install_browser_boundary

STATIC = Path(__file__).with_name("static")
log = logging.getLogger(__name__)
LearningAccount = Literal["uncapped_research", "reference_500k", "reference_1k", "reference_5k"]


class BacktestInput(BaseModel):
    sessions: Literal[0, 63, 126, 252] = 0
    enabled: bool = True


class LearningControl(BaseModel):
    enabled: bool


class LearningExperiment(BaseModel):
    structure: Literal["long_call", "long_put", "long_straddle"]
    budget: float = Field(gt=0, allow_inf_nan=False)
    hold_minutes: int = Field(ge=1, le=129600)
    repeat_minutes: int = Field(default=0, ge=0, le=129600)


class PlaySelection(BaseModel):
    idea_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    quantity: int = Field(default=1, strict=True, gt=0, le=2**53-1)
    request_id: str = Field(min_length=36, max_length=36)


class SelectionAction(BaseModel):
    action: Literal["cancel", "close"]


def saved_view(paths: DeskPaths, name: str) -> dict[str, Any] | None:
    try:
        return saved(paths, name)
    except DeskError:
        return None  # derived report is unavailable; System reports it, authoritative DBs are kept


@contextmanager
def reader(paths: DeskPaths, name: str) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect((paths.db_dir / name).as_uri() + "?mode=ro", uri=True, timeout=2)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def snapshot(paths: DeskPaths, loaded: LoadedConfig) -> dict[str, Any]:
    from desk.stocks.options_view import overlay

    preview = saved_view(paths, 'research')
    options_view = saved_view(paths, 'options')
    with reader(paths, RESEARCH_FILE) as conn:
        research = StocksResearch(conn)
        lists = overlay(research_lists(research, preview), options_view)
        latest = research.latest_asof()
        computed = conn.execute("SELECT MAX(computed_at) FROM stk_scores_latest").fetchone()[0]
        tracking = play_tracking.status(conn, utc_now())
    with reader(paths, MARKET_FILE) as conn:
        market = StocksMarket(conn)
        jobs = market.jobs()
        count = conn.execute("SELECT COUNT(*) FROM stk_listings WHERE active=1").fetchone()[0]
        progress = conn.execute("SELECT COUNT(*) sessions, SUM(rows) rows, MAX(day) latest, MAX(received_at) received "
                                "FROM stk_bar_days WHERE rows>0").fetchone()
        price_progress = {"sessions": progress["sessions"], "rows": progress["rows"] or 0,
                          "latest": date.fromordinal(progress["latest"]).isoformat() if progress["latest"] else None,
                          "received_at": progress["received"], "minimum_sessions": 60}
    credentials = load_secrets(paths.secrets_file)
    current_progress = saved_view(paths, "progress")
    return {"mode": "PAPER", "orders_enabled": False, "asof": str(latest) if latest else None, "lists": lists,
            "desks": DESK_LABELS, "listings": count, "jobs": jobs, "worker": saved_view(paths, "worker"),
            "report_errors": [name for name in ("worker", "research", "paper", "options")
                              if (paths.home / f"stocks-{name}.json").exists() and saved_view(paths, name) is None],
            "worker_running": WorkerLock(paths, "stocks-worker").held(), "stop": paths.stop_file.exists(),
            "connection": connection_status(paths, loaded.settings.stocks, credentials),
            "source_keys": {name: credentials.configured(name) for name in
                            ("MASSIVE_API_KEY", "SEC_CONTACT_EMAIL", "TRADIER_TOKEN", "FINNHUB_API_KEY")},
            "price_progress": price_progress,
            "options_research": options_view,
            "plays_tracking": tracking,
            "progress": current_progress,
            "research_preview": {key: (preview or {}).get(key)
                                 for key in ('asof', 'expected_asof', 'awaiting_newer_close')},
            "revision": [count, progress["sessions"], progress["received"], computed,
                         (current_progress or {}).get("updated_at"),
                         [(k, v.get("last_run_at")) for k, v in sorted(jobs.items())]]}


def detail(paths: DeskPaths, symbol: str) -> dict[str, Any]:
    if not 1 <= len(symbol) <= 12 or any(c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-" for c in symbol):
        raise HTTPException(400, "Invalid stock symbol")
    with reader(paths, RESEARCH_FILE) as conn:
        value = StocksResearch(conn).latest(symbol)
        watched = symbol in StocksResearch(conn).watchlist()
    with reader(paths, MARKET_FILE) as conn:
        rows = conn.execute("SELECT day, open, high, low, close, volume FROM stk_bars WHERE symbol=? "
                            "ORDER BY day DESC LIMIT 20000", (symbol,)).fetchall()
        bars = adjusted_bars(conn, symbol, [dict(r) for r in reversed(rows)])
        listing = conn.execute("SELECT name, exchange, kind, etf FROM stk_listings WHERE symbol=?", (symbol,)).fetchone()
        from desk.stocks.hub import INDEX_NAMES, SECTOR_NAMES

        if (listing and listing["etf"]) or symbol in INDEX_NAMES or symbol in SECTOR_NAMES:
            listing = {**(dict(listing) if listing else {"name": symbol, "exchange": "US ETF", "kind": "ETF"}), "etf": True}
            from desk.stocks.hub import etf_profile

            value = etf_profile(conn, symbol, bars, listing["name"])
        from desk.core.clock import utc_now
        from desk.stocks.stock_views import read as stock_view

        cached = stock_view(conn, symbol, utc_now())
        from desk.stocks.company_profiles import read as company_profile

        profile = company_profile(conn, symbol)
        basis = "Split-adjusted daily OHLC; no dividends"
        source = "Massive · stored bulk history"
        if cached["bars"]:
            bars = cached.pop("bars")
            basis, source = cached["basis"], cached["source"]
            from desk.stocks import calendar as cal

            for index, bar in enumerate(bars):
                bar["gap_before"] = bool(index and cal.next_trading_day(date.fromordinal(bars[index - 1]["day"]))
                                          != date.fromordinal(bar["day"]))
        else:
            cached.pop("bars")
    return {"symbol": symbol, "analysis": value, "bars": bars, "watchlisted": watched,
            "listing": dict(listing) if listing else None, "chart_basis": basis, "chart_source": source,
            "stock_data": cached, "quote": cached.get("quote"), "company_profile": profile,
            "coverage": {"first": date.fromordinal(bars[0]["day"]).isoformat() if bars else None,
                         "last": date.fromordinal(bars[-1]["day"]).isoformat() if bars else None,
                         "sessions": len(bars)}}


def paper(paths: DeskPaths) -> dict[str, Any]:
    from desk.core.clock import utc_now
    from desk.stocks import calendar as cal

    with reader(paths, LEDGER_FILE) as conn:
        book = PaperBook(conn)
        positions = [asdict(p) for p in book.positions(limit=500)]
        last = book.last_step()
        result = {"report": saved_view(paths, "paper"), "positions": positions, "decisions": book.decisions(limit=100),
                  "events": book.events(limit=100)}
        result["trades"] = [position_view(p) for p in book.positions(state=None, limit=500)]
        result["pending"] = [{"id": o.id, "account": o.account, "decision_id": o.decision_id,
                              "fill_after": o.fill_after.isoformat(), "order": o.order} for o in book.pending()][:200]
        pending = len(book.pending())
    delay = load_config(paths.config_dir).settings.stocks.refresh_after_close_minutes
    now = utc_now()
    target = cal.last_closed_session(now, timedelta(minutes=delay))
    local = cal.new_york(now)
    trading_day = cal.is_trading_day(local.date())
    market_open = trading_day and (local.hour,local.minute) >= (9,30) and now < cal.session_close(local.date())
    next_day = (local.date() if trading_day and now < cal.session_close(local.date()) + timedelta(minutes=delay)
                else cal.next_trading_day(local.date()))
    with reader(paths, MARKET_FILE) as conn:
        days = StocksMarket(conn).bar_days()
        latest = days[-1] if days else None
        price_error = StocksMarket(conn).jobs().get("prices", {}).get("last_error")
    result["session_status"] = {"last_processed": last.isoformat() if last else None,
        "latest_prices": latest.isoformat() if latest else None, "expected_close": target.isoformat(),
        "waiting_for_prices": latest is None or latest < target, "pending_orders": pending,
        "price_source_error": bool(price_error),
        "market_open": market_open,
        "next_daily_update_after": (cal.session_close(next_day) + timedelta(minutes=delay)).isoformat(),
        "explanation": "Sessions are recorded trading days, not imports or application restarts. Evidence-grade "
                       "thresholds do not block PAPER entries. Daily decisions need the next session's prices to fill."}
    return result


def create_app(paths: DeskPaths, loaded: LoadedConfig | None = None, *, worker: ResearchWorker | None = None,
               auto_start: bool = True) -> FastAPI:
    config = loaded or load_config(paths.config_dir)
    # A running Python server must not start serving a newer interface after an in-place git pull.
    assets = {path.name: path.read_bytes() for path in STATIC.iterdir() if path.is_file()}
    running_revision = source_revision()
    csrf = secrets.token_urlsafe(32)
    attempt: LoginAttempt | None = None
    login_lock = asyncio.Lock()
    collector = worker or ResearchWorker(paths)
    evidence = EvidenceWorker(paths)
    backtests = BacktestWorker(paths)
    analysis_start_lock = asyncio.Lock()
    learning_state: dict[str, Any] = {"phase": "Preparing learning workspace"}

    def login_pending() -> bool:
        return attempt is not None and (not attempt.complete.is_set() or attempt.thread.is_alive())

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        dbs = StocksDatabases.open(paths)
        from desk.stocks.play_selections import register

        try:
            previous_board = saved_view(paths, "plays")
            if previous_board:
                register(dbs.research, previous_board, utc_now())
            from desk.stocks.play_archive import recover

            recover(dbs.research,utc_now())
        finally:
            dbs.close()
        from desk.stocks import learning

        learning_conn = learning.open_book(paths)
        try:
            learning.initialize(learning_conn, utc_now())
        finally:
            learning_conn.close()
        automation_stop = asyncio.Event()

        async def learn_options() -> None:
            from desk.stocks import learning_service

            while not automation_stop.is_set():
                delay = 15
                try:
                    if collector.status().get("enabled") and learning_service.allowed(paths):
                        learning_state.update(phase="Learning is active; watching new option observations", error=None)
                        await asyncio.wait_for(learning_service.cycle(paths, config), 45)
                    else:
                        learning_state.update(phase="Learning paused; observations and losses remain saved", error=None)
                except Exception:
                    delay = 60
                    learning_state.update(phase="Learning will retry in one minute; saved records are preserved",
                                          error="Source or storage unavailable. Check Settings and data health.")
                with suppress(TimeoutError):
                    await asyncio.wait_for(automation_stop.wait(), delay)

        async def stock_data() -> None:
            from desk.stocks.stock_views import refresh

            while not automation_stop.is_set():
                if collector.status().get("enabled") and not paths.stop_file.exists():
                    with suppress(DeskError, sqlite3.Error, TimeoutError):
                        await asyncio.wait_for(refresh(paths, config), 45)
                with suppress(TimeoutError):
                    await asyncio.wait_for(automation_stop.wait(), 5)

        async def track_plays() -> None:
            from desk.stocks import play_tracking

            while not automation_stop.is_set():
                if collector.status().get("enabled") and not paths.stop_file.exists():
                    try:
                        await asyncio.wait_for(play_tracking.cycle(paths, config), play_tracking.PASS_TIMEOUT)
                    except Exception as exc:  # the next pass retries; quotes already saved are kept
                        log.warning("plays tracking pass failed", extra={"error": type(exc).__name__})
                with suppress(TimeoutError):
                    await asyncio.wait_for(automation_stop.wait(), 30)

        async def company_data() -> None:
            from desk.stocks.company_profiles import refresh

            while not automation_stop.is_set():
                if collector.status().get("enabled") and not paths.stop_file.exists():
                    try:
                        await asyncio.wait_for(refresh(paths, config), 45)
                    except Exception as exc:
                        log.warning("company profile refresh will retry", extra={"error": type(exc).__name__})
                with suppress(TimeoutError):
                    await asyncio.wait_for(automation_stop.wait(), 10)

        async def symbol_data() -> None:
            from desk.stocks.symbol_feeds import refresh

            while not automation_stop.is_set():
                if collector.status().get("enabled") and not paths.stop_file.exists():
                    try:
                        await asyncio.wait_for(refresh(paths, config), 45)
                    except Exception as exc:  # the next pass retries; saved values are kept
                        log.warning("company feed refresh will retry", extra={"error": type(exc).__name__})
                with suppress(TimeoutError):
                    await asyncio.wait_for(automation_stop.wait(), 8)

        async def maintain() -> None:
            while not automation_stop.is_set():
                await asyncio.to_thread(collector.maintain)
                if collector.status().get("enabled") and not paths.stop_file.exists():
                    async with analysis_start_lock:
                        if not evidence.status()["running"]:
                            await asyncio.to_thread(backtests.maintain)
                else:
                    await asyncio.to_thread(backtests.pause_automatic)
                with suppress(TimeoutError):
                    await asyncio.wait_for(automation_stop.wait(), 5)

        if auto_start:
            await asyncio.to_thread(collector.maintain)
        automation = asyncio.create_task(maintain()) if auto_start else None
        stock_automation = asyncio.create_task(stock_data()) if auto_start else None
        learning_automation = asyncio.create_task(learn_options()) if auto_start else None
        tracking_automation = asyncio.create_task(track_plays()) if auto_start else None
        company_automation = asyncio.create_task(company_data()) if auto_start else None
        feeds_automation = asyncio.create_task(symbol_data()) if auto_start else None
        try:
            yield
        finally:
            if automation is not None:
                automation_stop.set()
                await asyncio.gather(automation, return_exceptions=True)
            if stock_automation is not None:
                stock_automation.cancel()
                await asyncio.gather(stock_automation, return_exceptions=True)
            if learning_automation is not None:
                learning_automation.cancel()
                await asyncio.gather(learning_automation, return_exceptions=True)
            if tracking_automation is not None:
                tracking_automation.cancel()
                await asyncio.gather(tracking_automation, return_exceptions=True)
            if company_automation is not None:
                company_automation.cancel()
                await asyncio.gather(company_automation, return_exceptions=True)
            if feeds_automation is not None:
                feeds_automation.cancel()
                await asyncio.gather(feeds_automation, return_exceptions=True)
            await asyncio.to_thread(collector.stop, pause=False)
            await asyncio.to_thread(evidence.close)
            await asyncio.to_thread(backtests.close)
            if attempt is not None:
                await asyncio.to_thread(attempt.stop)

    app = FastAPI(title="KESTREL Stocks PAPER", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    install_browser_boundary(app, csrf)

    @app.exception_handler(DeskError)
    async def desk_error(_: Request, exc: DeskError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.exception_handler(sqlite3.Error)
    async def storage_error(_: Request, exc: sqlite3.Error) -> JSONResponse:
        return JSONResponse({"detail": "Stored data is temporarily busy or unavailable. Use Evidence → Check data health "
                                       "and retry. Existing data was preserved."}, status_code=503)

    @app.get("/")
    def home() -> Response:
        return Response(assets["index.html"], media_type="text/html")

    @app.get("/assets/{name}")
    def asset(name: str) -> Response:
        if name not in assets:
            raise HTTPException(404, "Asset not found")
        return Response(assets[name], media_type=mimetypes.guess_type(name)[0] or "application/octet-stream")

    @app.get("/connection-guide")
    def guide() -> FileResponse:
        return FileResponse(REPO_ROOT / "docs" / "SCHWAB.md", media_type="text/plain")

    @app.get("/api/stocks/session")
    def session() -> dict[str, Any]:
        return {"csrf_token": csrf, "ui_protocol": UI_PROTOCOL}

    @app.get("/api/stocks/runtime")
    def runtime() -> dict[str, Any]:
        installed = source_revision()
        return {"application": "kestrel-stocks", "protocol": UI_PROTOCOL, "running_revision": running_revision,
                "installed_revision": installed, "restart_required": installed != running_revision}

    @app.get("/api/stocks/snapshot")
    def overview() -> dict[str, Any]:
        return {**snapshot(paths, config), "collection": collector.status()}

    @app.get("/api/stocks/plays")
    def plays_view() -> dict[str, Any]:
        value = saved_view(paths, "plays")
        if value is None:
            return {"version": 1, "asof": None, "categories": [],
                    "message": "Plays are built by the Stocks worker after each options pass. Collection starts with "
                               "PAPER (System), and needs an options connection (Settings)."}
        from desk.stocks.play_selections import idea_id

        for category in value.get("categories", []):
            for play in [*category.get("plays", []), *category.get("budget_plays", [])]:
                play["idea_id"] = idea_id(value["asof"], category["id"], str(value.get("method", "unknown")), play)
        return value

    @app.get("/api/stocks/plays/idea/{ident}")
    def play_idea(ident: str) -> dict[str, Any]:
        from desk.stocks.play_selections import view

        with reader(paths, RESEARCH_FILE) as conn:
            return view(conn, ident, utc_now())

    @app.post("/api/stocks/plays/select")
    def select_play(inputs: PlaySelection) -> dict[str, Any]:
        from desk.stocks.play_selections import confirm

        conn = open_database(paths.db_dir / RESEARCH_FILE, DbKind.STOCKS_RESEARCH)
        try:
            return confirm(conn, inputs.idea_id, inputs.quantity, inputs.request_id, utc_now())
        finally:
            conn.close()

    @app.get("/api/stocks/plays/mine")
    def my_plays(offset: int = Query(default=0, ge=0), limit: int = Query(default=20, ge=1, le=100)) -> dict[str, Any]:
        from desk.stocks.play_selections import listing

        with reader(paths, RESEARCH_FILE) as conn:
            return listing(conn, utc_now(), offset, limit)

    @app.get("/api/stocks/plays/mine/{ident}")
    def my_play(ident: str) -> dict[str, Any]:
        from desk.stocks.play_selections import view

        with reader(paths, RESEARCH_FILE) as conn:
            return view(conn, ident, utc_now(), personal=True)

    @app.post("/api/stocks/plays/mine/{ident}/action")
    def my_play_action(ident: str, inputs: SelectionAction) -> dict[str, Any]:
        from desk.stocks.play_selections import action

        conn = open_database(paths.db_dir / RESEARCH_FILE, DbKind.STOCKS_RESEARCH)
        try:
            return action(conn, ident, inputs.action, utc_now())
        finally:
            conn.close()

    @app.get("/api/stocks/plays/sessions")
    def plays_sessions() -> dict[str, Any]:
        from desk.stocks import play_archive

        with reader(paths, RESEARCH_FILE) as conn:
            return play_archive.sessions(conn)

    @app.get("/api/stocks/plays/history")
    def plays_history(when: str = Query(default="latest",
                                        pattern=r"^(latest|yesterday|week|month|quarter|year|\d{4}-\d{2}-\d{2})$"),
                      scope: Literal["all","closing"] = "all"
                      ) -> dict[str, Any]:
        from desk.stocks import play_archive

        now = utc_now()
        try:
            with reader(paths, RESEARCH_FILE) as conn:
                session = (play_archive.resolve(conn, when, now) if scope == "all"
                           else play_history.resolve(conn, when, now))
                if session is None:
                    return {"version": play_history.VERSION, "session": None, "categories": [], "summary": None,
                            "message": "No saved plays yet. Every automatically identified play is archived; "
                                       "manual confirmations are not required."}
                return (play_archive.session_view(conn,session,now) if scope == "all" else
                        {**play_history.session_view(conn,session,now),"scope":"closing"})
        except ValueError as exc:
            raise HTTPException(400, "Choose a valid date") from exc

    @app.get("/api/stocks/plays/play")
    def plays_play(session: date, category: str = Query(pattern=r"^[a-z_]{1,40}$"),
                   rank: int = Query(ge=1, le=1000)) -> dict[str, Any]:
        with reader(paths, RESEARCH_FILE) as conn:
            value = play_history.play_view(conn, session, category, rank, utc_now())
        if not value["found"]:
            raise HTTPException(404, "That play is not in the record")
        return value

    @app.get("/api/stocks/plays/performance")
    def plays_performance() -> dict[str, Any]:
        value = saved_view(paths, "plays-performance")
        if value is None:
            return {"version": 1, "categories": [], "generated_at": None,
                    "message": "Performance is built after the close once plays have been marked. The first numbers "
                               "appear the evening after the first recorded session."}
        return value

    @app.get("/api/stocks/plays/tracking")
    def plays_tracking() -> dict[str, Any]:
        with reader(paths, RESEARCH_FILE) as conn:
            return play_tracking.status(conn, utc_now())

    @app.get("/api/stocks/home")
    def research_home() -> dict[str, Any]:
        with reader(paths, MARKET_FILE) as market, reader(paths, RESEARCH_FILE) as research:
            return hub_home(market, research, preview=saved_view(paths, "research"), options_view=saved_view(paths, "options"))

    @app.get("/api/stocks/lists/{list_id}")
    def research_list(list_id: str) -> dict[str, Any]:
        from desk.stocks.options_view import overlay

        preview, options_view = saved_view(paths, "research"), saved_view(paths, "options")
        with reader(paths, MARKET_FILE) as market, reader(paths, RESEARCH_FILE) as research:
            lists = overlay(research_lists(StocksResearch(research), preview), options_view)
            value = list_detail(market, research, lists, list_id)
        if value is None:
            raise HTTPException(404, "No such research list")
        return value

    @app.get("/api/stocks/explore")
    def explore() -> dict[str, Any]:
        from desk.core.clock import utc_now
        from desk.stocks.explore import option_setups, price_volume

        with reader(paths, MARKET_FILE) as market, reader(paths, RESEARCH_FILE) as research:
            return {"option_setups": option_setups(market, utc_now()), "price_volume": price_volume(research)}

    @app.post("/api/stocks/research/start")
    async def start_research() -> dict[str, Any]:
        return await asyncio.to_thread(collector.start)

    @app.post("/api/stocks/research/pause")
    async def pause_research() -> dict[str, Any]:
        return await asyncio.to_thread(collector.stop)

    @app.post("/api/stocks/sources")
    async def configure_sources(request: Request) -> dict[str, Any]:
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 8192:
                raise HTTPException(413, "Settings request too large")
        try:
            data = json.loads(raw)
            if (not isinstance(data, dict) or set(data) - {"massive_key", "sec_contact", "tradier_token", "finnhub_key"}
                    or any(not isinstance(v, str) for v in data.values())):
                raise ValueError
        except (ValueError, TypeError) as exc:
            raise HTTPException(400, "Invalid source settings") from exc
        await asyncio.to_thread(save_sources, paths, **data)
        credentials = load_secrets(paths.secrets_file)
        return {"saved": True, "source_keys": {name: credentials.configured(name) for name in
                                               ("MASSIVE_API_KEY", "SEC_CONTACT_EMAIL", "TRADIER_TOKEN", "FINNHUB_API_KEY")}}

    @app.get("/api/stocks/stock/{symbol}")
    def stock(symbol: str) -> dict[str, Any]:
        return detail(paths, symbol.upper())

    @app.get("/api/stocks/stock/{symbol}/options")
    def options(symbol: str, expiration: date | None = None, limit: int = Query(default=40, ge=2, le=1000),
                idea_id: str | None = Query(default=None, pattern=r"^[a-f0-9]{32}$")) -> dict[str, Any]:
        if not 1 <= len(symbol) <= 12:
            raise HTTPException(400, "Invalid stock symbol")
        with reader(paths, MARKET_FILE) as conn:
            if idea_id is not None:
                from desk.stocks.play_selections import calculator_chain, idea

                with reader(paths, RESEARCH_FILE) as research:
                    chosen = expiration or date.fromisoformat(idea(research, idea_id)["play"]["expiration"])
                    return calculator_chain(conn, research, idea_id, symbol.upper(), chosen, limit)
            return option_table(conn, symbol.upper(), expiration=expiration, limit=limit)

    @app.post("/api/stocks/stock/{symbol}/calculator")
    def calculator(symbol: str, inputs: ScenarioInput) -> dict[str, Any]:
        from desk.core.clock import utc_now
        from desk.stocks.calculator import calculate
        from desk.stocks.contracts import OptionQuote

        if not 1 <= len(symbol) <= 12:
            raise HTTPException(400, "Invalid stock symbol")
        with reader(paths, MARKET_FILE) as conn:
            quotes = {}
            first = None
            try:
                expirations = {date(2000 + int(leg.occ[-15:-13]), int(leg.occ[-13:-11]), int(leg.occ[-11:-9]))
                               for leg in inputs.legs}
            except ValueError as exc:
                raise HTTPException(400, "Invalid contract expiration") from exc
            for expiration in sorted(expirations):
                if inputs.idea_id:
                    from desk.stocks.play_selections import calculator_chain

                    with reader(paths, RESEARCH_FILE) as research:
                        view = calculator_chain(conn, research, inputs.idea_id, symbol.upper(), expiration)
                else:
                    view = option_table(conn, symbol.upper(), expiration=expiration, limit=1000,
                                        focus=tuple(leg.occ for leg in inputs.legs))
                first = first or view
                for q in view["contracts"]:
                    quotes[q["occ"]] = OptionQuote(q["occ"], symbol.upper(), q["kind"], q["strike"], expiration,
                        q["bid"], q["ask"], q["last"], q["volume"], q["open_interest"], None,
                        datetime.fromisoformat(q["quoted_at"]) if q["quoted_at"] else None)
            if not first or not first.get("asof") or not first.get("underlying_price"):
                raise HTTPException(400, "A saved chain and underlying price are needed. Open a collected options chain.")
            return calculate(inputs, quotes, first["underlying_price"], date.fromisoformat(first["asof"]),
                             utc_now().date())

    @app.post("/api/stocks/stock/{symbol}/history/request")
    def request_history(symbol: str) -> dict[str, Any]:
        from desk.core.clock import utc_now
        from desk.stocks.hub import INDEX_NAMES, SECTOR_NAMES
        from desk.stocks.stock_views import request as request_view

        symbol = symbol.upper()
        with reader(paths, MARKET_FILE) as conn:
            if (symbol not in INDEX_NAMES and symbol not in SECTOR_NAMES
                    and conn.execute("SELECT 1 FROM stk_listings WHERE symbol=? AND active=1 AND test=0",
                                     (symbol,)).fetchone() is None):
                raise HTTPException(404, "No active listing for this symbol")
        conn = open_database(paths.db_dir / MARKET_FILE, DbKind.STOCKS_MARKET)
        try:
            request_view(conn, symbol, utc_now())
            from desk.stocks.company_profiles import request as request_profile
            from desk.stocks.symbol_feeds import request as request_feeds

            request_profile(conn, symbol, utc_now())
            request_feeds(conn, symbol, utc_now())
        finally:
            conn.close()
        return {"queued": True, "enabled": collector.status().get("enabled", False),
                "note": "Schwab quotes, 1-minute intraday candles and up to 20 years of daily history; cached data is preserved."}

    @app.get("/api/stocks/stock/{symbol}/profile")
    def stock_profile(symbol: str) -> dict[str, Any]:
        """Everything beside the chart: company facts, statistics, financials, short interest, insiders, dividends,
        earnings, news, lists and peers. Each section names its source; missing data stays missing."""
        from desk.stocks import stock_page, symbol_feeds
        from desk.stocks.company_profiles import read as company_profile
        from desk.stocks.lists import LISTS
        from desk.stocks.stock_views import read as stock_view

        symbol = symbol.upper()
        if not re.fullmatch(r"[A-Z0-9.\-]{1,12}", symbol):
            raise HTTPException(404, "No such symbol")
        now = utc_now()
        configured = load_secrets(paths.secrets_file).configured("FINNHUB_API_KEY")
        with reader(paths, MARKET_FILE) as market, reader(paths, RESEARCH_FILE) as research:
            from desk.stocks.hub import INDEX_NAMES, SECTOR_NAMES

            if (symbol not in INDEX_NAMES and symbol not in SECTOR_NAMES and
                    market.execute("SELECT 1 FROM stk_listings WHERE symbol=?", (symbol,)).fetchone() is None):
                raise HTTPException(404, "No listing for this symbol")
            return stock_page.build(market, research, symbol, now, detail=StocksResearch(research).latest(symbol),
                                    quote=stock_view(market, symbol, now).get("quote"),
                                    profile=company_profile(market, symbol), feeds=symbol_feeds.read(market, symbol),
                                    list_titles={spec.id: spec.title for spec in LISTS},
                                    finnhub_configured=configured,
                                    has_logo=symbol_feeds.logo(market, symbol) is not None)

    @app.get("/api/stocks/stock/{symbol}/logo")
    def stock_logo(symbol: str) -> Response:
        from desk.stocks.symbol_feeds import logo

        with reader(paths, MARKET_FILE) as market:
            found = logo(market, symbol.upper())
        if found is None:
            raise HTTPException(404, "No company icon saved")
        return Response(found[1], media_type=found[0], headers={
            "Cache-Control": "private, max-age=86400", "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'"})

    @app.post("/api/stocks/stock/{symbol}/profile/request")
    def request_profile(symbol: str) -> dict[str, Any]:
        from desk.stocks.company_profiles import request

        conn = open_database(paths.db_dir / MARKET_FILE, DbKind.STOCKS_MARKET)
        try:
            request(conn, symbol.upper(), utc_now())
            conn.execute("UPDATE stk_company_profiles SET updated_at=NULL WHERE cik IN "
                         "(SELECT cik FROM stk_listings WHERE symbol=? AND etf=0)", (symbol.upper(),))
        finally:
            conn.close()
        return {"queued": True, "note": "SEC metadata and the annual business section refresh in the background. "
                "Failed requests retry after ten minutes; existing information is preserved."}

    @app.post("/api/stocks/stock/{symbol}/options/request")
    def request_options(symbol: str) -> dict[str, Any]:
        from desk.core.clock import utc_now
        from desk.stocks.hub import INDEX_NAMES, SECTOR_NAMES

        symbol = symbol.upper()
        with reader(paths, MARKET_FILE) as conn:
            if (symbol not in INDEX_NAMES and symbol not in SECTOR_NAMES
                    and conn.execute("SELECT 1 FROM stk_listings WHERE symbol=? AND active=1 AND test=0",
                                     (symbol,)).fetchone() is None):
                raise HTTPException(404, "No active listing for this symbol")
        conn = open_database(paths.db_dir / MARKET_FILE, DbKind.STOCKS_MARKET)
        try:
            StocksMarket(conn).request_options(symbol, utc_now())
        finally:
            conn.close()
        return {"queued": True, "symbol": symbol, "running": collector.status()["running"],
                "note": "Market-data research only. Processed by the next available bounded collection pass."}

    @app.post("/api/stocks/watch/{symbol}")
    def watch(symbol: str, action: Literal["add", "remove"] = "add") -> dict[str, Any]:
        from desk.core.clock import utc_now

        symbol = symbol.upper()
        with reader(paths, MARKET_FILE) as conn:
            if conn.execute("SELECT 1 FROM stk_listings WHERE symbol=?", (symbol,)).fetchone() is None:
                raise HTTPException(404, "No stored listing for this symbol")
        conn = open_database(paths.db_dir / RESEARCH_FILE, DbKind.STOCKS_RESEARCH)
        try:
            StocksResearch(conn).watch(symbol, utc_now(), add=action == "add")
        finally:
            conn.close()
        return {"symbol": symbol, "watchlisted": action == "add"}

    @app.get("/api/stocks/paper")
    def paper_record() -> dict[str, Any]:
        return paper(paths)

    @app.get("/api/stocks/learning")
    def learning_report() -> dict[str, Any]:
        from desk.stocks import learning, learning_service

        try:
            prefs = learning_service.preferences(paths)
        except DeskError:
            prefs = {"enabled": False, "error": "Save Start/Pause Learning to repair preferences"}
        with reader(paths, learning.FILE) as conn:
            return {**learning.report(conn), "control": prefs, "worker": learning_state,
                    "paper_enabled": collector.status().get("enabled"), "stop": paths.stop_file.exists()}

    @app.post("/api/stocks/learning/control")
    def learning_control(inputs: LearningControl) -> dict[str, Any]:
        from desk.stocks.learning_service import control

        return control(paths, inputs.enabled)

    @app.post("/api/stocks/learning/experiments")
    def learning_experiment(inputs: LearningExperiment) -> dict[str, Any]:
        from desk.core.clock import utc_now
        from desk.stocks import learning

        conn = learning.open_book(paths)
        try:
            ident = learning.add_experiment(conn, utc_now(), inputs.structure, inputs.budget,
                                            inputs.hold_minutes, inputs.repeat_minutes)
            return {"id": ident, "registered": True}
        finally:
            conn.close()

    @app.get("/api/stocks/learning/trades")
    def learning_trades(account: LearningAccount = "uncapped_research",
                        state: Literal["OPEN", "CLOSED"] = "OPEN", offset: int = Query(default=0, ge=0),
                        limit: int = Query(default=12, ge=1, le=100)) -> dict[str, Any]:
        from desk.stocks import learning

        with reader(paths, learning.FILE) as conn:
            book = PaperBook(conn)
            rows = book.positions(account, state, limit=limit, offset=offset)
            values = []
            for position in rows:
                reason = conn.execute("SELECT reasons FROM stk_decisions WHERE id=?", (position.decision_id,)).fetchone()
                values.append({**position_view(position), "entry_reason": reason[0] if reason else None})
            return {"positions": values, "total": conn.execute(
                "SELECT COUNT(*) FROM stk_positions WHERE account=? AND state=?", (account, state)).fetchone()[0],
                "decisions": book.decisions(account, limit=100)}

    @app.get("/api/stocks/learning/orders")
    def learning_orders(account: LearningAccount = "uncapped_research",
                        offset: int = Query(default=0, ge=0), limit: int = Query(default=12, ge=1, le=100)) -> dict[str, Any]:
        from desk.stocks import learning

        with reader(paths, learning.FILE) as conn:
            rows = conn.execute(
                "SELECT p.id,p.account,p.state,p.created_at,p.note,json_extract(p.order_json,'$.symbol') symbol,"
                "i.desired_units,COALESCE(SUM(f.units),0) filled_units FROM stk_pending p "
                "JOIN stk_learning_intents i ON i.order_id=p.id LEFT JOIN stk_learning_fills f ON f.order_id=p.id "
                "WHERE p.account=? GROUP BY p.id ORDER BY p.id DESC LIMIT ? OFFSET ?", (account,limit,offset))
            return {"orders": [dict(r) for r in rows], "total": conn.execute(
                "SELECT COUNT(*) FROM stk_pending WHERE account=?", (account,)).fetchone()[0]}

    @app.get("/api/stocks/paper/equity")
    def paper_equity(account: str = Query(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_]+$")) -> dict[str, Any]:
        with reader(paths, LEDGER_FILE) as conn:
            rows = PaperBook(conn).equity(account)
            return {"curve": [{"day": day.isoformat(), "equity": value} for day, _, _, value in rows]}

    @app.get("/api/stocks/paper/trades")
    def paper_trades(account: str = Query(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_]+$"),
                     state: Literal["OPEN", "CLOSED"] = "OPEN", offset: int = Query(default=0, ge=0, le=10000000),
                     limit: int = Query(default=60, ge=1, le=100),
                     symbol: str = Query(default="", max_length=12, pattern=r"^[A-Z0-9.-]*$")) -> dict[str, Any]:
        with reader(paths, LEDGER_FILE) as conn:
            book = PaperBook(conn)
            rows = book.positions(account, state, limit=limit, offset=offset, symbol=symbol)
            total = conn.execute("SELECT COUNT(*) FROM stk_positions WHERE account=? AND state=? AND symbol LIKE ?",
                                 (account,state,symbol + "%")).fetchone()[0]
            reasons = {r["id"]: r["reasons"] for r in conn.execute(
                "SELECT d.id,d.reasons FROM stk_decisions d JOIN stk_positions p ON p.decision_id=d.id "
                "WHERE p.account=? AND p.state=? AND p.symbol LIKE ? ORDER BY "
                "CASE WHEN p.state='CLOSED' THEN p.closed_at ELSE p.opened_at END DESC,p.id DESC LIMIT ? OFFSET ?",
                (account,state,symbol + "%",limit,offset))}
            return {"trades": [{**position_view(p), "entry_reason": reasons.get(p.decision_id)} for p in rows],
                    "total": total, "offset": offset, "limit": limit, "decisions": book.decisions(account,limit=200),
                    "pending": [{"id": o.id, "account": o.account, "decision_id": o.decision_id,
                                 "fill_after": o.fill_after.isoformat(), "order": o.order} for o in book.pending(account)]}

    @app.get("/api/stocks/backtest")
    def backtest_record() -> dict[str, Any]:
        return {"job": backtests.status(), "report": evidence_document(paths, "report", kind="backtest")}

    @app.get("/api/stocks/backtest/session")
    def replay_session(day: date, run_id: str = Query(pattern=r"^[a-f0-9]{32}$"),
                       account: str = Query(pattern=r"^[A-Za-z0-9_]+$", max_length=100),
                       offset: int = Query(default=0, ge=0), closed: bool = False,
                       symbol: str = Query(default="", max_length=12, pattern=r"^[A-Z0-9.-]*$")) -> dict[str, Any]:
        from desk.stocks.replay_archive import session

        return session(paths, run_id, account, day, offset, closed, symbol)

    @app.post("/api/stocks/backtest/start")
    async def start_backtest(inputs: BacktestInput) -> dict[str, Any]:
        async with analysis_start_lock:
            if evidence.status()["running"]:
                raise HTTPException(409, "Evidence evaluation is running. Finish or cancel it in Evidence "
                                        "before replaying history.")
            return await asyncio.to_thread(backtests.start, inputs.sessions)

    @app.post("/api/stocks/backtest/automatic")
    def automatic_backtest(inputs: BacktestInput) -> dict[str, Any]:
        return backtests.automatic(inputs.enabled, inputs.sessions)

    @app.post("/api/stocks/backtest/cancel")
    async def cancel_backtest() -> dict[str, Any]:
        return await asyncio.to_thread(backtests.cancel)

    @app.get("/api/stocks/proof")
    def proof() -> dict[str, Any]:
        with reader(paths, RESEARCH_FILE) as conn:
            research = StocksResearch(conn)
            return {"validation": evidence_document(paths, "report") or research.latest_validation(),
                    "job": evidence.status(), "runs": [dict(r) for r in research.runs(limit=20)]}

    @app.post("/api/stocks/proof/start")
    async def start_evaluation() -> dict[str, Any]:
        async with analysis_start_lock:
            if backtests.status()["running"]:
                raise HTTPException(409, "Historical replay is running. Finish or cancel it on the Paper page before evaluating.")
            return await asyncio.to_thread(evidence.start)

    @app.post("/api/stocks/proof/cancel")
    async def cancel_evaluation() -> dict[str, Any]:
        return await asyncio.to_thread(evidence.cancel)

    @app.get("/api/stocks/proof/health")
    def evidence_health() -> dict[str, Any]:
        from desk.stocks.diagnostics import diagnose

        return diagnose(paths)

    @app.get("/api/stocks/connection")
    def connection() -> dict[str, Any]:
        return {**connection_status(paths, config.settings.stocks, load_secrets(paths.secrets_file)),
                "login": {"pending": login_pending(),
                          "error": attempt.error if attempt is not None else None}}

    @app.post("/api/stocks/connection/test")
    async def test_connection() -> dict[str, Any]:
        from desk.stocks.suite import build_stocks_suite

        dbs = StocksDatabases.open(paths)
        try:
            suite = build_stocks_suite(paths, config, load_secrets(paths.secrets_file), dbs.runtime)
        except BaseException:
            dbs.close()
            raise
        try:
            quotes = await suite.options.quotes(["SPY"])
            message = ("Market-data access works. A SPY quote was received; no order was placed." if quotes else
                       "The provider returned no SPY quote. Review its connection and data entitlement.")
            diagnostics = {}
            if quotes and suite.option_source == "schwab":
                from desk.core.clock import utc_now
                from desk.stocks.activity import quote_session

                session = quote_session(utc_now())
                expiries = [d for d in await suite.options.expirations("SPY") if 14 <= (d-session).days <= 60]
                if expiries:
                    await suite.options.chain("SPY", min(expiries, key=lambda d: abs((d-session).days-30)))
                    diagnostics = getattr(suite.options, "chain_diagnostics", {}).get("SPY", {})
                    if not diagnostics.get("positive_volume_contracts"):
                        message += (" Standard chain quotes were checked, but the provider reports no positive day volume "
                                    "in this sample. Quote-based research is available; activity rankings need volume.")
            return {"provider": suite.option_source, "received": bool(quotes), "message": message,
                    "chain_diagnostics": diagnostics}
        finally:
            await suite.aclose()
            dbs.close()

    @app.post("/api/stocks/connection")
    async def configure(request: Request) -> dict[str, Any]:
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 8192:
                raise HTTPException(413, "Settings request too large")
        try:
            data = json.loads(raw)
            if (not isinstance(data, dict) or set(data) - {"app_key", "app_secret", "callback_url", "options_provider"}
                    or any(not isinstance(v, str) for v in data.values())):
                raise ValueError
        except (ValueError, TypeError) as exc:
            raise HTTPException(400, "Invalid connection settings") from exc
        async with login_lock:
            if login_pending():
                raise HTTPException(409, "Use Cancel pending authorization before correcting and saving these settings")
            lock = WorkerLock(paths, "stocks-worker")
            if lock.held():
                owned = collector.status()["owned"]
                raise HTTPException(409, "Pause collector to save in Settings, then wait for the collector to stop. "
                                    "Keep the dashboard terminal open." if owned else
                                    "A collector is running in another terminal. Stop only desk stocks collect, "
                                    "desk stocks paper, or desk stocks refresh in that terminal. "
                                    "Keep desk stocks dashboard running; then try Save privately again.")
            with lock:
                await asyncio.to_thread(save_connection, paths, app_key=data.get("app_key", ""),
                                        app_secret=data.get("app_secret", ""), callback=data.get("callback_url", ""),
                                        provider=data.get("options_provider", ""))
        return connection()

    @app.post("/api/stocks/connect")
    async def connect() -> dict[str, str]:
        nonlocal attempt
        async with login_lock:
            if login_pending():
                raise HTTPException(409, "A login is already pending; finish it or use Cancel pending authorization")
            if attempt is not None:
                await asyncio.to_thread(attempt.stop)
            status = connection_status(paths, config.settings.stocks, load_secrets(paths.secrets_file))
            if status["options_provider"] != "schwab":
                raise HTTPException(400, "Choose Schwab market data and Save privately before starting Schwab authorization")
            attempt = await asyncio.to_thread(new_login, paths, status["callback_url"])
            return {"authorize_url": attempt.start(), "expires_in_seconds": "300"}

    @app.post("/api/stocks/connect/cancel")
    async def cancel_connect() -> dict[str, Any]:
        nonlocal attempt
        async with login_lock:
            if attempt is not None:
                await asyncio.to_thread(attempt.stop)
                if attempt.thread.is_alive():
                    raise HTTPException(409, "The current Schwab request is finishing. Retry cancellation shortly.")
                attempt = None
        return connection()

    return app
