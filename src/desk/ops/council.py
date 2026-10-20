"""Record-only evidence council runner and report (research/council). Zero network requests; research.db writes only.

The runner reads primary decisions in the order they were recorded (SHADOW in market.db, PAPER and LIVE as ledger
DECISION events), and stores each decision's two advisories with the decision's hash. It runs off the event loop
inside ``desk paper`` every 15 minutes and on demand with ``desk council run``. It never writes to a ledger, never
calls a provider and is imported by no process that decides, sizes, signs or exits; a failure is logged and the
trading paths carry on unchanged.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections import Counter
from datetime import datetime, timedelta
from typing import Any

from desk.contracts.strategy import DecisionContext
from desk.core.clock import parse_iso8601, to_iso, utc_now
from desk.core.config import LoadedConfig
from desk.core.hashing import canonical_json, fingerprint
from desk.core.paths import DeskPaths
from desk.council.benchmark import reconstruct_from_raw
from desk.council.evaluate import Advisor, PrimaryDecision
from desk.council.specs import BRC, COMMON, ECDR, PROFILE, SPECS, spec_sha256
from desk.storage.council_repo import CouncilRepository
from desk.storage.databases import Databases
from desk.storage.raw_store import RawStore

log = logging.getLogger(__name__)
COUNCIL_INTERVAL_SECONDS = 900
MAX_DECISIONS_PER_RUN = 300
REBUILD_KIND = "COUNCIL_BENCHMARK_REBUILD"
ENTRY_FLOOR = 300                     # closed primary trades before any comparison is more than descriptive


def _read_only(path: Any) -> sqlite3.Connection | None:
    if not path.is_file():
        return None
    conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def pending_decisions(dbs: Databases, live: sqlite3.Connection | None, repo: CouncilRepository,
                      limit: int) -> list[PrimaryDecision]:
    """The next recorded primary decisions after each source's cursor, merged in decision-time order."""
    found: list[PrimaryDecision] = []
    for row in dbs.market.execute(
            "SELECT rowid AS row_id, candidate_id, as_of, decision_hash, context_json, result_json FROM shadow_decisions"
            " WHERE rowid > ? ORDER BY rowid LIMIT ?", (repo.cursor("SHADOW"), limit)):
        found.append(PrimaryDecision("SHADOW", int(row["row_id"]), row["decision_hash"],
                                     json.loads(row["result_json"])["chosen"], parse_iso8601(row["as_of"]),
                                     row["candidate_id"], DecisionContext.model_validate_json(row["context_json"])))
    for source, conn in (("PAPER", dbs.paper_ledger), ("LIVE", live)):
        if conn is None:
            continue
        for row in conn.execute("SELECT id, at, candidate_id, payload_json FROM paper_events WHERE kind = 'DECISION'"
                                " AND id > ? ORDER BY id LIMIT ?", (repo.cursor(source), limit)):
            payload = json.loads(row["payload_json"])
            found.append(PrimaryDecision(source, int(row["id"]), payload["decision_hash"], payload["chosen"],
                                         parse_iso8601(row["at"]), row["candidate_id"],
                                         DecisionContext.model_validate(payload["context"])))
    found.sort(key=lambda d: (d.decided_at, d.source, d.row_id))
    return found[:limit]


def rebuild_benchmark(dbs: Databases, loaded: LoadedConfig, now: datetime) -> dict[str, Any]:
    """Once per database: recover SOL/USD minutes from retained raw payloads with their original receipt times."""
    report = reconstruct_from_raw(dbs.market, RawStore(dbs.paths.raw_dir, dbs.market),
                                  loaded.settings.context.sol_reference_pool)
    dbs.research.execute("INSERT OR IGNORE INTO experiments(id, created_at, kind, payload_json) VALUES (?, ?, ?, ?)",
                         ("council_rebuild_" + fingerprint({"at": to_iso(now)})[:24], to_iso(now), REBUILD_KIND,
                          canonical_json(report)))
    return report


def run_council(paths: DeskPaths, loaded: LoadedConfig, now: datetime | None = None, *,
                limit: int = MAX_DECISIONS_PER_RUN) -> dict[str, Any]:
    """Advise the next recorded decisions. Opens its own connections, so it is safe in a worker thread."""
    now = now or utc_now()
    dbs = Databases.open(paths)
    live = _read_only(paths.db_dir / "ledger-live.db")
    stats: dict[str, Any] = {"decisions": 0, "advisories": 0, "unreadable": 0, "stances": Counter()}
    try:
        repo = CouncilRepository(dbs.research)
        repo.register(now)
        if dbs.research.execute("SELECT 1 FROM experiments WHERE kind = ?", (REBUILD_KIND,)).fetchone() is None:
            stats["benchmark_rebuild"] = rebuild_benchmark(dbs, loaded, now)

        def member(day: str, mint: str, pool: str, decision: PrimaryDecision) -> bool:
            return repo.admit(PROFILE, day, mint, pool, decision.decided_at, decision.decision_hash,
                              int(COMMON["universe_max"]))

        advisor = Advisor(dbs.market, loaded, benchmark_pool=loaded.settings.context.sol_reference_pool,
                          member=member, now=now)
        for decision in pending_decisions(dbs, live, repo, limit):
            stats["decisions"] += 1
            try:
                advisories = advisor.advise(decision)
            except (ValueError, KeyError, TypeError, ArithmeticError) as exc:
                # an unreadable record is counted and passed, never retried forever or guessed around
                stats["unreadable"] += 1
                log.warning("council could not advise a recorded decision", extra={
                    "source": decision.source, "row_id": decision.row_id, "error": f"{type(exc).__name__}: {exc}"[:200]})
                repo.advance(decision.source, decision.row_id, now)
                continue
            stats["advisories"] += repo.save(decision.source, advisories, row_id=decision.row_id, at=now)
            stats["stances"].update(f"{a.advisor_id}:{a.stance}" for a in advisories)
    finally:
        if live is not None:
            live.close()
        dbs.close()
    stats["stances"] = dict(stats["stances"])
    return stats


# ── report ──────────────────────────────────────────────────────────────────────────────────────────────
def _verdict(closed: int, removed: int) -> str:
    if closed == 0:
        return "BLOCKED: PRIMARY BASELINE DEGENERATE (no closed primary PAPER trade has an advisory yet)"
    if removed == 0:
        return "NO FILTER ACTIVITY: no advised primary entry had a valid OPPOSE, so every arm equals the primary"
    if closed < ENTRY_FLOOR:
        return f"INSUFFICIENT EVIDENCE: {closed} of {ENTRY_FLOOR} closed primary trades; descriptive only"
    return "DESCRIPTIVE ONLY: the confirmatory test is the registered validation plan, not this table"


def council_report(dbs: Databases, now: datetime | None = None) -> dict[str, Any]:
    """Coverage, stances, reasons, disagreements and the opportunity-matched filter diagnostic, from records only."""
    now = now or utc_now()
    research = dbs.research
    since = to_iso(now - timedelta(hours=24)).replace("+00:00", "Z")
    stances: dict[str, dict[str, Any]] = {}
    for advisor in SPECS:
        all_time = dict(research.execute("SELECT stance, COUNT(*) FROM council_advisories WHERE advisor_id = ?"
                                         " GROUP BY stance", (advisor,)).fetchall())
        recent = dict(research.execute("SELECT stance, COUNT(*) FROM council_advisories WHERE advisor_id = ? AND"
                                       " decision_at >= ? GROUP BY stance", (advisor, since)).fetchall())
        reasons: Counter[str] = Counter()
        for row in research.execute("SELECT reasons FROM council_advisories WHERE advisor_id = ? ORDER BY rowid DESC"
                                    " LIMIT 5000", (advisor,)):
            reasons.update(code.split(":", 1)[0] for code in row["reasons"].split(","))
        stances[advisor] = {"spec_sha256": spec_sha256(SPECS[advisor]), "all_time": all_time, "last_24h": recent,
                            "top_reasons": dict(reasons.most_common(8))}
    decisions = research.execute("SELECT COUNT(DISTINCT primary_decision_hash) FROM council_advisories").fetchone()[0]
    eligible = research.execute(
        "SELECT COUNT(DISTINCT primary_decision_hash) FROM council_advisories WHERE payload_json NOT LIKE"
        " '%\"membership_id\":\"not-enrolled\"%'").fetchone()[0]
    warmed = research.execute("SELECT COUNT(*) FROM council_advisories WHERE advisor_id = 'C1_BRC' AND"
                              " payload_json LIKE '%\"residual_z\"%'").fetchone()[0]
    conflicts = research.execute(
        "SELECT COUNT(*) FROM council_advisories a JOIN council_advisories b ON a.primary_decision_hash ="
        " b.primary_decision_hash AND a.source = b.source AND a.advisor_id = 'C1_BRC' AND b.advisor_id = 'C2_ECDR'"
        " WHERE (a.stance = 'SUPPORT' AND b.stance = 'OPPOSE') OR (a.stance = 'OPPOSE' AND b.stance = 'SUPPORT')"
    ).fetchone()[0]
    support_vs_no_trade = research.execute(
        "SELECT COUNT(*) FROM council_advisories WHERE stance = 'SUPPORT' AND primary_action = 'NO_TRADE'").fetchone()[0]
    members = research.execute("SELECT COUNT(*), COUNT(DISTINCT day) FROM council_members").fetchone()
    benchmark = dbs.market.execute("SELECT COUNT(*), MIN(bar_start), MAX(bar_start) FROM benchmark_bars").fetchone()

    # opportunity-matched diagnostic on closed primary PAPER entries (removed trades become cash; nothing reused)
    oppose: dict[str, set[str]] = {}
    for row in research.execute("SELECT primary_decision_hash, advisor_id FROM council_advisories WHERE source ="
                                " 'PAPER' AND stance = 'OPPOSE'"):
        oppose.setdefault(row["primary_decision_hash"], set()).add(row["advisor_id"])
    advised = {row[0] for row in research.execute(
        "SELECT DISTINCT primary_decision_hash FROM council_advisories WHERE source = 'PAPER'")}
    closed = [(row["decision_hash"], int(row["realized_pnl_lamports"])) for row in dbs.paper_ledger.execute(
        "SELECT decision_hash, realized_pnl_lamports FROM positions WHERE state = 'CLOSED'")
        if row["decision_hash"] in advised]
    arms = {"P": set(), "P+A": {"C1_BRC"}, "P+B": {"C2_ECDR"}, "P+A+B": {"C1_BRC", "C2_ECDR"}}
    table = {}
    for arm, filters in arms.items():
        kept = [pnl for digest, pnl in closed if not (oppose.get(digest, set()) & filters)]
        removed = [pnl for digest, pnl in closed if oppose.get(digest, set()) & filters]
        table[arm] = {"trades": len(kept), "realized_pnl_lamports": sum(kept), "removed": len(removed),
                      "removed_winners": sum(1 for p in removed if p > 0),
                      "removed_losers": sum(1 for p in removed if p <= 0)}
    return {
        "generated_at": now.isoformat(), "mode": "SHADOW", "influence": "NONE", "profile": PROFILE,
        "specs": {"C1_BRC": spec_sha256(BRC), "C2_ECDR": spec_sha256(ECDR)},
        "coverage": {"decisions_advised": decisions, "decisions_in_universe": eligible,
                     "c1_with_full_history": warmed, "cohort_rows": members[0], "cohort_days": members[1],
                     "benchmark_minutes": benchmark[0], "benchmark_first": benchmark[1], "benchmark_last": benchmark[2]},
        "stances": stances,
        "disagreements": {"c1_vs_c2_support_oppose": conflicts, "support_while_primary_no_trade": support_vs_no_trade},
        "filter_diagnostic": {"closed_primary_trades_with_advisories": len(closed), "arms": table,
                              "verdict": _verdict(len(closed), table["P+A+B"]["removed"]),
                              "note": "OPPOSE removes an entry; its capital stays cash. Not a performance claim."},
    }


__all__ = ["COUNCIL_INTERVAL_SECONDS", "MAX_DECISIONS_PER_RUN", "council_report", "pending_decisions",
           "rebuild_benchmark", "run_council"]
