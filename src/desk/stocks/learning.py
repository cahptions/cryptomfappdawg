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
from desk.stocks.paper.book import MICRO, Order, PaperBook, to_micros
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
MIN_COMPARISON_DAYS = 4
MIN_COMPARISON_BLOCKS = 12
CHECK_METHOD_VERSION = 2


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
        for structure in STRUCTURES:
            for hold in (30, 120):
                for budget in (5_000, 50_000, 500_000, 1_000_000):
                    add_experiment(conn, now, structure, budget, hold)
                for repeat in (30, 120):
                    add_experiment(conn, now, structure, 50_000, hold, repeat)


def fund(book: PaperBook, account: str, required: float, now: datetime) -> bool:
    """Whether the account's own cash covers ``required``. Every account, research included, trades only what it
    holds; no virtual deposit is added (the research account was capped on 2026-10-30)."""
    return to_micros(required, cost=True) <= to_micros(book.cash(account), cost=False)


def fresh(q: OptionQuote, now: datetime) -> bool:
    return bool(q.quoted_at and 0 <= (now - q.quoted_at).total_seconds() <= 120 and fills.fair_spread(q))


def quote_key(q: OptionQuote) -> str:
    return sha256_hex(canonical_json({"occ": q.occ, "at": to_iso(q.quoted_at) if q.quoted_at else None}))


def capacity(conn: sqlite3.Connection, account: str, quotes: list[OptionQuote]) -> int:
    # Depth is unavailable: explicit estimated capacity, shared across orders using the same quote timestamp.
    available = []
    for q in quotes:
        limit = max(1, int(q.open_interest * 0.001))
        row = conn.execute(
            "SELECT used FROM stk_learning_capacity WHERE account=? AND quote_key=?", (account, quote_key(q))
        ).fetchone()
        available.append(max(0, limit - (row[0] if row else 0)))
    return min(available, default=0)


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
                "capacity_basis": "Estimated: 0.1% of OI per fresh quote timestamp; depth unavailable",
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
                "capacity_estimated": True,
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
        book.mark(
            position.id, closing.debit, marks={"at": to_iso(now), "basis": "bid less modeled impact", "capacity_estimated": True}
        )
        elapsed = now - datetime.fromisoformat(position.opened_at)
        if elapsed < timedelta(minutes=position.plan["hold_minutes"]):
            continue
        # Exit capacity is shared too. An oversized position waits instead of receiving a fictional full fill.
        if capacity(conn, position.account, actual) < units:
            continue
        pnl = book.close_position(
            position,
            at=now,
            day=cal.new_york(now).date(),
            credit=closing.debit,
            fees=closing.fees,
            reason="Registered holding horizon reached; later executable quote",
            evidence={"quote_times": [to_iso(q.quoted_at) for q in actual if q.quoted_at], "capacity_estimated": True},
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
        if position.account == RESEARCH:
            conn.execute(
                "INSERT INTO stk_learning_outcomes VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    position.id,
                    position.plan["experiment_id"],
                    position.opened_at,
                    to_iso(now),
                    position.entry_day.isoformat() + ":" + position.symbol,
                    pnl / (position.entry_cost + position.entry_fees),
                    pnl,
                    position.plan["desired_units"],
                    units,
                ),
            )
        count += 1
    return count


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
    experiments = conn.execute(
        "SELECT * FROM stk_learning_experiments ORDER BY "
        "CASE WHEN json_extract(config_json,'$.structure')=? THEN 0 ELSE 1 END,id",
        (champion,),
    ).fetchall()
    if not experiments or not snapshots:
        return 0
    symbols = sorted(snapshots)
    total = len(symbols) * len(experiments)
    start = state(conn, "proposal_offset", 0) % total
    indices = [(start + i) % total for i in range(min(100, total))]
    chosen = [(experiments[i % len(experiments)], symbols[i // len(experiments)]) for i in indices]
    set_state(conn, "proposal_offset", (start + len(chosen)) % total)
    count = 0
    for experiment, symbol in chosen:
        cfg = json.loads(experiment["config_json"])
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
    families: dict[tuple[str,int,int],dict[str,Any]] = {}
    for row in rows:
        cfg = json.loads(row["config_json"])
        key = (cfg["structure"],cfg["hold_minutes"],cfg.get("repeat_minutes",0))
        family = families.setdefault(key,{"structure":key[0],"hold_minutes":key[1],"repeat_minutes":key[2],
                                         "outcomes":0,"returns":{}})
        family["outcomes"] += row["outcomes"]
        family["returns"].setdefault(row["block"],[]).append(row["net_return"])
    comparisons = []
    for family in families.values():
        values = [mean(v) for v in family.pop("returns").values()]
        comparisons.append({**family,"blocks":len(values),"mean_net_return":mean(values)})
    comparisons.sort(key=lambda f:(-f["mean_net_return"],f["structure"],f["hold_minutes"],f["repeat_minutes"]))
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
    reasons = []
    if not total:
        reasons.append("No eligible completed research outcomes yet. Open trades need a later executable exit quote.")
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
        "unresolved_expiries":int(open_["expired"] or 0),"blockers":reasons,
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
                    training["next_size_experiment"] = "Ledger representation limit reached; existing experiments continue"
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
    if not cal.is_trading_day(local.date()) or not (9, 30) <= (local.hour, local.minute) < (16, 0):
        with PaperBook(conn).atomic():
            check_in(conn,now)
        result = {"phase": "Market closed; saved experiments and losses are retained", "orders": 0}
        set_state(conn, "progress", result)
        return result
    rows = market.conn.execute(
        "SELECT DISTINCT underlying FROM stk_option_chains WHERE day=? AND observed_at<=? AND observed_at>=?",
        (local.date().toordinal(), to_iso(now), to_iso(now - timedelta(minutes=3))),
    ).fetchall()
    with PaperBook(conn).atomic():
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
            payload = [
                {**asdict(q), "expiration": q.expiration.isoformat(), "quoted_at": to_iso(q.quoted_at) if q.quoted_at else None}
                for q in flat
            ]
            observation = {"symbol": symbol, "quotes": payload, "underlying_price": spot}
            source_key = sha256_hex(canonical_json(observation))
            conn.execute(
                "INSERT OR IGNORE INTO stk_learning_sources VALUES (?,?,?,?)",
                (source_key, symbol, to_iso(now), zlib.compress(canonical_json(observation).encode())),
            )
            snapshots[symbol] = (source_key, spot, valid)
            quotes.update({q.occ: q for q in flat})
        closed = manage(conn, quotes, now)
        filled = fill_orders(conn, quotes, now)
        orders = propose(conn, market, now, quotes, snapshots)
        from desk.stocks.learning_retail import propose as propose_retail

        orders += propose_retail(conn,now,quotes,snapshots)
        book = PaperBook(conn)
        for account, _title, _capital in ACCOUNTS:
            marked = (
                conn.execute(
                    "SELECT COALESCE(SUM(last_value_micros),0) FROM stk_positions WHERE state='OPEN' AND account=?", (account,)
                ).fetchone()[0]
                / MICRO
            )
            equity, contributed = book.cash(account) + marked, book.deposits(account)
            conn.execute(
                "INSERT OR IGNORE INTO stk_learning_nav VALUES (?,?,?,?,?)",
                (to_iso(now), account, equity, contributed, equity - contributed),
            )
        check_in(conn, now)
        result = {
            "phase": "Observing fresh quotes and running registered experiments"
            if quotes
            else "Waiting for fresh timestamped option quotes; no synthetic fills",
            "contracts": len(quotes),
            "orders": orders,
            "filled": filled,
            "closed": closed,
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
    for experiment in experiments:
        experiment["configuration"] = json.loads(experiment.pop("config_json"))
        stats = conn.execute(
            "SELECT COUNT(*),AVG(net_return),SUM(pnl) FROM stk_learning_outcomes WHERE experiment_id=?", (experiment["id"],)
        ).fetchone()
        experiment.update(outcomes=stats[0], mean_net_return=stats[1], pnl=stats[2])
    positions = [position_view(p) for p in book.positions(state=None, limit=100)]
    for position in positions:
        reason = conn.execute("SELECT reasons FROM stk_decisions WHERE id=?", (position["decision_id"],)).fetchone()
        position["entry_reason"] = reason[0] if reason else None
    return {
        "accounts": accounts,
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
        "execution_note": "Estimated depth: 0.1% of OI per quote timestamp, adverse impact and $0.65/contract/side. "
        "Unfilled quantities receive no invented P/L. Only long calls, puts and straddles are supported. "
        "Expiry/assignment settlement without fresh tradable quotes remains unresolved and is not a training label.",
        "mode": "PAPER",
        "orders_enabled": False,
    }
