"""Production per-intent LIVE BUY authorization (PLAN §16.5; ACCEPTANCE M8-06).

The signer calls this synchronously immediately before each check that may lead to a signature, so no await can
separate the final authorization from signing. Every call opens fresh read-only snapshots of the local databases
and re-establishes all seven gates for this exact intent:

1. mode — config ``desk.mode = LIVE`` (loaded when the signer was armed) and ``DESK_LIVE_CONFIRM=yes`` in the
   signer's own environment;
2. approval — a typed ``desk live approve`` record for the intent's exact strategy version;
3. readiness — the latest confidence report, at most seven days old, says ELIGIBLE FOR MANUAL LIVE REVIEW for
   that version, or a recorded operator override exists;
4. capital — ``0 < live.max_capital_sol <= ceiling``;
5. signer unlocked — true by construction: only an armed signer process calls this (a disarmed one refuses first);
6. preflight — no STOP file or LIVE halt, the LIVE guardian heartbeat is fresh, market data is fresh, the intent's
   quote was recorded seconds ago, the token passed the security assessment within its maximum age, the LIVE book
   holds fewer than the allowed positions and none in this token (wallet balance above reserve and fees is checked
   by the signer itself right after authorization);
7. regime — a calibrated regime (measured 30-day reference, status OK) whose label the strategy accepts.

Beyond the gates, the approved strategy must still choose this candidate when its decision is re-evaluated on the
current data. Any failure to read evidence is a refusal, never a pass.
"""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

from desk.contracts.discovery import Outcome
from desk.contracts.observed import DataStatus
from desk.contracts.strategy import DecisionContext, StrategyKind, StrategySpec
from desk.core.clock import parse_iso8601, to_epoch_millis
from desk.core.config import LoadedConfig, Mode
from desk.core.paths import DeskPaths
from desk.live.gates import ELIGIBLE, LIVE_GUARDIAN, approved, readiness_override
from desk.ops.shadow import decision_context
from desk.security.policy import SOL_MINT
from desk.signer.intents import Intent
from desk.strategies import evaluate_decision
from desk.strategies.specs import paper_specs

READINESS_MAX_AGE = timedelta(days=7)
QUOTE_MAX_AGE_SECONDS = 30
ContextProvider = Callable[[sqlite3.Connection, str, datetime], DecisionContext]


def _read_only(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise FileNotFoundError(path.name)
    conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, isolation_level=None, timeout=2)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=2000")
    conn.execute("BEGIN")                           # one consistent snapshot for the whole authorization
    return conn


def _decision_context(market: sqlite3.Connection, candidate_id: str, now: datetime) -> DecisionContext:
    return decision_context(market, candidate_id, now)


@dataclass
class LiveAuthorizer:
    paths: DeskPaths
    loaded: LoadedConfig
    capital_ceiling_sol: Decimal
    env: Mapping[str, str] = field(default_factory=lambda: os.environ)
    context: ContextProvider = _decision_context

    @contextmanager
    def _snapshots(self) -> Iterator[dict[str, sqlite3.Connection]]:
        opened: dict[str, sqlite3.Connection] = {}
        try:
            for name in ("market", "research", "runtime", "ledger-live"):
                opened[name] = _read_only(self.paths.db_dir / f"{name}.db")
            yield opened
        finally:
            for conn in opened.values():
                conn.close()

    def __call__(self, intent: Intent, now: datetime) -> list[str]:
        if intent.side != "BUY" or intent.strategy_version is None or intent.candidate_id is None:
            return ["NOT_A_BUY_DECISION"]
        try:
            with self._snapshots() as db:
                return self._evaluate(intent, intent.strategy_version, intent.candidate_id, now, db)
        except FileNotFoundError as exc:
            return [f"EVIDENCE_UNAVAILABLE:{exc.args[0] if exc.args else 'database'}"]
        except (sqlite3.Error, ValueError, KeyError, TypeError) as exc:
            return [f"EVIDENCE_UNREADABLE:{type(exc).__name__}"]

    def _evaluate(self, intent: Intent, version: str, candidate: str, now: datetime,
                  db: dict[str, sqlite3.Connection]) -> list[str]:
        problems: list[str] = []
        settings = self.loaded.settings
        # 1. mode
        if settings.desk.mode is not Mode.LIVE or self.env.get("DESK_LIVE_CONFIRM") != "yes":
            problems.append("GATE_1_MODE")
        # 2. approval
        if not approved(db["research"], version):
            problems.append("GATE_2_NOT_APPROVED")
        # 3. readiness
        if not (self._eligible(db["research"], version, now) or readiness_override(db["research"], version)):
            problems.append("GATE_3_READINESS")
        # 4. capital
        if not Decimal(0) < settings.live.max_capital_sol <= self.capital_ceiling_sol:
            problems.append("GATE_4_CAPITAL")
        # 5. signer unlocked: this code runs only inside an armed signer
        # 6. preflight
        problems += self._preflight(intent, now, db)
        # 7. calibrated regime, and the approved strategy still chooses this candidate on current data
        spec = self._spec(db["market"], version)
        if spec is None:
            problems.append("STRATEGY_VERSION_UNKNOWN")
            return problems
        context = self.context(db["market"], candidate, now)
        if context.features.mint != intent.mint:
            problems.append("CANDIDATE_MINT_DIFFERS")
        if context.regime_status is not DataStatus.OK or context.regime_label is None:
            problems.append("GATE_7_REGIME_UNCALIBRATED")
        elif context.regime_label not in spec.eligibility.regimes:
            problems.append("GATE_7_REGIME_NOT_ELIGIBLE")
        if context.security_outcome is not Outcome.PASS or context.security_assessed_at is None \
                or not 0 <= (now - context.security_assessed_at).total_seconds() <= context.security_max_age_seconds:
            problems.append("GATE_6_SECURITY")
        # the same overlays as every PAPER/LIVE decision (H7 can only remove eligibility)
        overlays = tuple(s for s in paper_specs() if s.kind is StrategyKind.OVERLAY)
        if evaluate_decision(candidate, context, (spec, *overlays)).chosen != spec.strategy_id:
            problems.append("DECISION_NO_LONGER_HOLDS")
        return problems

    @staticmethod
    def _eligible(research: sqlite3.Connection, version: str, now: datetime) -> bool:
        row = research.execute("SELECT created_at, payload_json FROM experiments WHERE kind = 'CONFIDENCE_MATRIX' "
                               "ORDER BY created_at DESC, id DESC LIMIT 1").fetchone()
        if row is None or now - parse_iso8601(row["created_at"]) > READINESS_MAX_AGE:
            return False
        return any(m.get("strategy_version") == version and m.get("readiness") == ELIGIBLE
                   for m in json.loads(row["payload_json"]).get("matrices", []))

    def _preflight(self, intent: Intent, now: datetime, db: dict[str, sqlite3.Connection]) -> list[str]:
        settings, problems = self.loaded.settings, []
        if self.paths.stop_file.exists():
            problems.append("GATE_6_STOP")
        halt = db["ledger-live"].execute("SELECT value FROM system_state WHERE key = 'entries_halted'").fetchone()
        if halt is not None and halt["value"] != "no":
            problems.append(f"GATE_6_HALTED:{halt['value']}")
        beat = db["runtime"].execute("SELECT at FROM heartbeats WHERE process = ?", (LIVE_GUARDIAN,)).fetchone()
        age = None if beat is None else (now - parse_iso8601(beat["at"])).total_seconds()
        if age is None or not 0 <= age <= settings.paper.guardian_heartbeat_max_age_seconds:
            problems.append("GATE_6_GUARDIAN")
        context = db["market"].execute("SELECT MAX(observed_at_ms) FROM market_context").fetchone()[0]
        if context is None or not 0 <= to_epoch_millis(now) - context <= settings.limits.stale_data_halt_seconds * 1000:
            problems.append("GATE_6_STALE_DATA")
        quote = db["market"].execute(
            "SELECT received_at_ms FROM quotes WHERE raw_sha256 = ? AND input_mint = ? AND output_mint = ? "
            "AND outcome = 'quote' ORDER BY received_at_ms DESC LIMIT 1",
            (intent.quote_ref, SOL_MINT, intent.mint)).fetchone()
        if quote is None or not 0 <= to_epoch_millis(now) - quote["received_at_ms"] <= QUOTE_MAX_AGE_SECONDS * 1000:
            problems.append("GATE_6_QUOTE_STALE")
        open_rows = db["ledger-live"].execute("SELECT mint FROM positions WHERE state != 'CLOSED'").fetchall()
        if len(open_rows) >= settings.limits.max_open_positions:
            problems.append("GATE_6_POSITION_LIMIT")
        if any(row["mint"] == intent.mint for row in open_rows):
            problems.append("GATE_6_MINT_ALREADY_OPEN")
        return problems

    @staticmethod
    def _spec(market: sqlite3.Connection, version: str) -> StrategySpec | None:
        row = market.execute("SELECT content_json FROM strategy_versions WHERE spec_hash = ?",
                             (version.rsplit(":", 1)[-1],)).fetchone()
        if row is None:
            return None
        spec = StrategySpec.model_validate_json(row["content_json"])
        return spec if spec.version_id == version else None
