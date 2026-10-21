"""Small loopback-only Stocks dashboard. Reads stored research; never runs a collector or broker order on a GET."""

from __future__ import annotations

import asyncio
import json
import secrets
import sqlite3
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from desk.core.config import LoadedConfig, load_config
from desk.core.errors import DeskError
from desk.core.paths import REPO_ROOT, DeskPaths
from desk.core.secrets import load_secrets
from desk.core.worker_lock import WorkerLock
from desk.stocks.cli import new_login
from desk.stocks.connection import LoginAttempt, connection_status, save_connection
from desk.stocks.desks import DESK_LABELS
from desk.stocks.lists import LISTS
from desk.stocks.paper.book import PaperBook
from desk.stocks.repo import LEDGER_FILE, MARKET_FILE, RESEARCH_FILE, StocksDatabases, StocksMarket, StocksResearch
from desk.stocks.runner import saved
from desk.web.app import install_browser_boundary

STATIC = Path(__file__).with_name("static")


@contextmanager
def reader(paths: DeskPaths, name: str) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect((paths.db_dir / name).as_uri() + "?mode=ro", uri=True, timeout=2)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def snapshot(paths: DeskPaths, loaded: LoadedConfig) -> dict[str, Any]:
    with reader(paths, RESEARCH_FILE) as conn:
        research = StocksResearch(conn)
        lists = []
        for spec in LISTS:
            day, rows = research.list_on(spec.id)
            lists.append({"id": spec.id, "title": spec.title, "blurb": spec.blurb,
                          "asof": str(day) if day else None, "rows": [dict(r) for r in rows[:25]]})
        latest = research.latest_asof()
    with reader(paths, MARKET_FILE) as conn:
        market = StocksMarket(conn)
        jobs = market.jobs()
        count = conn.execute("SELECT COUNT(*) FROM stk_listings WHERE active=1").fetchone()[0]
    credentials = load_secrets(paths.secrets_file)
    return {"mode": "PAPER", "orders_enabled": False, "asof": str(latest) if latest else None, "lists": lists,
            "desks": DESK_LABELS, "listings": count, "jobs": jobs, "worker": saved(paths, "worker"),
            "worker_running": WorkerLock(paths, "stocks-worker").held(), "stop": paths.stop_file.exists(),
            "connection": connection_status(paths, loaded.settings.stocks, credentials),
            "source_keys": {name: credentials.configured(name) for name in
                            ("MASSIVE_API_KEY", "SEC_CONTACT_EMAIL", "TRADIER_TOKEN")}}


def detail(paths: DeskPaths, symbol: str) -> dict[str, Any]:
    if not 1 <= len(symbol) <= 12 or any(c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-" for c in symbol):
        raise HTTPException(400, "Invalid stock symbol")
    with reader(paths, RESEARCH_FILE) as conn:
        value = StocksResearch(conn).latest(symbol)
    with reader(paths, MARKET_FILE) as conn:
        rows = conn.execute("SELECT day, open, high, low, close, volume FROM stk_bars WHERE symbol=? "
                            "ORDER BY day DESC LIMIT 400", (symbol,)).fetchall()
    return {"symbol": symbol, "analysis": value, "bars": [dict(r) for r in reversed(rows)]}


def paper(paths: DeskPaths) -> dict[str, Any]:
    with reader(paths, LEDGER_FILE) as conn:
        book = PaperBook(conn)
        positions = [asdict(p) for p in book.positions()][:500]
        return {"report": saved(paths, "paper"), "positions": positions, "decisions": book.decisions(limit=100),
                "events": book.events(limit=100)}


def create_app(paths: DeskPaths, loaded: LoadedConfig | None = None) -> FastAPI:
    config = loaded or load_config(paths.config_dir)
    csrf = secrets.token_urlsafe(32)
    attempt: LoginAttempt | None = None
    login_lock = asyncio.Lock()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        dbs = StocksDatabases.open(paths)
        dbs.close()
        try:
            yield
        finally:
            if attempt is not None:
                await asyncio.to_thread(attempt.stop)

    app = FastAPI(title="KESTREL Stocks PAPER", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    install_browser_boundary(app, csrf)

    @app.exception_handler(DeskError)
    async def desk_error(_: Request, exc: DeskError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=400)

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
        return snapshot(paths, config)

    @app.get("/api/stocks/stock/{symbol}")
    def stock(symbol: str) -> dict[str, Any]:
        return detail(paths, symbol.upper())

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
                "login": {"pending": attempt is not None and not attempt.complete.is_set(),
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
            if attempt is not None and not attempt.complete.is_set():
                raise HTTPException(409, "Finish the pending authorization before changing settings")
            lock = WorkerLock(paths, "stocks-worker")
            if lock.held():
                raise HTTPException(409, "Stop the Stocks worker with Ctrl+C before changing its data connection")
            with lock:
                await asyncio.to_thread(save_connection, paths, app_key=data.get("app_key", ""),
                                        app_secret=data.get("app_secret", ""), callback=data.get("callback_url", ""),
                                        provider=data.get("options_provider", ""))
        return connection()

    @app.post("/api/stocks/connect")
    async def connect() -> dict[str, str]:
        nonlocal attempt
        async with login_lock:
            if attempt is not None and not attempt.complete.is_set():
                raise HTTPException(409, "A login is already pending; finish it or wait five minutes")
            if attempt is not None:
                await asyncio.to_thread(attempt.stop)
            status = connection_status(paths, config.settings.stocks, load_secrets(paths.secrets_file))
            attempt = await asyncio.to_thread(new_login, paths, status["callback_url"])
            return {"authorize_url": attempt.start(), "expires_in_seconds": "300"}

    app.mount("/assets", StaticFiles(directory=STATIC), name="stocks-assets")
    return app
