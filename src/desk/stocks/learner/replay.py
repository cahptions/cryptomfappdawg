"""Replay pre-training data: the same candidates and labels built from the real past prices the replays already
downloaded (docs/STOCKS_LEARNER.md → Replay examples), stored with source ``replay`` and never mixed with forward rows
without that flag.

Each **decision day** is the first session of a week (``play_replay.decision_days``) whose replay inputs are complete
(``play_replay.Inputs.ready``: every name's real monthly listing as of that day and its ladder of contracts' daily
bars; the lotto names' weekly listings when those are complete too). The day's chain for a name is every downloaded
contract that traded that day, priced at its **real closing trade with the replay's stated spread**
(``play_replay.SPREAD``), its volume standing in for open interest; ``plays.table`` prices it exactly as a live chain.
The grid, its caps and the features are the forward ones (``experience.build_groups``, ``features``), read as they
stood before the day; the replayed Plays picks of that day are added and flagged (``replay_plays:<section>#<rank>``).
There is no exploration slice in replay (nothing could ever trade it).

Labels come from the same contracts' later daily bars (``labels.ReplayPrices``): a session without a trade in a
contract is a missing value for it, never an estimate. The chains are sparse (about fourteen strikes a name and month),
so most dial settings find no strike within ``actions.DELTA_TOLERANCE`` and fewer candidates a day exist than forward.
"""

from __future__ import annotations

import json
import time
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any

from desk.stocks import calendar as cal
from desk.stocks.learner import actions as act
from desk.stocks.learner import experience as ex
from desk.stocks.learner import features as ft
from desk.stocks.repo import StocksMarket, StocksResearch, day_of

DAYS_PER_PASS = 4                    # decision days built per collection pass (each a second or two)


def picks_on(market: StocksMarket, d: date) -> dict[str, list[tuple[str, list[dict[str, Any]]]]]:
    from desk.stocks import play_replay

    out: dict[str, list[tuple[str, list[dict[str, Any]]]]] = defaultdict(list)
    try:
        rows = market.conn.execute("SELECT category, rank, symbol, legs_json FROM stk_replay_plays WHERE method=? AND "
                                   "session=?", (play_replay.method(), day_of(d))).fetchall()
    except Exception:  # a database from before the section replay
        return out
    for category, rank, symbol, legs_json in rows:
        out[symbol].append((f"replay_plays:{category}#{rank}", json.loads(legs_json)))
    return out


def record_day(market: StocksMarket, research: StocksResearch, inputs: Any, d: date, now: datetime) -> dict[str, Any]:
    """One decision day's replay candidates (append-only; a recorded day is skipped)."""
    from desk.stocks import play_replay

    started = time.monotonic()
    ctx = play_replay._context(market, d)
    items: dict[str, dict[date, Any]] = {s: {e: inputs.lists[(s, e)] for e in inputs.monthly(d)} for s in inputs.names}
    if inputs.lotto_ready(d):
        for s in inputs.lotto_names:
            items.setdefault(s, {}).update({e: inputs.lotto[(s, e)] for e in inputs.weekly(d)})
    tables = play_replay.tables(market, d, ctx, items)
    reader = ft.Reader(market.conn, research.conn, d)
    spy = next((tb.u for tb in tables if tb.u.symbol == "SPY"), None)
    market_x = ft.market_features(reader, spy, ctx.rate)
    at = cal.session_close(d).isoformat()
    groups, supplied = ex.build_groups(tables, ctx, lambda u: reader, market_x, picks_on(market, d),
                                       seed=f"replay:{d.isoformat()}:{act.version_hash()}", decided_at=lambda u: at,
                                       explore=False)
    for g in groups:
        g.price_source = play_replay.SOURCE
    out = ex.save(research.conn, "replay", d, groups, now, time.monotonic() - started)
    return {**out, "supplied": supplied}


def step(market: StocksMarket, research: StocksResearch, now: datetime, *, days: int = DAYS_PER_PASS) -> dict[str, Any]:
    """Build up to ``days`` decision days not yet recorded, newest first (recent replay days label soonest)."""
    from desk.stocks import play_replay

    today = cal.new_york(now).date()
    inputs = play_replay.Inputs(market, today)
    if not inputs.months or not inputs.names:
        return {"days": 0, "candidates": 0, "waiting": "no replay downloads yet"}
    done = {int(r[0]) for r in research.conn.execute(
        "SELECT session FROM stk_learn_days WHERE source='replay' AND actions_version=?", (act.version_hash(),))}
    first = play_replay.listed_on(min(inputs.months))
    built = candidates = 0
    for d in reversed(play_replay.decision_days(first, min(today - timedelta(days=1), max(inputs.months)))):
        if built >= days:
            break
        if day_of(d) in done or not inputs.ready(d):
            continue
        got = record_day(market, research, inputs, d, now)
        built += 1
        candidates += int(got["candidates"])
    return {"days": built, "candidates": candidates}


__all__ = ["DAYS_PER_PASS", "picks_on", "record_day", "step"]
