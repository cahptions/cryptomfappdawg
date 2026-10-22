"""Independent Arbitrage commands; no shared trading bootstrap, credentials or collector controls."""

from __future__ import annotations

import argparse
import asyncio
import json
import threading
import time
import webbrowser
from typing import Any

from desk.arbitrage.repo import Repo, isolated_paths
from desk.arbitrage.runner import initialize
from desk.core.paths import DeskPaths
from desk.core.worker_lock import WorkerBusy, WorkerLock


def add_parser(sub: Any) -> None:
    commands = sub.add_parser("arbitrage", help="Separate retail-arbitrage research desk; no purchases").add_subparsers(
        dest="arbitrage_command", required=True
    )
    dashboard = commands.add_parser("dashboard", help="Start local research and bounded observation collection")
    dashboard.add_argument("--port", type=int, choices=range(1024, 65536), default=None, metavar="1024..65535")
    dashboard.add_argument("--open", action="store_true")
    dashboard.add_argument("--restart", action="store_true", help="Gracefully restart only this Arbitrage dashboard")
    dashboard.set_defaults(func=run)
    for action in ("collect", "diagnose"):
        commands.add_parser(
            action,
            help="One bounded read-only collection pass" if action == "collect" else "Read local status without provider calls",
        ).set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    paths = isolated_paths(DeskPaths.from_env())
    if args.arbitrage_command == "diagnose":
        if not (paths.db_dir / "arbitrage.db").exists():
            print(json.dumps({"initialized": False, "desk": "ARBITRAGE"}))
            return 0
        with Repo.reader(paths) as repo:
            print(
                json.dumps(
                    {
                        "desk": "ARBITRAGE",
                        "integrity": repo.conn.execute("PRAGMA quick_check").fetchone()[0],
                        "products": len(repo.products()),
                        "sources": len(repo.sources()),
                        "jobs": repo.jobs(),
                        "enabled": repo.settings().enabled,
                        "marketplace_writes": False,
                    },
                    indent=2,
                )
            )
        return 0
    initialize(paths)
    if args.arbitrage_command == "collect":
        import httpx

        from desk.arbitrage.runner import cycle

        async def once() -> None:
            with WorkerLock(paths):
                async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
                    print(json.dumps(await cycle(paths, client=client)))

        asyncio.run(once())
        return 0
    import uvicorn

    from desk.arbitrage.web import create_app
    from desk.ops.launcher import responding

    lock = WorkerLock(paths, "dashboard")
    with Repo.reader(paths) as repo:
        port = args.port or repo.settings().dashboard_port
    url = f"http://127.0.0.1:{port}"
    if getattr(args, "restart", False):
        from desk.ops.dashboard_restart import restart_dashboard

        restart_dashboard(paths, desk="arbitrage")
    try:
        lock.acquire()
    except WorkerBusy:
        if responding(url):
            print(f"Arbitrage dashboard is already running: {url}")
            if args.open:
                webbrowser.open(url)
            return 0
        raise
    try:
        server = uvicorn.Server(uvicorn.Config(create_app(paths), host="127.0.0.1", port=port, access_log=False))
        if args.open:

            def open_ready() -> None:
                deadline = time.monotonic() + 15
                while not server.started and not server.should_exit and time.monotonic() < deadline:
                    time.sleep(0.1)
                if server.started:
                    webbrowser.open(url)

            threading.Thread(target=open_ready, daemon=True).start()
        print(f"KESTREL Arbitrage: {url}")
        server.run()
    finally:
        lock.release()
    return 0
