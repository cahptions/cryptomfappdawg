"""The Robinhood desk's own dashboard: a separate loopback app (default port 8766), read-only.

It has no controls: the PAPER engine runs from the terminal (``desk rh paper``) and nothing in this app can
start, stop or change it. The same browser protections as the Solana dashboard apply.
"""

from __future__ import annotations

import asyncio
import contextlib
import secrets
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from desk.core.config import LoadedConfig, load_config
from desk.core.paths import DeskPaths
from desk.rh.repo import RhDatabases
from desk.rh.view import SYMBOL, RhView
from desk.web.app import install_browser_boundary

STATIC = Path(__file__).resolve().parents[1] / "web" / "static"
Span = Literal["1D", "1W", "1M", "3M", "1Y", "ALL"]


def create_rh_app(paths: DeskPaths, loaded: LoadedConfig | None = None) -> FastAPI:
    config = loaded or load_config(paths.config_dir)
    view = RhView(paths, config)
    RhDatabases.open(paths).close()        # migrate once at startup, never as a side effect of a read
    app = FastAPI(title="KESTREL Robinhood desk", docs_url=None, redoc_url=None, openapi_url=None)
    install_browser_boundary(app, secrets.token_urlsafe(32))

    @app.get("/api/rh/snapshot")
    async def snapshot() -> dict[str, object]:
        return await asyncio.to_thread(view.snapshot)

    @app.get("/api/rh/learning")
    async def learning_view() -> dict[str, object]:
        return await asyncio.to_thread(view.learning)

    @app.get("/api/rh/opportunities")
    async def opportunities() -> dict[str, object]:
        from desk.ops.opportunities import read_report

        return await asyncio.to_thread(read_report, paths, "robinhood")

    @app.get("/api/rh/equity")
    async def equity(span: Span = "1M") -> dict[str, object]:
        return await asyncio.to_thread(view.equity, span)

    @app.get("/api/rh/coin/{symbol}")
    async def coin(symbol: str, span: Span = "1M", interval: Literal["15m", "1h", "4h", "1d"] | None = None
                   ) -> dict[str, object]:
        if not SYMBOL.fullmatch(symbol):
            raise HTTPException(422, "symbol must look like BTC-USD")
        found = await asyncio.to_thread(view.coin, symbol, span, interval)
        if found is None:
            raise HTTPException(404, "no history for this coin yet")
        return found

    if (STATIC / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=STATIC / "assets"), name="assets")

    @app.get("/favicon.svg")
    def favicon() -> Response:
        if not (STATIC / "favicon.svg").exists():
            raise HTTPException(404, "favicon not built")
        return FileResponse(STATIC / "favicon.svg", media_type="image/svg+xml")

    @app.get("/")
    def index() -> Response:
        if not (STATIC / "rh.html").exists():
            return JSONResponse({"detail": "dashboard assets missing; run scripts/build_ui.sh"}, status_code=503)
        return FileResponse(STATIC / "rh.html")

    return app


def serve_rh(paths: DeskPaths, *, port: int) -> None:
    import uvicorn

    from desk.core.worker_lock import WorkerLock

    if not 1024 <= port <= 65535:
        raise ValueError("dashboard port must be between 1024 and 65535")
    with WorkerLock(paths, "rh-dashboard"), contextlib.suppress(KeyboardInterrupt):
        uvicorn.run(create_rh_app(paths), host="127.0.0.1", port=port, access_log=False, server_header=False)


__all__ = ["create_rh_app", "serve_rh"]
