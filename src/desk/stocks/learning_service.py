"""Bounded independent market-data collection and simulation for the options learning workspace."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from typing import Any

from desk.core.clock import to_iso, utc_now
from desk.core.config import LoadedConfig
from desk.core.errors import DeskError
from desk.core.paths import DeskPaths
from desk.core.secrets import load_secrets
from desk.core.worker_lock import WorkerBusy, WorkerLock
from desk.stocks import calendar as cal
from desk.stocks import learning
from desk.stocks.collect import pick_expirations
from desk.stocks.connection import _atomic_private
from desk.stocks.repo import MARKET_FILE, StocksMarket
from desk.stocks.suite import build_stocks_suite
from desk.storage.db import DbKind, connect


def preferences(paths: DeskPaths) -> dict[str, Any]:
    path = paths.home / "stocks-learning-preferences.json"
    if not path.exists():
        return {"enabled": True}
    try:
        if path.stat().st_size > 4096:
            raise ValueError
        value = json.loads(path.read_text())
        if not isinstance(value, dict) or not isinstance(value.get("enabled"), bool):
            raise ValueError
        return {"enabled": value["enabled"]}
    except (OSError, ValueError) as exc:
        raise DeskError("Learning preferences need repair. Use Start or Pause Learning to save them again.") from exc


def control(paths: DeskPaths, enabled: bool) -> dict[str, Any]:
    _atomic_private(paths.home / "stocks-learning-preferences.json", json.dumps({"enabled": enabled}))
    return {"enabled": enabled}


def allowed(paths: DeskPaths) -> bool:
    if paths.stop_file.exists() or not preferences(paths)["enabled"]:
        return False
    from desk.stocks.research_worker import ResearchWorker

    return ResearchWorker(paths)._enabled()


async def collect(
    paths: DeskPaths, loaded: LoadedConfig, conn: sqlite3.Connection, market: StocksMarket, now: datetime
) -> dict[str, Any]:
    local = cal.new_york(now)
    if not cal.is_trading_day(local.date()) or not (9, 30) <= (local.hour, local.minute) < (16, 0):
        return {"phase": "Market closed"}
    last = learning.state(conn, "collection_at")
    if last and now - datetime.fromisoformat(last) < timedelta(seconds=60):
        return {"phase": "Watching saved quotes; provider requests paced to one minute"}
    learning.set_state(conn, "collection_at", to_iso(now))
    held: dict[str, set[Any]] = {}
    from datetime import date

    # Query only contract identities, rather than loading every historic position and order into memory.
    for row in conn.execute(
        "SELECT DISTINCT p.symbol,json_extract(j.value,'$.expiration') expiry "
        "FROM stk_positions p,json_each(p.legs_json) j WHERE p.state='OPEN' AND p.expires_day>=? "
        "UNION SELECT DISTINCT json_extract(p.order_json,'$.symbol'),json_extract(j.value,'$.expiration') "
        "FROM stk_pending p,json_each(p.order_json,'$.legs') j WHERE p.state='PENDING'",
        (local.date().toordinal(),),
    ):
        if row[1]:
            held.setdefault(row[0], set()).add(date.fromisoformat(row[1]))
    known = [r[0] for r in market.conn.execute("SELECT DISTINCT underlying FROM stk_option_chains ORDER BY underlying")]
    symbols = list(dict.fromkeys([*held, "SPY", "QQQ", *known]))
    offset = learning.state(conn, "collection_cursor", 0) % len(symbols)
    selected = (symbols + symbols)[offset : offset + min(2, len(symbols))]
    learning.set_state(conn, "collection_cursor", (offset + len(selected)) % len(symbols))
    runtime = connect(paths.db_dir / "runtime.db", DbKind.RUNTIME)
    suite = None
    try:
        suite = build_stocks_suite(paths, loaded, load_secrets(paths.secrets_file), runtime)
        if not suite.options.configured:
            return {"phase": "Connect the selected options provider in Settings"}
        spot_quotes = await suite.options.quotes(selected)
        quote_clock = utc_now()
        spots = {
            q.symbol: q.last
            for q in spot_quotes
            if q.last and q.last > 0 and q.quoted_at and 0 <= (quote_clock - q.quoted_at).total_seconds() <= 120
        }
        total = 0
        errors = []
        for symbol in selected:
            if not allowed(paths):
                return {"phase": "Learning paused; collected observations preserved"}
            try:
                expiries = await suite.options.expirations(symbol)
                wanted = list(dict.fromkeys([*sorted(held.get(symbol, set())), *pick_expirations(expiries, local.date())]))
                # Expiry selection rotates independently: many positions cannot make one request unbounded.
                cursor_key = "expiry:" + symbol
                start = learning.state(conn, cursor_key, 0) % max(1, len(wanted))
                chosen = (wanted + wanted)[start : start + min(2, len(wanted))]
                learning.set_state(conn, cursor_key, (start + len(chosen)) % max(1, len(wanted)))
                for expiry in chosen:
                    if expiry not in expiries:
                        continue
                    quotes = await suite.options.chain(symbol, expiry)
                    received = utc_now()
                    if quotes and spots.get(symbol):
                        market.save_option_chain(
                            symbol,
                            cal.new_york(received).date(),
                            expiry,
                            received,
                            spots[symbol],
                            quotes,
                            source=suite.option_source,
                        )
                        total += len(quotes)
            except DeskError:
                errors.append(symbol + ": provider request failed; verify access in Settings")
        market.job(
            "learning_quotes",
            utc_now(),
            error="; ".join(errors) if errors else None,
            detail={"contracts": total, "checked": selected, "queued": max(0, len(symbols) - len(selected))},
        )
        return {
            "phase": "Bounded options collection complete",
            "contracts_received": total,
            "errors": errors,
            "checked": selected,
            "queued": max(0, len(symbols) - len(selected)),
        }
    finally:
        if suite is not None:
            await suite.aclose()
        runtime.close()


async def cycle(paths: DeskPaths, loaded: LoadedConfig) -> None:
    if not allowed(paths):
        return
    try:
        lock = WorkerLock(paths, "stocks-learning")
        lock.acquire()
    except WorkerBusy:
        return
    conn = market_conn = None
    try:
        conn = learning.open_book(paths)
        learning.initialize(conn, utc_now())
        market_conn = connect(paths.db_dir / MARKET_FILE, DbKind.STOCKS_MARKET)
        market = StocksMarket(market_conn)
        # Saved observations and matured results must not wait behind network requests or their timeouts.
        if allowed(paths):
            learning.tick(conn, market, utc_now())
        try:
            result = await collect(paths, loaded, conn, market, utc_now())
            learning.set_state(conn, "collection", result)
        except DeskError:
            learning.set_state(conn, "collection", {"phase": "Provider unavailable; saved data retained. Check Settings."})
        if allowed(paths):
            learning.tick(conn, market, utc_now())
    finally:
        if conn is not None:
            conn.close()
        if market_conn is not None:
            market_conn.close()
        lock.release()
