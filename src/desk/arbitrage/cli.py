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
    check = commands.add_parser("ebay-check", help="One eBay sold-results request: does this computer read sold prices?")
    check.add_argument("query", nargs="*", help="what to search (default: LEGO 75192)")
    check.set_defaults(func=run)
    commands.add_parser("stop", help="Gracefully stop this computer's Arbitrage dashboard (the icon starts it again)"
                        ).set_defaults(func=run)
    for action in ("collect", "diagnose"):
        commands.add_parser(
            action,
            help="One bounded read-only collection pass" if action == "collect" else "Read local status without provider calls",
        ).set_defaults(func=run)


def ebay_check(paths: DeskPaths, query: str) -> int:
    """One polite request; says whether eBay answered, how many sales were read, and saves an unread page for review."""
    import httpx

    from desk.arbitrage import comps, markets
    from desk.arbitrage.hunt import Hunt
    from desk.core.clock import utc_now

    initialize(paths)
    with Repo.open(paths) as repo:
        hunt = Hunt(repo.conn)
        state = hunt.state("ebay")
        if (why := markets.paused(state, utc_now())) is not None:
            print(f"✗ Paused: {why}")
            return 1
        url = markets.sold_url(query)
        try:
            response = httpx.get(url, headers={"User-Agent": markets.AGENT, "Accept-Language": "en-US"},
                                 follow_redirects=True, timeout=20)
        except httpx.HTTPError as exc:
            print(f"✗ eBay didn't answer ({type(exc).__name__})")
            return 1
        state["last_request"] = time.time()
        page = response.text[:markets.MAX_PAGE]
        refused = comps.blocked(response.status_code, page)
        if refused:
            state.update(paused_until=(utc_now() + markets.EBAY_PAUSE).isoformat(), reason=refused)
            hunt.save_state("ebay", state)
            print(f"✗ {refused}. eBay reading pauses 6 hours; pasting sold results still works.")
            return 1
        hunt.save_state("ebay", state)
        sales = comps.parse_html(page)
        if sales:
            print(f"✓ Read {len(sales)} sold results for \"{query}\" (HTTP {response.status_code}). Newest:")
            for s in sales[:3]:
                print(f"  {s.sold_on}  ${s.total:,.2f}  {s.title[:70]}")
            return 0
        saved = paths.home / "ebay-check.html"
        saved.write_text(page)
        lines = comps.html_lines(page)
        print(f"✗ eBay answered (HTTP {response.status_code}, {len(page):,} characters) but no sold results were read. "
              f"Saved to {saved}. Send this line to Claude: results={page.count('s-item')}/{page.count('s-card')} "
              f"sold_lines={sum(1 for x in lines if x.startswith('Sold'))} title={page[page.find('<title>'):][:80]!r}")
        return 1


def run(args: argparse.Namespace) -> int:
    paths = isolated_paths(DeskPaths.from_env())
    if args.arbitrage_command == "ebay-check":
        return ebay_check(paths, " ".join(args.query) or "LEGO 75192")
    if args.arbitrage_command == "stop":
        from desk.ops.dashboard_restart import restart_dashboard

        restart_dashboard(paths, "arbitrage")
        return 0
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
