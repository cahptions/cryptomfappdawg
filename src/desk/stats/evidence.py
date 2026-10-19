"""Load PAPER-ledger evidence for the confidence matrix and record each report in the research registry."""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from datetime import datetime
from typing import Any

from desk.contracts.strategy import DecisionContext
from desk.core.clock import parse_iso8601, to_iso
from desk.core.hashing import canonical_json, fingerprint
from desk.core.ids import new_id
from desk.paper.ledger import PaperLedger
from desk.stats.matrix import Evidence, Trade, build_matrix
from desk.strategies import evaluate_decision
from desk.strategies.evaluate import EVALUATOR_VERSION
from desk.strategies.specs import paper_specs

NO_ROUTE_REASONS = ("NO_ENTRY_ROUTE", "NO_EXIT_ROUTE_AT_ENTRY", "ENTRY_ROUTE_VANISHED")
REPLAY_SAMPLE = 500


def _return(profile: dict[str, Any]) -> float:
    return float(profile["return"])


def load_trades(ledger: sqlite3.Connection) -> list[Trade]:
    holds = {spec.version_id: spec.max_hold_seconds for spec in paper_specs()}
    problems = {r["position_id"] for r in ledger.execute(
        "SELECT DISTINCT position_id FROM paper_events WHERE kind IN ('IMPAIRED', 'EXIT_BLOCKED')")}
    trades = []
    for row in ledger.execute("SELECT * FROM positions WHERE state = 'CLOSED' ORDER BY opened_at, id"):
        profiles = json.loads(row["profiles_json"])
        trades.append(Trade(
            strategy_id=row["strategy_id"], strategy_version=row["strategy_version"],
            opened_at=parse_iso8601(row["opened_at"]), closed_at=parse_iso8601(row["closed_at"]),
            ret=row["realized_pnl_lamports"] / row["ticket_lamports"],
            observed_ret=_return(profiles["observed"]), stress_ret=_return(profiles["stress"]),
            regime_label=row["regime_label"], exit_problem=row["id"] in problems,
            pnl_lamports=row["realized_pnl_lamports"], max_hold_seconds=float(holds.get(row["strategy_version"], 21600))))
    return trades


def replay_agreement(ledger: sqlite3.Connection) -> tuple[int, int]:
    """Re-evaluate recorded PAPER decisions of the current evaluator from their stored inputs."""
    specs = paper_specs()
    checked = identical = 0
    for row in ledger.execute("SELECT candidate_id, payload_json FROM paper_events WHERE kind = 'DECISION' "
                              "ORDER BY id DESC LIMIT ?", (REPLAY_SAMPLE,)):
        payload = json.loads(row["payload_json"])
        if payload.get("evaluator_version") != EVALUATOR_VERSION:
            continue
        context = DecisionContext.model_validate(payload["context"])
        decision = evaluate_decision(row["candidate_id"], context, specs, seed=int(payload.get("seed", 0)))
        checked += 1
        identical += decision.decision_hash == payload["decision_hash"]
    return checked, identical


def confidence_report(ledger_conn: sqlite3.Connection, research: sqlite3.Connection | None, now: datetime, *,
                      seed: int = 0, resamples: int = 2000) -> dict[str, Any]:
    ledger = PaperLedger(ledger_conn)
    trades = load_trades(ledger_conn)
    by_version: dict[str, list[Trade]] = defaultdict(list)
    for trade in trades:
        by_version[trade.strategy_version].append(trade)
    attempts: dict[str, int] = defaultdict(int)
    no_route: dict[str, int] = defaultdict(int)
    for row in ledger_conn.execute("SELECT kind, payload_json FROM paper_events WHERE kind IN ('ENTRY_ATTEMPT', 'ENTRY_FAILED')"):
        payload = json.loads(row["payload_json"])
        version = payload.get("strategy_version", "?")
        if row["kind"] == "ENTRY_ATTEMPT":
            attempts[version] += 1
        elif payload.get("reason") in NO_ROUTE_REASONS:
            no_route[version] += 1
    open_problems: dict[str, int] = defaultdict(int)
    for row in ledger_conn.execute("SELECT strategy_version FROM positions WHERE state IN ('IMPAIRED', 'EXIT_BLOCKED')"):
        open_problems[row["strategy_version"]] += 1
    unreconciled = ledger.reconcile()["difference_lamports"] if ledger.initialized() else 0
    checked, identical = replay_agreement(ledger_conn)
    baseline = {"B1": [t.ret for t in trades if t.strategy_id == "B1"]}
    matrices = []
    for version, rows in sorted(by_version.items(), key=lambda item: item[1][0].strategy_id):
        evidence = Evidence(entry_attempts=attempts[version], entries_without_route=no_route[version],
                            unreconciled_lamports=unreconciled, replay_checked=checked, replay_identical=identical,
                            open_exit_problems=open_problems[version], baseline_returns=baseline)
        matrices.append(build_matrix(rows, evidence, seed=seed, resamples=resamples))
    report = {"kind": "CONFIDENCE_MATRIX", "generated_at": to_iso(now), "seed": seed, "resamples": resamples,
              "evaluator_version": EVALUATOR_VERSION, "closed_trades": len(trades),
              "inputs_hash": fingerprint([(t.strategy_version, t.opened_at, t.ret) for t in trades]),
              "replay": {"checked": checked, "identical": identical}, "matrices": matrices,
              "note": "ELIGIBLE FOR MANUAL LIVE REVIEW only permits a review by the operator; it never arms LIVE"}
    if research is not None:
        experiment = new_id("conf")
        research.execute("INSERT INTO experiments(id, created_at, kind, payload_json) VALUES (?, ?, 'CONFIDENCE_MATRIX', ?)",
                         (experiment, to_iso(now), canonical_json(report)))
        report["experiment_id"] = experiment
    return report


def render(report: dict[str, Any]) -> str:
    lines = [f"\nConfidence matrix ({report['closed_trades']} closed PAPER trades; replay "
             f"{report['replay']['identical']}/{report['replay']['checked']} identical)", ""]
    if not report["matrices"]:
        lines.append("  no closed PAPER trades yet: every strategy is INSUFFICIENT EVIDENCE")
    for matrix in report["matrices"]:
        lines.append(f"  {matrix['strategy_id']}  ({matrix['trades']} trades)  →  {matrix['readiness']}")
        for cell in matrix["cells"]:
            estimate = "" if cell["estimate"] is None else f"{cell['estimate']:.4g}"
            bound = "" if cell["lower"] is None else f"  lower {cell['lower']:.4g}"
            lines.append(f"      {cell['status']:<14} {cell['dimension']:<24} {estimate:>10}{bound}  {cell['detail']}")
        lines.append("")
    lines.append("  " + report["note"])
    return "\n".join(lines)
