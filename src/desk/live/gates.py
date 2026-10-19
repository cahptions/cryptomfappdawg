"""PLAN §16.5 LIVE gates. Every gate must hold; each is evaluated and shown separately. Nothing here arms LIVE."""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

ELIGIBLE = "ELIGIBLE FOR MANUAL LIVE REVIEW"


@dataclass(frozen=True)
class LiveContext:
    config_mode: str
    env_confirm: str | None
    strategy_version: str
    approved: bool
    readiness: str | None
    readiness_override: bool
    max_capital_sol: Decimal
    capital_ceiling_sol: Decimal
    signer_unlocked: bool
    fee_balance_ok: bool
    quote_fresh: bool
    token_program_ok: bool
    guardian_fresh: bool
    halted: str | None
    regime_status_ok: bool
    regime_eligible: bool


@dataclass(frozen=True)
class Gate:
    number: int
    name: str
    ok: bool
    detail: str


def evaluate_gates(ctx: LiveContext) -> list[Gate]:
    return [
        Gate(1, "mode", ctx.config_mode == "LIVE" and ctx.env_confirm == "yes",
             "config desk.mode = LIVE and environment DESK_LIVE_CONFIRM=yes"),
        Gate(2, "strategy approval", ctx.approved, f"typed `desk live approve {ctx.strategy_version}` recorded"),
        Gate(3, "readiness", ctx.readiness == ELIGIBLE or ctx.readiness_override,
             f"latest readiness: {ctx.readiness or 'none'}"
             + (" (recorded operator override)" if ctx.readiness_override else "")),
        Gate(4, "capital allocation", Decimal(0) < ctx.max_capital_sol <= ctx.capital_ceiling_sol,
             f"live.max_capital_sol = {ctx.max_capital_sol} (ceiling {ctx.capital_ceiling_sol})"),
        Gate(5, "signer unlocked", ctx.signer_unlocked, "passphrase entered at `desk live arm`"),
        Gate(6, "preflight", ctx.fee_balance_ok and ctx.quote_fresh and ctx.token_program_ok and ctx.guardian_fresh
             and not ctx.halted, "fee balance, fresh quote, token program, guardian heartbeat, no halt"
             + (f" (halted: {ctx.halted})" if ctx.halted else "")),
        Gate(7, "calibrated regime", ctx.regime_status_ok and ctx.regime_eligible,
             "measured 30-day reference, status OK, label eligible for the strategy"),
    ]


def live_allowed(gates: list[Gate]) -> bool:
    return len(gates) == 7 and all(gate.ok for gate in gates)


def approved(research: sqlite3.Connection, version: str) -> bool:
    return research.execute("SELECT 1 FROM approvals WHERE kind = 'LIVE_STRATEGY' AND subject = ?", (version,)).fetchone() \
        is not None


def readiness_override(research: sqlite3.Connection, version: str) -> bool:
    return research.execute("SELECT 1 FROM approvals WHERE kind = 'READINESS_OVERRIDE' AND subject = ?",
                            (version,)).fetchone() is not None


def latest_readiness(research: sqlite3.Connection, version: str) -> str | None:
    row = research.execute("SELECT payload_json FROM experiments WHERE kind = 'CONFIDENCE_MATRIX' "
                           "ORDER BY created_at DESC, id DESC LIMIT 1").fetchone()
    if row is None:
        return None
    for matrix in json.loads(row["payload_json"])["matrices"]:
        if matrix["strategy_version"] == version:
            return str(matrix["readiness"])
    return None


def status_context(research: sqlite3.Connection, loaded: Any, version: str, *, signer_unlocked: bool,
                   ceiling: Decimal, halted: str | None, guardian_fresh: bool) -> LiveContext:
    """The context `desk live status` can establish without a pending entry: per-entry preflight shows as not met."""
    return LiveContext(
        config_mode=str(loaded.settings.desk.mode), env_confirm=os.environ.get("DESK_LIVE_CONFIRM"),
        strategy_version=version, approved=approved(research, version), readiness=latest_readiness(research, version),
        readiness_override=readiness_override(research, version), max_capital_sol=loaded.settings.live.max_capital_sol,
        capital_ceiling_sol=ceiling, signer_unlocked=signer_unlocked, fee_balance_ok=False, quote_fresh=False,
        token_program_ok=False, guardian_fresh=guardian_fresh, halted=halted, regime_status_ok=False,
        regime_eligible=False)
