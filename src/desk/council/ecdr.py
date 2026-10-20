"""C2_ECDR, execution-confirmed dislocation reversal: the event test (research/council/STRATEGIES.md §4).

The event is the latest closed 15-minute token/SOL return, standardized by the median and MAD scale of the 96
returns before it (excluded from its own baseline). A dislocation needs all three: standardized move <= -3, the
SOL move within +-1%, and reported bar volume >= 2x the median of the 20 bars before it.

Whether a dislocation is a reversal opportunity depends on evidence this version does not collect: a pre-enrolled
pool with six same-ticket quote pairs before the event, reserve depth, and two recovery quote sets. The minimum
version adds no requests, so a detected dislocation is ABSTAIN (PRE_EVENT_BASELINE_MISSING), never SUPPORT.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, localcontext
from typing import Any

from desk.council.brc import PRECISION, log_returns, mad_scale, median
from desk.council.specs import COMMON, decimal


@dataclass(frozen=True)
class EcdrResult:
    reasons: tuple[str, ...]              # empty when the calculation is valid
    shock_z: Decimal | None = None
    benchmark_event_return: Decimal | None = None
    volume_multiple: Decimal | None = None
    dislocation: bool = False


def required_closes(spec: dict[str, Any]) -> int:
    return int(spec["relative_return_lookback"]) + 2


def ecdr(token_closes: list[Decimal], benchmark_closes: list[Decimal], token_volumes: list[Decimal],
         spec: dict[str, Any]) -> EcdrResult:
    lookback, volume_lookback = int(spec["relative_return_lookback"]), int(spec["volume_lookback"])
    if len(token_closes) != lookback + 2 or len(benchmark_closes) != len(token_closes) \
            or len(token_volumes) != len(token_closes):
        raise ValueError("C2 needs exactly lookback + 2 aligned closes and volumes")
    with localcontext() as ctx:
        ctx.prec = PRECISION
        r_i, r_b = log_returns(token_closes), log_returns(benchmark_closes)
        relative = [a - b for a, b in zip(r_i, r_b, strict=True)]
        prior, event = relative[:-1], relative[-1]
        scale = mad_scale(prior)
        benchmark_move = r_b[-1].exp() - 1
        prior_volume = median(token_volumes[-1 - volume_lookback:-1])
        volume_multiple = None if prior_volume <= 0 else token_volumes[-1] / prior_volume
        if scale < decimal(COMMON, "min_robust_residual_scale_per_bar"):
            return EcdrResult(("RESIDUAL_SCALE_LOW",), None, benchmark_move, volume_multiple)
        shock = (event - median(prior)) / scale
        dislocation = (shock <= decimal(spec, "shock_z_max")
                       and abs(benchmark_move) <= decimal(spec, "max_abs_benchmark_event_return")
                       and volume_multiple is not None
                       and volume_multiple >= decimal(spec, "reported_bar_volume_multiple_min"))
        return EcdrResult((), shock, benchmark_move, volume_multiple, dislocation)


__all__ = ["EcdrResult", "ecdr", "required_closes"]
