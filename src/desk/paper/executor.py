"""Quote-based PAPER fills (PLAN §12). Every fill is a Jupiter ``/order`` quote for the exact amount; a displayed
price is never used. The conservative profile waits, re-quotes, takes the worse quote and simulates failures.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from decimal import Decimal

from desk.contracts.quotes import QuoteObservation, QuoteUnavailable
from desk.paper.model import FeeModel, Profiles, entry_profiles, exit_profiles, failure_draw
from desk.security.policy import SOL_MINT

Quote = QuoteObservation | QuoteUnavailable
QuoteFn = Callable[[str, str, int], Awaitable[Quote]]
SleepFn = Callable[[float], Awaitable[None]]
MAX_ATTEMPTS = 3


def _usable(quote: Quote, amount: int) -> QuoteObservation | None:
    if isinstance(quote, QuoteObservation) and quote.in_amount_raw == amount and quote.out_amount_raw > 0:
        return quote
    return None


@dataclass(frozen=True)
class Execution:
    wait_seconds: float
    failure_rate: Decimal
    stress_failure_rate: Decimal
    fees: FeeModel


@dataclass
class EntryResult:
    status: str                                   # FILLED | ENTRY_FAILED | ENTRY_REJECTED_COST
    reason: str = ""
    profiles: Profiles | None = None
    fee_lamports: int = 0                         # the successful transaction
    failed_fees_lamports: int = 0                 # conservative simulated failures
    stress_extra_fees_lamports: int = 0           # additional stress-profile failures
    roundtrip_cost_fraction: Decimal | None = None
    quotes: list[dict[str, object]] = field(default_factory=list)


@dataclass
class ExitResult:
    status: str                                   # FILLED | NO_ROUTE | TX_FAILED
    profiles: Profiles | None = None
    fee_lamports: int = 0
    stress_extra_fees_lamports: int = 0
    quotes: list[dict[str, object]] = field(default_factory=list)


def _summary(quote: Quote) -> dict[str, object]:
    if isinstance(quote, QuoteObservation):
        return {"in": quote.in_amount_raw, "out": quote.out_amount_raw, "impact_pct": str(quote.price_impact_pct),
                "router": quote.router, "received_at": quote.received_at.isoformat(), "latency_ms": str(quote.latency_ms),
                "raw_sha256": quote.raw_sha256}
    return {"unavailable": quote.error_code or quote.error_message or "no route", "received_at": quote.received_at.isoformat()}


def _stress_fees(execution: Execution, intent_id: str, leg: str, fee: int) -> int:
    extra = 0
    for attempt in range(MAX_ATTEMPTS):
        if failure_draw(intent_id, f"{leg}-stress", attempt) >= execution.stress_failure_rate:
            break
        extra += fee
    return extra


async def enter(execution: Execution, quote: QuoteFn, sleep: SleepFn, *, intent_id: str, mint: str,
                ticket_lamports: int, max_roundtrip_cost_fraction: Decimal) -> EntryResult:
    result = EntryResult(status="ENTRY_FAILED")
    for attempt in range(MAX_ATTEMPTS):
        first_raw = await quote(SOL_MINT, mint, ticket_lamports)
        result.quotes.append(_summary(first_raw))
        first = _usable(first_raw, ticket_lamports)
        if first is None:
            result.reason = "NO_ENTRY_ROUTE"
            return result
        back_raw = await quote(mint, SOL_MINT, first.out_amount_raw)
        result.quotes.append(_summary(back_raw))
        back = _usable(back_raw, first.out_amount_raw)
        if back is None:
            result.reason = "NO_EXIT_ROUTE_AT_ENTRY"
            return result
        fee = execution.fees.fixed(first)
        cost = (Decimal(ticket_lamports - back.out_amount_raw) + 2 * fee) / Decimal(ticket_lamports)
        result.roundtrip_cost_fraction = cost
        if cost > max_roundtrip_cost_fraction:
            result.status, result.reason = "ENTRY_REJECTED_COST", f"roundtrip {cost:.4f} > {max_roundtrip_cost_fraction}"
            return result
        await sleep(execution.wait_seconds)
        second_raw = await quote(SOL_MINT, mint, ticket_lamports)
        result.quotes.append(_summary(second_raw))
        second = _usable(second_raw, ticket_lamports)
        if second is None:
            result.reason = "ENTRY_ROUTE_VANISHED"
            return result
        if failure_draw(intent_id, "entry", attempt) < execution.failure_rate:
            result.failed_fees_lamports += execution.fees.fixed(second)   # a failed transaction still pays its fee
            continue
        result.status, result.reason = "FILLED", ""
        result.profiles = entry_profiles(first, second)
        result.fee_lamports = execution.fees.fixed(second)
        result.stress_extra_fees_lamports = _stress_fees(execution, intent_id, "entry", result.fee_lamports)
        return result
    result.reason = "SIMULATED_TX_FAILURES"
    return result


async def exit_(execution: Execution, quote: QuoteFn, sleep: SleepFn, *, intent_id: str, mint: str,
                entry: Profiles, attempt: int) -> ExitResult:
    tokens = entry.conservative
    first_raw = await quote(mint, SOL_MINT, tokens)
    result = ExitResult(status="NO_ROUTE", quotes=[_summary(first_raw)])
    first = _usable(first_raw, tokens)
    if first is None:
        return result
    await sleep(execution.wait_seconds)
    second_raw = await quote(mint, SOL_MINT, tokens)
    result.quotes.append(_summary(second_raw))
    second = _usable(second_raw, tokens)
    if second is None:
        return result
    result.fee_lamports = execution.fees.fixed(second)
    if failure_draw(intent_id, "exit", attempt) < execution.failure_rate:
        result.status = "TX_FAILED"
        return result
    result.status, result.profiles = "FILLED", exit_profiles(first, second, entry)
    result.stress_extra_fees_lamports = _stress_fees(execution, intent_id, "exit", result.fee_lamports)
    return result
