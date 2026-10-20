"""FastAPI adapter for the bounded, loopback-only KESTREL dashboard."""

from __future__ import annotations

import asyncio
import contextlib
import csv
import io
import json
import secrets
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from desk.core.config import LoadedConfig, load_config
from desk.core.paths import DeskPaths
from desk.storage.databases import Databases
from desk.web.control import ControlError, WorkerManager
from desk.web.data import DashboardData

STATIC = Path(__file__).parent / "static"


class ControlBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    minutes: Annotated[float, Field(gt=0, le=10080, allow_inf_nan=False)] = 4320


class SnapshotCache:
    """One shared result for all tabs; a disconnect cancels its stream, not other subscribers."""

    def __init__(self, fetch: Callable[[], dict[str, Any]], ttl: float = 2) -> None:
        self.fetch = fetch
        self.ttl = ttl
        self._lock = asyncio.Lock()
        self._at = float("-inf")
        self._value: dict[str, Any] = {}

    def invalidate(self) -> None:
        self._at = float("-inf")

    async def get(self) -> dict[str, Any]:
        async with self._lock:
            if time.monotonic() - self._at >= self.ttl:
                self._value = await asyncio.to_thread(self.fetch)
                self._at = time.monotonic()
            return self._value


def _csv_value(value: Any) -> str:
    text = "" if value is None else str(value)
    # Spreadsheet exports may contain untrusted token symbols or upstream labels.
    return "'" + text if text.startswith(("=", "+", "-", "@", "\t", "\r", "\n")) else text


def install_browser_boundary(app: FastAPI, csrf_token: str) -> None:
    """Loopback host only, same-origin requests only (a top-level page load from a link excepted), CSRF on writes,
    strict response headers. Shared by the Solana and Robinhood dashboards."""

    @app.middleware("http")
    async def browser_boundary(request: Request, call_next: Any) -> Response:
        host = request.headers.get("host", "")
        # Exact host check also blocks DNS rebinding. No externally accessible bind option is exposed.
        hostname, _, port = host.partition(":")
        if hostname not in {"127.0.0.1", "localhost"} or (port and not port.isdigit()):
            return JSONResponse({"detail": "loopback host required"}, status_code=403)
        origin = request.headers.get("origin")
        expected = f"{request.url.scheme}://{host}"
        # Opening the page from a link elsewhere is a cross-site top-level navigation; it may load the page shell
        # (static files only). Every /api request, and anything framed or fetched, must still come from this page.
        navigation = (request.method in {"GET", "HEAD"} and not request.url.path.startswith("/api/")
                      and request.headers.get("sec-fetch-mode") == "navigate"
                      and request.headers.get("sec-fetch-dest") == "document")
        if (origin is not None and origin != expected) or (
                request.headers.get("sec-fetch-site") == "cross-site" and not navigation):
            return JSONResponse({"detail": "same-origin requests required"}, status_code=403)
        if request.method not in {"GET", "HEAD"}:
            supplied = request.headers.get("x-csrf-token", "")
            if not secrets.compare_digest(supplied, csrf_token):
                return JSONResponse({"detail": "valid dashboard session required"}, status_code=403)
        response: Response = await call_next(request)
        response.headers.update({
            "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer",
            "Content-Security-Policy": "default-src 'self'; connect-src 'self'; img-src 'self' data:; "
                                       "style-src 'self'; script-src 'self'; font-src 'self'; "
                                       "frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
        })
        return response


def create_app(paths: DeskPaths, loaded: LoadedConfig | None = None, *,
               manager: WorkerManager | None = None) -> FastAPI:
    config = loaded or load_config(paths.config_dir)
    workers = manager or WorkerManager(paths)
    data = DashboardData(paths, config)
    cache = SnapshotCache(data.snapshot)
    csrf_token = secrets.token_urlsafe(32)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        # Migration is a startup operation, never a side effect of a dashboard read.
        dbs = Databases.open(paths)
        dbs.close()
        yield

    app = FastAPI(title="KESTREL local dashboard", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

    install_browser_boundary(app, csrf_token)

    @app.get("/api/session")
    def session() -> dict[str, str]:
        return {"csrf_token": csrf_token}

    @app.get("/api/dashboard")
    @app.get("/api/snapshot")
    async def snapshot() -> dict[str, Any]:
        value = dict(await cache.get())
        value["live"] = dict(value.get("live", {}))
        value["live"]["gates"] = [dict(gate) for gate in value["live"].get("gates", [])]
        from desk.signer.server import request as signer_request

        value["live"].update(armed=False, signer_status="offline", buy_enabled=False)
        if paths.signer_socket.exists():
            try:
                state = await signer_request(paths.signer_socket, {"op": "status"}, timeout=0.5)
            except (OSError, TimeoutError, ValueError, asyncio.IncompleteReadError):
                value["live"].update(armed=None, signer_status="unavailable")
            else:
                value["live"].update(armed=bool(state.get("unlocked")), signer_status="online",
                                     buy_enabled=bool(state.get("buy_enabled")))
        for gate in value["live"]["gates"]:
            if gate.get("number") == 5:
                gate["ok"] = value["live"]["armed"] is True
        value["jobs"] = workers.jobs()
        if value.get("mode") != "LIVE" and any(
            job["name"] == "paper" and job["returncode"] is None for job in value["jobs"]
        ):
            value["mode"] = "PAPER"
        return value

    @app.get("/api/events")
    async def events(request: Request) -> StreamingResponse:
        async def stream() -> AsyncIterator[str]:
            while not await request.is_disconnected():
                payload = await snapshot()
                yield "event: snapshot\ndata: " + json.dumps(payload, default=str, separators=(",", ":")) + "\n\n"
                await asyncio.sleep(1)

        return StreamingResponse(stream(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"})

    @app.get("/api/paper")
    def paper() -> dict[str, Any]:
        return data.paper()

    @app.get("/api/candidates/{candidate_id}")
    def candidate(candidate_id: str) -> dict[str, Any]:
        value = data.candidate(candidate_id)
        if value is None:
            raise HTTPException(404, "candidate not found")
        return value

    @app.get("/api/charts/{mint}")
    def chart(mint: str, pool_address: Annotated[str | None, Query(max_length=44)] = None,
              limit: Annotated[int, Query(ge=1, le=500)] = 200) -> dict[str, Any]:
        from desk.web.charts import chart_data

        try:
            return chart_data(paths, mint, pool_address=pool_address, limit=limit)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/api/positions/{position_id}")
    def position(position_id: str) -> dict[str, Any]:
        value = data.position(position_id)
        if value is None:
            raise HTTPException(404, "position not found")
        return value

    @app.get("/api/strategies/{strategy_id}")
    def strategy(strategy_id: str) -> dict[str, Any]:
        value = data.strategy(strategy_id)
        if value is None:
            raise HTTPException(404, "strategy not found")
        return value

    @app.post("/api/control/{action}")
    async def control(action: Literal["start-shadow", "start-paper", "stop", "report", "disarm"],
                      body: ControlBody | None = None) -> dict[str, Any]:
        try:
            if action == "stop":
                result = await asyncio.to_thread(workers.stop_entries)
            elif action == "disarm":
                from desk.signer.server import request as signer_request

                if not paths.signer_socket.exists():
                    result = {"armed": False, "reason": "signer is not running"}
                else:
                    result = await signer_request(paths.signer_socket, {"op": "disarm"}, timeout=5)
            else:
                kind = {"start-shadow": "shadow", "start-paper": "paper", "report": "report"}[action]
                result = await asyncio.to_thread(workers.start, kind, (body or ControlBody()).minutes)
        except ControlError as exc:
            raise HTTPException(409, str(exc)) from exc
        except (OSError, TimeoutError) as exc:
            raise HTTPException(503, "local worker or signer unavailable; check desk status") from exc
        cache.invalidate()
        return result

    @app.get("/api/export/trades.json")
    def export_json() -> JSONResponse:
        return JSONResponse(data.trades(), headers={"Content-Disposition": 'attachment; filename="kestrel-trades.json"',
                                                   "X-Export-Limit": "10000"})

    @app.get("/api/export/trades.csv")
    def export_csv() -> Response:
        rows = data.trades()
        columns = ["id", "mint", "strategy_id", "state", "opened_at", "closed_at", "ticket_lamports",
                   "exit_lamports", "realized_pnl_lamports", "exit_reason"]
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(columns)
        writer.writerows([_csv_value(row.get(column)) for column in columns] for row in rows)
        return Response(output.getvalue(), media_type="text/csv", headers={
            "Content-Disposition": 'attachment; filename="kestrel-trades.csv"',
            "X-Export-Limit": "10000",
        })

    if (STATIC / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=STATIC / "assets"), name="assets")

    @app.get("/favicon.svg")
    def favicon() -> Response:
        if not (STATIC / "favicon.svg").exists():
            raise HTTPException(404, "favicon not built")
        return FileResponse(STATIC / "favicon.svg", media_type="image/svg+xml")

    @app.get("/")
    def index() -> Response:
        if not (STATIC / "index.html").exists():
            return JSONResponse({"detail": "dashboard assets missing; run scripts/build_ui.sh"}, status_code=503)
        return FileResponse(STATIC / "index.html")

    return app


def serve(paths: DeskPaths, *, port: int = 8765) -> None:
    import uvicorn

    from desk.core.worker_lock import WorkerLock

    if not 1024 <= port <= 65535:
        raise ValueError("dashboard port must be between 1024 and 65535")
    with WorkerLock(paths, "dashboard"), contextlib.suppress(KeyboardInterrupt):
        uvicorn.run(create_app(paths), host="127.0.0.1", port=port, access_log=False, server_header=False)
