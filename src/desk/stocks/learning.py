"""An independently funded, timestamp-driven options research book. No broker methods or forward-ledger writes."""

from __future__ import annotations

import json
import math
import sqlite3
import zlib
from dataclasses import asdict
from datetime import date, datetime, time, timedelta
from statistics import mean, stdev
from typing import Any

from desk.core.clock import to_iso, utc_now
from desk.core.errors import DeskError
from desk.core.hashing import canonical_json, sha256_hex
from desk.core.paths import DeskPaths
from desk.stocks import calendar as cal
from desk.stocks.backtest import position_view
from desk.stocks.contracts import OptionQuote
from desk.stocks.paper import fills
from desk.stocks.paper.book import MICRO, Order, PaperBook, Position, _position, to_micros
from desk.stocks.paper.strategies import build_structure
from desk.stocks.repo import StocksMarket
from desk.storage.db import DbKind, connect, migrate

FILE = "stocks-learning-paper.db"
RESEARCH = "uncapped_research"
REFERENCE = "reference_500k"
CAPITAL = 500_000.0
SMALL_ACCOUNTS = ("reference_1k", "reference_5k")
ACCOUNTS = ((RESEARCH, "Research account", CAPITAL), (REFERENCE, "$500,000 reference", CAPITAL),
            (SMALL_ACCOUNTS[0], "$1,000 account", 1_000.0), (SMALL_ACCOUNTS[1], "$5,000 account", 5_000.0))
STRUCTURES = ("long_call", "long_put", "long_straddle")
# Lifecycle experiments (since 2026-10-31): every expiry, bought once and valued as it ages, so the book learns which
# expiries and how long to hold, instead of minute-long holds that cannot judge a 3-month option or a LEAPS.
# id: (title, days to expiry window, held up to (days), sold when this few days remain)
BUCKETS = {"weekly": ("Weekly", (5, 12), 7, 1), "1m": ("About 1 month", (21, 45), 30, 3),
           "3m": ("About 3 months", (60, 120), 90, 14), "6m": ("About 6 months", (150, 240), 120, 30),
           "leaps": ("LEAPS (9+ months)", (270, 800), 180, 60)}
CHECKPOINTS = (1, 7, 14, 30, 60, 90, 120, 180, 270, 365)   # calendar days after entry (270/365: long LEAPS holds)
LIFECYCLE_DEBIT = 5_000.0
LIFECYCLE_CADENCE_DAYS = 7                            # a recipe re-enters the same stock at most once a week
LIFECYCLE_DAILY_ENTRIES = 2                           # per recipe per day: evidence from many days, not one burst
LIFECYCLE_SWITCH = "lifecycle_switch"
MIN_COMPARISON_DAYS = 4
MIN_COMPARISON_BLOCKS = 12
CHECK_METHOD_VERSION = 2
SOLD, SETTLED = "sold at a quote", "settled at intrinsic"     # stk_learning_outcomes.basis
HELD_LEGS = "(held legs)"     # stk_learning_sources.underlying of a per-contract quote observation of held/pending legs


def open_book(paths: DeskPaths) -> sqlite3.Connection:
    conn = connect(paths.db_dir / FILE, DbKind.STOCKS_LEDGER)
    try:
        migrate(conn, DbKind.STOCKS_LEDGER)
        return conn
    except BaseException:
        conn.close()
        raise


def state(conn: sqlite3.Connection, key: str, default: Any = None) -> Any:
    row = conn.execute("SELECT value FROM stk_learning_state WHERE key=?", (key,)).fetchone()
    return json.loads(row[0]) if row else default


def set_state(conn: sqlite3.Connection, key: str, value: Any) -> None:
    conn.execute(
        "INSERT INTO stk_learning_state VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, canonical_json(value)),
    )


def add_experiment(
    conn: sqlite3.Connection,
    now: datetime,
    structure: str,
    budget: float,
    hold_minutes: int,
    repeat_minutes: int = 0,
    parent: str | None = None,
) -> str:
    if structure not in STRUCTURES or not math.isfinite(budget) or budget <= 0 or hold_minutes < 1 or repeat_minutes < 0:
        raise DeskError("Choose a supported long-option structure, positive virtual budget and holding time")
    if parent and not conn.execute("SELECT 1 FROM stk_learning_experiments WHERE id=?", (parent,)).fetchone():
        raise DeskError("The parent experiment must exist before a follow-up is registered")
    to_micros(budget, cost=True)  # representation bound, not an allocation policy or risk cap
    spec = {
        "version": 2,
        "structure": structure,
        "target_debit": budget,
        "hold_minutes": hold_minutes,
        "repeat_minutes": repeat_minutes,
        "parent_experiment": parent,
        "change": "Double the prior target debit; retain the original evidence" if parent else "Registered seed hypothesis",
        "dte": [7, 90],
        "delta": 0.5,
        "execution": "next timestamped quote, bid/ask, impact and capacity v1",
    }
    digest = sha256_hex(canonical_json(spec))
    ident = digest[:20]
    conn.execute(
        "INSERT OR IGNORE INTO stk_learning_experiments VALUES (?,?,?,?,?)",
        (
            ident,
            to_iso(now),
            f"Test {structure} held {hold_minutes} minutes at ${budget:,.2f} target debit, "
            f"entry spacing {repeat_minutes} minutes, net of costs",
            digest,
            canonical_json(spec),
        ),
    )
    return ident


def longest_hold(bucket: str) -> int:
    """The longest holding time a bucket's position can reach: its latest expiry less the days left when it is sold."""
    _title, (_lo, hi), _hold, days_left = BUCKETS[bucket]
    return hi - days_left


def add_lifecycle(conn: sqlite3.Connection, now: datetime, structure: str, bucket: str, hold_days: int | None = None
                  ) -> str:
    """One recipe: ``structure`` at the 0.5-delta strike in ``bucket``'s expiries, $5,000 an entry, valued at every
    checkpoint it lives to and sold at ``hold_days`` (the bucket's own horizon unless another is chosen on the Learning
    page) or when expiry gets close, whichever comes first. The same choices always make the same recipe, so choosing a
    seed's own horizon adds nothing."""
    if structure not in STRUCTURES or bucket not in BUCKETS:
        raise DeskError("Choose a long call, long put or long straddle and one of the expiry buckets")
    title, (lo, hi), seed_days, days_left = BUCKETS[bucket]
    hold_days = seed_days if hold_days is None else hold_days
    if not 1 <= hold_days <= longest_hold(bucket):
        raise DeskError(f"{title} options can be held from 1 to {longest_hold(bucket)} days: they are sold when "
                        f"{days_left} day{'s' if days_left > 1 else ''} remain before expiry")
    spec = {"version": 3, "kind": "lifecycle", "structure": structure, "bucket": bucket,
            "label": f"{title}, held up to {hold_days} days", "target_debit": LIFECYCLE_DEBIT,
            "hold_minutes": hold_days * 1440, "repeat_minutes": LIFECYCLE_CADENCE_DAYS * 1440,
            "exit_days_left": days_left, "checkpoints": [d for d in CHECKPOINTS if d <= hold_days],
            "daily_entries": LIFECYCLE_DAILY_ENTRIES, "dte": [lo, hi], "delta": 0.5, "parent_experiment": None,
            "change": "Lifecycle seed: every expiry, valued as it ages" if hold_days == seed_days else
                      f"Holding-time variant registered on the Learning page: up to {hold_days} days, not {seed_days}",
            "execution": "next timestamped quote, bid/ask, impact and capacity v1"}
    digest = sha256_hex(canonical_json(spec))
    ident = digest[:20]
    conn.execute("INSERT OR IGNORE INTO stk_learning_experiments VALUES (?,?,?,?,?)",
                 (ident, to_iso(now), f"Test {structure.replace('_', ' ')} on {title.lower()} options at a "
                  f"${LIFECYCLE_DEBIT:,.0f} target debit, held up to {hold_days} days and valued along the way, net of "
                  "costs", digest, canonical_json(spec)))
    return ident


def retired(conn: sqlite3.Connection) -> set[str]:
    """Minute-hold seeds from before the lifecycle switch: they open nothing new; their record stays."""
    return set((state(conn, LIFECYCLE_SWITCH) or {}).get("retired", []))


def initialize(conn: sqlite3.Connection, now: datetime) -> None:
    book = PaperBook(conn)
    with book.atomic():
        for account, _title, capital in ACCOUNTS:
            created = book.open_account(account, capital, now, cal.new_york(now).date())
            if created and account in SMALL_ACCOUNTS:
                conn.execute("INSERT OR IGNORE INTO stk_learning_nav VALUES (?,?,?,?,?)",
                             (to_iso(now),account,capital,capital,0.0))
        # The research account once received automatic virtual deposits. At the user's request (2026-10-30) it is
        # capped, once, at what it holds when this first runs: earlier deposits stay in its history, none follow.
        if state(conn, "research_cap") is None:
            marked = conn.execute("SELECT COALESCE(SUM(last_value_micros),0) FROM stk_positions "
                                  "WHERE state='OPEN' AND account=?", (RESEARCH,)).fetchone()[0] / MICRO
            set_state(conn, "research_cap", {"at": to_iso(now), "equity": book.cash(RESEARCH) + marked,
                                             "contributed": book.deposits(RESEARCH)})
        if state(conn, LIFECYCLE_SWITCH) is None:            # once: retire what existed before, then seed
            set_state(conn, LIFECYCLE_SWITCH, {"at": to_iso(now), "retired": [
                r[0] for r in conn.execute("SELECT id FROM stk_learning_experiments ORDER BY id")]})
        for structure in STRUCTURES:
            for bucket in BUCKETS:
                add_lifecycle(conn, now, structure, bucket)


def fund(book: PaperBook, account: str, required: float, now: datetime) -> bool:
    """Whether the account's own cash covers ``required``. Every account, research included, trades only what it
    holds; no virtual deposit is added (the research account was capped on 2026-10-30)."""
    return to_micros(required, cost=True) <= to_micros(book.cash(account), cost=False)


def regular_session(now: datetime) -> bool:
    """Whether ``now`` is inside a regular NYSE session: 9:30 to the calendar's close, which is earlier on half days."""
    local = cal.new_york(now)
    day = local.date()
    return cal.is_trading_day(day) and datetime.combine(day, time(9, 30), cal.NEW_YORK) <= local < cal.session_close(day)


def fresh(q: OptionQuote, now: datetime) -> bool:
    return bool(q.quoted_at and 0 <= (now - q.quoted_at).total_seconds() <= 120 and fills.fair_spread(q))


def quote_payload(q: OptionQuote) -> dict[str, Any]:
    return {**asdict(q), "expiration": q.expiration.isoformat(), "quoted_at": to_iso(q.quoted_at) if q.quoted_at else None}


def quote_from(d: dict[str, Any]) -> OptionQuote:
    return OptionQuote(**{**d, "expiration": date.fromisoformat(d["expiration"]),
                          "quoted_at": datetime.fromisoformat(d["quoted_at"]) if d["quoted_at"] else None})


def save_leg_quotes(conn: sqlite3.Connection, quotes: list[OptionQuote], now: datetime) -> str | None:
    """This minute's per-contract quotes of every held and pending leg (Schwab ``/quotes``), kept as an immutable
    observation like a chain snapshot; the latest is what the following ticks read (``leg_quotes``)."""
    if not quotes:
        return None
    observation = {"symbol": HELD_LEGS, "source": "per-contract option quotes",
                   "quotes": [quote_payload(q) for q in sorted(quotes, key=lambda q: q.occ)]}
    key = sha256_hex(canonical_json(observation))
    conn.execute("INSERT OR IGNORE INTO stk_learning_sources VALUES (?,?,?,?)",
                 (key, HELD_LEGS, to_iso(now), zlib.compress(canonical_json(observation).encode())))
    set_state(conn, "leg_quotes", {"source_key": key, "at": to_iso(now), "contracts": len(quotes)})
    return key


def leg_quotes(conn: sqlite3.Connection, now: datetime) -> dict[str, OptionQuote]:
    """The latest saved held-leg quotes that are still fresh at ``now`` (never one observed after it)."""
    saved = state(conn, "leg_quotes")
    if not saved or not 0 <= (now - datetime.fromisoformat(saved["at"])).total_seconds() <= 120:
        return {}
    row = conn.execute("SELECT payload_zlib FROM stk_learning_sources WHERE source_key=?", (saved["source_key"],)).fetchone()
    if row is None:
        return {}
    quotes = [quote_from(d) for d in json.loads(zlib.decompress(row[0]))["quotes"]]
    return {q.occ: q for q in quotes if fresh(q, now)}


def quote_key(q: OptionQuote) -> str:
    return sha256_hex(canonical_json({"occ": q.occ, "at": to_iso(q.quoted_at) if q.quoted_at else None}))


def capacity(conn: sqlite3.Connection, account: str, quotes: list[OptionQuote], opening: bool = True) -> int:
    """Contracts one quote timestamp can still fill in this account's book: the size displayed at the side traded (the
    ask to buy, the bid to sell) where the quote reports it, never more than 0.1% of open interest (at least one). There
    is no depth beyond the top of the book, so a fill never takes more than was displayed. Usage is shared across orders
    on the same quote timestamp, and a later quote brings fresh size."""
    available = []
    for q in quotes:
        limit = max(1, int(q.open_interest * 0.001))
        size = q.ask_size if opening else q.bid_size
        if size is not None:
            limit = min(limit, size)
        row = conn.execute(
            "SELECT used FROM stk_learning_capacity WHERE account=? AND quote_key=?", (account, quote_key(q))
        ).fetchone()
        available.append(max(0, limit - (row[0] if row else 0)))
    return min(available, default=0)


def capacity_basis(quotes: list[OptionQuote], opening: bool) -> str:
    side = "ask" if opening else "bid"
    if all((q.ask_size if opening else q.bid_size) is not None for q in quotes):
        return f"Size displayed at the {side}, capped at 0.1% of open interest, per quote timestamp; top of book only"
    return f"Estimated: 0.1% of open interest per quote timestamp; no size was displayed at the {side}"


def consume(conn: sqlite3.Connection, account: str, quotes: list[OptionQuote], units: int) -> None:
    for q in quotes:
        conn.execute(
            "INSERT INTO stk_learning_capacity VALUES (?,?,?) ON CONFLICT(account,quote_key) "
            "DO UPDATE SET used=used+excluded.used",
            (account, quote_key(q), units),
        )


def impact(legs: list[dict[str, Any]], quotes: dict[str, OptionQuote], units: int, opening: bool) -> fills.OptionFill:
    # Current supported structures are long premium only. Capacity impact is adverse on entry and exit.
    out = []
    total = 0.0
    for leg in legs:
        q = quotes[leg["occ"]]
        quantity = abs(leg["quantity"]) * units
        seen = q.ask if opening else q.bid
        if seen is None:
            raise DeskError("A timestamped two-sided option quote is required")
        base = float(seen)
        adjustment = 0.005 * math.sqrt(quantity / max(q.open_interest, 1))
        price = base * (1 + adjustment if opening else max(0, 1 - adjustment))
        out.append({**leg, "quantity": quantity, "entry_price": price})
        total += price * quantity * fills.CONTRACT
    return fills.OptionFill(out, total, round(sum(x["quantity"] for x in out) * fills.OPTION_FEE_PER_CONTRACT, 2))


def pending_page(conn: sqlite3.Connection, limit: int = 200) -> list[Order]:
    cursor = state(conn, "pending_cursor", 0)
    rows = conn.execute(
        "SELECT * FROM stk_pending WHERE state='PENDING' AND id>? ORDER BY id LIMIT ?", (cursor, limit)
    ).fetchall()
    if not rows and cursor:
        rows = conn.execute("SELECT * FROM stk_pending WHERE state='PENDING' ORDER BY id LIMIT ?", (limit,)).fetchall()
    set_state(conn, "pending_cursor", rows[-1]["id"] if rows else 0)
    # A large uncapped queue cannot starve the small independent accounts of their next fresh quote.
    priority = conn.execute("SELECT * FROM stk_pending WHERE state='PENDING' AND account IN (?,?) "
                            "ORDER BY id LIMIT 50", SMALL_ACCOUNTS).fetchall()
    rows = list({row["id"]:row for row in [*priority,*rows]}.values())
    return [
        Order(
            r["id"],
            r["decision_id"],
            r["account"],
            r["created_at"],
            cal.new_york(datetime.fromisoformat(r["created_at"])).date(),
            json.loads(r["order_json"]),
        )
        for r in rows
    ]


def fill_orders(conn: sqlite3.Connection, quotes: dict[str, OptionQuote], now: datetime) -> int:
    book = PaperBook(conn)
    count = 0
    for order in pending_page(conn):
        intent = conn.execute("SELECT * FROM stk_learning_intents WHERE order_id=?", (order.id,)).fetchone()
        plan = order.order
        created = datetime.fromisoformat(order.created_at)
        if now - created > timedelta(minutes=90):
            book.resolve(order.id, "CANCELLED", now, "Unfilled remainder expired; fresh quotes can create a new experiment")
            continue
        legs = plan["legs"]
        qs = [quotes.get(leg["occ"]) for leg in legs]
        if any(q is None or not fresh(q, now) or not q.quoted_at or q.quoted_at <= created for q in qs):
            continue
        actual = [q for q in qs if q is not None]
        used = conn.execute("SELECT COALESCE(SUM(units),0) FROM stk_learning_fills WHERE order_id=?", (order.id,)).fetchone()[0]
        remaining = intent["desired_units"] - used
        units = min(remaining, capacity(conn, order.account, actual))
        if units <= 0:
            continue
        # Size to the account's own cash: no account is topped up.
        low, high = 0, units
        while low < high:
            middle = (low + high + 1) // 2
            candidate = impact(legs, quotes, middle, True)
            needed = to_micros(candidate.debit,cost=True)+to_micros(candidate.fees,cost=True)
            if needed <= to_micros(book.cash(order.account),cost=False):
                low = middle
            else:
                high = middle - 1
        units = low
        if units <= 0:
            book.resolve(order.id, "CANCELLED", now, "This account has insufficient cash")
            continue
        fill = impact(legs, quotes, units, True)
        required = (to_micros(fill.debit,cost=True)+to_micros(fill.fees,cost=True))/MICRO
        if not fund(book, order.account, required, now):
            book.resolve(order.id, "CANCELLED", now, "Reference buying power cannot cover modeled execution impact")
            continue
        pid = book.open_position(
            account=order.account,
            spec_sha256=plan["spec_sha256"],
            asset="option",
            symbol=plan["symbol"],
            structure=plan["structure"],
            legs=fill.legs,
            decision_id=order.decision_id,
            at=now,
            day=cal.new_york(now).date(),
            cost=fill.debit,
            fees=fill.fees,
            max_loss=fill.debit + fill.fees,
            stop_price=None,
            expires=min(q.expiration for q in actual),
            plan={
                **plan,
                "filled_units": units,
                "capacity_basis": capacity_basis(actual, True),
            },
        )
        conn.execute("INSERT INTO stk_learning_fills VALUES (?,?,?,?)", (order.id, pid, units, to_iso(now)))
        liquidating = impact(legs, quotes, units, False)
        book.mark(pid, liquidating.debit, marks={"at": to_iso(now), "basis": "bid less modeled impact"})
        consume(conn, order.account, actual, units)
        book.event(
            now,
            order.account,
            "EXPERIMENT_FILL",
            pid,
            {
                "order_id": order.id,
                "requested_units": intent["desired_units"],
                "filled_units": units,
                "unfilled_units": remaining - units,
                "capacity_estimated": any(q.ask_size is None for q in actual),
                "quoted_size": [q.ask_size for q in actual],
            },
        )
        if units >= remaining:
            book.resolve(order.id, "FILLED", now, "Filled using a later timestamped quote")
        count += 1
    return count


def manage(conn: sqlite3.Connection, quotes: dict[str, OptionQuote], now: datetime) -> int:
    book = PaperBook(conn)
    count = 0
    # A rotating scheduling page, not a position limit. All positions remain in the ledger.
    offset = state(conn, "mark_offset", 0)
    positions = book.positions(limit=200, offset=offset)
    set_state(conn, "mark_offset", offset + len(positions) if len(positions) == 200 else 0)
    positions = [p for p in positions if p.account not in SMALL_ACCOUNTS]
    for account in SMALL_ACCOUNTS:
        key = "mark_offset:"+account
        at_offset = state(conn,key,0)
        current = book.positions(account,limit=100,offset=at_offset)
        set_state(conn,key,at_offset+len(current) if len(current)==100 else 0)
        positions.extend(current)
    for position in positions:
        qs = [quotes.get(leg["occ"]) for leg in position.legs]
        if any(
            q is None or not fresh(q, now) or not q.quoted_at or q.quoted_at <= datetime.fromisoformat(position.opened_at)
            for q in qs
        ):
            continue
        actual = [q for q in qs if q is not None]
        units = position.plan["filled_units"]
        unit_legs = [{**leg, "quantity": 1} for leg in position.legs]
        closing = impact(unit_legs, quotes, units, False)
        sized = all(q.bid_size is not None for q in actual)
        book.mark(position.id, closing.debit, marks={"at": to_iso(now), "basis": "bid less modeled impact",
                                                     "capacity_estimated": not sized})
        elapsed = now - datetime.fromisoformat(position.opened_at)
        paid = position.entry_cost + position.entry_fees
        for days in position.plan.get("checkpoints") or ():
            if elapsed >= timedelta(days=days):          # what selling now, at a real quote, would have returned
                value = closing.debit - closing.fees
                conn.execute("INSERT OR IGNORE INTO stk_learning_checkpoints VALUES (?,?,?,?,?,?)",
                             (position.id, days, to_iso(now), value, (value - paid) / paid if paid else 0.0,
                              position.plan.get("underlying_price")))
        left = None if position.expires is None else (position.expires - cal.new_york(now).date()).days
        expiring = left is not None and left <= position.plan.get("exit_days_left", -1)
        if elapsed < timedelta(minutes=position.plan["hold_minutes"]) and not expiring:
            continue
        # Exit capacity is shared too. An oversized position waits instead of receiving a fictional full fill.
        if capacity(conn, position.account, actual, opening=False) < units:
            continue
        pnl = book.close_position(
            position,
            at=now,
            day=cal.new_york(now).date(),
            credit=closing.debit,
            fees=closing.fees,
            reason=(f"Expiry approaching: sold with {left} days left; later executable quote" if expiring and
                    elapsed < timedelta(minutes=position.plan["hold_minutes"]) else
                    "Registered holding horizon reached; later executable quote"),
            evidence={"quote_times": [to_iso(q.quoted_at) for q in actual if q.quoted_at], "capacity_estimated": not sized,
                      "quoted_size": [q.bid_size for q in actual], "capacity_basis": capacity_basis(actual, False)},
        )
        consume(conn, position.account, actual, units)
        book.decide(
            now,
            cal.new_york(now).date(),
            position.account,
            position.spec_sha256,
            position.symbol,
            "EXIT",
            ["Registered holding horizon reached"],
            {"position_id": position.id, "pnl": pnl},
        )
        record_outcome(conn, position, now, pnl, units, SOLD)
        count += 1
    return count


def record_outcome(conn: sqlite3.Connection, position: Position, now: datetime, pnl: float, units: int,
                   basis: str) -> None:
    """A research position's finished result, a training label from ``now`` on (never earlier, so no decision made
    before it could see it). ``basis`` says how it finished: sold at a quote, or settled at intrinsic at expiry."""
    if position.account != RESEARCH:
        return
    paid = position.entry_cost + position.entry_fees
    conn.execute("INSERT INTO stk_learning_outcomes(position_id,experiment_id,entered_at,matured_at,block,net_return,"
                 "pnl,requested_units,filled_units,basis) VALUES (?,?,?,?,?,?,?,?,?,?)",
                 (position.id, position.plan["experiment_id"], position.opened_at, to_iso(now),
                  position.entry_day.isoformat() + ":" + position.symbol, pnl / paid if paid else 0.0, pnl,
                  position.plan["desired_units"], units, basis))


def settle_expired(conn: sqlite3.Connection, market: StocksMarket, now: datetime) -> int:
    """Expired positions settle at intrinsic value on the official close of their last trading day (``stk_bars``), as
    PaperEngine._expiries settles the ordinary PAPER books. This is a real outcome, not a stand-in: an expiring
    contract's value is fixed by that close. Learning holds only long calls, puts and straddles, so nothing is ever
    assigned: an in-the-money leg is worth its intrinsic value and pays the exit fee (it is exercised and the shares
    sold at the close), an out-of-the-money leg is worth nothing and pays no fee (nothing is traded).

    Runs on every tick, including while the market is closed, once the expiry day's close has passed and its official
    close is stored. The outcome matures when it is settled, never back-dated to the expiry, so no earlier decision
    sees it. A position whose stock split while it was held stays open: its contract was adjusted, and the
    unadjusted strike would settle the wrong amount."""
    book = PaperBook(conn)
    today = cal.new_york(now).date()
    count = 0
    for row in conn.execute("SELECT * FROM stk_positions WHERE state='OPEN' AND asset='option' AND expires_day<=? "
                            "ORDER BY expires_day,id", (today.toordinal(),)).fetchall():
        position = _position(row)
        if position.expires is None or not position.legs:
            continue
        expiry = position.expires
        last = expiry if cal.is_trading_day(expiry) else cal.previous_trading_day(expiry)
        if now < cal.session_close(last):
            continue
        bar = market.conn.execute("SELECT close FROM stk_bars WHERE symbol=? AND day=?",
                                  (position.symbol, last.toordinal())).fetchone()
        if bar is None or not bar[0] or not math.isfinite(bar[0]) or bar[0] <= 0:
            continue                                   # waits for the official close; stays visibly unresolved
        if market.conn.execute("SELECT 1 FROM stk_splits WHERE symbol=? AND execution_date>? AND execution_date<=? "
                               "LIMIT 1", (position.symbol, position.entry_day.toordinal(),
                                           expiry.toordinal())).fetchone():
            continue
        close = float(bar[0])
        value = fills.settle(position.legs, close)
        exercised = sum(abs(int(leg["quantity"])) for leg in position.legs if fills.intrinsic(leg, close) > 0)
        fee = round(exercised * fills.OPTION_FEE_PER_CONTRACT, 2) if value > 0 else 0.0
        reason = (f"Expired {expiry.isoformat()}: settled at intrinsic on the ${close:,.2f} official close"
                  + ("" if value > 0 else " (worthless)"))
        evidence = {"settlement": SETTLED, "underlying_close": close, "close_day": last.isoformat(),
                    "source": "official daily close (stk_bars)", "exercised_contracts": exercised}
        pnl = book.close_position(position, at=now, day=today, credit=value, fees=fee, reason=reason,
                                  evidence=evidence, cash_reason="EXPIRE")
        book.decide(now, today, position.account, position.spec_sha256, position.symbol, "EXIT", [reason],
                    {"position_id": position.id, "pnl": pnl, **evidence})
        book.event(now, position.account, "EXPIRY_SETTLEMENT", position.id, {**evidence, "value": value, "fees": fee})
        record_outcome(conn, position, now, pnl, int(position.plan.get("filled_units") or 0), SETTLED)
        count += 1
    return count


def record_nav(conn: sqlite3.Connection, now: datetime) -> None:
    book = PaperBook(conn)
    for account, _title, _capital in ACCOUNTS:
        marked = conn.execute("SELECT COALESCE(SUM(last_value_micros),0) FROM stk_positions WHERE state='OPEN' "
                              "AND account=?", (account,)).fetchone()[0] / MICRO
        equity, contributed = book.cash(account) + marked, book.deposits(account)
        conn.execute("INSERT OR IGNORE INTO stk_learning_nav VALUES (?,?,?,?,?)",
                     (to_iso(now), account, equity, contributed, equity - contributed))


def forecast(conn: sqlite3.Connection, experiment: str, now: datetime) -> dict[str, Any]:
    rows = conn.execute(
        "SELECT o.block,AVG(o.net_return) r FROM stk_learning_outcomes o "
        "JOIN stk_positions p ON p.id=o.position_id AND p.account=? "
        "WHERE o.experiment_id=? AND o.matured_at<? GROUP BY o.block",
        (RESEARCH,experiment, to_iso(now)),
    ).fetchall()
    returns = [r["r"] for r in rows]
    return {
        "version": "shrunk-block-mean-v1",
        "trained_before": to_iso(now),
        "blocks": len(rows),
        "expected_net_return": mean(returns) * len(returns) / (len(returns) + 10) if returns else None,
        "status": "provisional estimate" if rows else "Untrained registered hypothesis",
    }


def propose(
    conn: sqlite3.Connection,
    market: StocksMarket,
    now: datetime,
    quotes: dict[str, OptionQuote],
    snapshots: dict[str, tuple[str, float, dict[Any, list[OptionQuote]]]],
) -> int:
    book = PaperBook(conn)
    champion = state(conn, "model", {}).get("champion", "")
    gone = retired(conn)
    experiments = [e for e in conn.execute(
        "SELECT * FROM stk_learning_experiments ORDER BY "
        "CASE WHEN json_extract(config_json,'$.structure')=? THEN 0 ELSE 1 END,id",
        (champion,),
    ).fetchall() if e["id"] not in gone]
    if not experiments or not snapshots:
        return 0
    marked = conn.execute("SELECT COALESCE(SUM(last_value_micros),0) FROM stk_positions WHERE state='OPEN' AND account=?",
                          (RESEARCH,)).fetchone()[0] / MICRO
    share = (book.cash(RESEARCH) + marked) / len(experiments)   # each recipe gets an equal share of the account
    day_start = to_iso(datetime.combine(cal.new_york(now).date(), time(), cal.NEW_YORK))
    symbols = sorted(snapshots)
    total = len(symbols) * len(experiments)
    start = state(conn, "proposal_offset", 0) % total
    indices = [(start + i) % total for i in range(min(100, total))]
    chosen = [(experiments[i % len(experiments)], symbols[i // len(experiments)]) for i in indices]
    set_state(conn, "proposal_offset", (start + len(chosen)) % total)
    count = 0
    for experiment, symbol in chosen:
        cfg = json.loads(experiment["config_json"])
        if cfg.get("kind") == "lifecycle":
            today_entries = conn.execute(
                "SELECT COUNT(*) FROM stk_learning_intents i JOIN stk_pending p ON p.id=i.order_id "
                "WHERE i.experiment_id=? AND p.account=? AND p.created_at>=?",
                (experiment["id"], RESEARCH, day_start)).fetchone()[0]
            held = conn.execute(
                "SELECT COALESCE(SUM(entry_cost_micros+entry_fees_micros),0) FROM stk_positions WHERE account=? "
                "AND state='OPEN' AND json_extract(plan_json,'$.experiment_id')=?", (RESEARCH, experiment["id"])
            ).fetchone()[0] / MICRO
            if today_entries >= cfg["daily_entries"] or held + cfg["target_debit"] > share:
                continue
        source_key, spot, chains = snapshots[symbol]
        last_intent = state(conn, "entry:" + experiment["id"] + ":" + symbol)
        if last_intent and now - datetime.fromisoformat(last_intent) < timedelta(minutes=cfg.get("repeat_minutes", 0)):
            continue
        if conn.execute(
            "SELECT 1 FROM stk_learning_intents i JOIN stk_pending p ON p.id=i.order_id "
            "WHERE i.experiment_id=? AND p.account=? AND json_extract(p.order_json,'$.symbol')=? "
            "AND (p.state='PENDING' OR i.source_key=?) LIMIT 1",
            (experiment["id"], RESEARCH, symbol, source_key),
        ).fetchone():
            continue
        legs = build_structure(cfg["structure"], chains, spot, cal.new_york(now).date(), 0.04, cfg, {})
        if not legs or any(not fresh(quotes[leg["occ"]], now) for leg in legs):
            continue
        unit = sum((leg["decision_price"] or 0) * fills.CONTRACT + fills.OPTION_FEE_PER_CONTRACT for leg in legs)
        desired = max(1, int(cfg["target_debit"] // unit))
        prediction = forecast(conn, experiment["id"], now)
        for account in (RESEARCH, REFERENCE):
            plan = {
                **cfg,
                "experiment_id": experiment["id"],
                "spec_sha256": experiment["spec_sha256"],
                "symbol": symbol,
                "asset": "option",
                "underlying_price": spot,
                "legs": legs,
                "desired_units": desired,
                "forecast": prediction,
                "source_key": source_key,
                "comparison": "independent books; not combined capital",
            }
            did = book.decide(
                now,
                cal.new_york(now).date(),
                account,
                experiment["spec_sha256"],
                symbol,
                "ENTER",
                [experiment["hypothesis"]],
                {"prediction": prediction, "plan": plan},
            )
            oid = book.place(did, account, now, cal.new_york(now).date(), plan)
            conn.execute(
                "INSERT INTO stk_learning_intents VALUES (?,?,?,?,?)",
                (oid, experiment["id"], source_key, desired, canonical_json(prediction)),
            )
            count += 1
        set_state(conn, "entry:" + experiment["id"] + ":" + symbol, to_iso(now))
    return count


def family(cfg: dict[str, Any]) -> tuple[str, str | None, int, int]:
    """What makes two experiments the same recipe family: structure, expiry bucket (lifecycle recipes; none for the
    minute-hold kind), holding time and entry spacing. Sizing variants share a family; a 30-day hold of 1-month options
    and a 30-day hold of 3-month options do not."""
    return (cfg["structure"], cfg.get("bucket"), cfg["hold_minutes"], cfg.get("repeat_minutes", 0))


def outcome_rows(conn: sqlite3.Connection, now: datetime) -> list[sqlite3.Row]:
    """Only matured uncapped research; configuration variants within a block are not independent days."""
    return conn.execute(
        "SELECT o.experiment_id,MIN(o.entered_at) entered_at,MAX(o.matured_at) matured_at,o.block,"
        "AVG(o.net_return) net_return,COUNT(*) outcomes,e.config_json FROM stk_learning_outcomes o "
        "JOIN stk_learning_experiments e ON e.id=o.experiment_id "
        "JOIN stk_positions p ON p.id=o.position_id AND p.account=? "
        "WHERE o.matured_at<=? GROUP BY o.experiment_id,o.block ORDER BY entered_at",
        (RESEARCH,to_iso(now)),
    ).fetchall()


def partition_outcomes(rows: list[sqlite3.Row]) -> tuple[list[str],str | None,list[sqlite3.Row],list[sqlite3.Row]]:
    days = sorted({cal.new_york(datetime.fromisoformat(r["entered_at"])).date().isoformat() for r in rows})
    boundary = days[len(days)//2] if len(days)>=2 else None
    if boundary is None:
        return days,None,[],[]
    cutoff = datetime.combine(date.fromisoformat(boundary),time(),cal.NEW_YORK)
    train = [r for r in rows if datetime.fromisoformat(r["matured_at"]) < cutoff]
    test = [r for r in rows if datetime.fromisoformat(r["entered_at"]) >= cutoff]
    return days,boundary,train,test


def evidence_progress(conn: sqlite3.Connection, now: datetime) -> dict[str, Any]:
    """Read-only evidence funnel, including collected results before the formal model can be trained."""
    rows = outcome_rows(conn,now)
    days,boundary,train,test = partition_outcomes(rows)
    blocks = len({r["block"] for r in rows})
    total = sum(r["outcomes"] for r in rows)
    families: dict[tuple[str,str | None,int,int],dict[str,Any]] = {}
    for row in rows:
        cfg = json.loads(row["config_json"])
        key = family(cfg)
        group = families.setdefault(key,{"structure":key[0],"bucket":key[1],"hold_minutes":key[2],"repeat_minutes":key[3],
                                        "label":cfg.get("label"),"outcomes":0,"returns":{}})
        group["outcomes"] += row["outcomes"]
        group["returns"].setdefault(row["block"],[]).append(row["net_return"])
    comparisons = []
    for group in families.values():
        values = [mean(v) for v in group.pop("returns").values()]
        comparisons.append({**group,"blocks":len(values),"mean_net_return":mean(values)})
    comparisons.sort(key=lambda f:(-f["mean_net_return"],f["structure"],f["bucket"] or "",f["hold_minutes"],
                                   f["repeat_minutes"]))
    shown = comparisons if len(comparisons)<=8 else [*comparisons[:4],*comparisons[-4:]]
    raw = conn.execute("SELECT COUNT(*) n,MAX(o.matured_at) latest FROM stk_learning_outcomes o "
        "JOIN stk_positions p ON p.id=o.position_id AND p.account=? WHERE o.matured_at<=?",(RESEARCH,to_iso(now))).fetchone()
    closed = conn.execute("SELECT COUNT(*) n,SUM(NOT EXISTS (SELECT 1 FROM stk_learning_outcomes o "
        "WHERE o.position_id=p.id)) missing FROM stk_positions p WHERE p.account=? AND p.state='CLOSED' AND p.closed_at<=?",
        (RESEARCH,to_iso(now))).fetchone()
    open_ = conn.execute("SELECT COUNT(*) n,SUM(expires_day<?) expired,SUM(expires_day>=? AND "
        "julianday(opened_at)+COALESCE(json_extract(plan_json,'$.hold_minutes'),0)/1440.0<=julianday(?)) due "
        "FROM stk_positions WHERE account=? AND state='OPEN'",
        (cal.new_york(now).date().toordinal(),cal.new_york(now).date().toordinal(),to_iso(now),RESEARCH)).fetchone()
    settled = conn.execute("SELECT COUNT(*) FROM stk_learning_outcomes o JOIN stk_positions p ON p.id=o.position_id "
                           "AND p.account=? WHERE o.matured_at<=? AND o.basis=?", (RESEARCH, to_iso(now), SETTLED)).fetchone()[0]
    reasons = []
    if not total:
        reasons.append("No eligible completed research outcomes yet. Open trades finish when sold at a later executable "
                       "quote or, at expiry, when settled at intrinsic value on the official close.")
    if len(days)<MIN_COMPARISON_DAYS:
        reasons.append(f"Need {MIN_COMPARISON_DAYS-len(days)} more entry days with completed research outcomes.")
    if blocks<MIN_COMPARISON_BLOCKS:
        reasons.append(f"Need {MIN_COMPARISON_BLOCKS-blocks} more underlying/day blocks; "
                       "repeated fills do not add independent blocks.")
    if len(days)>=MIN_COMPARISON_DAYS and blocks>=MIN_COMPARISON_BLOCKS and not train:
        reasons.append("Training outcomes must finish before held-out entries; "
                       "the current split has no eligible training labels.")
    if len(days)>=MIN_COMPARISON_DAYS and blocks>=MIN_COMPARISON_BLOCKS and not test:
        reasons.append("No later held-out samples are available yet.")
    missing = int(closed["missing"] or 0)
    if missing:
        reasons.append(f"{missing} closed research trades lack an outcome record; "
                       "they are not silently counted as training evidence.")
    return {"asof":to_iso(now),"account":RESEARCH,"matured_outcomes":total,"recorded_outcomes":raw["n"],
        "closed_research_trades":closed["n"],"closed_without_outcome":missing,"unusable_outcomes":raw["n"]-total,
        "underlying_day_blocks":blocks,"entry_days":len(days),"first_entry_day":days[0] if days else None,
        "last_entry_day":days[-1] if days else None,"latest_outcome_at":raw["latest"],"boundary":boundary,
        "potential_training_samples":len(train),"potential_test_samples":len(test),
        "min_entry_days":MIN_COMPARISON_DAYS,"min_blocks":MIN_COMPARISON_BLOCKS,
        "ready_for_comparison":len(days)>=MIN_COMPARISON_DAYS and blocks>=MIN_COMPARISON_BLOCKS and bool(train) and bool(test),
        "open_research_trades":open_["n"],"exit_horizon_reached":int(open_["due"] or 0),
        "unresolved_expiries":int(open_["expired"] or 0),"settled_at_expiry":settled,"blockers":reasons,
        "families":shown,"family_total":len(comparisons),
        "latest_source_at":conn.execute("SELECT MAX(observed_at) FROM stk_learning_sources WHERE observed_at<=?",
                                         (to_iso(now),)).fetchone()[0],
        "latest_check_at":conn.execute("SELECT MAX(at) FROM stk_learning_checks WHERE at<=?",(to_iso(now),)).fetchone()[0]}


def check_in(conn: sqlite3.Connection, now: datetime) -> None:
    key = "check:" + cal.new_york(now).date().isoformat()
    previous = state(conn, key, 0)
    rows = outcome_rows(conn,now)
    matured = sum(r["outcomes"] for r in rows)
    if previous == matured and state(conn, key + ":exists", False) and state(conn,"check_method") == CHECK_METHOD_VERSION:
        return
    training = state(conn, "model", {"status": "Waiting for matured forward outcomes", "version": 0})
    blocks = len({r["block"] for r in rows})
    # Train/test by entry day with a temporal embargo: test entries must follow every training label's maturity.
    days,boundary,train,test = partition_outcomes(rows)
    if len(days) >= MIN_COMPARISON_DAYS and blocks >= MIN_COMPARISON_BLOCKS:
        scored = []
        for structure in STRUCTURES:
            past = [r for r in train if json.loads(r["config_json"])["structure"] == structure]
            grouped: dict[str, list[float]] = {}
            for r in past:
                grouped.setdefault(r["block"], []).append(r["net_return"])
            values = [mean(v) for v in grouped.values()]
            if values:
                score = mean(values) - 2 * stdev(values) / math.sqrt(len(values)) if len(values) > 1 else -math.inf
                scored.append((score, structure))
        if scored:
            _, chosen = max(scored)
            held = [r for r in test if json.loads(r["config_json"])["structure"] == chosen]
            held_blocks: dict[str, list[float]] = {}
            for row in held:
                held_blocks.setdefault(row["block"], []).append(row["net_return"])
            held_values = [mean(v) for v in held_blocks.values()]
            training = {
                "version": int(training.get("version", 0)) + 1,
                "status": "Chronological comparison; advisory",
                "train_labels_before": boundary,
                "candidate": chosen,
                "training_outcomes": len(train),
                "test_outcomes": len(held),
                "test_blocks": len(held_values),
                "test_net_return": mean(held_values) if held_values else None,
                "promoted": False,
                "note": "Overlapping days, modeled depth and multiple trials limit confidence",
            }
            daily: dict[str, list[float]] = {}
            for row in held:
                daily.setdefault(row["block"].split(":")[0], []).append(row["net_return"])
            prior_promotion = state(conn, "last_promotion_day", "")
            values = [mean(v) for day, v in daily.items() if day > prior_promotion]
            # Conservative approximate multiple-comparison hurdle, using days rather than correlated fill count.
            if len(values) >= 20 and mean(values) - 3 * stdev(values) / math.sqrt(len(values)) > 0:
                training.update(promoted=True, champion=chosen, status="PAPER challenger promoted; capacity remains modeled")
                set_state(conn, "last_promotion_day", max(daily))
                by_experiment: dict[str, list[Any]] = {}
                for r in train:
                    if json.loads(r["config_json"])["structure"] == chosen:
                        by_experiment.setdefault(r["experiment_id"], []).append(r)
                best_group = max(by_experiment.values(), key=lambda group: mean(r["net_return"] for r in group))
                best = best_group[0]
                config = json.loads(best["config_json"])
                target = config["target_debit"] * 2
                if config.get("kind") == "lifecycle" or best["experiment_id"] in retired(conn):
                    # Lifecycle recipes compare expiries and holding times at a fixed size; retired ones stay retired.
                    training["next_size_experiment"] = "Not applicable: recipes compare expiries and holding times"
                else:
                    try:
                        add_experiment(
                            conn,
                            now,
                            chosen,
                            target,
                            config["hold_minutes"],
                            config.get("repeat_minutes", 0),
                            parent=best["experiment_id"],
                        )
                        training["next_size_experiment"] = target
                    except DeskError:
                        training["next_size_experiment"] = ("Ledger representation limit reached; existing "
                                                            "experiments continue")
            elif state(conn, "model", {}).get("champion"):
                training["champion"] = state(conn, "model")["champion"]
            set_state(conn, "model", training)
    last_total = state(conn, "check_total", 0)
    payload = {
        "matured_outcomes": matured,
        "underlying_day_blocks": blocks,
        "new_outcomes": max(0, matured - last_total),
        "week_outcomes": conn.execute(
            "SELECT COUNT(*) FROM stk_learning_outcomes o JOIN stk_positions p ON p.id=o.position_id "
            "WHERE p.account=? AND o.matured_at>=? AND o.matured_at<=?",
            (RESEARCH,to_iso(now - timedelta(days=7)), to_iso(now)),
        ).fetchone()[0],
        "model": training,
        "evidence":evidence_progress(conn,now),
        "next": "Continue timestamped forward experiments; compare capital sizes on matched sources",
        "funding_excluded_from_profit": True,
        "baseline": "Uninvested cash: 0% trading return; the $500,000 reference mirrors research intents. "
                    "Separate $1,000/$5,000 accounts test affordable transfers of prior research findings.",
        "attribution": "Return comparisons average underlying/day blocks rather than dollars or fill count. "
        "Size, cadence and holding time remain separate registered variables; market edge is not established.",
    }
    conn.execute(
        "INSERT INTO stk_learning_checks(at,kind,payload_json) VALUES (?,?,?)",
        (to_iso(now), "weekly" if cal.new_york(now).weekday() == 4 else "daily", canonical_json(payload)),
    )
    set_state(conn, key, matured)
    set_state(conn, "check_total", matured)
    set_state(conn, key + ":exists", True)
    set_state(conn,"check_method",CHECK_METHOD_VERSION)


def tick(conn: sqlite3.Connection, market: StocksMarket, now: datetime) -> dict[str, Any]:
    initialize(conn, now)
    quotes: dict[str, OptionQuote] = {}
    snapshots: dict[str, tuple[str, float, dict[Any, list[OptionQuote]]]] = {}
    local = cal.new_york(now)
    if not regular_session(now):
        with PaperBook(conn).atomic():
            settled = settle_expired(conn, market, now)
            if settled:
                record_nav(conn, now)
            check_in(conn,now)
        result = {"phase": "Market closed; saved experiments and losses are retained", "orders": 0, "settled": settled}
        set_state(conn, "progress", result)
        return result
    rows = market.conn.execute(
        "SELECT DISTINCT underlying FROM stk_option_chains WHERE day=? AND observed_at<=? AND observed_at>=?",
        (local.date().toordinal(), to_iso(now), to_iso(now - timedelta(minutes=3))),
    ).fetchall()
    with PaperBook(conn).atomic():
        settled = settle_expired(conn, market, now)
        for row in rows:
            symbol = row[0]
            _, chains = market.option_chain(symbol, local.date(), observed_by=now)
            seen_spot = market.conn.execute(
                "SELECT underlying_price FROM stk_option_chains WHERE underlying=? AND day=? AND observed_at<=? "
                "ORDER BY observed_at DESC LIMIT 1",
                (symbol, local.date().toordinal(), to_iso(now)),
            ).fetchone()
            spot = seen_spot[0] if seen_spot else None
            if not spot or spot <= 0:
                continue
            valid = {expiry: [q for q in qs if fresh(q, now)] for expiry, qs in chains.items()}
            flat = [q for qs in valid.values() for q in qs]
            if not flat:
                continue
            observation = {"symbol": symbol, "quotes": [quote_payload(q) for q in flat], "underlying_price": spot}
            source_key = sha256_hex(canonical_json(observation))
            conn.execute(
                "INSERT OR IGNORE INTO stk_learning_sources VALUES (?,?,?,?)",
                (source_key, symbol, to_iso(now), zlib.compress(canonical_json(observation).encode())),
            )
            snapshots[symbol] = (source_key, spot, valid)
            quotes.update({q.occ: q for q in flat})
        # Held and pending legs are quoted contract by contract every minute; the newer of that and a chain quote wins.
        held = leg_quotes(conn, now)
        for occ, q in held.items():
            seen = quotes.get(occ)
            if seen is None or not seen.quoted_at or (q.quoted_at and q.quoted_at >= seen.quoted_at):
                quotes[occ] = q
        closed = manage(conn, quotes, now)
        filled = fill_orders(conn, quotes, now)
        orders = propose(conn, market, now, quotes, snapshots)
        from desk.stocks.learning_retail import propose as propose_retail

        orders += propose_retail(conn,now,quotes,snapshots)
        record_nav(conn, now)
        check_in(conn, now)
        result = {
            "phase": "Observing fresh quotes and running registered experiments"
            if quotes
            else "Waiting for fresh timestamped option quotes; no synthetic fills",
            "contracts": len(quotes),
            "held_leg_quotes": len(held),
            "orders": orders,
            "filled": filled,
            "closed": closed,
            "settled": settled,
            "at": to_iso(now),
        }
        set_state(conn, "progress", result)
        return result


def report(conn: sqlite3.Connection, now: datetime | None = None) -> dict[str, Any]:
    now = now or utc_now()
    book = PaperBook(conn)
    accounts = []
    for account, title, capital in ACCOUNTS:
        marked = conn.execute(
            "SELECT COALESCE(SUM(last_value_micros),0),COUNT(*) FROM stk_positions WHERE state='OPEN' AND account=?", (account,)
        ).fetchone()
        contributed = book.deposits(account)
        equity = book.cash(account) + marked[0] / MICRO
        closed = conn.execute(
            "SELECT COUNT(*),COALESCE(SUM(exit_credit_micros-exit_fees_micros-"
            "entry_cost_micros-entry_fees_micros),0) "
            "FROM stk_positions WHERE account=? AND state='CLOSED'",
            (account,),
        ).fetchone()
        fees = (
            conn.execute(
                "SELECT COALESCE(SUM(entry_fees_micros+COALESCE(exit_fees_micros,0)),0) FROM stk_positions WHERE account=?",
                (account,),
            ).fetchone()[0]
            / MICRO
        )
        drawdown = conn.execute(
            "SELECT COALESCE(MAX(peak-pnl),0) FROM (SELECT pnl,"
            "MAX(0,MAX(pnl) OVER (ORDER BY at ROWS UNBOUNDED PRECEDING)) peak "
            "FROM stk_learning_nav WHERE account=?)",
            (account,),
        ).fetchone()[0]
        unresolved = conn.execute(
            "SELECT COUNT(*) FROM stk_positions WHERE account=? AND state='OPEN' AND expires_day<?",
            (account, cal.new_york(now).date().toordinal()),
        ).fetchone()[0]
        accounts.append(
            {
                "id": account,
                "title": title,
                "starting_capital": capital,
                "contributed": contributed,
                "additional_funding": contributed - capital if contributed else 0,
                "funding_allowed": False,
                "cap": state(conn, "research_cap") if account == RESEARCH else None,
                "small_account": account in SMALL_ACCOUNTS,
                "started_at": conn.execute("SELECT MIN(at) FROM stk_cash WHERE account=? AND reason='DEPOSIT'",
                                           (account,)).fetchone()[0],
                "return_on_start": (equity-contributed)/capital,
                "pending_orders": conn.execute("SELECT COUNT(*) FROM stk_pending WHERE account=? AND state='PENDING'",
                                               (account,)).fetchone()[0],
                "policy": state(conn,"retail:"+account,{"phase":"Waiting for a fresh quote and a policy decision",
                    "mode":"starter", "note":"Starts forward from account creation; no earlier profit is fabricated."})
                    if account in SMALL_ACCOUNTS else None,
                "cash": book.cash(account),
                "equity": equity,
                "trading_pnl": equity - contributed,
                "realized_pnl": closed[1] / MICRO,
                "unrealized_pnl": equity - contributed - closed[1] / MICRO,
                "fees": fees,
                "capital_employed": marked[0] / MICRO,
                "trading_pnl_drawdown": drawdown,
                "unresolved_expiries": unresolved,
                "expiry_settlements": conn.execute("SELECT COUNT(*) FROM stk_cash WHERE account=? AND reason='EXPIRE'",
                                                   (account,)).fetchone()[0],
                "pnl_per_contributed_dollar": (equity - contributed) / contributed if contributed else None,
                "open_positions": marked[1],
                "closed_trades": closed[0],
                "curve": [
                    dict(r)
                    for r in conn.execute(
                        "SELECT at,pnl FROM stk_learning_nav WHERE account=? ORDER BY at DESC LIMIT 1000", (account,)
                    ).fetchall()[::-1]
                ],
            }
        )
    checks = [dict(r) for r in conn.execute("SELECT * FROM stk_learning_checks ORDER BY id DESC LIMIT 20")]
    for check in checks:
        check["report"] = json.loads(check.pop("payload_json"))
    experiment_total = conn.execute("SELECT COUNT(*) FROM stk_learning_experiments").fetchone()[0]
    experiments = [dict(r) for r in conn.execute("SELECT * FROM stk_learning_experiments ORDER BY created_at DESC,id LIMIT 100")]
    gone = retired(conn)
    for experiment in experiments:
        experiment["configuration"] = json.loads(experiment.pop("config_json"))
        experiment["retired"] = experiment["id"] in gone          # minute-hold seeds from before 2026-10-31
        stats = conn.execute(
            "SELECT COUNT(*),AVG(net_return),SUM(pnl) FROM stk_learning_outcomes WHERE experiment_id=?", (experiment["id"],)
        ).fetchone()
        experiment.update(outcomes=stats[0], mean_net_return=stats[1], pnl=stats[2])
    positions = [position_view(p) for p in book.positions(state=None, limit=100)]
    for position in positions:
        reason = conn.execute("SELECT reasons FROM stk_decisions WHERE id=?", (position["decision_id"],)).fetchone()
        position["entry_reason"] = reason[0] if reason else None
    from desk.stocks.learning_analysis import lifecycle

    return {
        "accounts": accounts,
        "lifecycle": lifecycle(conn),
        "progress": state(conn, "progress", {"phase": "Waiting for fresh option data"}),
        "collection": state(conn, "collection", {}),
        "model": state(conn, "model", {"version": 0, "status": "Untrained; collecting matured forward outcomes"}),
        "evidence":evidence_progress(conn,now),
        "experiments": experiments,
        "experiment_total": experiment_total,
        "check_ins": checks,
        "positions": positions,
        "decisions": book.decisions(limit=100),
        "funding": [
            {"at": r["at"], "kind": r["kind"], **json.loads(r["payload_json"])}
            for r in conn.execute(
                "SELECT at,kind,payload_json FROM stk_events WHERE account=? AND kind='VIRTUAL_FUNDING' "
                "ORDER BY id DESC LIMIT 50",
                (RESEARCH,),
            )
        ],
        "order_progress": [
            dict(r)
            for r in conn.execute(
                "SELECT p.id,p.account,p.state,p.created_at,p.note,"
                "json_extract(p.order_json,'$.symbol') symbol,i.desired_units,COALESCE(SUM(f.units),0) filled_units "
                "FROM stk_pending p JOIN stk_learning_intents i ON i.order_id=p.id "
                "LEFT JOIN stk_learning_fills f ON f.order_id=p.id GROUP BY p.id ORDER BY p.id DESC LIMIT 100"
            )
        ],
        "pending": conn.execute("SELECT COUNT(*) FROM stk_pending WHERE state='PENDING'").fetchone()[0],
        "limits": {"trade_count": None, "position_count": None, "contract_quantity": None, "virtual_spending": None},
        "buckets": [{"id": key, "title": title, "dte": list(window), "hold_days": hold, "exit_days_left": left,
                     "longest_hold": longest_hold(key)} for key, (title, window, hold, left) in BUCKETS.items()],
        "lifecycle_debit": LIFECYCLE_DEBIT,
        "execution_note": "Capacity per quote timestamp: the size displayed at the ask to buy or the bid to sell, capped "
        "at 0.1% of open interest (that rule alone when no size is reported); no depth beyond the top of the book. "
        "Adverse impact and $0.65/contract/side. "
        "Unfilled quantities receive no invented P/L. Only long calls, puts and straddles are supported. "
        "Lifecycle recipes buy weekly, 1-, 3-, 6-month and LEAPS options and value each position at real quotes "
        "after 1, 7, 14, 30, 60, 90, 120 and 180 days, selling at the horizon or before expiry. "
        "A position still held at expiry settles at intrinsic value on that day's official close (exit fee only when "
        "in the money; long options are never assigned); that is a real outcome and a training label.",
        "mode": "PAPER",
        "orders_enabled": False,
    }
