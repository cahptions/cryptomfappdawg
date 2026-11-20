"""A prospective PAPER comparison of one call-spread family, with a cash arm and bounded quote sampling."""
from __future__ import annotations

import asyncio
import json
import random
import sqlite3
from collections.abc import Callable
from datetime import date, datetime, time, timedelta
from statistics import fmean
from typing import Any

from desk.core.clock import to_iso
from desk.core.errors import DeskError
from desk.core.hashing import canonical_json, sha256_hex
from desk.stocks import calendar as cal
from desk.stocks.execution import Quote, evidence
from desk.stocks.learner import model, policy
from desk.stocks.paper.fills import CONTRACT, OPTION_FEE_PER_CONTRACT
from desk.stocks.repo import StocksMarket, day_of

FAMILY = "bull_call_debit"
BUDGET = 500.0
FLOOR = .01
MIN_BLOCKS = 20
MIN_SUPPORT = .80
WINDOWS = 12
WINDOW_SECONDS = 300
ENTRY_HOUR = timedelta(hours=1)
MAX_COHORTS_PER_REVIEW = 20


def content() -> dict[str, Any]:
    return {"family": FAMILY, "version": 1, "one_unit_budget": BUDGET, "windows": WINDOWS,
            "window_seconds": WINDOW_SECONDS, "min_independent_blocks": MIN_BLOCKS, "min_support": MIN_SUPPORT,
            "economic_floor": FLOOR, "challenger": "nearest 0.50 anchor delta then lowest displayed spread, hold to expiry",
            "control": "uniform among unused matched candidates, no allocation",
            "match": "same underlying, expiry and structure; risk within 25%, net delta within 0.10",
            "observations": "one joint quote receipt per five-minute slot in the first hour of the next session",
            "grade": "two-cent debit allowance, 80% complete slots, settle at the official expiry close",
            "comparison": "first 20 non-overlapping matched blocks; lower 95% paired bounds vs challenger and cash, "
                          "and mean improvement on the full comparable window including abstentions"}


VERSION = sha256_hex(canonical_json(content()))[:16]


def open_at(day: date) -> datetime:
    return datetime.combine(day, time(9, 30), cal.NEW_YORK)


def _candidate(o: policy.Option) -> dict[str, Any]:
    return {"symbol": o.symbol, "key": o.key, "expiry": date.fromordinal(int(o.row["expiration"])).isoformat(),
            "entry": float(o.row["entry"]), "risk": float(o.row["max_loss"]), "legs": o.row["legs"],
            "delta": o.row["x"].get("net_delta"), "anchor": o.row["x"].get("anchor_delta"),
            "spread": o.row["x"].get("spread_pct"), "group_index": o.index, "quote_cutoff": o.quote_cutoff}


def _order(c: dict[str, Any]) -> tuple[float, float, str, str, int]:
    return (abs(abs(c["anchor"]) - .5) if c["anchor"] is not None else 1.,
            c["spread"] if c["spread"] is not None else float("inf"), c["symbol"], c["key"], c["group_index"])


def _match(c: dict[str, Any], other: dict[str, Any]) -> bool:
    return (c["symbol"] == other["symbol"] and c["expiry"] == other["expiry"] and c["legs"] != other["legs"]
            and .8 * c["risk"] <= other["risk"] <= 1.25 * c["risk"]
            and c["delta"] is not None and other["delta"] is not None
            and abs(c["delta"] - other["delta"]) <= .10)


def record(research: sqlite3.Connection, ledger: sqlite3.Connection, session: date, now: datetime,
           *, stopped: bool = False) -> bool:
    day = day_of(session)
    if now < cal.session_close(session) or research.execute(
            "SELECT 1 FROM stk_execution_cohort WHERE session=? AND version=?", (day, VERSION)).fetchone():
        return False
    online = now < open_at(cal.next_trading_day(session)) and not stopped
    candidates = []
    if online:
        for opt in policy.session_candidates(research, session):
            if not opt.key.startswith(FAMILY + "|") or not 0 < float(opt.row["max_loss"]) <= BUDGET:
                continue
            if opt.quote_cutoff is None or datetime.fromisoformat(opt.quote_cutoff) > now:
                continue
            if date.fromordinal(int(opt.row["expiration"])) <= cal.next_trading_day(session):
                continue
            candidates.append(_candidate(opt))
    candidates.sort(key=_order)
    mine = None
    active_code = model.version_hash()
    accepted = ledger.execute(
        "SELECT status,why,model_code,model_id,policy_version FROM stk_learner_days WHERE session=? AND account=?",
        (day, policy.ACCOUNT)).fetchone()
    learner_outcome = "unavailable"
    if (online and candidates and accepted and accepted["status"] != "waiting"
            and accepted["model_code"] == active_code and accepted["policy_version"] == policy.version_hash()):
        orders = ledger.execute(
            "SELECT d.symbol,d.action_key,p.order_json,p.id AS order_id FROM stk_learner_decisions d "
            "JOIN stk_pending p ON p.decision_id=d.paper_decision_id "
            "WHERE d.session=? AND d.account=? AND d.action_key LIKE ? ORDER BY d.id",
            (day, policy.ACCOUNT, FAMILY + "|%")).fetchall()
        for row in orders:
            legs = [[x["occ"], x["quantity"]] for x in json.loads(row["order_json"])["legs"]]
            mine = next((c for c in candidates if row["symbol"] == c["symbol"] and row["action_key"] == c["key"]
                         and sorted(c["legs"]) == sorted(legs)), None)
            if mine is not None:
                mine = {**mine, "paper_order": row["order_id"]}
                break
        learner_outcome = "selected" if mine else "out_of_scope" if orders else "cash"
    alternatives = [c for c in candidates if _match(mine, c)] if mine else candidates
    simple = alternatives[0] if alternatives else None
    reference = mine or simple
    rejected = [c for c in alternatives if reference and _match(reference, c) and c != simple]
    sample = random.Random(f"{session}:{VERSION}:rejected").choice(rejected) if rejected else None  # noqa: S311
    why = ("Day recorded after the entry window or with STOP active." if not online else
           str(accepted["why"]) if accepted else "The Learner had no recorded decision for this close.")
    manifest = {"rules": content(), "model": active_code, "fit": accepted["model_id"] if accepted else None,
                "policy": policy.version_hash(), "trading_day": online,
                "eligible_candidates": len(candidates), "learner_outcome": learner_outcome,
                "learner": mine, "challenger": simple,
                "rejected": sample, "rejected_probability": 1 / len(rejected) if rejected else None, "why": why}
    cur = research.execute(
        "INSERT OR IGNORE INTO stk_execution_cohort VALUES (?,?,?,?)",
        (day, VERSION, to_iso(now), canonical_json(manifest)))
    return cur.rowcount == 1


def advance(market: StocksMarket, research: sqlite3.Connection, ledger: sqlite3.Connection, session: date, now: datetime,
            *, stopped: bool) -> dict[str, Any]:
    last = research.execute("SELECT MAX(session) FROM stk_execution_cohort WHERE version=?", (VERSION,)).fetchone()[0]
    first = date.fromordinal(last) if last else session
    # Store only missed days after this study began. Never replay a hypothetical entry across downtime.
    missed = sum(record(research, ledger, day, now, stopped=True)
                 for day in cal.trading_days(first, session) if first < day < session)
    return {"recorded": record(research, ledger, session, now, stopped=stopped),
            "missed": missed, "graded": grade(market, research, now, ledger=ledger)}


async def sample(research: sqlite3.Connection, client: Any, now: datetime,
                 clock: Callable[[], datetime]) -> dict[str, Any] | None:
    """Spend at most one quote request in a five-minute slot, charged to Plays tracking's existing budget."""
    day = cal.new_york(now).date()
    opened = open_at(day)
    if not client or not client.configured or not opened <= now < opened + ENTRY_HOUR:
        return None
    session = day_of(cal.previous_trading_day(day))
    row = research.execute("SELECT manifest_json FROM stk_execution_cohort WHERE session=? AND version=?",
                           (session, VERSION)).fetchone()
    if row is None:
        return None
    arms = json.loads(row[0])
    names = ("learner", "challenger", "rejected")
    occs = sorted({leg[0] for name in names if arms[name] for leg in arms[name]["legs"]})
    if not occs:
        return None
    slot = str(int((now - opened).total_seconds() // WINDOW_SECONDS))
    # Claim the request before awaiting it. An interrupted request leaves an explicit unknown receipt.
    cur = research.execute("INSERT OR IGNORE INTO stk_execution_windows VALUES (?,?,?,?,?)",
                           (session, VERSION, slot, to_iso(now), "{}"))
    if not cur.rowcount:
        return None
    try:
        quotes = {q.occ: Quote.of(q) for q in await asyncio.wait_for(client.option_quotes(occs), timeout=8)}
    except (DeskError, TimeoutError) as exc:
        return {"requests": 1, "status": "unavailable", "cause": type(exc).__name__}
    received = clock()
    outcomes = {}
    if opened <= received < opened + ENTRY_HOUR:
        for name in names:
            if arm := arms[name]:
                outcomes[name] = evidence(arm["legs"], quotes, received, received, max_debit=arm["entry"] * CONTRACT)
    research.execute("UPDATE stk_execution_windows SET received_at=?, evidence_json=? "
                     "WHERE session=? AND version=? AND slot=?",
                     (to_iso(received), canonical_json(outcomes), session, VERSION, slot))
    return {"requests": 1, "quotes": len(quotes), "checks": len(outcomes)}


def _grade_arm(market: StocksMarket, arm: dict[str, Any] | None, windows: list[dict[str, Any]], name: str,
               opened: date) -> dict[str, Any]:
    if not arm:
        return {"status": "cash", "net": 0., "conservative": 0.}
    matches = [w[name] for w in windows if name in w and w[name]["status"] in {"supported", "unsupported"}]
    eligible = [m for m in matches if m["status"] == "supported"]
    coverage = {"complete_windows": len(matches), "supported_windows": len(eligible)}
    if len(matches) < MIN_SUPPORT * WINDOWS:
        return {"status": "unknown", **coverage}
    if not eligible:
        return {"status": "no crossing in complete sampled windows", "net": 0., "conservative": 0., **coverage}
    expiry = date.fromisoformat(arm["expiry"])
    settle = expiry if cal.is_trading_day(expiry) else cal.previous_trading_day(expiry)
    conn, symbol = market.conn, arm["symbol"]
    row = conn.execute("SELECT close FROM stk_bars WHERE day=? AND symbol=?", (day_of(settle), symbol)).fetchone()
    split = conn.execute("SELECT 1 FROM stk_splits WHERE symbol=? AND execution_date BETWEEN ? AND ?",
                         (symbol, day_of(opened), day_of(settle))).fetchone()
    if row is None or split or not row[0]:
        return {"status": "unknown settlement", **coverage}
    from desk.stocks.play_tracking import parse_occ

    payout = sum(sign * max(0., float(row[0]) - parse_occ(occ)[2]) for occ, sign in arm["legs"]) * CONTRACT
    natural = float(eligible[0]["net_debit"])
    fees = len(arm["legs"]) * OPTION_FEE_PER_CONTRACT
    # An extra two cents of net execution allowance. If the limit would have been missed, keep cash.
    adverse = next((float(m["net_debit"]) + 2 for m in eligible if m["net_debit"] + 2 <= arm["entry"] * CONTRACT), None)
    return {"status": "quote-supported simulation", "net": round(payout - natural - fees, 2),
            "conservative": round(payout - adverse - fees, 2) if adverse is not None else 0.,
            **coverage, "entry": eligible[0], "exit_close": row[0]}


def grade(market: StocksMarket, research: sqlite3.Connection, now: datetime, *,
          ledger: sqlite3.Connection | None = None) -> int:
    count = 0
    rows = research.execute(
        "SELECT c.session,c.manifest_json FROM stk_execution_cohort c LEFT JOIN stk_execution_grades g "
        "USING(session,version) WHERE c.version=? AND g.session IS NULL ORDER BY c.session", (VERSION,))
    ready = []
    for session, blob in rows:
        arms = json.loads(blob)
        entry = cal.next_trading_day(date.fromordinal(session))
        last = max((date.fromisoformat(arms[name]["expiry"]) for name in ("learner", "challenger", "rejected")
                    if arms[name]), default=entry)
        if now >= cal.session_close(last if cal.is_trading_day(last) else cal.previous_trading_day(last)):
            ready.append((session, arms, entry, last))
        if len(ready) == MAX_COHORTS_PER_REVIEW:
            break
    for session, arms, entry, last in ready:
        windows = [json.loads(row[0]) for row in research.execute(
            "SELECT evidence_json FROM stk_execution_windows WHERE session=? AND version=? ORDER BY CAST(slot AS INT)",
            (session, VERSION))]
        result: dict[str, Any] = {name: _grade_arm(market, arms[name], windows, name, entry)
                                  for name in ("learner", "challenger", "rejected")}
        if arms.get("learner_outcome") in ("unavailable", "out_of_scope"):
            result["learner"] = {"status": arms["learner_outcome"]}
        result.update(cash={"status": "cash", "net": 0., "conservative": 0.}, through=last.isoformat())
        order_id = (arms["learner"] or {}).get("paper_order")
        if order_id and ledger is not None:
            paper = ledger.execute(
                "SELECT p.state,pos.entry_day FROM stk_pending p LEFT JOIN stk_positions pos ON pos.decision_id=p.decision_id "
                "WHERE p.id=?", (order_id,)).fetchone()
            if paper and paper[0] != "PENDING":
                result["paper_filled_on_entry_day"] = paper[0] == "FILLED" and paper[1] == day_of(entry)
        # A closing print can arrive late. Allow two more trading days before freezing an unresolved settlement.
        if any(x.get("status") == "unknown settlement" for x in result.values() if isinstance(x, dict)) and (
                now < cal.session_close(cal.next_trading_day(cal.next_trading_day(last)))):
            continue
        cur = research.execute("INSERT OR IGNORE INTO stk_execution_grades VALUES (?,?,?,?)",
                               (session, VERSION, to_iso(now), canonical_json(result)))
        count += cur.rowcount
    return count


def _lower(values: list[float], seed: int) -> float | None:
    if len(values) < MIN_BLOCKS:
        return None
    rng = random.Random(seed)  # noqa: S311
    return sorted(fmean(rng.choices(values, k=len(values))) for _ in range(2000))[49]


def summary(research: sqlite3.Connection) -> dict[str, Any]:
    """Score each eligible day, with a single fixed confirmation window of non-overlapping exposure blocks."""
    model_code = model.version_hash()
    counts = {"eligible": 0, "graded": 0, "paired": 0, "cash_days": 0, "unobserved_days": 0,
              "rejected_sampled": 0, "rejected_observed": 0,
              "paper_compared": 0, "paper_only": 0, "quote_support_only": 0}
    dollars: dict[str, list[float]] = {a: [] for a in ("learner", "challenger")}
    complete = scheduled = supported = 0
    blocks: list[tuple[float, float]] = []
    through = date.min
    confirm = None
    pending_earlier = False
    rows = research.execute(
        "SELECT c.session,c.manifest_json,g.result_json FROM stk_execution_cohort c LEFT JOIN stk_execution_grades g "
        "USING(session,version) WHERE c.version=? ORDER BY c.session", (VERSION,))
    for session, blob, grade_json in rows:
        cohort = json.loads(blob)
        if cohort["model"] != model_code or cohort["policy"] != policy.version_hash():
            continue
        counts["eligible"] += 1
        observed = bool(cohort.get("trading_day", True) and cohort.get("eligible_candidates", 1)
                        and cohort.get("learner_outcome") not in ("unavailable", "out_of_scope"))
        counts["unobserved_days"] += int(not observed)
        counts["cash_days"] += int(observed and cohort["learner"] is None)
        counts["rejected_sampled"] += int(cohort["rejected"] is not None)
        if not grade_json:
            pending_earlier = True
            continue
        result = json.loads(grade_json)
        counts["graded"] += 1
        for name in ("learner", "challenger", "rejected"):
            if cohort[name]:
                scheduled += WINDOWS
                complete += result[name].get("complete_windows", 0)
                supported += result[name].get("supported_windows", 0)
        counts["rejected_observed"] += int(cohort["rejected"] is not None and "net" in result["rejected"])
        if "paper_filled_on_entry_day" in result and "net" in result["learner"]:
            legacy_filled = result["paper_filled_on_entry_day"]
            shadow_filled = result["learner"]["status"] == "quote-supported simulation"
            counts["paper_compared"] += 1
            counts["paper_only"] += int(legacy_filled and not shadow_filled)
            counts["quote_support_only"] += int(shadow_filled and not legacy_filled)
        mine, peer = result["learner"].get("conservative"), result["challenger"].get("conservative")
        if not observed or mine is None or peer is None or (cohort["learner"] and not cohort["challenger"]):
            continue
        counts["paired"] += 1
        dollars["learner"].append(mine)
        dollars["challenger"].append(peer)
        day = date.fromordinal(session)
        # Cash-only days belong to the denominator, but provide no independent comparison of positions.
        if not pending_earlier and cohort["learner"] and cohort["challenger"] and day > through and len(blocks) < MIN_BLOCKS:
            through = date.fromisoformat(result["through"])
            blocks.append(((mine - peer) / BUDGET, mine / BUDGET))
            if len(blocks) == MIN_BLOCKS:
                lower = _lower([v[0] for v in blocks], 83)
                cash = _lower([v[1] for v in blocks], 89)
                support = counts["paired"] / counts["eligible"]
                overall_cash = fmean(dollars["learner"]) / BUDGET
                overall_peer = overall_cash - fmean(dollars["challenger"]) / BUDGET
                confirm = {"paired_lower": lower, "cash_lower": cash, "support": round(support, 4),
                           "all_comparable_vs_cash": round(overall_cash, 4),
                           "all_comparable_vs_peer": round(overall_peer, 4),
                           "passes": lower is not None and cash is not None and min(lower, cash) >= FLOOR
                           and min(overall_cash, overall_peer) >= FLOOR and support >= MIN_SUPPORT}
    capture = research.execute("SELECT COUNT(*),SUM(length(evidence_json)) FROM stk_execution_windows WHERE version=?",
                               (VERSION,)).fetchone()
    return {"version": VERSION, "family": FAMILY, **counts,
            "quote_requests_recorded": capture[0], "quote_evidence_bytes": capture[1] or 0, "independent_blocks": len(blocks),
            "needed_blocks": MIN_BLOCKS, "confirmation": confirm,
            "quote_coverage": {"scheduled": scheduled, "complete": complete,
                               "supported_min": supported, "supported_max": supported + scheduled - complete},
            "dollars_per_100": {k: round(fmean(v) / (BUDGET / 100), 3) if v else None for k, v in dollars.items()},
            "basis": "One-unit call spreads held to expiry. Quote-supported simulations, cash and unavailable days."}
