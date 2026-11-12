"""Your real Schwab account, read only: the opt-in switch, when to read it, and the append-only record it keeps.

Nothing here can place, change or cancel an order (``desk.stocks.sources.schwab_account`` only issues GET to three
account paths). Nothing is requested until the user turns on Settings → "Read my Schwab account (read-only)".

What is kept (research database, migration ``0016_schwab_account``; append-only):

- ``stk_account_pulls``: every read and its outcome (ok, not_enabled, signed_out, error), with a plain message;
- ``stk_account_positions``: each read's positions (symbol or OCC option, quantity, average price, market value,
  the day's P/L, asset type), stamped with the read's time and source;
- ``stk_account_fills``: each executed trade once, by Schwab's activity id, for grading decisions.

Accounts are stored as a one-way key from Schwab's hashValue plus the last four digits. No account number is kept,
logged, shown or exported.

When: on market days at 09:45, 12:30 and 15:30 New York time and once after the close (16:20), one attempt per
slot whatever its outcome; trades are read after the close (and on the first read, a year back). "Refresh now" on
the account view reads at once, at most once a minute.

For other features (the whole-portfolio Risk panel): ``account_positions(conn)`` returns the latest successful
read's positions as ``AccountPosition`` values, or an empty list when the switch was never on or nothing was read.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any

import httpx

from desk.core.clock import to_iso
from desk.core.config import LoadedConfig
from desk.core.errors import DeskError, ProviderError
from desk.core.paths import REPO_ROOT, DeskPaths
from desk.core.secrets import Secrets, load_secrets
from desk.providers.cache import SingleFlightCache
from desk.providers.health import HealthTracker
from desk.providers.http import ProviderHttp
from desk.providers.ratelimit import RateLimiter
from desk.providers.registry import load_registry
from desk.stocks import calendar as cal
from desk.stocks.schwab_auth import SchwabAuthError, SchwabTokens, token_path
from desk.stocks.sources import schwab_account as sa

log = logging.getLogger(__name__)
SLOTS = (time(9, 45), time(12, 30), time(15, 30))
AFTER_CLOSE = timedelta(minutes=20)          # the after-close read: 20 minutes after the session's close
MANUAL_GAP = timedelta(seconds=60)
FIRST_LOOKBACK = timedelta(days=364)         # Schwab serves at most a year of transactions per request
OVERLAP = timedelta(days=7)
NOT_ENABLED = ("Schwab refused account access. Your Schwab developer app needs the \"Accounts and Trading "
               "Production\" product: add it to your app at developer.schwab.com, wait until the app shows Ready "
               "For Use, then sign in again in Settings (Start browser authorization) and tick your accounts.")
SIGNED_OUT = ("Schwab is not signed in on this computer. Sign in under Settings → Schwab connection; the account "
              "is read with the same sign-in as market data.")
WATCH_TYPES = ("EQUITY", "COLLECTIVE_INVESTMENT", "OPTION")


@dataclass(frozen=True)
class AccountPosition:
    """One position from the latest successful read. ``quantity`` is long minus short (contracts for options)."""
    account_last4: str
    account_key: str
    taken_at: datetime
    asset_type: str
    symbol: str
    underlying: str | None
    put_call: str | None
    strike: float | None
    expiration: date | None
    quantity: float
    average_price: float | None
    market_value: float | None
    day_pnl: float | None
    day_pnl_pct: float | None
    source: str = sa.SOURCE

    @property
    def stock(self) -> str:
        """The stock this position moves with: the symbol itself, or an option's underlying."""
        return self.underlying or self.symbol


# ── the switch ───────────────────────────────────────────────────────────────────────────────────
def settings_path(paths: DeskPaths) -> Any:
    return paths.home / "stocks-account.json"


def enabled(paths: DeskPaths) -> bool:
    try:
        path = settings_path(paths)
        value = json.loads(path.read_text()) if path.exists() and path.stat().st_size < 1024 else {}
    except (OSError, ValueError):
        return False
    return isinstance(value, dict) and value.get("read_account") is True


def set_enabled(paths: DeskPaths, on: bool) -> None:
    from desk.stocks.connection import _atomic_private

    paths.ensure()
    _atomic_private(settings_path(paths), json.dumps({"read_account": bool(on)}) + "\n")


# ── when ─────────────────────────────────────────────────────────────────────────────────────────
def current_slot(now: datetime) -> tuple[str, datetime, bool] | None:
    """(label, start, after_close) of the latest scheduled read at or before ``now`` on a market day, else None."""
    local = cal.new_york(now)
    day = local.date()
    if not cal.is_trading_day(day):
        return None
    starts = [(f"{day} {t:%H:%M}", datetime.combine(day, t, cal.NEW_YORK), False) for t in SLOTS]
    close = cal.session_close(day) + AFTER_CLOSE
    starts.append((f"{day} close", close, True))
    passed = [s for s in starts if s[1] <= local]
    return passed[-1] if passed else None


def due(conn: sqlite3.Connection, now: datetime) -> tuple[str, bool] | None:
    """The slot to read now (label, after_close), or None when this slot was already attempted."""
    slot = current_slot(now)
    if slot is None:
        return None
    label, start, after_close = slot
    row = conn.execute("SELECT 1 FROM stk_account_pulls WHERE at >= ? AND slot != 'manual' LIMIT 1",
                       (to_iso(start),)).fetchone()
    return None if row else (label, after_close)


def last_pull(conn: sqlite3.Connection) -> sqlite3.Row | None:
    row: sqlite3.Row | None = conn.execute("SELECT * FROM stk_account_pulls ORDER BY id DESC LIMIT 1").fetchone()
    return row


# ── reading ──────────────────────────────────────────────────────────────────────────────────────
def build_client(paths: DeskPaths, loaded: LoadedConfig, secrets: Secrets, runtime: sqlite3.Connection,
                 http_client: httpx.AsyncClient) -> sa.SchwabAccountClient:
    registry = load_registry(paths.config_dir, REPO_ROOT)
    http = ProviderHttp("schwab_account", registry.get("schwab_account"), secrets=secrets,
                        limiter=RateLimiter(paths.db_dir / "runtime.db"),
                        health=HealthTracker(runtime, loaded.settings.health), settings=loaded.settings.http,
                        client=http_client, cache=SingleFlightCache())
    return sa.SchwabAccountClient(http, SchwabTokens(token_path(paths), secrets))


def _record(conn: sqlite3.Connection, now: datetime, slot: str, outcome: str, detail: str | None, *,
            accounts: list[sa.Account] | None = None, positions: list[sa.Position] | None = None,
            fills: list[sa.Fill] | None = None) -> dict[str, Any]:
    fills = fills or []
    conn.execute("BEGIN IMMEDIATE")
    try:
        before = conn.total_changes
        for f in fills:
            conn.execute("INSERT OR IGNORE INTO stk_account_fills(account_key, activity_id, leg, account_last4, "
                         "traded_at, trade_day, asset_type, symbol, underlying, put_call, strike, expiration, quantity,"
                         " price, fees, position_effect, recorded_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         (f.account_key, f.activity_id, f.leg, f.account_last4, to_iso(f.traded_at),
                          f.trade_day.isoformat(), f.asset_type, f.symbol, f.underlying, f.put_call, f.strike,
                          f.expiration.isoformat() if f.expiration else None, f.quantity, f.price, f.fees,
                          f.position_effect, to_iso(now)))
        new = conn.total_changes - before
        cur = conn.execute("INSERT INTO stk_account_pulls(at, slot, outcome, detail, accounts, positions, fills_new) "
                           "VALUES (?,?,?,?,?,?,?)", (to_iso(now), slot, outcome, detail, len(accounts or []),
                                                      len(positions or []), new))
        pull = cur.lastrowid
        merged: dict[tuple[str, str], sa.Position] = {}
        for p in positions or []:
            key = (p.account_key, p.symbol)
            if key in merged:            # the same instrument twice in one account (e.g. two lots): one row
                q = merged[key]
                qty = q.quantity + p.quantity
                avg = (None if q.average_price is None or p.average_price is None or qty == 0 else
                       (q.average_price * q.quantity + p.average_price * p.quantity) / qty)
                p = sa.Position(q.account_key, q.account_last4, q.asset_type, q.symbol, q.underlying, q.put_call,
                                q.strike, q.expiration, qty, avg, _add(q.market_value, p.market_value),
                                _add(q.day_pnl, p.day_pnl), None)
            merged[key] = p
        for p in merged.values():
            conn.execute("INSERT INTO stk_account_positions(pull_id, account_key, account_last4, taken_at, source, "
                         "asset_type, symbol, underlying, put_call, strike, expiration, quantity, average_price, "
                         "market_value, day_pnl, day_pnl_pct) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         (pull, p.account_key, p.account_last4, to_iso(now), sa.SOURCE, p.asset_type, p.symbol,
                          p.underlying, p.put_call, p.strike, p.expiration.isoformat() if p.expiration else None,
                          p.quantity, p.average_price, p.market_value, p.day_pnl, p.day_pnl_pct))
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return {"outcome": outcome, "detail": detail, "accounts": len(accounts or []), "positions": len(merged),
            "fills_new": new}


def _add(a: float | None, b: float | None) -> float | None:
    return None if a is None or b is None else a + b


def watch_holdings(research: sqlite3.Connection, market: sqlite3.Connection, positions: list[sa.Position],
                   now: datetime) -> list[str]:
    """Add held stocks (and options' underlyings) with a stored listing to the stock watchlist. Never removes one."""
    added = []
    for symbol in sorted({p.underlying or p.symbol for p in positions if p.asset_type in WATCH_TYPES}):
        if market.execute("SELECT 1 FROM stk_listings WHERE symbol = ?", (symbol,)).fetchone() is None:
            continue
        cur = research.execute("INSERT OR IGNORE INTO stk_watchlist(symbol, added_at) VALUES (?, ?)",
                               (symbol, to_iso(now)))
        if cur.rowcount:
            added.append(symbol)
    return added


async def read(client: sa.SchwabAccountClient, research: sqlite3.Connection, now: datetime, *,
               trades: bool) -> tuple[list[sa.Account], list[sa.Position], list[sa.Fill]]:
    """One read: account numbers → positions → (when ``trades``) each account's TRADE transactions since the last
    stored fill (a week's overlap; a year on the first read)."""
    numbers = await client.accounts()
    accounts, positions = await client.positions(numbers)
    fills: list[sa.Fill] = []
    if trades:
        for account in accounts:
            row = research.execute("SELECT MAX(traded_at) FROM stk_account_fills WHERE account_key = ?",
                                   (account.key,)).fetchone()
            start = (datetime.fromisoformat(row[0]) - OVERLAP) if row and row[0] else now - FIRST_LOOKBACK
            fills.extend(await client.trades(account, max(start, now - FIRST_LOOKBACK), now))
    return accounts, positions, fills


ClientFactory = Callable[[sqlite3.Connection, httpx.AsyncClient], sa.SchwabAccountClient]


async def cycle(paths: DeskPaths, loaded: LoadedConfig, now: datetime, *, manual: bool = False,
                factory: ClientFactory | None = None) -> dict[str, Any] | None:
    """Read the account if the switch is on and a slot is due (or ``manual``); None when nothing was due."""
    from desk.stocks.repo import StocksDatabases

    if not enabled(paths):
        return None
    dbs = StocksDatabases.open(paths)
    research = dbs.research
    try:
        if manual:
            last = research.execute("SELECT MAX(at) FROM stk_account_pulls WHERE slot = 'manual'").fetchone()[0]
            if last and now - datetime.fromisoformat(last) < MANUAL_GAP:
                raise DeskError("The account was read less than a minute ago; try again shortly")
            slot: tuple[str, bool] | None = ("manual", True)
        else:
            slot = due(research, now)
        if slot is None:
            return None
        label, after_close = slot
        first = research.execute("SELECT 1 FROM stk_account_pulls WHERE outcome = 'ok' LIMIT 1").fetchone() is None
        secrets = load_secrets(paths.secrets_file)
        async with httpx.AsyncClient(trust_env=True, follow_redirects=False) as http_client:
            client = (factory or (lambda runtime, h: build_client(paths, loaded, secrets, runtime, h)))(
                dbs.runtime, http_client)
            try:
                if not client.configured or client.tokens.relogin_required():
                    return _record(research, now, label, "signed_out", SIGNED_OUT)
                accounts, positions, fills = await read(client, research, now, trades=after_close or first)
            except sa.AccountNotEnabled:
                return _record(research, now, label, "not_enabled", NOT_ENABLED)
            except SchwabAuthError:
                return _record(research, now, label, "signed_out", SIGNED_OUT)
            except (ProviderError, DeskError) as exc:
                log.warning("schwab account read failed", extra={"error": type(exc).__name__})
                return _record(research, now, label, "error",
                               "Schwab did not answer the account read; the last saved positions are shown. "
                               "It is tried again at the next scheduled time.")
        result = _record(research, now, label, "ok", None, accounts=accounts, positions=positions, fills=fills)
        result["watched"] = watch_holdings(research, dbs.market, positions, now)
        return result
    finally:
        dbs.close()


# ── for other features ───────────────────────────────────────────────────────────────────────────
def account_positions(conn: sqlite3.Connection) -> list[AccountPosition]:
    """The latest successful read's positions (every account), for the Risk panel and other read-only features.

    ``conn``: the Stocks research database. Empty when the switch was never on or no read has succeeded. Check
    ``taken_at`` for age: a read failing later keeps the previous positions here."""
    row = conn.execute("SELECT id FROM stk_account_pulls WHERE outcome = 'ok' ORDER BY id DESC LIMIT 1").fetchone()
    if row is None:
        return []
    out = []
    for r in conn.execute("SELECT account_last4, account_key, taken_at, asset_type, symbol, underlying, put_call, "
                          "strike, expiration, quantity, average_price, market_value, day_pnl, day_pnl_pct, source "
                          "FROM stk_account_positions WHERE pull_id = ? ORDER BY account_last4, symbol", (row[0],)):
        out.append(AccountPosition(r[0], r[1], datetime.fromisoformat(r[2]), r[3], r[4], r[5], r[6], r[7],
                                   date.fromisoformat(r[8]) if r[8] else None, r[9], r[10], r[11], r[12], r[13],
                                   r[14]))
    return out


__all__ = ["NOT_ENABLED", "SIGNED_OUT", "AccountPosition", "account_positions", "build_client", "current_slot",
           "cycle", "due", "enabled", "last_pull", "read", "set_enabled", "watch_holdings"]
