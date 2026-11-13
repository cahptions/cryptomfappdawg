"""Every Massive Options Basic download, through one queue.

Massive's free Options Basic plan allows 5 requests a minute for the whole account, so every historical option
download shares one budget a collection pass: 8 requests while the market is open (a pass stays short while chains and
quotes matter) and 40 when it is closed. The work is split fairly, in order: each task gets an equal share of what is
left and hands back what it does not need, so a finished task never holds up the others and none starves.

Tasks, in order: the contracts the latest historical replay traded (``options_history``), the implied-volatility
history (``iv_history``), the straddles before past reports the replay wanted (``real_options``), the real monthly
listings of the stocks the replay trades (``options_history.listings_task``), the lottos on real prices
(``lotto_history``) and every Plays section on real past prices (``play_replay``, which takes at most 6 requests a pass
while the market is closed and 1 while it is open).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from desk.core.errors import DeskError, ProviderError
from desk.stocks import calendar as cal
from desk.stocks.repo import StocksMarket

PER_PASS_OPEN, PER_PASS_CLOSED = 8, 40
RATE_PER_MINUTE = 5
Task = Callable[[Any, StocksMarket, Path, datetime, int], Awaitable[dict[str, Any]]]


def budget(now: datetime) -> int:
    return PER_PASS_OPEN if cal.market_session(now)["state"] in ("pre", "open") else PER_PASS_CLOSED


def problem(exc: Exception) -> str | None:
    """What stops a download (None for a contract Massive has no bars for, which is simply empty)."""
    if isinstance(exc, ProviderError):
        if exc.status_code == 404:
            return None
        if exc.status_code in (401, 403):
            return (f"Massive refused option data (HTTP {exc.status_code}). Add the free Options Basic plan to the "
                    "account that owns MASSIVE_API_KEY.")
        return f"Massive could not answer: {str(exc)[:160]}"
    return f"Massive's answer could not be read: {str(exc)[:160]}"


async def ask(call: Awaitable[list[dict[str, Any]]]) -> tuple[list[dict[str, Any]], str | None]:
    """One request: its rows, or no rows and why it stopped (a 404 is just no rows)."""
    try:
        return await call, None
    except (ProviderError, DeskError) as exc:
        return [], problem(exc)


def tasks() -> list[tuple[str, Task]]:
    from desk.stocks import iv_history, lotto_history, options_history, play_replay, real_options

    return [("replay_contracts", options_history.task), ("iv_history", iv_history.task),
            ("earnings_history", real_options.task), ("replay_listings", options_history.listings_task),
            ("lotto_history", lotto_history.task), ("plays_replay", play_replay.task)]


async def job(massive: Any, market: StocksMarket, home: Path, now: datetime) -> dict[str, Any]:
    """One pass of the queue: each task in order gets an equal share of the requests left."""
    if not getattr(massive, "configured", False):
        return {"ok": False, "note": "No Massive key: option history needs MASSIVE_API_KEY (free Options Basic)."}
    total, used = budget(now), 0
    done: dict[str, Any] = {}
    stop = None
    pending = tasks()
    while pending and used < total and stop is None:
        name, task = pending.pop(0)
        share = max(1, (total - used) // (len(pending) + 1))
        got = await task(massive, market, home, now, share)
        used += int(got.get("requests", 0))
        done[name] = got
        if got.get("error") and "refused" in str(got["error"]):
            stop = got["error"]                      # a key or plan problem stops every task this pass
    detail = {"budget": total, "requests": used, "tasks": {k: v.get("requests", 0) for k, v in done.items()}}
    market.job("massive_options", now, error=stop, detail=detail)
    return {"ok": stop is None, **detail, "detail": done, **({"error": stop} if stop else {})}


__all__ = ["PER_PASS_CLOSED", "PER_PASS_OPEN", "RATE_PER_MINUTE", "ask", "budget", "job", "problem", "tasks"]
