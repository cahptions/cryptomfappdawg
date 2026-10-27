"""Operator commands for the separate, market-data-only Stocks PAPER desk."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import getpass
import json
import math
import signal
import time
import webbrowser
from typing import Any
from urllib.parse import urlsplit

from desk.core.config import load_config
from desk.core.errors import DeskError
from desk.core.paths import DeskPaths
from desk.core.secrets import load_secrets
from desk.core.worker_lock import WorkerBusy, WorkerLock
from desk.stocks.connection import LoginAttempt, connection_status, local_tls, preferences, save_connection
from desk.stocks.repo import StocksDatabases, StocksMarket
from desk.stocks.schwab_auth import SchwabAuthError, SchwabTokens, authorization_url, callback_code, token_path


def _bounded(text: str) -> int:
    n = int(text)
    if not 1 <= n <= 100:
        raise argparse.ArgumentTypeError("choose 1..100")
    return n


def add_parser(sub: Any) -> None:
    p = sub.add_parser("stocks", help="Stocks/options research, local dashboard and PAPER only")
    commands = p.add_subparsers(dest="stocks_command", required=True)
    commands.add_parser("diagnose", help="bounded read-only local report; no provider calls or credential values").set_defaults(
        func=run)
    doctor = commands.add_parser("doctor", help="local health; --online makes one market quote request")
    doctor.add_argument("--online", action="store_true")
    doctor.set_defaults(func=run)
    connect = commands.add_parser("connect", help="authorize Schwab market data in your browser")
    connect.add_argument("--callback-url")
    connect.add_argument("--listen-port", type=int)
    connect.add_argument("--manual", action="store_true", help="paste redirect privately in this terminal instead of a listener")
    connect.set_defaults(func=run)
    for name in ("refresh", "paper", "collect"):
        cmd = commands.add_parser(name, help="research collection only, no PAPER orders" if name == "collect" else
                                  "one bounded collection/PAPER pass" if name == "refresh" else
                                  "repeat collection and forward PAPER while this terminal stays open")
        cmd.add_argument("--price-days", type=_bounded, default=20 if name == "collect" else 5)
        cmd.add_argument("--companies", type=_bounded, default=20)
        cmd.add_argument("--options", type=_bounded, default=5)
        if name in {"paper", "collect"}:
            cmd.add_argument("--minutes", type=float, default=1440)
            cmd.add_argument("--interval", type=float, default=300)
        if name == "paper":
            cmd.add_argument("--continuous", action="store_true", help="keep PAPER and collection running until paused")
            cmd.add_argument("--managed", action="store_true", help=argparse.SUPPRESS)
        cmd.set_defaults(func=run)
    for name in ("report", "validate", "learn"):
        commands.add_parser(name, help="stored PAPER report" if name == "report" else
                            "explicit offline historical research; no LIVE changes").set_defaults(func=run)
    dash = commands.add_parser("dashboard", help="local Stocks dashboard with private Settings")
    dash.add_argument("--port", type=int)
    dash.add_argument("--open", action="store_true")
    dash.add_argument('--restart', action='store_true', help='gracefully restart this data directory\'s Stocks dashboard')
    dash.set_defaults(func=run)


def new_login(paths: DeskPaths, callback: str, *, port: int | None = None) -> LoginAttempt:
    credentials = load_secrets(paths.secrets_file)
    tokens = SchwabTokens(token_path(paths), credentials)
    if not tokens.has_credentials:
        raise SchwabAuthError("Save both Schwab app credentials privately in Settings first (secrets.env must be chmod 600)")
    parsed = urlsplit(callback)
    loopback = parsed.hostname in {"127.0.0.1", "localhost"}
    selected = port if port is not None else (parsed.port or 443) if loopback else 8788
    if loopback and selected != (parsed.port or 443):
        raise SchwabAuthError("Local callback port must exactly match the registered HTTPS URL")
    return LoginAttempt(tokens, callback, credentials.get("SCHWAB_APP_KEY") or "", port=selected,
                        tls=local_tls(paths) if loopback else None)


def run(args: argparse.Namespace) -> int:
    paths = DeskPaths.from_env()
    if args.stocks_command == "diagnose":
        from desk.stocks.diagnostics import diagnose

        print(json.dumps(diagnose(paths), indent=2))
        return 0
    paths.ensure()
    loaded = load_config(paths.config_dir)
    settings = loaded.settings.stocks
    action = args.stocks_command
    if action == "connect":
        callback = args.callback_url or preferences(paths, settings)["callback_url"]
        with WorkerLock(paths, "stocks-worker"):
            save_connection(paths, app_key="", app_secret="", callback=callback, provider="schwab")
        if args.manual:
            import secrets

            state = secrets.token_urlsafe(32)
            tokens = SchwabTokens(token_path(paths), load_secrets(paths.secrets_file))
            print("Open this URL in your browser. Then copy the complete final redirect address, even if it fails to load:")
            print(authorization_url(tokens.credentials.get("SCHWAB_APP_KEY") or "", callback, state))
            returned = getpass.getpass("Paste final redirect here (hidden; never paste it into chat): ")
            tokens.exchange(callback_code(returned.strip(), callback, state), callback)
        else:
            attempt = new_login(paths, callback, port=args.listen_port)
            try:
                print("Open this URL to authorize market data (no orders):")
                print(attempt.start())
                if urlsplit(callback).hostname in {"127.0.0.1", "localhost"}:
                    print("The local callback uses a self-signed certificate. Allow it only for your exact local URL.")
                while not attempt.complete.wait(0.5):
                    pass
                if attempt.error:
                    raise SchwabAuthError(attempt.error)
            finally:
                attempt.stop()
        print("Authorization saved privately. Run: uv run desk stocks doctor --online")
        return 0
    if action == "dashboard":
        import uvicorn

        from desk.stocks.web import create_app

        port = args.port or settings.dashboard_port
        if not 1024 <= port <= 65535:
            raise DeskError("Dashboard port must be 1024..65535")
        if getattr(args, 'restart', False):
            from desk.ops.dashboard_restart import restart_dashboard

            restart_dashboard(paths)
        lock = WorkerLock(paths, 'stocks-dashboard')
        try:
            lock.acquire()
        except WorkerBusy:
            from desk.ops.launcher import responding
            from desk.stocks.runtime import server_is_current

            url = f'http://127.0.0.1:{port}'
            if not responding(url):
                raise DeskError('A Stocks dashboard is already running or starting. To restart it safely, run: '
                                'uv run desk stocks dashboard --restart --open') from None
            if server_is_current(url):
                print(f'Stocks dashboard is already running: {url}')
                if args.open:
                    webbrowser.open(url)
                return 0
            from desk.ops.dashboard_restart import restart_dashboard

            print('Loading the installed Stocks update; saved data and PAPER preferences are preserved.')
            restart_dashboard(paths)
            lock.acquire()
        try:
            print(f"Stocks PAPER dashboard: http://127.0.0.1:{port}")
            server = uvicorn.Server(uvicorn.Config(create_app(paths, loaded), host="127.0.0.1", port=port, access_log=False))
            if args.open:
                import threading

                def open_when_ready() -> None:
                    until = time.monotonic() + 15
                    while not server.started and not server.should_exit and time.monotonic() < until:
                        time.sleep(0.05)
                    if server.started:
                        with contextlib.suppress(OSError):
                            webbrowser.open(f"http://127.0.0.1:{port}")

                threading.Thread(target=open_when_ready, name="stocks-open-browser", daemon=True).start()
            server.run()
        finally:
            lock.release()
        return 0
    if action == "doctor":
        dbs = StocksDatabases.open(paths)
        try:
            result = {"databases": dbs.health(), "connection": connection_status(paths, settings,
                                                                                load_secrets(paths.secrets_file)),
                      "latest_price_session": str(max(StocksMarket(dbs.market).bar_days(), default="none")),
                      "jobs": StocksMarket(dbs.market).jobs()}
            if args.online:
                from desk.stocks.suite import build_stocks_suite

                async def check() -> dict[str, Any]:
                    suite = build_stocks_suite(paths, loaded, load_secrets(paths.secrets_file), dbs.runtime)
                    try:
                        quotes = await suite.options.quotes(["SPY"])
                        online = {"provider": suite.option_source, "quote_received": bool(quotes),
                                  "timestamp": str(quotes[0].quoted_at) if quotes else None, "problems": suite.problems}
                        if suite.schwab is not None and suite.schwab.configured:
                            from desk.core.clock import utc_now
                            from desk.stocks.play_tracking import probe

                            online["plays_option_quotes"] = await probe(suite.schwab, suite.options, dbs.market,
                                                                        dbs.research, utc_now())
                        return online
                    finally:
                        await suite.aclose()

                result["online"] = asyncio.run(check())
            print(json.dumps(result, indent=2))
        finally:
            dbs.close()
        return 0
    from desk.stocks import runner

    if action == "report":
        print(json.dumps(runner.saved(paths, "paper") or {"waiting": "No PAPER sessions recorded yet"}, indent=2))
        return 0
    with WorkerLock(paths, "stocks-worker"):
        if action in {"validate", "learn"}:
            print(json.dumps(runner.evaluate(paths, loaded, learning=action == "learn"), indent=2))
            return 0
        if action in {"paper", "collect"} and (not math.isfinite(args.minutes) or args.minutes <= 0
                                  or not math.isfinite(args.interval) or args.interval < 60):
            raise DeskError("Use positive finite minutes and an interval of at least 60 seconds")

        async def collect() -> int:
            stop = asyncio.Event()
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                with contextlib.suppress(NotImplementedError):
                    loop.add_signal_handler(sig, stop.set)
            repeating = action in {"paper", "collect"}
            end = math.inf if getattr(args, "continuous", False) else time.monotonic() + (args.minutes * 60 if repeating else 0)
            async def watch_pause() -> None:
                from desk.stocks.research_worker import ResearchWorker

                control = ResearchWorker(paths)
                while not stop.is_set():
                    try:
                        if not control._enabled():
                            stop.set()
                            return
                    except DeskError:
                        print("PAPER paused: automation preference is unreadable. Save Start PAPER again.", flush=True)
                        stop.set()
                        return
                    await asyncio.sleep(1)

            watcher = asyncio.create_task(watch_pause()) if getattr(args, "managed", False) else None
            try:
                while True:
                    if stop.is_set():
                        return 0
                    work = runner.cycle(paths, loaded, price_days=args.price_days, company_limit=args.companies,
                                        option_limit=args.options, research_only=action == "collect",
                                        managed=getattr(args, "managed", False))
                    if action in {"collect", "paper"}:
                        # Cancel network/rate-limit waits; synchronous PAPER transactions finish atomically before
                        # the event loop can deliver cancellation. Cleanup finishes before releasing the worker lock.
                        task, stopping = asyncio.create_task(work), asyncio.create_task(stop.wait())
                        try:
                            done, _ = await asyncio.wait((task, stopping), return_when=asyncio.FIRST_COMPLETED)
                            if stopping in done and not task.done():
                                print("Research collection paused; completed imports remain saved.", flush=True)
                                return 0
                            result = await task
                        finally:
                            for pending in (task, stopping):
                                if not pending.done():
                                    pending.cancel()
                            await asyncio.gather(task, stopping, return_exceptions=True)
                    else:
                        result = await work
                    print(json.dumps(result, indent=2), flush=True)
                    if not repeating or stop.is_set() or time.monotonic() >= end:
                        return 0
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(stop.wait(), min(args.interval, max(0, end - time.monotonic())))
            finally:
                if watcher is not None:
                    watcher.cancel()
                    await asyncio.gather(watcher, return_exceptions=True)

        return asyncio.run(collect())
