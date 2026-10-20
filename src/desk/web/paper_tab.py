"""The PAPER trading tab: what the learning book is doing, in plain English, from committed ledger records only.

Every number comes from ``ledger-paper.db`` (and token symbols from ``market.db``). Reads are bounded: the
latest 100 trade/skip events plus the latest 50 decisions, 200 closed trades and 400 curve points. Decision
contexts are stripped in SQL before they are loaded, and counts use the ``(kind, at)`` index rather than parsing
decision payloads, so a refresh stays cheap however long the book runs.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from desk.core.clock import parse_iso8601, to_iso
from desk.core.config import LoadedConfig
from desk.core.units import sol_to_lamports
from desk.paper import conviction as paper_conviction
from desk.paper import fees as paper_fees
from desk.paper.engine import heartbeat_age, stop_file_set
from desk.paper.ledger import PaperLedger
from desk.paper.model import learning_ticket
from desk.storage.databases import Databases
from desk.strategies.specs import paper_specs
from desk.web.paper_view import _replica

EVENT_LIMIT, DECISION_LIMIT, CLOSED_LIMIT, CURVE_POINTS = 100, 50, 200, 400
LAMPORTS = Decimal(10 ** 9)

PLAIN = {
    # decision blockers
    "SECURITY_DEFER": "safety checks not finished", "SECURITY_REJECT": "failed safety checks",
    "SECURITY_PENDING": "safety checks pending", "SECURITY_STALE": "safety check is more than 5 minutes old",
    "SECURITY_TIMESTAMP_MISSING": "no safety-check time recorded",
    "SECURITY_OBSERVED_AFTER_DECISION": "safety check is later than the decision",
    "MARKET_SOL_CRASH_1H": "SOL fell 8% or more in the last hour", "REGIME_NOT_ELIGIBLE": "market regime not eligible",
    "DISCOVERY_REASON_NOT_ELIGIBLE": "wrong kind of discovery for this strategy",
    "MIGRATION_OCCURRENCE_TIMESTAMP_MISSING": "migration time unknown",
    "DISCOVERY_EVENT_TOO_RECENT": "discovered too recently", "DISCOVERY_EVENT_TOO_OLD": "discovered too long ago",
    "VERIFIED_LIQUIDITY_BELOW_MINIMUM": "verified liquidity too low",
    "ROUNDTRIP_COST_OUTSIDE_LIMIT": "a round trip would cost too much",
    "OVERLAY_ONLY_NEVER_STANDALONE": "overlay only", "UNVALIDATED_RESEARCH_SIGNAL": "signal (not yet validated)",
    # entry gates and sizing
    "KILL_SWITCH": "STOP is set", "STOP": "STOP is set",
    "LEARNING_LOSS_LIMIT": "the learning-loss limit was reached (clear with desk resume)",
    "GUARDIAN_NOT_SUPERVISING": "the exit guardian is not running",
    "SUSPEND_RECOVERY": "the laptop just woke up (5-minute pause)", "STALE_MARKET_CONTEXT": "market data is stale",
    "POSITION_LIMIT": "position limit reached", "MINT_ALREADY_OPEN": "already holding this token",
    "ORDER_RATE": "too many entries this minute", "CONSECUTIVE_FAILURES_PAUSE": "3 failed entries in a row (15-minute pause)",
    "LIQUIDITY_CAP_UNKNOWN": "pool liquidity unknown", "BELOW_MINIMUM_TICKET": "ticket below the 0.05 SOL minimum",
    "FEE_FLOOR": "fees would exceed 1% of the ticket", "INSUFFICIENT_CASH": "not enough paper cash",
    "BATTERY_LOW": "battery low", "NO_ENTRY_ROUTE": "no route to buy", "NO_EXIT_ROUTE_AT_ENTRY": "no route to sell back",
    "ENTRY_ROUTE_VANISHED": "the route vanished during the second quote",
    "SIMULATED_TX_FAILURES": "three simulated transaction failures",
    # exits
    "HARD_STOP": "hard stop", "TRAILING_STOP": "trailing stop", "MAX_HOLD": "maximum holding time",
    "LIQUIDITY_DETERIORATION": "liquidity dropped", "LIQUIDITY_BELOW_MINIMUM": "liquidity below the minimum",
    "SECURITY_CHANGE": "safety status changed", "CATASTROPHIC_MARKET": "SOL crash protection",
    "VOLUME_DECAY": "volume faded", "STRATEGY_INVALIDATED": "strategy invalidated", "IMPAIRED": "impaired",
    "MANUAL_LIQUIDATION": "manual liquidation", "EMERGENCY_LIQUIDATION": "emergency liquidation", "RETRY": "retry",
}
OPERATORS = {"gte": "≥", "gt": ">", "lte": "≤", "lt": "<", "eq": "="}


def plain(code: str | None) -> str:
    """Plain-English text for a recorded reason code; unknown codes stay readable rather than hidden."""
    if not code:
        return ""
    if code in PLAIN:
        return PLAIN[code]
    if code.startswith("HALTED:"):
        return f"halted ({plain(code.split(':', 1)[1]).lower() or code.split(':', 1)[1]})"
    head, _, rest = code.partition(":")
    feature = rest.split(":")[0].replace("_", " ")
    if head == "FEATURE_MISSING":
        return f"no {feature} data"
    if head == "FEATURE_STALE":
        return f"{feature} data is stale"
    if head.startswith("FEATURE_"):
        return f"{feature} {head.removeprefix('FEATURE_').replace('_', ' ').lower()}"
    if head == "ENTRY_CONDITION_FALSE":
        parts = rest.split(":")
        operator = OPERATORS.get(parts[1], parts[1]) if len(parts) > 1 else ""
        return f"needs {feature} {operator} {parts[2] if len(parts) > 2 else ''}".strip()
    if head == "REJECTION_CONDITION_TRUE":
        return f"{feature} hit a rejection rule"
    if head.startswith("OVERLAY_"):
        return f"overlay: {plain(rest)}"
    return code.replace("_", " ").lower()


def words(value: Any) -> str:
    return str(value or "").replace("_", " ").lower()


def _sol(lamports: int | None) -> str | None:
    return None if lamports is None else f"{Decimal(lamports) / LAMPORTS:.6f}"


def _ret(numerator: int | None, denominator: int | None) -> str | None:
    if numerator is None or not denominator:
        return None
    return f"{Decimal(numerator) / Decimal(denominator):.4f}"


def _symbols(market: sqlite3.Connection, mints: set[str]) -> dict[str, str]:
    if not mints:
        return {}
    marks = ",".join("?" * len(mints))
    return {row["mint"]: row["symbol"] for row in market.execute(
        f"SELECT mint, symbol FROM tokens WHERE mint IN ({marks}) AND symbol IS NOT NULL", tuple(mints))}  # noqa: S608


def _candidate_mints(market: sqlite3.Connection, candidates: set[str]) -> dict[str, str]:
    if not candidates:
        return {}
    marks = ",".join("?" * len(candidates))
    return {row["id"]: row["mint"] for row in market.execute(
        f"SELECT id, mint FROM discoveries WHERE id IN ({marks})", tuple(candidates))}  # noqa: S608


def _position_mints(ledger: sqlite3.Connection, positions: set[str]) -> dict[str, str]:
    if not positions:
        return {}
    marks = ",".join("?" * len(positions))
    return {row["id"]: row["mint"] for row in ledger.execute(
        f"SELECT id, mint FROM positions WHERE id IN ({marks})", tuple(positions))}  # noqa: S608


def _decision_blockers(ranking: list[dict[str, Any]]) -> list[str]:
    counts: Counter[str] = Counter()
    for row in ranking:
        if row.get("id") in ("NO_TRADE", "B0", "H7") or row.get("eligible"):
            continue
        for reason in row.get("reasons") or []:
            counts[plain(reason)] += 1
    return [text for text, _ in counts.most_common(3)]


def _describe(kind: str, payload: dict[str, Any], symbol: str, strategy: str | None) -> tuple[str, str, str]:
    """(title, detail, tone) for one recorded event."""
    name = symbol or "a token"
    if kind == "DECISION":
        chosen = payload.get("chosen")
        if chosen == "NO_TRADE":
            blockers = _decision_blockers(payload.get("ranking") or [])
            regime = "uncalibrated regime" if payload.get("regime_label") is None else f"regime {payload['regime_label']}"
            return (f"Looked at {name}: no trade", (", ".join(blockers) or "no strategy qualified") + f" · {regime}",
                    "neutral")
        return f"Signal on {name} from {chosen}", "sizing and quoting the entry next", "info"
    if kind == "ENTRY_BLOCKED":
        return f"Entry on {name} blocked", "; ".join(plain(r) for r in payload.get("reasons") or []), "warning"
    if kind == "ENTRY_REJECTED_SIZE":
        return f"Skipped {name}", "; ".join(plain(r) for r in payload.get("reasons") or []), "neutral"
    if kind == "ENTRY_ATTEMPT":
        return f"Quoting a {_sol(int(payload.get('ticket_lamports') or 0))} SOL entry in {name}", \
            "buy quote, sell-back cost check, 2.5 s wait, second quote (the worse one is used)", "info"
    if kind == "ENTRY_REJECTED_COST":
        cost = payload.get("roundtrip_cost_fraction")
        return f"Skipped {name}: too expensive", f"a round trip would cost {Decimal(cost) * 100:.2f}%" if cost not in (
            None, "None") else plain(payload.get("reason")), "neutral"
    if kind == "ENTRY_FAILED":
        return f"Entry in {name} failed", plain(payload.get("reason")), "warning"
    if kind == "ENTRY_ABORTED_AFTER_FILL":
        return f"Entry in {name} cancelled before booking", "; ".join(plain(r) for r in payload.get("reasons") or []) \
            or "a safety condition changed during the quote delay", "warning"
    if kind == "EXIT_FAILED":
        return f"Exit attempt for {name} failed", f"{words(payload.get('status'))} · attempt {payload.get('attempts')}", \
            "warning"
    if kind == "ENTRY_DUPLICATE_REFUSED":
        return f"Duplicate entry in {name} refused", "the ledger allows one open position per token", "neutral"
    if kind == "OPENED":
        return f"Bought {name} for {_sol(int(payload.get('ticket_lamports') or 0))} SOL", \
            f"strategy {payload.get('strategy') or strategy}", "positive"
    if kind == "EXITING":
        return f"Selling {name}", plain(payload.get("reason")), "info"
    if kind == "EXIT_BLOCKED":
        return f"Exit of {name} blocked — still open and supervised", \
            f"{plain(payload.get('reason'))} · attempt {payload.get('attempts')}", "critical"
    if kind == "IMPAIRED":
        return f"{name} impaired", "no sell route for 5 minutes or value fell below half of the last mark", "critical"
    if kind == "CLOSED":
        pnl = int(payload.get("realized_pnl_lamports") or 0)
        sign = "+" if pnl >= 0 else "−"
        return f"Sold {name}: {sign}{_sol(abs(pnl))} SOL", plain(payload.get("reason")), "positive" if pnl >= 0 else "negative"
    if kind == "HALT":
        return "New entries halted", plain(payload.get("reason")), "warning"
    if kind == "RESUME":
        return "New entries allowed again", "cleared with desk resume", "info"
    return kind.replace("_", " ").title(), "", "neutral"


GROUPS = {"ENTRY_BLOCKED": "skip", "ENTRY_REJECTED_SIZE": "skip", "ENTRY_REJECTED_COST": "skip",
          "ENTRY_FAILED": "skip", "ENTRY_ABORTED_AFTER_FILL": "skip", "ENTRY_DUPLICATE_REFUSED": "skip",
          "ENTRY_ATTEMPT": "trade", "OPENED": "trade", "EXITING": "trade", "EXIT_FAILED": "trade",
          "EXIT_BLOCKED": "trade", "IMPAIRED": "trade", "CLOSED": "trade", "HALT": "control", "RESUME": "control"}


def _group(kind: str, payload: dict[str, Any]) -> str:
    """Feed filter: trade, signal, skip, look (a decision not to trade) or control."""
    if kind == "DECISION":
        return "look" if payload.get("chosen") == "NO_TRADE" else "signal"
    return GROUPS.get(kind, "control")


def _status(dbs: Databases, loaded: LoadedConfig, ledger: PaperLedger, now: datetime, open_count: int) -> dict[str, Any]:
    paper = loaded.settings.paper
    guardian = heartbeat_age(dbs.runtime, "guardian", now)
    engine = min((age for age in (heartbeat_age(dbs.runtime, name, now) for name in ("paper-engine", "collector"))
                  if age is not None), default=None)
    halted = ledger.halted() or ("STOP" if stop_file_set(dbs) else None)
    guardian_ok = guardian is not None and 0 <= guardian <= paper.guardian_heartbeat_max_age_seconds
    engine_ok = engine is not None and engine <= 120
    if halted:
        headline, tone, detail = "Entries halted", "warning", f"{plain(halted)} · clear with desk resume in a terminal"
    elif not guardian_ok and open_count:
        headline, tone, detail = "Open positions are not supervised", "critical", "start desk guardian (desk paper starts it)"
    elif not guardian_ok:
        headline, tone, detail = "Entries refused", "warning", "the exit guardian is not running"
    elif not engine_ok:
        headline, tone, detail = "Paper engine not running", "neutral", "start it with desk paper (or from Overview)"
    else:
        headline, tone, detail = "Paper trading active", "positive", "looking for signals after every safety pass"
    return {"headline": headline, "tone": tone, "detail": detail, "halted": halted,
            "guardian_age_seconds": None if guardian is None else round(guardian, 1),
            "engine_age_seconds": None if engine is None else round(engine, 1)}


def paper_tab(dbs: Databases, loaded: LoadedConfig, now: datetime) -> dict[str, Any]:
    ledger = PaperLedger(dbs.paper_ledger)
    if not ledger.initialized():
        return {"initialized": False, "status": {"headline": "No paper book yet", "tone": "neutral",
                                                 "detail": "start desk paper; the book is created on its first run"}}
    conn = dbs.paper_ledger
    meta = ledger.meta()
    specs = {spec.version_id: spec for spec in paper_specs()}
    base, realized, cash = ledger.base_lamports(), ledger.realized_pnl_lamports(), ledger.cash_lamports()
    paper = loaded.settings.paper
    sizing = loaded.settings.sizing
    current_fees = paper_fees.estimate(dbs.market, loaded, now)
    # the engine's own sizing rule at today's fee estimate; the pool-liquidity cap is per candidate, so not applied
    next_ticket = learning_ticket(
        base_lamports=base, realized_pnl_lamports=realized, fixed_fraction=sizing.fixed_fraction,
        max_fraction=sizing.max_ticket_fraction_equity, min_ticket_lamports=sol_to_lamports(sizing.min_ticket_sol),
        max_fixed_cost_fraction=sizing.max_fixed_cost_fraction, fixed_costs_lamports=current_fees.round_trip,
        rent_lamports=current_fees.rent_lamports, cash_lamports=cash, liquidity_cap_lamports=10 ** 15)
    sized_up = learning_ticket(
        base_lamports=base, realized_pnl_lamports=realized,
        fixed_fraction=min(sizing.fixed_fraction * paper_conviction.MULTIPLIER, sizing.max_ticket_fraction_equity),
        max_fraction=sizing.max_ticket_fraction_equity, min_ticket_lamports=sol_to_lamports(sizing.min_ticket_sol),
        max_fixed_cost_fraction=sizing.max_fixed_cost_fraction, fixed_costs_lamports=current_fees.round_trip,
        rent_lamports=current_fees.rent_lamports, cash_lamports=cash, liquidity_cap_lamports=10 ** 15)

    # open positions with their exit levels
    open_rows = [dict(r) for r in conn.execute("SELECT * FROM positions WHERE state != 'CLOSED' ORDER BY opened_at, id")]
    closed_rows = [dict(r) for r in conn.execute(
        "SELECT id, mint, strategy_id, strategy_version, regime_label, opened_at, closed_at, ticket_lamports, "
        "exit_lamports, realized_pnl_lamports, exit_reason, profiles_json, entry_fees_lamports, exit_fees_lamports, "
        "exit_failed_fees_lamports, json_extract(entry_json, '$.conviction.applied') AS conviction_applied, "
        "json_extract(entry_json, '$.fees.source') AS fee_source FROM positions WHERE state = 'CLOSED' "
        "ORDER BY closed_at DESC, id DESC LIMIT ?", (CLOSED_LIMIT,))]
    # Decisions are the most frequent event; a separate bound keeps trades and skips visible beside them.
    columns = "SELECT id, at, kind, position_id, candidate_id, json_remove(payload_json, '$.context') AS payload "
    event_rows = sorted(
        [dict(r) for r in conn.execute(columns + "FROM paper_events WHERE kind != 'DECISION' ORDER BY id DESC LIMIT ?",
                                       (EVENT_LIMIT,))]
        + [dict(r) for r in conn.execute(columns + "FROM paper_events WHERE kind = 'DECISION' ORDER BY id DESC LIMIT ?",
                                         (DECISION_LIMIT,))],
        key=lambda event: event["id"], reverse=True)
    position_mints = _position_mints(conn, {e["position_id"] for e in event_rows if e["position_id"]})
    candidate_mints = _candidate_mints(dbs.market, {e["candidate_id"] for e in event_rows if e["candidate_id"]})
    mints = {r["mint"] for r in open_rows} | {r["mint"] for r in closed_rows} | set(position_mints.values()) \
        | set(candidate_mints.values())
    symbols = _symbols(dbs.market, mints)

    def label(mint: str | None) -> str:
        return (symbols.get(mint or "") or (f"{mint[:4]}…{mint[-4:]}" if mint else "")).strip()

    open_positions = []
    for row in open_rows:
        spec = specs.get(row["strategy_version"])
        ticket, mark, peak = row["ticket_lamports"], row["last_mark_lamports"], row["peak_value_lamports"]
        held = (now - parse_iso8601(row["opened_at"])).total_seconds()
        levels: dict[str, Any] = {}
        if spec is not None:
            stop = int(Decimal(ticket) * (1 - spec.initial_stop.loss_fraction))
            arm = int(Decimal(ticket) * (1 + spec.trailing.activation_gain_fraction))
            armed = peak is not None and peak >= arm
            trail = int(Decimal(peak) * (1 - spec.trailing.drawdown_fraction)) if armed and peak else None
            levels = {"hard_stop_sol": _sol(stop), "hard_stop_return": f"{-spec.initial_stop.loss_fraction:.4f}",
                      "trailing_armed": armed, "trailing_arms_at_sol": _sol(arm),
                      "trailing_stop_sol": _sol(trail), "max_hold_seconds": spec.max_hold_seconds,
                      "hold_remaining_seconds": max(0, int(spec.max_hold_seconds - held))}
        open_positions.append({
            "id": row["id"], "mint": row["mint"], "symbol": label(row["mint"]), "strategy": row["strategy_id"],
            "state": row["state"], "opened_at": row["opened_at"], "held_seconds": int(held),
            "ticket_sol": _sol(ticket), "mark_sol": _sol(mark), "mark_kind": row["last_mark_kind"],
            "mark_at": row["last_mark_at"], "return": _ret(None if mark is None else mark - ticket, ticket),
            "peak_return": _ret(None if peak is None else peak - ticket, ticket), "exit_reason": plain(row["exit_reason"]),
            "exit_attempts": row["exit_attempts"], "regime": row["regime_label"] or "uncalibrated",
            "entry_fees_sol": _sol(row["entry_fees_lamports"]), "rent_locked_sol": _sol(row["rent_lamports"]),
            "conviction": bool((json.loads(row["entry_json"]).get("conviction") or {}).get("applied")), **levels})

    closed = []
    for row in closed_rows:
        profiles = json.loads(row["profiles_json"] or "{}")
        hold = (parse_iso8601(row["closed_at"]) - parse_iso8601(row["opened_at"])).total_seconds()
        fees_paid = row["entry_fees_lamports"] + (row["exit_fees_lamports"] or 0) + row["exit_failed_fees_lamports"]
        closed.append({
            "id": row["id"], "mint": row["mint"], "symbol": label(row["mint"]), "strategy": row["strategy_id"],
            "opened_at": row["opened_at"], "closed_at": row["closed_at"], "hold_seconds": int(hold),
            "ticket_sol": _sol(row["ticket_lamports"]), "exit_sol": _sol(row["exit_lamports"]),
            "pnl_sol": _sol(row["realized_pnl_lamports"]),
            "return": _ret(row["realized_pnl_lamports"], row["ticket_lamports"]),
            "observed_return": (profiles.get("observed") or {}).get("return"),
            "stress_return": (profiles.get("stress") or {}).get("return"),
            "exit_reason": plain(row["exit_reason"]), "regime": row["regime_label"] or "uncalibrated",
            "fees_sol": _sol(fees_paid), "fees_pct_of_ticket": _ret(fees_paid, row["ticket_lamports"]),
            "conviction": bool(row["conviction_applied"]), "fee_source": row["fee_source"] or "FALLBACK_MODEL",
            "breakdown": profiles.get("fee_breakdown")})

    activity = []
    for event in event_rows:
        payload = json.loads(event["payload"] or "{}")
        mint = position_mints.get(event["position_id"] or "") or candidate_mints.get(event["candidate_id"] or "")
        title, detail, tone = _describe(event["kind"], payload, label(mint), payload.get("strategy"))
        activity.append({"id": event["id"], "at": event["at"], "kind": event["kind"],
                         "group": _group(event["kind"], payload), "title": title, "detail": detail, "tone": tone,
                         "position_id": event["position_id"], "candidate_id": event["candidate_id"],
                         "mint": mint, "symbol": label(mint)})

    def funnel(since: str | None) -> dict[str, int]:
        where, args = ("WHERE at >= ?", (since,)) if since else ("", ())
        counts = {r["kind"]: r["n"] for r in conn.execute(
            f"SELECT kind, COUNT(*) AS n FROM paper_events {where} GROUP BY kind", args)}  # noqa: S608
        # every signal is followed by exactly one of these (engine._consider), so no payload is parsed
        signals = sum(counts.get(kind, 0) for kind in ("ENTRY_BLOCKED", "ENTRY_REJECTED_SIZE", "ENTRY_ATTEMPT"))
        return {"decisions": counts.get("DECISION", 0), "no_trade": max(0, counts.get("DECISION", 0) - signals),
                "signals": signals, "blocked": counts.get("ENTRY_BLOCKED", 0),
                "skipped_size": counts.get("ENTRY_REJECTED_SIZE", 0), "skipped_cost": counts.get("ENTRY_REJECTED_COST", 0),
                "attempts": counts.get("ENTRY_ATTEMPT", 0), "failed": counts.get("ENTRY_FAILED", 0),
                "opened": counts.get("OPENED", 0), "closed": counts.get("CLOSED", 0)}

    blocked = Counter[str]()
    for row in conn.execute("SELECT reason.value AS code, COUNT(*) AS n FROM paper_events, json_each(payload_json, '$.reasons') "
                            "AS reason WHERE kind IN ('ENTRY_BLOCKED', 'ENTRY_REJECTED_SIZE') GROUP BY reason.value"):
        blocked[plain(row["code"])] += row["n"]

    strategies = []
    for row in conn.execute(
            "SELECT strategy_id, SUM(state = 'CLOSED') AS closed, SUM(state != 'CLOSED') AS open, "
            "SUM(CASE WHEN state = 'CLOSED' AND realized_pnl_lamports > 0 THEN 1 ELSE 0 END) AS wins, "
            "COALESCE(SUM(realized_pnl_lamports), 0) AS pnl, "
            "AVG(CASE WHEN state = 'CLOSED' THEN CAST(realized_pnl_lamports AS REAL) / ticket_lamports END) AS mean_return "
            "FROM positions GROUP BY strategy_id ORDER BY strategy_id"):
        strategies.append({"strategy": row["strategy_id"], "closed": row["closed"] or 0, "open": row["open"] or 0,
                           "win_rate": None if not row["closed"] else f"{(row['wins'] or 0) / row['closed']:.4f}",
                           "mean_return": None if row["mean_return"] is None else f"{row['mean_return']:.4f}",
                           "pnl_sol": _sol(int(row["pnl"]))})

    curve_rows = list(conn.execute("SELECT closed_at, realized_pnl_lamports FROM positions WHERE state = 'CLOSED' "
                                   "ORDER BY closed_at, id"))
    step = max(1, len(curve_rows) // CURVE_POINTS)
    running, curve = 0, []
    for i, row in enumerate(curve_rows):
        running += row["realized_pnl_lamports"]
        if i % step == 0 or i == len(curve_rows) - 1:
            curve.append({"at": row["closed_at"], "realized_sol": _sol(running)})

    closed_count = conn.execute("SELECT COUNT(*) FROM positions WHERE state = 'CLOSED'").fetchone()[0]
    wins = conn.execute("SELECT COUNT(*) FROM positions WHERE state = 'CLOSED' AND realized_pnl_lamports > 0").fetchone()[0]
    profile_totals = conn.execute(
        "SELECT COALESCE(SUM(json_extract(profiles_json,'$.observed.pnl_lamports')),0) AS observed, "
        "COALESCE(SUM(json_extract(profiles_json,'$.stress.pnl_lamports')),0) AS stress FROM positions "
        "WHERE state='CLOSED'").fetchone()
    unrealized = sum((r["last_mark_lamports"] or 0) - r["ticket_lamports"] for r in open_rows
                     if r["last_mark_kind"] == "EXECUTABLE")
    paid = conn.execute(
        "SELECT COALESCE(SUM(entry_fees_lamports), 0) AS entry, COALESCE(SUM(exit_fees_lamports), 0) AS exit_, "
        "COALESCE(SUM(exit_failed_fees_lamports), 0) AS failed, "
        "COALESCE(SUM(CASE WHEN state = 'CLOSED' THEN ticket_lamports END), 0) AS closed_tickets, "
        "COALESCE(SUM(CASE WHEN state = 'CLOSED' THEN entry_fees_lamports + exit_fees_lamports + exit_failed_fees_lamports "
        "END), 0) AS closed_fees, COALESCE(SUM(CASE WHEN state != 'CLOSED' THEN rent_lamports END), 0) AS rent_locked "
        "FROM positions").fetchone()
    fee_summary = {
        "paid_total_sol": _sol(paid["entry"] + paid["exit_"] + paid["failed"]), "entry_tx_sol": _sol(paid["entry"]),
        "exit_tx_sol": _sol(paid["exit_"]), "failed_tx_sol": _sol(paid["failed"]),
        "rent_locked_sol": _sol(paid["rent_locked"]),
        "average_round_trip_pct": _ret(paid["closed_fees"], paid["closed_tickets"]),
        "current_estimate": current_fees.as_dict(),
        "explanation": ["Jupiter's swap fee, pool fees and price impact are inside every quote (verified 2026-10-19).",
                        "Each round trip also pays three network transactions: buy, sell and closing the token "
                        "account (that is how the rent comes back).",
                        "Network fees use real Jupiter fee reports (90th percentile x 1.5) once six or more exist "
                        "in the last 24 hours; before that the conservative 0.000105 SOL per transaction applies."]}
    h8 = next((spec for spec in specs.values() if spec.strategy_id == "H8"), None)
    moonshot: dict[str, Any] = {"strategy": "H8", "version": None if h8 is None else h8.version_id}
    if h8 is not None:
        evidence, detail = paper_conviction.evidence_status(dbs.research, h8.version_id, now)
        stats = conn.execute(
            "SELECT SUM(state = 'CLOSED') AS closed, SUM(state != 'CLOSED') AS open, "
            "SUM(state = 'CLOSED' AND realized_pnl_lamports > 0) AS wins, COALESCE(SUM(realized_pnl_lamports), 0) AS pnl, "
            "SUM(json_extract(entry_json, '$.conviction.applied') = 1) AS sized_up FROM positions WHERE strategy_id = 'H8'"
        ).fetchone()
        moonshot.update({"closed": stats["closed"] or 0, "open": stats["open"] or 0, "wins": stats["wins"] or 0,
                         "pnl_sol": _sol(int(stats["pnl"])), "sized_up_trades": stats["sized_up"] or 0,
                         "conviction_evidence": evidence, "conviction_detail": detail,
                         "normal_ticket_sol": _sol(next_ticket.lamports), "sized_up_ticket_sol": _sol(sized_up.lamports),
                         "rule": "1.5x size (15% cap) only when H8's own net expectancy is proven (PASS in the latest "
                                 "confidence report) and the signal shows extra confirmation; otherwise normal size"})
    return {
        "initialized": True,
        "status": _status(dbs, loaded, ledger, now, len(open_rows)),
        "book": {"base_usd": ledger.reference_capital()["base_usd"], "reference_capital": ledger.reference_capital(),
                 "started_at": meta["book_started_at"], "sol_usd_at_start": meta["sol_usd_at_start"],
                 "cash_sol": _sol(cash), "deposit_sol": _sol(int(meta["deposit_lamports"])), "base_sol": _sol(base),
                 "sizing_capital_sol": _sol(base + max(0, realized)), "realized_sol": _sol(realized),
                 "realized_observed_sol": _sol(int(profile_totals["observed"])),
                 "realized_stress_sol": _sol(int(profile_totals["stress"])), "unrealized_sol": _sol(unrealized),
                 "return_on_base": _ret(realized, base), "next_ticket_sol": _sol(next_ticket.lamports),
                 "next_ticket_blockers": [plain(r) for r in next_ticket.reasons],
                 "open_positions": len(open_rows), "max_positions": paper.max_learning_positions,
                 "closed_trades": closed_count, "win_rate": None if not closed_count else f"{wins / closed_count:.4f}",
                 "hours_running": round((now - parse_iso8601(meta["book_started_at"])).total_seconds() / 3600, 2)},
        "open": open_positions, "closed": closed, "activity": activity,
        "funnel": {"last_24h": funnel(to_iso(now - timedelta(hours=24))), "all_time": funnel(None)},
        "blocked_reasons": dict(blocked.most_common(12)), "strategies": strategies,
        "exit_reasons": {plain(r["exit_reason"]): r["n"] for r in conn.execute(
            "SELECT exit_reason, COUNT(*) AS n FROM positions WHERE state = 'CLOSED' GROUP BY exit_reason ORDER BY n DESC")},
        "curve": curve, "replica": _replica(dbs, loaded, base), "fees": fee_summary, "moonshot": moonshot,
        "notes": ["Paper cash is $100,000; tickets use the configured reference base plus realized profit, "
                  "up to 15% for an evidence-backed moonshot signal.",
                  "Realized results use the conservative fill; observed and stress are shown for comparison.",
                  "Unrealized values use executable sell quotes only; stops are not guaranteed."],
    }
