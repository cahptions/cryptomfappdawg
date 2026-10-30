"""`kestrel` / `desk stocks status`: this computer's desk in plain words. App, collector, the last close analysed,
stored prices, the Schwab sign-in, one live test quote and any failing job. Read-only apart from that one quote."""

from __future__ import annotations

import asyncio
import socket
from datetime import date, datetime, timedelta
from typing import Any

from desk.core.config import LoadedConfig
from desk.core.errors import DeskError
from desk.core.paths import DeskPaths
from desk.core.secrets import load_secrets
from desk.core.worker_lock import WorkerLock
from desk.stocks import calendar as cal
from desk.stocks.repo import StocksDatabases, StocksMarket, StocksResearch, date_of

Line = tuple[bool, str, str]          # ok, label, text


def _day(d: date) -> str:
    return f"{d:%a %b} {d.day}"


def _time(stamp: str | None) -> str:
    if not stamp:
        return "never"
    local = cal.new_york(datetime.fromisoformat(stamp))
    return f"{local:%a %b} {local.day}, {local:%I:%M %p}".replace(" 0", " ") + " ET"


def _listening(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


async def _live(paths: DeskPaths, loaded: LoadedConfig, dbs: StocksDatabases) -> Line:
    from desk.stocks.suite import build_stocks_suite

    suite = build_stocks_suite(paths, loaded, load_secrets(paths.secrets_file), dbs.runtime)
    try:
        if suite.schwab is None or not getattr(suite.schwab, "configured", False):
            return False, "Live quote", "Schwab is not connected: open KESTREL → Settings"
        quotes = await suite.schwab.quotes(["SPY"])
        if not quotes or quotes[0].last is None:
            return False, "Live quote", "Schwab answered without a SPY price"
        at = quotes[0].quoted_at.isoformat() if quotes[0].quoted_at else None
        return True, "Live quote", f"SPY ${quotes[0].last:,.2f} · {_time(at)}"
    except DeskError as exc:
        return False, "Live quote", str(exc)[:160]
    finally:
        await suite.aclose()


def lines(paths: DeskPaths, loaded: LoadedConfig, now: datetime, *, online: bool = True) -> list[Line]:
    from desk.stocks.connection import connection_status

    settings = loaded.settings.stocks
    port = settings.dashboard_port
    up = _listening(port)
    out: list[Line] = [
        (up, "App", f"running · http://127.0.0.1:{port}" if up else "not running · start it with: kestrel open"),
        (held := WorkerLock(paths, "stocks-worker").held(), "Collector",
         "running" if held else "paused · use Start PAPER in the app"),
    ]
    dbs = StocksDatabases.open(paths)
    try:
        market, research = StocksMarket(dbs.market), StocksResearch(dbs.research)
        expected = cal.last_closed_session(now, timedelta(minutes=settings.refresh_after_close_minutes))
        analysed = research.latest_asof()
        out.append((analysed is not None and analysed >= expected, "Last close",
                    f"{_day(analysed)} analysed" + ("" if analysed >= expected else
                                                     f" · waiting for {_day(expected)}'s prices")
                    if analysed else "nothing analysed yet"))
        row = market.conn.execute("SELECT day, source FROM stk_bar_days WHERE rows > 0 ORDER BY day DESC LIMIT 1"
                                  ).fetchone()
        out.append((row is not None, "Prices", f"through {_day(date_of(row[0]))} ({row[1].replace('_', ' ')})"
                    if row else "none stored yet"))
        schwab: dict[str, Any] = connection_status(paths, settings, load_secrets(paths.secrets_file))["schwab"]
        signed_in = bool(schwab.get("connected")) and not schwab.get("relogin_required")
        out.append((signed_in, "Schwab", (schwab.get("note") or "signed in") if signed_in else
                    "sign-in needed: open KESTREL → Settings → Open Schwab authorization"))
        if online:
            out.append(asyncio.run(_live(paths, loaded, dbs)))
        jobs = market.jobs()
        failing = {name: job for name, job in jobs.items() if job.get("error")}
        out.append((not failing, "Jobs", f"{len(jobs) - len(failing)} of {len(jobs)} ok"))
        for name, job in sorted(failing.items()):
            out.append((False, f"  {name.replace('_', ' ')}",
                        f"{str(job['error'])[:110]} · last ok {_time(job.get('last_ok_at'))}"))
    finally:
        dbs.close()
    return out


def render(found: list[Line], now: datetime) -> str:
    local = cal.new_york(now)
    head = f"KESTREL · {socket.gethostname()} · {local:%a %b} {local.day}, {local:%I:%M %p}".replace(" 0", " ") + " ET"
    body = [f"  {'✓' if ok else '✗'} {label:<12} {text}" for ok, label, text in found]
    problems = sum(1 for ok, label, _ in found if not ok and not label.startswith("  "))
    tail = "Everything is running." if not problems else \
        f"{problems} thing{'s' if problems > 1 else ''} need{'' if problems > 1 else 's'} attention."
    return "\n".join([head, *body, tail])


__all__ = ["lines", "render"]
