"""M9 readiness report (PLAN §14.3; ACCEPTANCE M9-01…M9-05): one verdict, generated from recorded data only.

The report is a pure function of the databases. Its time is the latest recorded PAPER event, the confidence
matrix is recomputed with a fixed seed, and no clock reading enters the text, so regenerating from the same data
gives byte-identical Markdown. Every figure is listed in an appendix with the query it came from.

Nothing here changes the mode, an approval or a gate. ``ELIGIBLE FOR MANUAL LIVE REVIEW`` permits the operator
to review a strategy version; LIVE still needs every PLAN §16.5 gate, including a typed approval.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from desk.core.clock import from_epoch_millis, parse_iso8601, to_epoch_millis, to_iso
from desk.core.config import LoadedConfig
from desk.core.hashing import canonical_json
from desk.core.ids import new_id
from desk.core.paths import DeskPaths
from desk.paper import fees as paper_fees
from desk.paper.ledger import PaperLedger
from desk.paper.report import replica
from desk.regime.context import build_reference
from desk.stats.evidence import confidence_report, load_trades
from desk.stats.matrix import BASELINES, RULES
from desk.storage.databases import Databases

INSUFFICIENT_EVIDENCE = "INSUFFICIENT EVIDENCE"
FAILED_VALIDATION = "FAILED VALIDATION"
CONTINUE_PAPER = "CONTINUE PAPER TESTING"
ELIGIBLE = "ELIGIBLE FOR MANUAL LIVE REVIEW"
# The desk's verdict is the most advanced state any strategy version reached: one eligible version permits a
# review of that version; failures only decide the verdict when nothing else is still being tested.
PRECEDENCE = (ELIGIBLE, CONTINUE_PAPER, INSUFFICIENT_EVIDENCE, FAILED_VALIDATION)
SEED, RESAMPLES = 0, 2000
CONTEXT_GAP_MS = 5 * 60_000                 # market context is recorded every minute while collection runs
LAMPORTS = Decimal(10 ** 9)

NEXT_STEP = {
    INSUFFICIENT_EVIDENCE: "Keep PAPER running. No strategy version has the sample PLAN §14.1 requires yet "
                           f"({int(RULES['min_trades'])} closed trades, effective sample size "
                           f"{int(RULES['min_ess'])}, {int(RULES['min_days'])} calendar days).",
    FAILED_VALIDATION: "Do not add money. Every strategy version with a sufficient sample failed a validation "
                       "rule; keep PAPER running only to test revised versions.",
    CONTINUE_PAPER: "Keep PAPER running. At least one strategy version has a sufficient sample and no failure, "
                    "but not every dimension passes yet.",
    ELIGIBLE: "You may review the eligible version(s) below for LIVE. Nothing has changed: real money still needs "
              "every LIVE gate, including your typed `desk live approve <version>`, a capital allocation you set, "
              "the armed signer and a calibrated market regime.",
}
DISCLOSURES = (
    "Past PAPER results do not guarantee future returns. Memecoin returns are heavy-tailed; a strategy that "
    "validated can still lose money live.",
    "PAPER fills come from real Jupiter quotes for the exact amount, with a waiting period, the worse of two quotes, "
    "simulated transaction failures and every fee, but they are still simulated: no transaction was landed, so "
    "landing failures, MEV/sandwich exposure and state changes during inclusion are not measured (PLAN §16.4).",
    "Stops are evaluated on executable quotes and are not guaranteed. A rug pull or liquidity removal can skip a "
    "stop entirely.",
    "Each strategy version is one of several evaluated at the same time; the confidence matrix reports each "
    "separately and does not make them independent evidence.",
    "Decisions before a calibrated 30-day market regime existed were taken with the regime recorded as "
    "uncalibrated; LIVE requires a calibrated regime (gate 7).",
    "The reference-account replica is an estimate replayed from the learning-book fills, not a funded account.",
)


@dataclass
class Sources:
    """Figure → (database, query) for the appendix; each figure in the report is registered here."""

    rows: list[tuple[str, str, str]] = field(default_factory=list)

    def note(self, figure: str, database: str, query: str) -> None:
        self.rows.append((figure, database, " ".join(query.split())))

    def scalar(self, conn: sqlite3.Connection, database: str, figure: str, query: str, args: tuple[Any, ...] = ()) -> Any:
        self.note(figure, database, query)
        row = conn.execute(query, args).fetchone()
        return None if row is None else row[0]


def overall_verdict(states: list[str]) -> str:
    """Exactly one PLAN §14.3 state for the desk, from the per-version states (baselines excluded)."""
    present = [s for s in states if s in PRECEDENCE]
    return next((state for state in PRECEDENCE if state in present), INSUFFICIENT_EVIDENCE)


def _sol(lamports: int | None) -> str:
    return "—" if lamports is None else f"{Decimal(lamports) / LAMPORTS:.6f} SOL"


def _pct(value: float | None, digits: int = 2) -> str:
    return "—" if value is None else f"{value * 100:+.{digits}f}%"


def _num(value: float | None) -> str:
    return "—" if value is None else f"{value:.4g}"


def _context_gaps(market: sqlite3.Connection, start: datetime, end: datetime, sources: Sources) -> dict[str, Any]:
    query = "SELECT observed_at_ms FROM market_context WHERE observed_at_ms >= ? AND observed_at_ms <= ? " \
            "ORDER BY observed_at_ms"
    sources.note("market-context coverage and gaps", "market.db", query)
    lo, hi = to_epoch_millis(start), to_epoch_millis(end)
    previous, count, missing, longest, longest_at = lo, 0, 0, 0, None
    for (at,) in market.execute(query, (lo, hi)):
        gap = at - previous
        if gap > CONTEXT_GAP_MS:
            count += 1
            missing += gap
            if gap > longest:
                longest, longest_at = gap, previous
        previous = at
    if hi - previous > CONTEXT_GAP_MS:                 # collection stopped before the last recorded event
        count += 1
        missing += hi - previous
        if hi - previous > longest:
            longest, longest_at = hi - previous, previous
    span = max(hi - lo, 1)
    return {"gaps": count, "missing_hours": missing / 3_600_000, "coverage": 1 - missing / span,
            "longest_hours": longest / 3_600_000,
            "longest_from": None if longest_at is None else to_iso(from_epoch_millis(longest_at))}


def _short(version: str | None) -> str:
    if not version:
        return "—"
    head, _, digest = version.rpartition(":")
    return f"{head}:{digest[:12]}" if head else version[:24]


def readiness_report(dbs: Databases, loaded: LoadedConfig) -> tuple[str, dict[str, Any]]:
    """(Markdown, summary) for the recorded data. Deterministic: same databases ⇒ same bytes."""
    sources = Sources()
    ledger = PaperLedger(dbs.paper_ledger)
    lines = ["# KESTREL readiness report", ""]
    if not ledger.initialized():
        verdict = INSUFFICIENT_EVIDENCE
        lines += [f"**Verdict: {verdict}**", "", "No PAPER book exists yet, so there is no evidence to evaluate. "
                  "Start one with `desk paper`.", ""]
        lines += _footer(sources)
        markdown = "\n".join(lines) + "\n"
        return markdown, {"verdict": verdict, "next_step": NEXT_STEP[verdict], "as_of": None, "strategies": {},
                          "markdown_sha256": hashlib.sha256(markdown.encode()).hexdigest()}

    conn, market = dbs.paper_ledger, dbs.market
    meta = ledger.meta()
    started = parse_iso8601(meta["book_started_at"])
    sources.note("book start", "ledger-paper.db", "SELECT value FROM ledger_meta WHERE key = 'book_started_at'")
    latest = sources.scalar(conn, "ledger-paper.db", "as-of time (latest recorded PAPER event)",
                            "SELECT MAX(at) FROM (SELECT MAX(at) AS at FROM paper_events UNION ALL "
                            "SELECT MAX(opened_at) FROM positions UNION ALL SELECT MAX(closed_at) FROM positions)")
    as_of = max(started, parse_iso8601(latest)) if latest else started
    days = (as_of - started).total_seconds() / 86_400
    first_trade = sources.scalar(conn, "ledger-paper.db", "first trade", "SELECT MIN(opened_at) FROM positions")
    closed = int(sources.scalar(conn, "ledger-paper.db", "closed trades",
                                "SELECT COUNT(*) FROM positions WHERE state = 'CLOSED'"))
    still_open = int(sources.scalar(conn, "ledger-paper.db", "open positions",
                                    "SELECT COUNT(*) FROM positions WHERE state != 'CLOSED'"))
    exit_problems = int(sources.scalar(conn, "ledger-paper.db", "open exit problems",
                                       "SELECT COUNT(*) FROM positions WHERE state IN ('IMPAIRED', 'EXIT_BLOCKED')"))
    new_markets = int(sources.scalar(market, "market.db", "candidates first observed during the book",
                                     "SELECT COUNT(*) FROM discoveries WHERE first_observed_at >= ? AND "
                                     "first_observed_at <= ?", (to_iso(started), to_iso(as_of))))
    ingest_errors = int(sources.scalar(market, "market.db", "ingest errors during the book",
                                       "SELECT COUNT(*) FROM ingest_errors WHERE at >= ? AND at <= ?",
                                       (to_iso(started), to_iso(as_of))))
    gaps = _context_gaps(market, started, as_of, sources)

    report = confidence_report(conn, None, as_of, seed=SEED, resamples=RESAMPLES)
    sources.note("confidence matrices, replay agreement", "ledger-paper.db",
                 f"desk.stats.evidence.confidence_report(seed={SEED}, resamples={RESAMPLES}) over every closed "
                 "position (positions.state = 'CLOSED') and the latest recorded DECISION events")
    matrices = sorted(report["matrices"], key=lambda m: (m["strategy_id"], m["strategy_version"] or ""))
    tested = [m for m in matrices if m["strategy_id"] not in BASELINES]
    verdict = overall_verdict([m["readiness"] for m in tested])
    deciding = [m for m in tested if m["readiness"] == verdict]

    trades = load_trades(conn)
    profiles: dict[str, list[tuple[float, float, float]]] = defaultdict(list)
    for t in trades:
        profiles[t.strategy_version].append((t.observed_ret, t.ret, t.stress_ret))
    totals = conn.execute(
        "SELECT COALESCE(SUM(json_extract(profiles_json,'$.observed.pnl_lamports')),0), "
        "COALESCE(SUM(json_extract(profiles_json,'$.conservative.pnl_lamports')),0), "
        "COALESCE(SUM(json_extract(profiles_json,'$.stress.pnl_lamports')),0) FROM positions "
        "WHERE state = 'CLOSED'").fetchone()
    sources.note("realized P&L by cost profile", "ledger-paper.db",
                 "SELECT SUM(json_extract(profiles_json,'$.<profile>.pnl_lamports')) FROM positions WHERE state = 'CLOSED'")
    fees_paid = int(sources.scalar(conn, "ledger-paper.db", "network fees paid",
                                   "SELECT COALESCE(SUM(entry_fees_lamports + exit_fees_lamports + "
                                   "exit_failed_fees_lamports), 0) FROM positions"))
    positions = [dict(r) for r in conn.execute("SELECT * FROM positions ORDER BY opened_at, id")]
    account = replica([r for r in positions if r["state"] == "CLOSED"], [r for r in positions if r["state"] != "CLOSED"],
                      loaded, ledger.base_lamports())
    sources.note("reference-account replica", "ledger-paper.db",
                 "desk.paper.report.replica over SELECT * FROM positions ORDER BY opened_at, id")
    reconciliation = ledger.reconcile()
    sources.note("ledger reconciliation", "ledger-paper.db",
                 "SUM(cash_entries.amount_lamports) vs deposit − tickets − fees − rent + exits over positions")
    estimate = paper_fees.estimate(market, loaded, as_of)
    sources.note("network-fee source at the as-of time", "market.db",
                 "SELECT signature_fee_lamports, prioritization_fee_lamports FROM fee_observations "
                 "WHERE observed_at_ms in the 24 h before the as-of time")
    calibrated = build_reference(market, as_of) is not None
    sources.note("calibrated 30-day regime reference", "market.db",
                 "desk.regime.context.build_reference: complete market_context rows covering the 30 days before "
                 "the as-of hour")

    lines += [f"**Verdict: {verdict}**", "",
              f"As of {to_iso(as_of)} — the latest recorded PAPER event. The report reads recorded data only; "
              "regenerating it from the same databases gives the same text.", "",
              NEXT_STEP[verdict], ""]
    if deciding and verdict != INSUFFICIENT_EVIDENCE:
        lines += ["Deciding version(s): " + ", ".join(f"`{m['strategy_version']}`" for m in deciding), ""]
    lines += ["This report never changes the mode, an approval or a LIVE gate (PLAN §16.5).", ""]

    lines += ["## How the verdict is decided", "",
              "Each strategy version gets one PLAN §14.3 state from its confidence matrix. The desk's verdict is "
              "the most advanced state any version reached, in this order: "
              + " › ".join(PRECEDENCE) + ". Baselines (B0, B1, B2) are comparisons and never promoted.", "",
              "| Strategy | Version | Closed trades | State | Dimensions not passing |",
              "|---|---|---:|---|---|"]
    for m in tested:
        open_dims = [f"{c['dimension']} ({c['status'].lower()})" for c in m["cells"]
                     if c["status"] not in ("PASS", "NOT_APPLICABLE")]
        lines.append(f"| {m['strategy_id']} | `{_short(m['strategy_version'])}` | {m['trades']} | {m['readiness']} | "
                     f"{', '.join(open_dims) or 'none'} |")
    if not tested:
        lines.append("| — | — | 0 | INSUFFICIENT EVIDENCE | no closed trades of any tested strategy |")
    lines.append("")

    lines += ["## Forward PAPER operation (M9-01)", "",
              "| Figure | Value |", "|---|---:|",
              f"| Book started | {meta['book_started_at']} |",
              f"| First trade | {first_trade or '—'} |",
              f"| Calendar days of PAPER data | {days:.1f} (required {int(RULES['min_days'])}) |",
              f"| Closed trades / open positions | {closed} / {still_open} |",
              f"| Candidates first observed during the book | {new_markets} |",
              f"| Market-context coverage | {gaps['coverage'] * 100:.1f}% |",
              f"| Collection gaps longer than 5 min | {gaps['gaps']} ({gaps['missing_hours']:.1f} h in total) |",
              f"| Longest gap | {gaps['longest_hours']:.1f} h from {gaps['longest_from'] or '—'} |",
              f"| Ingest errors during the book | {ingest_errors} |", ""]

    lines += ["## Cost profiles (observed, conservative, stress)", "",
              "Conservative drives the book; observed is the best case and stress the adverse case for the same "
              "trades.", "",
              "| Version | Trades | Observed mean | Conservative mean | Stress mean |", "|---|---:|---:|---:|---:|"]
    for version in sorted(profiles):
        rows = profiles[version]
        means = [sum(r[i] for r in rows) / len(rows) for i in range(3)]
        lines.append(f"| `{_short(version)}` | {len(rows)} | {_pct(means[0])} | {_pct(means[1])} | {_pct(means[2])} |")
    if not profiles:
        lines.append("| — | 0 | — | — | — |")
    lines += ["", f"Realized P&L across all closed trades: observed {_sol(int(totals[0]))}, conservative "
              f"{_sol(int(totals[1]))}, stress {_sol(int(totals[2]))}. Network fees paid: {_sol(fees_paid)}.", "",
              "Your reference account, replayed from the same fills under its own limits (an estimate): return "
              f"{_pct(float(account['return']))}, maximum drawdown {float(account['max_drawdown']) * 100:.2f}%, "
              f"{account['trades_taken']} trades taken, {sum(account['skipped'].values())} skipped by its limits"
              + (", halted by the cumulative-drawdown limit." if account.get("halted_by_cumulative_drawdown") else "."),
              ""]

    lines += ["## Confidence matrices", ""]
    for m in tested:
        lines += [f"### {m['strategy_id']} — {m['readiness']}", "", f"Version `{m['strategy_version']}`, "
                  f"{m['trades']} closed trades.", "",
                  "| Dimension | Status | Estimate | Lower | Upper | Evidence | Detail |",
                  "|---|---|---:|---:|---:|---:|---|"]
        for c in m["cells"]:
            lines.append(f"| {c['dimension']} | {c['status']} | {_num(c['estimate'])} | {_num(c['lower'])} | "
                         f"{_num(c['upper'])} | {c['evidence']} | {c['detail']} |")
        lines.append("")
    if not tested:
        lines += ["No tested strategy has a closed trade yet.", ""]

    replay = report["replay"]
    lines += ["## Data integrity", "",
              "| Check | Result |", "|---|---|",
              f"| Ledger reconciliation difference | {reconciliation['difference_lamports']} lamports |",
              f"| Recorded decisions replayed identically | {replay['identical']} of {replay['checked']} "
              f"(sample needed: {int(RULES['min_replay_sample'])}) |",
              f"| Open positions with an exit problem | {exit_problems} |",
              f"| Network-fee basis at the as-of time | {estimate.source.lower().replace('_', ' ')} "
              f"({estimate.observations} reports in 24 h) |",
              f"| Calibrated 30-day regime reference | {'yes' if calibrated else 'no'} |", ""]

    risks = _risks(days, tested, gaps, reconciliation["difference_lamports"], exit_problems, replay, estimate.source,
                   calibrated, ingest_errors)
    lines += ["## Unresolved risks and data gaps", ""] + [f"- {risk}" for risk in risks] + [""]
    lines += ["## Disclosures", ""] + [f"- {text}" for text in DISCLOSURES] + [""]
    lines += _footer(sources)
    markdown = "\n".join(lines) + "\n"
    summary = {"verdict": verdict, "next_step": NEXT_STEP[verdict], "as_of": to_iso(as_of),
               "calendar_days": round(days, 2), "closed_trades": closed,
               "strategies": {m["strategy_version"]: m["readiness"] for m in tested},
               "deciding_versions": [m["strategy_version"] for m in deciding],
               "markdown_sha256": hashlib.sha256(markdown.encode()).hexdigest()}
    return markdown, summary


def _risks(days: float, tested: list[dict[str, Any]], gaps: dict[str, Any], difference: int, exit_problems: int,
           replay: dict[str, int], fee_source: str, calibrated: bool, ingest_errors: int) -> list[str]:
    risks = []
    if days < RULES["min_days"]:
        risks.append(f"The forward period is {days:.1f} days; PLAN §14.1 needs at least {int(RULES['min_days'])}.")
    short = [m["strategy_id"] for m in tested if m["readiness"] == INSUFFICIENT_EVIDENCE]
    if short:
        risks.append(f"{len(short)} strategy version(s) lack a sufficient sample: {', '.join(short)}.")
    if gaps["gaps"]:
        risks.append(f"Collection had {gaps['gaps']} gap(s) longer than 5 minutes ({gaps['missing_hours']:.1f} h); "
                     "decisions could not be taken while data was missing.")
    if difference:
        risks.append(f"The PAPER ledger does not reconcile ({difference} lamports): treat every result as unverified.")
    if exit_problems:
        risks.append(f"{exit_problems} open position(s) are impaired or have a blocked exit.")
    if replay["checked"] < RULES["min_replay_sample"]:
        risks.append(f"Only {replay['checked']} recorded decisions were replayed; the data-integrity rule needs "
                     f"{int(RULES['min_replay_sample'])}.")
    elif replay["identical"] != replay["checked"]:
        risks.append("Some recorded decisions did not replay identically.")
    if fee_source != "MEASURED":
        risks.append("Network fees are priced by the conservative model, not recent measurements.")
    if not calibrated:
        risks.append("No calibrated 30-day market regime exists, so LIVE gate 7 cannot pass.")
    if ingest_errors:
        risks.append(f"{ingest_errors} provider responses failed validation during the book and were not used.")
    return risks or ["None detected in the recorded data. The disclosures below still apply."]


def _footer(sources: Sources) -> list[str]:
    lines = ["## Sources", "", "Every figure above comes from one of these reads of the local databases.", "",
             "| Figure | Database | Query |", "|---|---|---|"]
    for figure, database, query in sources.rows:
        lines.append(f"| {figure} | {database} | `{query.replace('|', '¦')}` |")
    return lines


def record(dbs: Databases, summary: dict[str, Any], path: str, now: datetime) -> str:
    """Audit trail: which report was produced and when (outside the report, so its bytes stay reproducible)."""
    experiment = new_id("readiness")
    dbs.research.execute("INSERT INTO experiments(id, created_at, kind, payload_json) VALUES (?, ?, 'READINESS_REPORT', ?)",
                         (experiment, to_iso(now), canonical_json({**summary, "path": path})))
    return experiment


def latest_recorded(research: sqlite3.Connection, now: datetime, max_age: timedelta | None = None) -> dict[str, Any] | None:
    """The most recent recorded report summary (for status displays); None when absent or older than ``max_age``."""
    row = research.execute("SELECT created_at, payload_json FROM experiments WHERE kind = 'READINESS_REPORT' "
                           "ORDER BY created_at DESC, id DESC LIMIT 1").fetchone()
    if row is None or (max_age is not None and now - parse_iso8601(row["created_at"]) > max_age):
        return None
    return {"recorded_at": row["created_at"], **json.loads(row["payload_json"])}


def default_path(paths: DeskPaths) -> Path:
    return paths.home / "reports" / "READINESS_REPORT.md"


def refresh_evidence(paths: DeskPaths, loaded: LoadedConfig, now: datetime) -> dict[str, Any]:
    """Record a confidence matrix and rewrite the readiness report, on connections owned by the calling thread.

    The PAPER collector runs this every few hours so evidence-gated sizing (PLAN §13.1.1) and the verdict on the
    dashboard stay current without the operator remembering `desk confidence`.
    """
    dbs = Databases.open(paths)
    try:
        confidence_report(dbs.paper_ledger, dbs.research, now, seed=SEED, resamples=RESAMPLES)
        markdown, summary = readiness_report(dbs, loaded)
        path = default_path(paths)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(markdown, encoding="utf-8")
        record(dbs, summary, str(path), now)
        return summary
    finally:
        dbs.close()

