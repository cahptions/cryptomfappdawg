"""Every Plays section replayed on real past option prices (docs/STOCKS_PLAYS.md, "Replay on real past prices").

Forward plays take months to grade. This replays each section's own selection rules on the last two years of real
option prices (Massive's Options Basic archive: daily bars of every contract, and past listings via ``as_of``) and
grades every pick at expiry exactly as a forward play is graded. Results are **replay** results: stored in their own
tables (market migration ``0013_plays_replay.sql``), shown apart from the forward record, and never used to grade,
calibrate, label, size or unlock anything forward.

**Download** (``task``, a modest share of the one Massive queue): for the ``NAMES`` most traded optioned stocks (the
first of the IV history's names, the core ETFs first) and every standard monthly expiry already past, the contracts
Massive listed nine weeks before it (one listing request, the same day the IV history lists, so its at-the-money call
and put are reused), then a chosen ladder of strikes' daily bars (one request each): the at-the-money call and put and
six out-of-the-money calls and puts at 0.25-1.75 standard deviations of the move to expiry.

**Replay**, on the first session of every week whose inputs are fully downloaded: each name's chain that day is every
downloaded contract that traded (volume > 0), priced at its real closing trade with an assumed spread (``SPREAD``) and
its volume standing in for open interest (daily bars carry none). ``plays.table`` prices it exactly as a live chain
(the chain's own smile where it fits, ``chain_odds``), each replayed section's selector runs on it, one pick per stock,
the top 12, ``plays.finish`` makes each pick a play, and ``plays.twin`` draws its random twin from the same day's
chains. The section reads only that day's and earlier data: the stock's closes to that day, IV30 from that day's chain
and IV rank from the IV history's closing-trade series before it.

Sections that need inputs the replay cannot rebuild as they stood each day are skipped with the reason (``SKIPPED``);
sections whose live rule reads the committee's view replay only on underlyings that have none live (ETFs, which the
research universe leaves out), where the live rule is reproduced exactly (``ETF_ONLY``).

**Grade**, at expiry, as ``plays.grade``: settled at the stock's close on expiry day (none within four days, or a split
in between, leaves it unresolved), net of the opening fees; the entry was the real close plus half the assumed spread
for a bought leg and minus it for a sold one. Each pick's chances (KESTREL's raw chance, the market's, profit and the
return targets) are graded against what happened into a replay calibration, per event and section, never mixed into
the forward calibration.
"""

from __future__ import annotations

import json
import math
import random
import sqlite3
import statistics
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from desk.core.clock import to_iso
from desk.core.hashing import canonical_json, sha256_hex
from desk.stocks import calendar as cal
from desk.stocks import odds as od
from desk.stocks import options as opt
from desk.stocks.contracts import OptionQuote
from desk.stocks.paper.fills import OPTION_FEE_PER_CONTRACT as FEE
from desk.stocks.repo import StocksMarket, date_of, day_of
from desk.storage.db import transaction

VERSION = 1
PURPOSE = "plays_replay"
SOURCE = "massive_daily_bars"
NAMES = 20                       # the first of the IV history's names (the core ETFs, then by dollar volume)
LEAD_DAYS = 63                   # listed nine weeks before expiry, as the IV history lists (its pair is reused)
LADDER = (0.25, 0.5, 0.75, 1.0, 1.35, 1.75)   # out-of-the-money strikes, in standard deviations of the move
WINDOW = (7, 60)                 # days to a monthly expiry a replayed section can use (the sections' own windows)
HALF_SPREAD, MIN_HALF = 0.03, 0.015          # assumed quoted spread: 6% of the close, at least 3 cents wide
PER_PASS_OPEN, PER_PASS_CLOSED = 1, 6        # the replay's share of a Massive pass, at most
PASSES_PER_DAY = 100             # closed-market passes a day, for the days-to-complete estimate
PER_NAME_MONTH = 1 + 2 * len(LADDER)         # listing + ladder bars (the at-the-money pair comes from the IV history)
SPREAD = (f"Entry at the real closing trade plus half an assumed spread of {2 * HALF_SPREAD:.0%} of the price (at least "
          f"{2 * MIN_HALF * 100:.0f} cents) for a bought leg, minus it for a sold one, plus ${FEE:.2f} a contract; "
          "settled at intrinsic value at expiry, as forward plays.")
EVERY = ("edge", "cheap_vol", "strangles")
ETF_ONLY = ("sell_premium", "iron_condors", "iron_butterflies", "cash_secured_puts")
LOTTO = "lottos"
REPLAYED = (*EVERY, *ETF_ONLY, LOTTO)
SKIPPED = {
    "likely_itm": "needs the committee's lean on each stock as it stood that day, which only the full desk analysis of "
                  "every stock can rebuild; ETFs have no committee view, so the live rule picks none of them",
    "debit_spreads": "needs the committee's lean as it stood that day (as Most likely to finish ITM)",
    "unusual": "needs open interest and each contract's earlier volumes and quote positions, which daily bars do not "
               "carry",
    "earnings": "needs the results dates as they were announced then, which are not stored point in time (the "
                "historical replay trades earnings straddles on real prices separately)",
    "trend_calls": "needs the momentum desk's score, a ranking against every stock that day",
    "bear_puts": "needs the valuation and momentum desks and red flags as they stood that day",
    "squeeze": "needs the short-interest desk as published that day",
    "watchlist": "needs your watchlist as it stood that day, which is not recorded",
}
METHOD = ("Weekly decision days, real closing trades from Massive's daily bars, the live section rules on each day's "
          "chain, graded at expiry on the stock's close.")


def method() -> str:
    """The replay's rule fingerprint: its own settings and the live rules it runs (a change records afresh)."""
    from desk.stocks import plays

    return sha256_hex(canonical_json({
        "version": VERSION, "names": NAMES, "lead": LEAD_DAYS, "ladder": LADDER, "window": WINDOW,
        "spread": [HALF_SPREAD, MIN_HALF], "replayed": REPLAYED, "etf_only": ETF_ONLY, "plays": plays.VERSION,
        "categories": [c.content() for c in plays.CATEGORIES if c.id in REPLAYED], "odds": od.MODEL_VERSION,
        "per_category": plays.PER_CATEGORY}))[:16]


# ── what to download ─────────────────────────────────────────────────────────────────────────────
def replay_names(market: StocksMarket, today: date) -> list[str]:
    from desk.stocks import iv_history

    return iv_history.names(market, today)[:NAMES]


def listed_on(expiry: date) -> date:
    as_of = expiry - timedelta(days=LEAD_DAYS)
    return as_of if cal.is_trading_day(as_of) else cal.previous_trading_day(as_of)


def _close_on_or_before(conn: sqlite3.Connection, symbol: str, day: date) -> float | None:
    row = conn.execute("SELECT close FROM stk_bars WHERE symbol=? AND day<=? AND day>=? ORDER BY day DESC LIMIT 1",
                       (symbol, day_of(day), day_of(day - timedelta(days=7)))).fetchone()
    return float(row[0]) if row else None


def _volatility(conn: sqlite3.Connection, symbol: str, day: date) -> float:
    """The IV history's IV30 near ``day``, else 20-session realized volatility, else 35%: only to place the ladder."""
    row = conn.execute("SELECT iv30 FROM stk_iv_daily WHERE symbol=? AND day<=? AND day>=? ORDER BY day DESC LIMIT 1",
                       (symbol, day_of(day), day_of(day - timedelta(days=10)))).fetchone()
    if row and row[0]:
        return float(row[0])
    from desk.stocks.plays import _realized

    closes = [r[0] for r in conn.execute("SELECT close FROM stk_bars WHERE symbol=? AND day<=? ORDER BY day DESC LIMIT 21",
                                         (symbol, day_of(day)))][::-1]
    return _realized(closes) or 0.35


def choose(listed: Iterable[dict[str, Any]], spot: float, sigma: float, expiry: date) -> list[dict[str, Any]]:
    """The at-the-money call and put (the listed strike nearest ``spot`` with both), then the out-of-the-money call
    and put strikes nearest each ``LADDER`` step of the move to expiry; each contract once."""
    by: dict[float, dict[str, str]] = defaultdict(dict)
    for c in listed:
        if c["expiration"] == expiry:
            by[float(c["strike"])][c["kind"]] = c["ticker"]
    out: dict[str, dict[str, Any]] = {}
    both = [k for k, v in by.items() if {"call", "put"} <= v.keys()]
    if both:
        k = min(both, key=lambda x: (abs(x - spot), x))
        for kind in ("call", "put"):
            out[by[k][kind]] = {"occ": by[k][kind], "kind": kind, "strike": k}
    move = sigma * math.sqrt(LEAD_DAYS / 365)
    for kind, sign in (("call", 1), ("put", -1)):
        side = [k for k, v in by.items() if kind in v and (k > spot if kind == "call" else k < spot)]
        for z in LADDER:
            if not side:
                break
            target = spot * math.exp(sign * z * move)
            k = min(side, key=lambda x: (abs(x - target), x))
            out[by[k][kind]] = {"occ": by[k][kind], "kind": kind, "strike": k}
    return sorted(out.values(), key=lambda c: (c["kind"], c["strike"]))


async def download(massive: Any, market: StocksMarket, now: datetime, budget: int) -> dict[str, Any]:
    """Up to ``budget`` requests: the next name-months' listings and ladders, newest months first."""
    from desk.stocks import iv_history
    from desk.stocks.massive_options import ask

    today = cal.new_york(now).date()
    names, have, fetched = replay_names(market, today), iv_history.lists(market.conn, PURPOSE), market.option_bar_fetches()
    used, problem = 0, None
    for expiry in iv_history.expiries(today):
        for symbol in names:
            if used >= budget or problem:
                break
            item = have.get((symbol, expiry))
            if item is None:
                as_of = listed_on(expiry)
                spot = _close_on_or_before(market.conn, symbol, as_of)
                chosen: list[dict[str, Any]] = []
                listed: list[dict[str, Any]] = []
                if spot is not None:
                    sigma = _volatility(market.conn, symbol, as_of)
                    reach = math.exp(2.2 * sigma * math.sqrt(LEAD_DAYS / 365))
                    listed, problem = await ask(massive.option_contracts(symbol, as_of, expiry, expiry,
                                                                         strikes=(spot / reach, spot * reach)))
                    used += 1
                    if problem:
                        break
                    chosen = choose(listed, spot, sigma, expiry)
                note = None if chosen else f"no stored {symbol} price on {as_of}" if spot is None else "nothing listed"
                iv_history.save_list(market.conn, PURPOSE, symbol, expiry, as_of, len(listed), chosen, note, now)
                item = have[(symbol, expiry)] = {"as_of": as_of, "contracts": chosen, "listed": len(listed)}
            for c in item["contracts"]:
                if used >= budget or c["occ"] in fetched:
                    continue
                bars, problem = await ask(massive.option_bars(c["occ"], item["as_of"], expiry))
                used += 1
                if problem:
                    break
                market.save_option_bars(c["occ"], item["as_of"], expiry, bars, now)
                fetched[c["occ"]] = expiry
        if used >= budget or problem:
            break
    return {"requests": used, **({"error": problem} if problem else {})}


def coverage(market: StocksMarket, today: date) -> dict[str, Any]:
    """Name-months listed and fetched, the requests left and how long they take at full speed and at this share."""
    from desk.stocks import iv_history

    names, months = replay_names(market, today), iv_history.expiries(today)
    have, fetched = iv_history.lists(market.conn, PURPOSE), market.option_bar_fetches()
    listed = sum(1 for s in names for e in months if (s, e) in have)
    wanted = [c["occ"] for s in names for e in months for c in (have.get((s, e)) or {}).get("contracts", [])]
    left = (len(names) * len(months) - listed) * PER_NAME_MONTH + sum(1 for o in wanted if o not in fetched)
    return {"names": len(names), "months": len(months), "name_months": len(names) * len(months),
            "name_months_listed": listed, "contracts_wanted": len(wanted),
            "contracts_fetched": sum(1 for o in wanted if o in fetched),
            "requests_planned": len(names) * len(months) * PER_NAME_MONTH, "requests_left": left,
            "hours_left_at_full_speed": round(left / 5 / 60, 1),
            "days_left_at_share": round(left / (PER_PASS_CLOSED * PASSES_PER_DAY), 1),
            "share": {"open": PER_PASS_OPEN, "closed": PER_PASS_CLOSED, "passes_per_day": PASSES_PER_DAY}}


# ── the replay ───────────────────────────────────────────────────────────────────────────────────
def decision_days(first: date, last: date) -> list[date]:
    """The first session of every week from ``first`` to ``last``."""
    out: list[date] = []
    seen: set[tuple[int, int]] = set()
    for d in cal.trading_days(first, last):
        week = d.isocalendar()[:2]
        if week not in seen:
            seen.add(week)
            out.append(d)
    return out


class Inputs:
    """What the replay has downloaded, read once: listings by purpose and the contracts fetched."""

    def __init__(self, market: StocksMarket, today: date) -> None:
        from desk.stocks import iv_history, lotto_history

        self.market, self.today = market, today
        self.names = replay_names(market, today)
        self.months = iv_history.expiries(today)
        self.lists = iv_history.lists(market.conn, PURPOSE)
        self.iv = iv_history.lists(market.conn, iv_history.PURPOSE)
        self.lotto = {k: v for k, v in lotto_history._listings(market.conn).items()}
        self.lotto_names = [s for s in lotto_history.NAMES]
        self.weeklies = lotto_history.expiries(today)
        self.fetched = market.option_bar_fetches()
        self.etfs = {r[0] for r in market.conn.execute("SELECT symbol FROM stk_listings WHERE etf=1")}

    def _complete(self, item: Mapping[str, Any] | None) -> bool:
        return item is not None and all(c["occ"] in self.fetched for c in item["contracts"])

    def monthly(self, d: date) -> list[date]:
        return [e for e in self.months if listed_on(e) <= d and WINDOW[0] <= (e - d).days <= WINDOW[1]]

    def weekly(self, d: date) -> list[date]:
        from desk.stocks.lotto_history import DTE
        from desk.stocks.lotto_history import LEAD_DAYS as LOTTO_LEAD

        return [e for e in self.weeklies if e - timedelta(days=LOTTO_LEAD) <= d and DTE[0] <= (e - d).days <= DTE[1]]

    def ready(self, d: date) -> bool:
        """Every name's listing and ladder for every monthly expiry the day can use, and the IV history behind its
        IV rank (the year before it), are downloaded."""
        expiries = self.monthly(d)
        if not expiries or not self.names:
            return False
        first = d - timedelta(days=400)
        for s in self.names:
            if not all(self._complete(self.lists.get((s, e))) for e in expiries):
                return False
            if not all(self._complete(self.iv.get((s, e))) for e in self.months if first < e and listed_on(e) <= d):
                return False
        return True

    def lotto_ready(self, d: date) -> bool:
        expiries = self.weekly(d)
        return bool(expiries) and all(self._complete(self.lotto.get((s, e))) for s in self.lotto_names
                                      for e in expiries)


def _quotes(conn: sqlite3.Connection, symbol: str, contracts: Sequence[Mapping[str, Any]], expiry: date,
            d: date) -> list[OptionQuote]:
    """Each contract that traded on ``d``, at its real close with the assumed spread; its volume stands in for open
    interest (daily bars carry none)."""
    if not contracts:
        return []
    marks = ",".join("?" * len(contracts))       # placeholders only; values are bound
    rows = {r[0]: (float(r[1]), int(r[2] or 0)) for r in conn.execute(
        f"SELECT occ, close, volume FROM stk_option_bars WHERE day = ? AND occ IN ({marks})",  # noqa: S608
        (day_of(d), *[c["occ"] for c in contracts]))}
    out = []
    at = cal.session_close(d)
    for c in contracts:
        close, volume = rows.get(c["occ"], (0.0, 0))
        if close <= 0 or volume <= 0:
            continue
        half = max(MIN_HALF, HALF_SPREAD * close)
        out.append(OptionQuote(c["occ"], symbol, c["kind"], float(c["strike"]), expiry, round(max(close - half, 0.01), 4),
                               round(close + half, 4), close, volume, volume, None, at))
    return out


def underlying(market: StocksMarket, symbol: str, d: date, chains: dict[date, list[OptionQuote]],
               rate: float) -> Any:
    """``plays.Underlying`` as the section would have seen it on ``d``: no committee view, the stock's closes to that
    day, realized volatility, IV30 from the day's own chain and IV rank from the IV history before it."""
    from desk.stocks import iv_history
    from desk.stocks import plays as pl
    from desk.stocks.hub import adjusted_bars

    price = market.conn.execute("SELECT close FROM stk_bars WHERE symbol=? AND day=?", (symbol, day_of(d))).fetchone()
    if price is None or not price[0]:
        return None
    rows = [dict(r) for r in market.conn.execute(
        "SELECT day, close FROM stk_bars WHERE symbol = ? AND day <= ? ORDER BY day DESC LIMIT ?",
        (symbol, day_of(d), pl.HISTORY_SESSIONS))][::-1]
    u = pl.Underlying(symbol, float(price[0]), cal.session_close(d).isoformat(), SOURCE, chains)
    u.closes = [r["close"] for r in adjusted_bars(market.conn, symbol, rows)]
    u.rv = pl._realized(u.closes)
    u.iv30 = opt.daily_summary(chains, u.price, d, rate).get("iv30")
    if u.iv30:
        prior = iv_history.series(market.conn, [symbol], d - timedelta(days=iv_history.RANK_DAYS),
                                  d - timedelta(days=1)).get(symbol, {})
        values = [prior[k] for k in sorted(prior)]
        u.iv_rank = opt.iv_rank(values, u.iv30)[0]
        u.iv_basis = {"days": len(values), "collected": 0, "massive": len(values),
                      "first": date_of(min(prior)).isoformat() if prior else None}
    return u


def _splits(conn: sqlite3.Connection, symbol: str, first: date, last: date) -> bool:
    return conn.execute("SELECT 1 FROM stk_splits WHERE symbol=? AND execution_date>? AND execution_date<=?",
                        (symbol, day_of(first), day_of(last))).fetchone() is not None


def tables(market: StocksMarket, d: date, ctx: Any, items: Mapping[str, Mapping[date, Mapping[str, Any]]]) -> list[Any]:
    """Each name's replayed chain on ``d`` priced by ``plays.table``; a split inside an expiry's life drops it."""
    from desk.stocks import plays as pl

    out = []
    for symbol, by_expiry in sorted(items.items()):
        chains = {}
        for expiry, item in by_expiry.items():
            if _splits(market.conn, symbol, item["as_of"], expiry):
                continue
            quotes = _quotes(market.conn, symbol, item["contracts"], expiry, d)
            if quotes:
                chains[expiry] = quotes
        if not chains:
            continue
        u = underlying(market, symbol, d, chains, ctx.rate)
        if u is not None and (tb := pl.table(u, ctx)) is not None:
            out.append(tb)
    return out


def _pick(category: str, pool: Sequence[Any], ctx: Any) -> list[Any]:
    """The section's live ranking: every candidate by score, one per stock, the top ``plays.PER_CATEGORY``."""
    from desk.stocks import plays as pl

    found = [c for tb in pool for c in pl.SELECTORS[category](tb, ctx)]
    found.sort(key=lambda c: (-c.score, c.table.u.symbol))
    out, per_symbol = [], Counter[str]()
    for c in found:
        if per_symbol[c.table.u.symbol]:
            continue
        per_symbol[c.table.u.symbol] += 1
        out.append(c)
        if len(out) >= pl.PER_CATEGORY:
            break
    return out


def _chances(play: Mapping[str, Any]) -> dict[str, Any]:
    """The model's own chances at the pick (before any calibration), what the replay calibration grades, and the return
    that is the maximum gain (to grade its chance)."""
    from desk.stocks.plays import raw_chances

    out = {k: v for k, v in raw_chances(play).items() if k != "touch"}
    for key in ("itm_kestrel_raw", "itm_market"):
        if play.get(key) is not None:
            out[key] = float(play[key])
    gain = play.get("max_gain")
    return {"chances": {k: round(v, 4) for k, v in out.items()},
            "max_gain_return": None if not gain else round(float(gain) / float(play["max_loss"]), 6)}


def _context(market: StocksMarket, d: date) -> Any:
    from desk.stocks import plays as pl

    return pl.Context(d, cal.session_close(d), pl._rate(market, d), None, 0.0, None, {}, [])


def record_day(market: StocksMarket, d: date, sections: Mapping[str, Sequence[Any]], m: str, now: datetime) -> int:
    """Each section's picks on ``d`` from its pool of replayed chains, with twins from the same pool; append-only."""
    from desk.stocks import plays as pl

    ctx = _context(market, d)
    rows, days = [], []
    for category, pool in sections.items():
        picks = []
        for rank, c in enumerate(_pick(category, pool, ctx), start=1):
            play = {**pl.finish(c, ctx), "category": category, "rank": rank}
            twin = pl.twin(play, pool, d)
            picks.append((rank, play, twin))
        for rank, p, twin in picks:
            rows.append((m, day_of(d), category, rank, p["symbol"], p["structure"], p["action"],
                         day_of(date.fromisoformat(p["expiration"])), p["event"], p["event_kind"],
                         float(p["event_strike"]), float(p["event_width"]), float(p["entry"]), float(p["max_loss"]),
                         int(p["dte"]), canonical_json(p["legs"]), canonical_json(_chances(p)),
                         (p.get("odds_basis") or {}).get("basis", "lognormal"),
                         None if twin is None else canonical_json(twin), SOURCE, to_iso(now)))
        days.append((m, day_of(d), category, len(picks), len(pool), to_iso(now)))
    with transaction(market.conn):
        market.conn.executemany("INSERT OR IGNORE INTO stk_replay_plays VALUES "
                                "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        market.conn.executemany("INSERT OR IGNORE INTO stk_replay_days VALUES (?,?,?,?,?,?)", days)
    return len(rows)


def record(market: StocksMarket, now: datetime, *, limit: int = 40) -> dict[str, Any]:
    """Replay every decision day whose inputs are complete and not yet replayed under the current rules (at most
    ``limit`` days a call, so a pass stays short while a backlog clears)."""
    today = cal.new_york(now).date()
    inputs, m = Inputs(market, today), method()
    if not inputs.months:
        return {"days": 0, "plays": 0}
    done: dict[int, set[str]] = defaultdict(set)
    for r in market.conn.execute("SELECT session, category FROM stk_replay_days WHERE method=?", (m,)):
        done[int(r[0])].add(r[1])
    first = listed_on(min(inputs.months))
    days = plays_ = 0
    for d in decision_days(first, min(today - timedelta(days=1), max(inputs.months))):
        if days >= limit:
            break
        main = [c for c in (*EVERY, *ETF_ONLY) if c not in done[day_of(d)]]
        lotto = LOTTO not in done[day_of(d)]
        want_main = bool(main) and inputs.ready(d)
        want_lotto = lotto and inputs.lotto_ready(d)
        if not (want_main or want_lotto):
            continue
        ctx = _context(market, d)
        sections: dict[str, Sequence[Any]] = {}
        if want_main:
            items = {s: {e: inputs.lists[(s, e)] for e in inputs.monthly(d)} for s in inputs.names}
            pool = tables(market, d, ctx, items)
            for c in main:
                sections[c] = pool if c in EVERY else [tb for tb in pool if tb.u.symbol in inputs.etfs]
        if want_lotto:
            items = {s: {e: inputs.lotto[(s, e)] for e in inputs.weekly(d)} for s in inputs.lotto_names}
            sections[LOTTO] = tables(market, d, ctx, items)
        plays_ += record_day(market, d, sections, m, now)
        days += 1
    return {"days": days, "plays": plays_}


def _net(action: str, entry: float, value: float, legs: int, max_loss: float) -> float:
    """The return at expiry on the money at risk, after the opening fees: the same arithmetic as ``plays.grade``."""
    fees = FEE * legs / 100
    if action == "BUY":
        return (value - entry - fees) / (entry + fees)
    return (entry + value - fees) / (max_loss / 100)


def _settle(conn: sqlite3.Connection, symbol: str, session: int, expiration: int) -> float | None:
    bar = conn.execute("SELECT close FROM stk_bars WHERE symbol = ? AND day <= ? AND day >= ? ORDER BY day DESC LIMIT 1",
                       (symbol, expiration, expiration - 4)).fetchone()
    split = conn.execute("SELECT 1 FROM stk_splits WHERE symbol = ? AND execution_date > ? AND execution_date <= ?",
                         (symbol, session, expiration)).fetchone()
    return None if bar is None or split is not None else float(bar[0])


def grade(market: StocksMarket, now: datetime) -> int:
    """Settle every replayed pick (and its twin) whose expiry close is stored, as a forward play; append-only."""
    from desk.stocks.plays import settle_value

    conn = market.conn
    latest = conn.execute("SELECT MAX(day) FROM stk_bar_days WHERE rows > 0").fetchone()[0]
    if latest is None:
        return 0
    rows = []
    for p in conn.execute("SELECT p.* FROM stk_replay_plays p LEFT JOIN stk_replay_outcomes o USING (method, session, "
                          "category, rank) WHERE o.session IS NULL AND p.expiration <= ?", (latest,)).fetchall():
        close = _settle(conn, p["symbol"], p["session"], p["expiration"])
        if close is None:
            continue
        legs = json.loads(p["legs_json"])
        value = settle_value(legs, close)
        if p["event"] == "move":
            hit = abs(close - p["event_strike"]) > p["event_width"]
        else:
            hit = close > p["event_strike"] if p["event_kind"] == "call" else close < p["event_strike"]
        twin_net = None
        if p["twin_json"]:
            t = json.loads(p["twin_json"])
            twin_close = _settle(conn, t["symbol"], p["session"], day_of(date.fromisoformat(t["expiration"])))
            if twin_close is not None:
                twin_net = _net(t["action"], float(t["entry"]), settle_value(t["legs"], twin_close), len(t["legs"]),
                                float(t["max_loss"]))
        rows.append((p["method"], p["session"], p["category"], p["rank"], close, int(hit), value,
                     _net(p["action"], float(p["entry"]), value, len(legs), float(p["max_loss"])), twin_net,
                     to_iso(now)))
    with transaction(conn):
        conn.executemany("INSERT OR IGNORE INTO stk_replay_outcomes VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
    return len(rows)


# ── what it shows ────────────────────────────────────────────────────────────────────────────────
CHANCES = (("itm_kestrel_raw", "KESTREL"), ("itm_market", "Market"))
EVENTS = {"profit": "Profit at expiry", "target25": "+25% on the money at risk at expiry",
          "target50": "+50% on the money at risk at expiry", "target100": "+100% on the money at risk at expiry",
          "max_gain": "Maximum gain at expiry"}


def _happened(event: str, net: float, hit: bool, max_gain_return: float | None) -> bool | None:
    if event in ("itm_kestrel_raw", "itm_market"):
        return hit
    if event == "profit":
        return net > 0
    if event == "max_gain":
        return None if max_gain_return is None else net >= max_gain_return - 1e-3
    return net >= int(event[6:]) / 100 - 1e-9


def _table(pairs: Sequence[tuple[float, bool]], label: str) -> dict[str, Any]:
    bins: dict[int, list[tuple[float, bool]]] = defaultdict(list)
    for p, h in pairs:
        bins[min(int(p * 10), 9)].append((p, h))
    return {"label": label, "resolved": len(pairs),
            "points": [{"predicted": round(statistics.fmean(p for p, _ in v), 4),
                        "observed": round(sum(h for _, h in v) / len(v), 4), "n": len(v),
                        "bucket": f"{10 * b}–{10 * b + 10}%"} for b, v in sorted(bins.items())],
            "brier": round(statistics.fmean((p - h) ** 2 for p, h in pairs), 4) if pairs else None}


def calibration_of(rows: Sequence[sqlite3.Row]) -> dict[str, Any]:
    """The replay calibration of a set of resolved picks, shaped like Performance's forward one."""
    pairs: dict[str, list[tuple[float, bool]]] = defaultdict(list)
    for r in rows:
        stored = json.loads(r["chances_json"])
        gain = stored.get("max_gain_return")
        for event, p in stored["chances"].items():
            happened = _happened(event, float(r["net_return"]), bool(r["hit"]), gain)
            if happened is not None:
                pairs[event].append((float(p), happened))
    out: dict[str, Any] = {key: _table(pairs[key], label) for key, label in CHANCES}
    out["events"] = {e: {**_table(pairs[e], label), "outcomes": {}} for e, label in EVENTS.items()}
    return out


def versus(rows: Sequence[sqlite3.Row], draws: int = 2000) -> dict[str, Any] | None:
    """The picks' mean return minus their twins', session by session, with a bootstrap over sessions."""
    by: dict[int, list[float]] = defaultdict(list)
    for r in rows:
        if r["twin_net_return"] is not None:
            by[r["session"]].append(float(r["net_return"]) - float(r["twin_net_return"]))
    gaps = [statistics.fmean(v) for v in by.values()]
    if len(gaps) < 5:
        return None
    rng = random.Random(1)       # noqa: S311 - a reproducible resample
    boots = sorted(statistics.fmean(rng.choices(gaps, k=len(gaps))) for _ in range(draws))
    return {"sessions": len(gaps), "mean_gap": round(statistics.fmean(gaps), 4), "low": round(boots[int(0.05 * draws)], 4),
            "high": round(boots[int(0.95 * draws)], 4), "p_not_better": round(sum(b <= 0 for b in boots) / draws, 4)}


def view(conn: sqlite3.Connection, now: datetime | None = None) -> dict[str, Any]:
    """The replay's record per section and its calibration, for Performance and the Learning report: labelled
    replay throughout, never merged with the forward record."""
    from desk.stocks.plays import BY_CATEGORY

    m = method()
    base = {"version": VERSION, "method": m, "source": SOURCE, "spread": SPREAD, "about": METHOD,
            "label": "Replay on real past prices — not forward results",
            "note": "Replayed on real past option prices (Massive's daily bars). Replay results never grade, calibrate, "
                    "label or unlock anything forward.",
            "skipped": [{"id": k, "title": BY_CATEGORY[k].title, "emoji": BY_CATEGORY[k].emoji, "reason": v}
                        for k, v in SKIPPED.items()],
            "etf_only": list(ETF_ONLY)}
    try:
        plays_rows = conn.execute("SELECT p.*, o.net_return, o.hit, o.twin_net_return FROM stk_replay_plays p LEFT JOIN "
                                  "stk_replay_outcomes o USING (method, session, category, rank) WHERE p.method = ?",
                                  (m,)).fetchall()
        days = conn.execute("SELECT category, COUNT(*), SUM(plays) FROM stk_replay_days WHERE method = ? "
                            "GROUP BY category", (m,)).fetchall()
        earlier = conn.execute("SELECT COUNT(*) FROM stk_replay_plays WHERE method != ?", (m,)).fetchone()[0]
        job = conn.execute("SELECT detail_json FROM stk_jobs WHERE name = 'plays_replay'").fetchone()
    except sqlite3.OperationalError:          # a database from before the replay
        return {**base, "sections": [], "calibration": None, "by_section": {}}
    sessions = {r[0]: int(r[1]) for r in days}
    by: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for r in plays_rows:
        by[r["category"]].append(r)
    sections = []
    for category in REPLAYED:
        rows = by.get(category, [])
        done = [r for r in rows if r["net_return"] is not None]
        twins = [float(r["twin_net_return"]) for r in done if r["twin_net_return"] is not None]
        nets = [float(r["net_return"]) for r in done]
        c = BY_CATEGORY[category]
        sections.append({
            "id": category, "title": c.title, "emoji": c.emoji, "sessions": sessions.get(category, 0),
            "where": "ETFs only (no committee view live)" if category in ETF_ONLY else
                     "lotto names, weekly expiries" if category == LOTTO else "every replayed name",
            "plays": len(rows), "resolved": len(done),
            "mean_return": round(statistics.fmean(nets), 4) if nets else None,
            "median_return": round(statistics.median(nets), 4) if nets else None,
            "hit_rate": round(sum(r["hit"] for r in done) / len(done), 4) if done else None,
            "win_rate": round(sum(n > 0 for n in nets) / len(nets), 4) if nets else None,
            "twins": {"resolved": len(twins), "mean_return": round(statistics.fmean(twins), 4) if twins else None},
            "versus_twins": versus(done), "chain_basis": round(sum(r["basis"] == "chain" for r in rows) / len(rows), 4)
            if rows else None,
            "first": date_of(min(r["session"] for r in rows)).isoformat() if rows else None,
            "last": date_of(max(r["session"] for r in rows)).isoformat() if rows else None})
    resolved = [r for r in plays_rows if r["net_return"] is not None]
    detail = json.loads(job[0]) if job and job[0] else {}
    return {**base, "sections": sections, "plays": len(plays_rows), "resolved": len(resolved),
            "earlier_rules_plays": int(earlier),
            "calibration": calibration_of(resolved) if resolved else None,
            "by_section": {c: calibration_of([r for r in resolved if r["category"] == c]) for c in REPLAYED
                           if any(r["category"] == c for r in resolved)},
            "coverage": detail.get("coverage"), "generated_at": None if now is None else to_iso(now)}


# ── the queue's share ────────────────────────────────────────────────────────────────────────────
async def task(massive: Any, market: StocksMarket, home: Path, now: datetime, budget: int) -> dict[str, Any]:
    """The Massive queue's share for the section replay (``desk.stocks.massive_options``): at most ``PER_PASS_*``
    requests a pass, then every newly complete decision day replayed and every expired pick graded."""
    share = min(budget, PER_PASS_OPEN if cal.market_session(now)["state"] in ("pre", "open") else PER_PASS_CLOSED)
    got = await download(massive, market, now, share)
    done = record(market, now)
    graded = grade(market, now)
    today = cal.new_york(now).date()
    detail = {"requests": got["requests"], "recorded_days": done["days"], "recorded_plays": done["plays"],
              "graded": graded, "coverage": coverage(market, today)}
    market.job("plays_replay", now, error=got.get("error"), detail=detail)
    return {"ok": "error" not in got, **detail, **({"error": got["error"]} if "error" in got else {})}


__all__ = ["ETF_ONLY", "EVERY", "LOTTO", "NAMES", "PURPOSE", "REPLAYED", "SKIPPED", "SOURCE", "SPREAD", "Inputs",
           "calibration_of", "choose", "coverage", "decision_days", "download", "grade", "method", "record",
           "record_day", "replay_names", "tables", "task", "underlying", "versus", "view"]
