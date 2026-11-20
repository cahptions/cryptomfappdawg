"""Prospectively challenge a stock-only choice using the option-implied call-minus-put IV spread.

The version is a hash of the rules. A new version gets a new prospective inception. The daily choices and grades
are append-only. One observation is scheduled every fifth session for the fixed final test.
No simulation, model fitting, provider connection, or account/order dependency is involved.
"""
from __future__ import annotations

import json
import math
import sqlite3
from collections import Counter
from datetime import date, datetime, time, timedelta
from statistics import fmean
from typing import Any

from desk.core.clock import to_iso
from desk.core.hashing import canonical_json, sha256_hex
from desk.stocks import calendar as cal
from desk.stocks import option_signals

TITLE = "Does option pricing improve a stock-only choice?"
HYPOTHESIS = ("Among common stocks with similar momentum, risk and sector exposure, a larger call-minus-put implied "
              "volatility spread predicts a higher stock return from the next open through the fifth session’s close.")
PROTOCOL = {
    "revision": 1, "hypothesis": HYPOTHESIS, "signal_version": option_signals.VERSION,
    "counterhypothesis": "Relative option prices may reflect borrow costs, dividends or stale quotes without predictive value.",
    "window": "First worker pass with sufficient eligible observations from close + 90 minutes until next regular open.",
    "universe": "Up to 200 active common stocks in known sectors with same-session signals, "
                "most liquid pairs first, ticker ties.",
    "signal": "Open-interest-weighted matched call IV minus put IV over 10–60 days. "
              "Chain observation must be between session open and four hours after close, computed before selection.",
    "matched": "Highest five-session price momentum with a same-sector peer within 2 percentage points of momentum "
               "and 5 points of annualized 22-session realized volatility. Minimum eight eligible stocks.",
    "choice": "Largest IV spread in that peer group, requiring a one-volatility-point improvement over momentum leader.",
    "outcome": "Unadjusted regular-session stock price return, next open to fifth close, per arm and relative to SPY. "
               "Known intervening splits invalidate the pair. Cash dividends and execution costs excluded.",
    "adjudication": "Endpoint bar batches must arrive by the third following session close plus four hours.",
    "inference": "30 scheduled five-session blocks from inception, at least 24 graded disagreements, "
                 "one one-sided exact sign test against 10 bp at 5%, mean above 10 bp. Ties count as failures.",
}
VERSION = sha256_hex(canonical_json(PROTOCOL))[:16]
PRIMARY_BLOCKS = 30
MIN_GRADED = 24
HORIZON = 5
ECONOMIC_BPS = 10
_MIN_NAMES = 8
_ABSTAIN_WINDOW = "Decision window ended before a qualifying pair could be frozen."
_LIMITATIONS = [
    "This measures stock price returns. It excludes dividends, trading costs, option fills and account sizing.",
    "Matching controls sector, recent momentum and realized volatility. Market beta, borrow costs and events can still differ.",
    "Chain times describe acquisition and the daily-bar receipt applies to its batch. Individual quote ages can differ.",
    "Nonoverlapping weeks can still be correlated. Missing observations and stock coverage can select the sample.",
]


def _shift(day: date, sessions: int) -> date:
    for _ in range(abs(sessions)):
        day = cal.next_trading_day(day) if sessions > 0 else cal.previous_trading_day(day)
    return day


def _open(day: date) -> datetime:
    return datetime.combine(day, time(9, 30), cal.NEW_YORK)


def _features(conn: sqlite3.Connection, day: date, now: datetime) -> tuple[list[dict[str, Any]], str]:
    start = _shift(day, -HORIZON)
    start_ord, today = start.toordinal(), day.toordinal()
    cutoff = to_iso(now)
    # Price batches are frozen on receipt. Exclude recent known splits from raw momentum.
    rows = conn.execute(
        "SELECT s.symbol,c.sector,s.iv_spread,s.rv22,s.pairs,s.source,s.observed_at,s.computed_at,"
        " s.borrow_rate,s.hard_to_borrow,b.close AS last,a.close AS first "
        "FROM stk_option_signals s JOIN stk_listings l ON l.symbol=s.symbol "
        "JOIN stk_companies c ON c.cik=l.cik JOIN stk_bars b ON b.symbol=s.symbol AND b.day=s.day "
        "JOIN stk_bars a ON a.symbol=s.symbol AND a.day=? "
        "JOIN stk_bar_days rb ON rb.day=b.day JOIN stk_bar_days ra ON ra.day=a.day "
        "WHERE s.day=? AND s.version=? AND s.pairs>=? AND s.liquid>=? AND s.iv_spread IS NOT NULL "
        "AND s.rv22>0 AND c.sector IS NOT NULL AND c.sector NOT IN ('','Other') AND l.active=1 AND l.kind='common' "
        "AND l.test=0 AND l.etf=0 AND b.volume>0 AND a.volume>0 AND a.close>0 AND b.close>0 "
        "AND s.observed_at BETWEEN ? AND ? AND s.computed_at BETWEEN s.observed_at AND ? "
        "AND ra.received_at<=? AND rb.received_at<=? AND NOT EXISTS "
        "(SELECT 1 FROM stk_splits p WHERE p.symbol=s.symbol AND p.execution_date>? AND p.execution_date<=? "
        "AND p.received_at<=?) ORDER BY s.pairs DESC,s.symbol LIMIT 200",
        (start_ord, today, option_signals.VERSION, option_signals.MIN_PAIRS, option_signals.MIN_LIQUID,
         to_iso(_open(day)), to_iso(min(now, cal.session_close(day) + timedelta(hours=4))), cutoff,
         cutoff, cutoff, start_ord, today, cutoff))
    candidates = []
    for r in rows:
        momentum = r["last"] / r["first"] - 1
        if all(math.isfinite(v) for v in (momentum, r["iv_spread"], r["rv22"])):
            candidates.append({**dict(r), "momentum": momentum})
    spy = _bars(conn, "SPY", start, day, now)
    regime = "unknown"
    if spy:
        regime = "SPY rising" if spy["exit_close"] >= spy["entry_close"] else "SPY falling"
    return sorted(candidates, key=lambda c: (-c["momentum"], c["symbol"])), regime


def _decision(conn: sqlite3.Connection, day: date, now: datetime) -> tuple[str, dict[str, Any]] | None:
    candidates, regime = _features(conn, day, now)
    if len(candidates) < _MIN_NAMES:
        return None
    evidence: dict[str, Any] = {"eligible": len(candidates), "regime": regime, "universe": candidates}
    for leader in candidates:
        peers = [c for c in candidates if c["symbol"] != leader["symbol"] and c["sector"] == leader["sector"]
                 and abs(c["momentum"] - leader["momentum"]) <= .02 and abs(c["rv22"] - leader["rv22"]) <= .05]
        if not peers:
            continue
        choice = min(peers, key=lambda c: (-c["iv_spread"], c["symbol"]))
        evidence.update(comparator=leader["symbol"], peers=len(peers))
        if choice["iv_spread"] - leader["iv_spread"] < .01:
            return "abstained", {**evidence, "reason": "Option prices did not clear the one-point IV hurdle."}
        return "proposed", {**evidence, "challenger": choice["symbol"], "reason":
                            "Option prices favor a matched peer over the stock-only momentum leader."}
    return "abstained", {**evidence, "reason": "No same-sector pair met the price momentum and risk limits."}


def _bars(conn: sqlite3.Connection, symbol: str, entry: date, exit_day: date, cutoff: datetime) -> dict[str, Any] | None:
    rows = conn.execute(
        "SELECT b.day,b.open,b.close,d.received_at,d.source FROM stk_bars b JOIN stk_bar_days d ON d.day=b.day "
        "WHERE b.symbol=? AND b.day IN (?,?) AND d.received_at<=? AND b.volume>0 AND b.open>0 AND b.close>0 "
        "ORDER BY b.day", (symbol, entry.toordinal(), exit_day.toordinal(), to_iso(cutoff))).fetchall()
    if len(rows) != 2:
        return None
    if any(datetime.fromisoformat(r["received_at"]) < cal.session_close(date.fromordinal(r["day"])) for r in rows):
        return None
    result = {"entry_open": rows[0]["open"], "entry_close": rows[0]["close"], "exit_close": rows[1]["close"],
              "entry_receipt": rows[0]["received_at"], "exit_receipt": rows[1]["received_at"],
              "entry_source": rows[0]["source"], "exit_source": rows[1]["source"]}
    if not all(math.isfinite(result[k]) for k in ("entry_open", "entry_close", "exit_close")):
        return None
    return result


def _review(market: sqlite3.Connection, research: sqlite3.Connection, now: datetime) -> int:
    recent = cal.last_closed_session(now).toordinal()
    pending = research.execute(
        "SELECT d.* FROM stk_brain_decisions d LEFT JOIN stk_brain_verdicts v USING(version,session) "
        "WHERE d.version=? AND d.decision='proposed' AND d.exit_session<=? AND v.session IS NULL "
        "ORDER BY d.session LIMIT 32", (VERSION, recent)).fetchall()
    written = 0
    for d in pending:
        entry, end = date.fromordinal(d["entry_session"]), date.fromordinal(d["exit_session"])
        deadline = cal.session_close(_shift(end, 3)) + timedelta(hours=4)
        cutoff = min(now, deadline)
        frozen = json.loads(d["evidence"])
        result: dict[str, Any] = {"arms": {}, "regime": frozen["regime"]}
        reason = None
        for role, symbol in (("challenger", frozen["challenger"]), ("comparator", frozen["comparator"]), ("market", "SPY")):
            legs = _bars(market, symbol, entry, end, cutoff)
            split = market.execute(
                "SELECT 1 FROM stk_splits WHERE symbol=? AND execution_date>? AND execution_date<=? AND received_at<=?",
                (symbol, entry.toordinal(), end.toordinal(), to_iso(cutoff))).fetchone()
            if split or legs is None:
                reason = f"{symbol} has a recorded split." if split else f"Awaiting {symbol} endpoint bars."
                break
            bps = 10000 * (legs["exit_close"] / legs["entry_open"] - 1)
            dividends = market.execute(
                "SELECT COALESCE(SUM(cash),0) FROM stk_dividends WHERE symbol=? AND ex_date>? AND ex_date<=? "
                "AND received_at<=?", (symbol, entry.toordinal(), end.toordinal(), to_iso(cutoff)),
            ).fetchone()[0]
            result["arms"][role] = {**legs, "symbol": symbol, "price_return_bps": bps,
                                    "reported_cash_dividends": dividends}
        if reason:
            if now < deadline:
                continue
            result["reason"] = reason.replace("Awaiting", "Missing at the grading deadline:")
            verdict = "inconclusive"
        else:
            arms = result["arms"]
            for role in ("challenger", "comparator"):
                arms[role]["vs_spy_bps"] = arms[role]["price_return_bps"] - arms["market"]["price_return_bps"]
            result["excess_bps"] = arms["challenger"]["price_return_bps"] - arms["comparator"]["price_return_bps"]
            verdict = "graded"
        written += research.execute("INSERT INTO stk_brain_verdicts VALUES (?,?,?,?,?) ON CONFLICT DO NOTHING",
                                    (VERSION, d["session"], to_iso(now), verdict, canonical_json(result))).rowcount
    return written


def advance(market: sqlite3.Connection, research: sqlite3.Connection, now: datetime, *, enabled: bool) -> None:
    """Write decisions only before their next open. An outage keeps its original place in the study."""
    from desk.storage.db import transaction

    current = cal.new_york(now).date()
    with transaction(research):
        if enabled:
            first = current if cal.is_trading_day(current) else cal.next_trading_day(current)
            research.execute("INSERT OR IGNORE INTO stk_brain_protocols VALUES (?,?,?,?)",
                             (VERSION, first.toordinal(), to_iso(now), canonical_json(PROTOCOL)))
            frozen = research.execute("SELECT first_session FROM stk_brain_protocols WHERE version=?", (VERSION,)).fetchone()
            first = date.fromordinal(frozen["first_session"])
            # At most 30 possible cases, never schedule additional blocks to compensate for missing results.
            finished = {r[0] for r in research.execute("SELECT session FROM stk_brain_decisions WHERE version=?", (VERSION,))}
            day = first
            for block in range(PRIMARY_BLOCKS):
                if now < cal.session_close(day) + timedelta(minutes=90):
                    break
                entry = _shift(day, 1)
                end = _shift(day, HORIZON)
                if day.toordinal() not in finished:
                    case = ("abstained", {"reason": _ABSTAIN_WINDOW, "missed": True}) if now >= _open(entry) else (
                        _decision(market, day, now))
                    if case:
                        decision, evidence = case
                        research.execute("INSERT INTO stk_brain_decisions VALUES (?,?,?,?,?,?,?,?)",
                                         (VERSION, day.toordinal(), block, entry.toordinal(), end.toordinal(),
                                          to_iso(now), decision, canonical_json(evidence)))
                day = end
        _review(market, research, now)


def summary(research: sqlite3.Connection, *, enabled: bool) -> dict[str, Any]:
    reg = research.execute("SELECT * FROM stk_brain_protocols WHERE version=?", (VERSION,)).fetchone()
    rows = research.execute(
        "SELECT d.session,d.frozen_at,d.decision,d.evidence,v.verdict,v.evidence AS results "
        "FROM stk_brain_decisions d LEFT JOIN stk_brain_verdicts v USING(version,session) "
        "WHERE d.version=? ORDER BY d.session DESC", (VERSION,),
    ).fetchall()
    counts: Counter[str] = Counter()
    results = []
    recent = []
    for row in rows:
        item = json.loads(row["evidence"])
        counts[row["decision"]] += 1
        if item.get("missed"):
            counts["missed"] += 1
        result = json.loads(row["results"]) if row["results"] else {}
        if row["decision"] == "proposed":
            counts[row["verdict"] or "pending"] += 1
        if row["verdict"] == "graded":
            results.append(result["excess_bps"])
        recent.append({"session": date.fromordinal(row["session"]).isoformat(), "frozen_at": row["frozen_at"],
                       "decision": row["decision"], "reason": item["reason"], "result": row["verdict"],
                       "challenger": item.get("challenger"), "comparator": item.get("comparator"),
                       "excess_bps": result.get("excess_bps"), "outcome_note": result.get("reason")})
    n = len(results)
    complete = len(rows) == PRIMARY_BLOCKS and not counts["pending"]
    wins = sum(x > ECONOMIC_BPS for x in results)
    mean = fmean(results) if results else None
    p = sum(math.comb(n, k) for k in range(wins, n + 1)) / 2**n if complete and n >= MIN_GRADED else None
    if complete:
        status = ("Inconclusive coverage" if p is None else
                  "Cleared the preregistered threshold" if p < .05 and mean is not None and mean > ECONOMIC_BPS else
                  "The challenger did not clear the threshold")
    elif not enabled:
        status = "Enrollment paused" if reg else "Enrollment available in Stocks configuration"
    else:
        status = "Collecting untouched observations" if reg else "Waiting for the worker to register the study"
    return {
        "title": TITLE, "hypothesis": HYPOTHESIS, "version": VERSION, "status": status, "protocol": PROTOCOL,
        "enabled": enabled, "registered_at": reg["registered_at"] if reg else None, "counts": dict(counts),
        "scheduled": PRIMARY_BLOCKS, "recorded": len(rows), "minimum_graded": MIN_GRADED,
        "comparison": {"graded_pairs": n, "mean_excess_bps": mean, "one_sided_p": p, "wins_over_hurdle": wins},
        "recent": recent[:8], "limitations": _LIMITATIONS,
    }
