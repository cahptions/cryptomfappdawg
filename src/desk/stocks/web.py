"""Small loopback-only Stocks dashboard. Reads stored research; never runs a collector or broker order on a GET."""

from __future__ import annotations

import asyncio
import json
import secrets
import sqlite3
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager, suppress
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from desk.core.config import LoadedConfig, load_config
from desk.core.errors import DeskError
from desk.core.paths import REPO_ROOT, DeskPaths
from desk.core.secrets import load_secrets
from desk.core.worker_lock import WorkerLock
from desk.stocks.cli import new_login
from desk.stocks.connection import LoginAttempt, connection_status, save_connection, save_sources
from desk.stocks.desks import DESK_LABELS
from desk.stocks.hub import adjusted_bars, option_table, research_lists
from desk.stocks.hub import home as hub_home
from desk.stocks.paper.book import PaperBook
from desk.stocks.repo import LEDGER_FILE, MARKET_FILE, RESEARCH_FILE, StocksDatabases, StocksMarket, StocksResearch
from desk.stocks.research_worker import ResearchWorker
from desk.stocks.runner import saved
from desk.storage.db import DbKind
from desk.storage.db import connect as open_database
from desk.web.app import install_browser_boundary

STATIC = Path(__file__).with_name("static")


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
                            ("MASSIVE_API_KEY", "SEC_CONTACT_EMAIL", "TRADIER_TOKEN")},
            "price_progress": price_progress,
            "options_research": options_view,
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
                            "ORDER BY day DESC LIMIT 400", (symbol,)).fetchall()
        bars = adjusted_bars(conn, symbol, [dict(r) for r in reversed(rows)])
        listing = conn.execute("SELECT name, exchange, kind, etf FROM stk_listings WHERE symbol=?", (symbol,)).fetchone()
        from desk.stocks.hub import INDEX_NAMES, SECTOR_NAMES

        if (listing and listing["etf"]) or symbol in INDEX_NAMES or symbol in SECTOR_NAMES:
            listing = {**(dict(listing) if listing else {"name": symbol, "exchange": "US ETF", "kind": "ETF"}), "etf": True}
            from desk.stocks.hub import etf_profile

            value = etf_profile(conn, symbol, bars, listing["name"])
    return {"symbol": symbol, "analysis": value, "bars": bars, "watchlisted": watched,
            "listing": dict(listing) if listing else None, "chart_basis": "Split-adjusted daily OHLC; no dividends"}


def paper(paths: DeskPaths) -> dict[str, Any]:
    with reader(paths, LEDGER_FILE) as conn:
        book = PaperBook(conn)
        positions = [asdict(p) for p in book.positions()][:500]
        return {"report": saved_view(paths, "paper"), "positions": positions, "decisions": book.decisions(limit=100),
                "events": book.events(limit=100)}


def create_app(paths: DeskPaths, loaded: LoadedConfig | None = None, *, worker: ResearchWorker | None = None,
               auto_start: bool = True) -> FastAPI:
    config = loaded or load_config(paths.config_dir)
    csrf = secrets.token_urlsafe(32)
    attempt: LoginAttempt | None = None
    login_lock = asyncio.Lock()
    collector = worker or ResearchWorker(paths)

    def login_pending() -> bool:
        return attempt is not None and (not attempt.complete.is_set() or attempt.thread.is_alive())

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        dbs = StocksDatabases.open(paths)
        dbs.close()
        automation_stop = asyncio.Event()

        async def maintain() -> None:
            while not automation_stop.is_set():
                await asyncio.to_thread(collector.maintain)
                with suppress(TimeoutError):
                    await asyncio.wait_for(automation_stop.wait(), 5)

        if auto_start:
            await asyncio.to_thread(collector.maintain)
        automation = asyncio.create_task(maintain()) if auto_start else None
        try:
            yield
        finally:
            if automation is not None:
                automation_stop.set()
                await asyncio.gather(automation, return_exceptions=True)
            await asyncio.to_thread(collector.stop, pause=False)
            if attempt is not None:
                await asyncio.to_thread(attempt.stop)

    app = FastAPI(title="KESTREL Stocks PAPER", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    install_browser_boundary(app, csrf)

    @app.exception_handler(DeskError)
    async def desk_error(_: Request, exc: DeskError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.exception_handler(sqlite3.Error)
    async def storage_error(_: Request, exc: sqlite3.Error) -> JSONResponse:
        return JSONResponse({"detail": "Stocks database is busy or unavailable. Run desk stocks diagnose; "
                                       "existing data was preserved."}, status_code=503)

    @app.get("/")
    def home() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    @app.get("/connection-guide")
    def guide() -> FileResponse:
        return FileResponse(REPO_ROOT / "docs" / "SCHWAB.md", media_type="text/plain")

    @app.get("/api/stocks/session")
    def session() -> dict[str, str]:
        return {"csrf_token": csrf}

    @app.get("/api/stocks/snapshot")
    def overview() -> dict[str, Any]:
        return {**snapshot(paths, config), "collection": collector.status()}

    @app.get("/api/stocks/home")
    def research_home() -> dict[str, Any]:
        with reader(paths, MARKET_FILE) as market, reader(paths, RESEARCH_FILE) as research:
            return hub_home(market, research, preview=saved_view(paths, "research"), options_view=saved_view(paths, "options"))

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
            if (not isinstance(data, dict) or set(data) - {"massive_key", "sec_contact", "tradier_token"}
                    or any(not isinstance(v, str) for v in data.values())):
                raise ValueError
        except (ValueError, TypeError) as exc:
            raise HTTPException(400, "Invalid source settings") from exc
        await asyncio.to_thread(save_sources, paths, **data)
        credentials = load_secrets(paths.secrets_file)
        return {"saved": True, "source_keys": {name: credentials.configured(name) for name in
                                               ("MASSIVE_API_KEY", "SEC_CONTACT_EMAIL", "TRADIER_TOKEN")}}

    @app.get("/api/stocks/stock/{symbol}")
    def stock(symbol: str) -> dict[str, Any]:
        return detail(paths, symbol.upper())

    @app.get("/api/stocks/stock/{symbol}/options")
    def options(symbol: str, expiration: date | None = None) -> dict[str, Any]:
        if not 1 <= len(symbol) <= 12:
            raise HTTPException(400, "Invalid stock symbol")
        with reader(paths, MARKET_FILE) as conn:
            return option_table(conn, symbol.upper(), expiration=expiration)

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

    @app.get("/api/stocks/proof")
    def proof() -> dict[str, Any]:
        with reader(paths, RESEARCH_FILE) as conn:
            research = StocksResearch(conn)
            return {"validation": research.latest_validation(), "runs": [dict(r) for r in research.runs(limit=20)]}

    @app.get("/api/stocks/connection")
    def connection() -> dict[str, Any]:
        return {**connection_status(paths, config.settings.stocks, load_secrets(paths.secrets_file)),
                "login": {"pending": login_pending(),
                          "error": attempt.error if attempt is not None else None}}

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

    app.mount("/assets", StaticFiles(directory=STATIC), name="stocks-assets")
    return app
