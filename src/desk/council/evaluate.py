"""Advisories for one recorded primary decision: deterministic, causal, no provider request, no trading import.

Only evidence that had ended and been received by the decision time is used. Common gates come first (security
PASS, first observed >= 96 h, verified liquidity >= $100,000, pool identity, a place in the day's 20-member
cohort); a failed gate makes both advisors ABSTAIN with its reason. Then each advisor needs its own consecutive
history of 15-minute bars common to the token and SOL/USD. Stances follow research/council/STRATEGIES.md;
missing evidence is ABSTAIN, never a negative signal.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from desk.contracts.advisory import Advisory, Economics, InputRef, zulu
from desk.contracts.discovery import Outcome
from desk.contracts.strategy import DecisionContext
from desk.core.clock import from_epoch_millis, parse_iso8601, to_epoch_millis
from desk.core.config import LoadedConfig
from desk.core.hashing import fingerprint
from desk.council.brc import brc
from desk.council.ecdr import ecdr, required_closes
from desk.council.panel import BAR_MS, Bar, Panel, load_panel
from desk.council.specs import BRC, CODE_SHA256, COMMON, ECDR, PROFILE, decimal, spec_sha256
from desk.paper import fees as paper_fees
from desk.security.policy import SOL_MINT

TRANSFORMS = "council-transforms-v1"     # 15m aggregation, alignment, log returns, cost formula
SHARED = ("token_usd_ohlcv", "sol_usd_benchmark", "security_assessment", "jupiter_exact_ticket_quotes")


@dataclass(frozen=True)
class PrimaryDecision:
    source: str                 # SHADOW, PAPER or LIVE
    row_id: int
    decision_hash: str
    chosen: str
    decided_at: datetime
    candidate_id: str
    context: DecisionContext

    @property
    def action(self) -> str:
        return "NO_TRADE" if self.chosen == "NO_TRADE" else "LONG_CANDIDATE"


@dataclass(frozen=True)
class Costs:
    economics: Economics
    refs: tuple[InputRef, ...]
    manifest: dict[str, Any]
    reason: str | None          # why it is unmeasured
    expires_at: datetime | None  # when the measurement stops being fresh


def _ms(value: datetime) -> int:
    return to_epoch_millis(value)


def _num(value: Decimal | None) -> str | None:
    return None if value is None else format(value.quantize(Decimal("1E-12")).normalize(), "f")


def exact_ticket_costs(market: sqlite3.Connection, loaded: LoadedConfig, decision: PrimaryDecision) -> Costs:
    """The decision's exact-ticket round trip from the security assessment's linked quotes, if fresh at the decision."""
    d = decision.decided_at
    row = market.execute("SELECT id, assessed_at, payload_json FROM security_assessments WHERE candidate_id = ?"
                         " AND assessed_at <= ? ORDER BY assessed_at DESC, id DESC LIMIT 1",
                         (decision.candidate_id, d.isoformat(timespec="microseconds"))).fetchone()
    unmeasured = Economics(status="UNMEASURED", ticket_raw=None, roundtrip_cost_fraction=None)
    if row is None:
        return Costs(unmeasured, (), {"assessment": None}, "INPUT_MISSING", None)
    payload = json.loads(row["payload_json"])
    buy, sell = payload.get("buy_quote"), payload.get("sell_quote")
    assessed = parse_iso8601(row["assessed_at"])
    refs = [InputRef(store="market.security_assessments", row_id=str(row["id"]), raw_sha256=None,
                     event_end=zulu(assessed), first_available_at=zulu(assessed))]
    if not buy or not sell or "out_amount_raw" not in buy or "out_amount_raw" not in sell:
        return Costs(unmeasured, tuple(refs), {"assessment": row["id"], "quotes": None}, "INPUT_MISSING", None)
    got_buy, got_sell = parse_iso8601(buy["received_at"]), parse_iso8601(sell["received_at"])
    if max(got_buy, got_sell) > d:                       # received after the decision: not part of its information
        return Costs(unmeasured, tuple(refs), {"assessment": row["id"], "quotes": "after decision"},
                     "INPUT_AFTER_DECISION", None)
    for quote, received in ((buy, got_buy), (sell, got_sell)):
        refs.append(InputRef(store="market.quotes", row_id=f"{quote['input_mint']}>{quote['output_mint']}",
                             raw_sha256=quote.get("raw_sha256"), event_end=zulu(received),
                             first_available_at=zulu(received)))
    manifest = {"assessment": row["id"], "buy": [buy["in_amount_raw"], buy["out_amount_raw"], buy["received_at"]],
                "sell": [sell["in_amount_raw"], sell["out_amount_raw"], sell["received_at"]]}
    identity_ok = (buy["input_mint"] == SOL_MINT and buy["output_mint"] == decision.context.features.mint
                   and sell["input_mint"] == decision.context.features.mint and sell["output_mint"] == SOL_MINT
                   and int(sell["in_amount_raw"]) == int(buy["out_amount_raw"]) and int(buy["in_amount_raw"]) > 0
                   and int(buy["out_amount_raw"]) > 0)
    if not identity_ok:
        return Costs(unmeasured, tuple(refs), manifest, "IDENTITY_CONFLICT", None)
    common = COMMON
    oldest, newest = min(got_buy, got_sell), max(got_buy, got_sell)
    if (d - oldest).total_seconds() > common["final_quote_max_age_seconds"] \
            or (newest - oldest).total_seconds() > common["quote_pair_max_receipt_separation_seconds"]:
        return Costs(unmeasured, tuple(refs), manifest, "INPUT_STALE", None)
    estimate = paper_fees.estimate(market, loaded, d)
    q, back = int(buy["in_amount_raw"]), int(sell["out_amount_raw"])
    cost = Decimal(q - back + estimate.round_trip) / Decimal(q)
    manifest["network_fees_lamports"] = estimate.round_trip
    if cost < 0:
        return Costs(unmeasured, tuple(refs), manifest, "ECONOMICS_INCOHERENT", None)
    ceiling = min(decimal(common, "cost_ceiling_fraction"), loaded.settings.discovery.max_roundtrip_cost_fraction)
    economics = Economics(status="CURRENT_COST_QUALIFIED_ONLY" if cost <= ceiling else "FAILED_CURRENT_COST",
                          ticket_raw=str(q), roundtrip_cost_fraction=_num(cost))
    return Costs(economics, tuple(refs), manifest, None,
                 oldest + timedelta(seconds=common["final_quote_max_age_seconds"]))


def _first_observed(market: sqlite3.Connection, mint: str, d: datetime) -> tuple[datetime | None, str | None]:
    row = market.execute("SELECT id, first_observed_at FROM discoveries WHERE mint = ? AND first_observed_at <= ?"
                         " ORDER BY first_observed_at, id LIMIT 1", (mint, d.isoformat(timespec="microseconds"))
                         ).fetchone()
    return (None, None) if row is None else (parse_iso8601(row["first_observed_at"]), str(row["id"]))


def _range_ref(store: str, pool: str, bars: tuple[Bar, ...]) -> InputRef:
    return InputRef(store=store, row_id=f"{pool}:rows {min(b.first_row for b in bars)}-{max(b.last_row for b in bars)}"
                                         f":{len(bars)} bars", raw_sha256=None,
                    event_end=zulu(from_epoch_millis(bars[-1].end_ms)),
                    first_available_at=zulu(from_epoch_millis(max(b.available_ms for b in bars))))


class Advisor:
    """Builds the advisories of one primary decision. Market reads only; the caller stores the records."""

    def __init__(self, market: sqlite3.Connection, loaded: LoadedConfig, *, benchmark_pool: str,
                 member: Any, now: datetime) -> None:
        self.market, self.loaded, self.benchmark_pool = market, loaded, benchmark_pool
        self.member = member        # callable(day, mint, pool, decision) -> bool, the day's cohort
        self.now = now

    def advise(self, decision: PrimaryDecision) -> list[Advisory]:
        d = decision.decided_at
        context, features = decision.context, decision.context.features
        mint, pool = features.mint, features.pool_address
        common_reasons: list[str] = []
        refs: list[InputRef] = []
        security_ok = (context.security_outcome is Outcome.PASS and context.security_assessed_at is not None
                       and 0 <= (d - context.security_assessed_at).total_seconds() <= context.security_max_age_seconds)
        if not security_ok:
            common_reasons.append("PRIMARY_SECURITY_BLOCK")
        liquidity = features.usable("verified_liquidity_usd")
        if liquidity is None:
            common_reasons.append("INPUT_MISSING:verified_liquidity_usd")
        elif liquidity < decimal(COMMON, "verified_liquidity_min_usd"):
            common_reasons.append("UNIVERSE_LIQUIDITY")
        first_seen, discovery_row = _first_observed(self.market, mint, d)
        if first_seen is None or d - first_seen < timedelta(hours=COMMON["first_observed_min_hours"]):
            common_reasons.append("UNIVERSE_AGE")
        if first_seen is not None:
            refs.append(InputRef(store="market.discoveries", row_id=str(discovery_row), raw_sha256=None,
                                 event_end=zulu(first_seen), first_available_at=zulu(first_seen)))
        known = self.market.execute("SELECT base_mint FROM pools WHERE address = ?", (pool,)).fetchone()
        if known is None or known["base_mint"] != mint:
            common_reasons.append("IDENTITY_CONFLICT")
        day = d.astimezone(UTC).date().isoformat()
        membership = "not-enrolled"
        if not common_reasons:
            if self.member(day, mint, pool, decision):
                membership = f"{PROFILE}:{day}"
            else:
                common_reasons.append("UNIVERSE_FULL")
        base_manifest = {"decision": decision.decision_hash, "source": decision.source, "membership": membership,
                         "transforms": TRANSFORMS, "security_ok": security_ok, "liquidity_usd": _num(liquidity),
                         "first_observed": None if first_seen is None else first_seen.isoformat(), "pool_known": bool(known),
                         "common": common_reasons}
        if common_reasons:
            return [self._record(spec, decision, membership, "ABSTAIN", "UNKNOWN", tuple(common_reasons), {}, refs,
                                 base_manifest, Panel((), (), None), None, None, 0, 0)
                    for spec in (BRC, ECDR)]
        panel = load_panel(self.market, pool=pool, mint=mint, benchmark_pool=self.benchmark_pool,
                           decision_ms=_ms(d), count=int(BRC["required_closed_prices"]))
        costs = exact_ticket_costs(self.market, self.loaded, decision)
        return [self._brc(decision, membership, panel, costs, refs, base_manifest),
                self._ecdr(decision, membership, panel, refs, base_manifest)]

    # ── C1 ──────────────────────────────────────────────────────────────────────────────────────────
    def _brc(self, decision: PrimaryDecision, membership: str, panel: Panel, costs: Costs, refs: list[InputRef],
             manifest: dict[str, Any]) -> Advisory:
        needed = int(BRC["required_closed_prices"])
        reasons = self._panel_reasons(decision, panel, needed)
        if reasons:
            return self._record(BRC, decision, membership, "ABSTAIN", "UNKNOWN", reasons, {}, refs, manifest, panel,
                                None, None, 0, 0, needed)
        token, bench = panel.last(needed)
        result = brc([b.close for b in token], [b.close for b in bench], BRC)
        signal = {"residual_z": _num(result.z), "beta": _num(result.beta), "alpha": _num(result.alpha),
                  "residual_scale": _num(result.scale)}
        all_refs = [*refs, *costs.refs]
        manifest = {**manifest, "costs": costs.manifest}
        trained = (int(BRC["train_returns"]), int(BRC["signal_returns"]))
        if not result.valid:
            return self._record(BRC, decision, membership, "ABSTAIN", "UNKNOWN", result.reasons, signal, all_refs,
                                manifest, panel, costs, None, *trained, needed)
        assert result.z is not None and result.skipped_relative_return is not None
        if result.z <= decimal(BRC, "oppose_score_max"):
            stance, direction, why = "OPPOSE", "AGAINST_LONG", ("NEGATIVE_BENCHMARK_RESIDUAL",)
        elif result.z < decimal(BRC, "support_score_min"):
            stance, direction, why = "CAUTION", "NEUTRAL", ("NO_RESIDUAL_CONTINUATION",)
        elif result.skipped_relative_return < 0:
            stance, direction, why = "CAUTION", "LONG", ("RECENT_REVERSAL",)
        elif costs.economics.status == "CURRENT_COST_QUALIFIED_ONLY":
            stance, direction, why = "SUPPORT", "LONG", ("SUPPORT_SETUP",)
        elif costs.economics.status == "FAILED_CURRENT_COST":
            stance, direction, why = "OPPOSE", "LONG", ("ROUNDTRIP_COST_ABOVE_CEILING",)
        else:                                                # a setup without measured current costs
            stance, direction, why = "ABSTAIN", "LONG", (f"ECONOMICS_{costs.reason or 'UNMEASURED'}",)
        return self._record(BRC, decision, membership, stance, direction, why, signal, all_refs, manifest, panel,
                            costs, costs.expires_at if stance == "SUPPORT" else None, *trained, needed)

    # ── C2 ──────────────────────────────────────────────────────────────────────────────────────────
    def _ecdr(self, decision: PrimaryDecision, membership: str, panel: Panel, refs: list[InputRef],
              manifest: dict[str, Any]) -> Advisory:
        needed = required_closes(ECDR)
        reasons = self._panel_reasons(decision, panel, needed)
        if reasons:
            return self._record(ECDR, decision, membership, "ABSTAIN", "UNKNOWN", reasons, {}, refs, manifest, panel,
                                None, None, 0, 0, needed)
        token, bench = panel.last(needed)
        result = ecdr([b.close for b in token], [b.close for b in bench], [b.volume_usd for b in token], ECDR)
        signal = {"shock_z": _num(result.shock_z)}
        counts = (int(ECDR["relative_return_lookback"]), 1)
        if result.reasons:
            stance, direction, why = "ABSTAIN", "UNKNOWN", result.reasons
        elif result.dislocation:   # the panel that could qualify it is not collected in this version
            stance, direction, why = "ABSTAIN", "UNKNOWN", ("PRE_EVENT_BASELINE_MISSING",)
        else:
            stance, direction, why = "CAUTION", "NEUTRAL", ("NO_REVERSAL_THESIS",)
        return self._record(ECDR, decision, membership, stance, direction, why, signal, refs, manifest, panel, None,
                            None, *counts, needed)

    def _panel_reasons(self, decision: PrimaryDecision, panel: Panel, needed: int) -> tuple[str, ...]:
        if panel.latest_end_ms is None:
            return ("INPUT_MISSING:aligned_15m_bars",)
        if _ms(decision.decided_at) - panel.latest_end_ms > COMMON["max_latest_bar_age_seconds"] * 1000:
            return ("INPUT_STALE:latest_15m_bar",)
        if panel.length < needed:
            return (f"INPUT_MISSING:history {panel.length}/{needed} consecutive 15m bars",)
        return ()

    # ── the record ──────────────────────────────────────────────────────────────────────────────────
    def _record(self, spec: dict[str, Any], decision: PrimaryDecision, membership: str, stance: str, direction: str,
                reasons: tuple[str, ...], signal: dict[str, str | None], refs: list[InputRef],
                manifest: dict[str, Any], panel: Panel, costs: Costs | None, quote_expiry: datetime | None,
                training: int, signal_bars: int, needed: int | None = None) -> Advisory:
        d = decision.decided_at
        used = panel.last(needed) if needed and panel.length >= needed else ((), ())
        token, bench = used
        all_refs = list(refs)
        if token:
            all_refs += [_range_ref("market.ohlcv_bars", decision.context.features.pool_address, token),
                         _range_ref("market.benchmark_bars", self.benchmark_pool, bench)]
        spec_hash = spec_sha256(spec)
        full_manifest = {**manifest, "spec": spec_hash, "panel_length": panel.length,
                         "latest_end_ms": panel.latest_end_ms,
                         "token": [[b.start_ms, b.digest] for b in token],
                         "benchmark": [[b.start_ms, b.digest] for b in bench]}
        next_bar = (panel.latest_end_ms + BAR_MS) if panel.latest_end_ms else _ms(d) + BAR_MS
        expires = from_epoch_millis(next_bar)
        if quote_expiry is not None:
            expires = min(expires, quote_expiry)
        missing = tuple(r.split(":", 1)[-1] for r in reasons if r.startswith("INPUT_MISSING"))
        stale = tuple(r.split(":", 1)[-1] for r in reasons if r.startswith("INPUT_STALE"))
        status = "MISSING" if missing else "STALE" if stale else "CONFLICT" if "IDENTITY_CONFLICT" in reasons else "OK"
        latest_end = None if not all_refs else max(r.event_end for r in all_refs)
        latest_available = None if not all_refs else max(r.first_available_at for r in all_refs)
        economics = costs.economics if costs is not None else Economics(status="UNMEASURED", ticket_raw=None,
                                                                        roundtrip_cost_fraction=None)
        return Advisory(
            advisory_id="adv_" + fingerprint({"advisor": spec["advisor_id"], "spec": spec_hash,
                                              "source": decision.source, "decision": decision.decision_hash})[:32],
            advisor_id=spec["advisor_id"], spec_version=int(spec["version"]), spec_sha256=spec_hash,
            code_sha256=CODE_SHA256, primary_decision_hash=decision.decision_hash, primary_action=decision.action,
            asset={"profile": PROFILE, "instrument_id": decision.context.features.mint,
                   "pool_or_venue": decision.context.features.pool_address, "source": "geckoterminal.ohlcv",
                   "price_currency": "USD", "account_numeraire": "SOL", "membership_id": membership},
            decision_at=zulu(d), computed_at=zulu(max(self.now, d)), latest_event_end=latest_end,
            latest_input_available_at=latest_available, expires_at=zulu(expires), signal_direction=direction,
            stance=stance, candidate_signal={k: v for k, v in signal.items() if v is not None},
            horizon={"maximum": int(spec["max_hold_seconds"]), "unit": "SECONDS"}, rationale_codes=reasons,
            inputs=tuple(sorted(all_refs, key=lambda r: (r.store, r.row_id, r.raw_sha256 or ""))),
            freshness={"status": status, "missing": missing, "stale": stale},
            evidence={"training_bars": training, "signal_bars": signal_bars,
                      "quote_pairs": 1 if costs is not None and costs.economics.status != "UNMEASURED" else 0,
                      "forward_closed_trades": 0, "effective_observations": None},
            uncertainty={"reason": "diagnostic score only; no calibration experiment exists"},
            regime={"status": decision.context.regime_status.value, "label": decision.context.regime_label},
            shared_dependencies=SHARED, economics=economics, input_sha256=fingerprint(full_manifest))


__all__ = ["TRANSFORMS", "Advisor", "Costs", "PrimaryDecision", "exact_ticket_costs"]
