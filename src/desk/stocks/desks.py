"""The eleven desks and the committee (docs/STOCKS_METHOD.md).

Every desk scores every stock 0-100, where higher is better for a buyer (the crowd desk measures attention, and the
options desk how cheap options are for a buyer). A score is the weighted average of the stock's percentile on each
of the desk's measures; valuation and quality are ranked within the stock's sector. Confidence is the share of the
desk's weight that had data. Every reason is a template filled with the numbers that produced the score.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from desk.core.hashing import canonical_json, sha256_hex
from desk.stocks.fundamentals import dcf_value, implied_growth
from desk.stocks.options import iv_rank
from desk.stocks.sectors import LABELS

METHOD_VERSION = 2
DESKS = ("valuation", "quality", "growth", "momentum", "earnings", "insiders", "shorts", "crowd", "risk", "macro", "options")
DESK_LABELS = {"valuation": "Valuation", "quality": "Quality", "growth": "Growth", "momentum": "Momentum",
               "earnings": "Earnings and events", "insiders": "Insiders", "shorts": "Short interest", "crowd": "The crowd",
               "risk": "Risk", "macro": "Macro and sectors", "options": "Options"}
# The committee's starting weights (docs/STOCKS_METHOD.md): fundamentals and price evidence, not attention or option
# pricing. Validation on unseen periods replaces them (desk.stocks.validate) and the report card shows which applied.
DEFAULT_WEIGHTS = {"valuation": 1.0, "quality": 1.0, "growth": 0.8, "momentum": 1.0, "earnings": 0.6, "insiders": 0.6,
                   "shorts": 0.4, "crowd": 0.0, "risk": 0.6, "macro": 0.4, "options": 0.0}
EQUITY_RISK_PREMIUM = 0.05
TERMINAL_GROWTH = 0.025


@dataclass
class Context:
    asof: date
    symbols: list[str]
    names: dict[str, str]
    sectors: dict[str, str]
    financial: dict[str, str | None]
    price: dict[str, dict[str, float | None]]          # per symbol: price features
    fund: dict[str, dict[str, Any]]                     # per symbol: fundamentals with price multiples
    insiders: dict[str, dict[str, Any]] = field(default_factory=dict)
    events: dict[str, dict[str, Any]] = field(default_factory=dict)
    shorts: dict[str, dict[str, Any]] = field(default_factory=dict)
    crowd: dict[str, dict[str, Any]] = field(default_factory=dict)
    options: dict[str, dict[str, Any]] = field(default_factory=dict)
    macro: dict[str, Any] = field(default_factory=dict)
    sector_rs: dict[str, float] = field(default_factory=dict)
    valuation_history: dict[str, list[float]] = field(default_factory=dict)

    def f(self, symbol: str, key: str) -> float | None:
        v = self.fund.get(symbol, {}).get(key)
        return None if v is None or (isinstance(v, float) and math.isnan(v)) else float(v)

    def p(self, symbol: str, key: str) -> float | None:
        v = self.price.get(symbol, {}).get(key)
        return None if v is None or (isinstance(v, float) and math.isnan(v)) else float(v)


@dataclass
class DeskResult:
    score: float | None
    confidence: float
    verdict: str
    reasons: list[str]
    facts: dict[str, Any] = field(default_factory=dict)
    signals: list[tuple[float, str]] = field(default_factory=list)   # (strength -1..1, sentence) for bull/bear cases

    def as_json(self) -> dict[str, Any]:
        return {"score": None if self.score is None else round(self.score, 1), "confidence": round(self.confidence, 2),
                "verdict": self.verdict, "reasons": self.reasons, "facts": self.facts}


# ── formatting ───────────────────────────────────────────────────────────────────────────────────
def pct(x: float | None, digits: int = 0, sign: bool = True) -> str:
    if x is None:
        return "n/a"
    return f"{x * 100:+.{digits}f}%" if sign else f"{x * 100:.{digits}f}%"


def mult(x: float | None) -> str:
    return "n/a" if x is None else f"{x:.1f}×"


def usd(x: float | None) -> str:
    if x is None:
        return "n/a"
    a = abs(x)
    s = f"${a / 1e12:.1f}T" if a >= 1e12 else f"${a / 1e9:.1f}B" if a >= 1e9 else f"${a / 1e6:.1f}M" if a >= 1e6 else (
        f"${a / 1e3:.0f}K" if a >= 1e3 else f"${a:.0f}")
    return ("−" if x < 0 else "") + s


# ── ranking ──────────────────────────────────────────────────────────────────────────────────────
def percentiles(values: dict[str, float | None], *, higher_is_better: bool, groups: dict[str, str] | None = None,
                min_group: int = 10) -> dict[str, float]:
    """0..1 percentile per symbol (ties share the average rank), within its group when the group is big enough."""
    valid = {s: v for s, v in values.items() if v is not None and not math.isnan(v)}

    def rank(members: dict[str, float]) -> dict[str, float]:
        ordered = sorted(members.items(), key=lambda kv: kv[1])
        n = len(ordered)
        out: dict[str, float] = {}
        i = 0
        while i < n:
            j = i
            while j + 1 < n and ordered[j + 1][1] == ordered[i][1]:
                j += 1
            r = ((i + j) / 2) / (n - 1) if n > 1 else 0.5
            for k in range(i, j + 1):
                out[ordered[k][0]] = r if higher_is_better else 1 - r
            i = j + 1
        return out

    overall = rank(valid)
    if groups is None:
        return overall
    by: dict[str, dict[str, float]] = {}
    for s, v in valid.items():
        by.setdefault(groups.get(s, "Other"), {})[s] = v
    out = dict(overall)
    for members in by.values():
        if len(members) >= min_group:
            out.update(rank(members))
    return out


def group_median(values: dict[str, float | None], groups: dict[str, str]) -> dict[str, float]:
    by: dict[str, list[float]] = {}
    for s, v in values.items():
        if v is not None and not math.isnan(v):
            by.setdefault(groups.get(s, "Other"), []).append(v)
    return {g: statistics.median(v) for g, v in by.items() if len(v) >= 5}


@dataclass(frozen=True)
class Component:
    key: str
    weight: float
    higher_is_better: bool
    in_sector: bool
    value: Callable[[Context, str], float | None]
    describe: Callable[[Context, str, float, float | None], str]     # (ctx, symbol, value, sector median) -> sentence


def score_components(ctx: Context, symbols: Sequence[str], comps: Sequence[Component]
                     ) -> dict[str, tuple[float | None, float, list[tuple[float, str]]]]:
    """Per symbol: score 0-100, confidence, and the (signed strength, sentence) of each available component."""
    pcts, medians = {}, {}
    for c in comps:
        values = {s: c.value(ctx, s) for s in symbols}
        pcts[c.key] = percentiles(values, higher_is_better=c.higher_is_better, groups=ctx.sectors if c.in_sector else None)
        medians[c.key] = (group_median(values, ctx.sectors) if c.in_sector else
                          {"*": statistics.median([v for v in values.values() if v is not None])} if any(
                              v is not None for v in values.values()) else {})
    total_w = sum(c.weight for c in comps) or 1.0
    out: dict[str, tuple[float | None, float, list[tuple[float, str]]]] = {}
    for s in symbols:
        num = den = 0.0
        parts: list[tuple[float, str]] = []
        for c in comps:
            p = pcts[c.key].get(s)
            if p is None:
                continue
            v = c.value(ctx, s)
            assert v is not None
            num += p * c.weight
            den += c.weight
            med = medians[c.key].get(ctx.sectors.get(s, "Other")) if c.in_sector else medians[c.key].get("*")
            parts.append(((p - 0.5) * 2 * c.weight, c.describe(ctx, s, v, med)))
        out[s] = (None if den == 0 else num / den * 100, den / total_w, parts)
    return out


def verdict(score: float | None, labels: Sequence[str]) -> str:
    """Five labels from best to worst at 75/60/40/25."""
    if score is None:
        return "Not enough data"
    for threshold, label in zip((75, 60, 40, 25), labels, strict=False):
        if score >= threshold:
            return label
    return labels[4]


def _top_reasons(parts: list[tuple[float, str]], n: int = 3) -> list[str]:
    return [text for _, text in sorted(parts, key=lambda x: -abs(x[0]))[:n]]


def _sector_name(ctx: Context, s: str) -> str:
    return LABELS.get(ctx.sectors.get(s, "Other"), "its sector").lower()


def _vs(ctx: Context, s: str, med: float | None, fmt: Callable[[float | None], str]) -> str:
    return "" if med is None else f" vs {fmt(med)} for {_sector_name(ctx, s)}"


# ── 1. valuation ─────────────────────────────────────────────────────────────────────────────────
def valuation(ctx: Context, symbols: Sequence[str]) -> dict[str, DeskResult]:
    rate = (ctx.macro.get("ust10y") or 4.0) / 100
    industrial = [s for s in symbols if not ctx.financial.get(s)]
    banks = [s for s in symbols if ctx.financial.get(s) in ("bank", "insurer")]
    reits = [s for s in symbols if ctx.financial.get(s) == "reit"]

    def own_history(c: Context, s: str) -> float | None:
        hist = c.valuation_history.get(s) or []
        cur = c.f(s, "ev_sales") if not c.financial.get(s) else c.f(s, "pb")
        if cur is None or len(hist) < 6:
            return None
        return sum(1 for h in hist if h < cur) / len(hist)          # 0 = cheapest it has been

    def growth_gap(c: Context, s: str) -> float | None:
        ev, fcf = c.f(s, "ev"), c.f(s, "fcf")
        delivered = c.f(s, "revenue_cagr_5y") if c.f(s, "revenue_cagr_5y") is not None else c.f(s, "revenue_growth")
        if ev is None or fcf is None or delivered is None:
            return None
        implied = implied_growth(ev, fcf, rate + EQUITY_RISK_PREMIUM, TERMINAL_GROWTH)
        return None if implied is None else delivered - implied

    hist_comp = Component("own_history", 0.6, False, False, own_history,
                          lambda c, s, v, m: (f"Pricier than {v * 100:.0f}% of its own last "
                                              f"{len(c.valuation_history.get(s, []))} months"))
    comps_ind = [
        Component("ev_ebit", 1.0, False, True, lambda c, s: c.f(s, "ev_ebit"),
                  lambda c, s, v, m: f"{mult(v)} EV/EBIT{_vs(c, s, m, mult)}"),
        Component("ev_sales", 0.5, False, True, lambda c, s: c.f(s, "ev_sales"),
                  lambda c, s, v, m: f"{mult(v)} EV/sales{_vs(c, s, m, mult)}"),
        Component("pe", 0.7, False, True, lambda c, s: c.f(s, "pe"),
                  lambda c, s, v, m: f"P/E {v:.1f}{'' if m is None else f' vs {m:.1f} for ' + _sector_name(c, s)}"),
        Component("fcf_yield", 0.8, True, True, lambda c, s: c.f(s, "fcf_yield"),
                  lambda c, s, v, m: f"Free cash flow yield {pct(v, 1, False)}{_vs(c, s, m, lambda x: pct(x, 1, False))}"),
        Component("growth_gap", 0.8, True, False, growth_gap,
                  lambda c, s, v, m: _implied_text(c, s, rate)),
        hist_comp,
    ]
    comps_bank = [
        Component("pb", 1.0, False, True, lambda c, s: c.f(s, "pb"),
                  lambda c, s, v, m: f"{v:.2f}× book value{_vs(c, s, m, lambda x: f'{x:.2f}×' if x else 'n/a')}"),
        Component("pe", 1.0, False, True, lambda c, s: c.f(s, "pe"),
                  lambda c, s, v, m: f"P/E {v:.1f}{'' if m is None else f' vs {m:.1f} for ' + _sector_name(c, s)}"),
        Component("pb_per_roe", 0.7, False, True,
                  lambda c, s: None if not c.f(s, "pb") or not c.f(s, "roe") or (c.f(s, "roe") or 0) <= 0
                  else (c.f(s, "pb") or 0) / ((c.f(s, "roe") or 1) * 100),
                  lambda c, s, v, m: f"Price-to-book per point of return on equity {v:.2f}"),
        hist_comp,
    ]
    comps_reit = [
        Component("p_ffo", 1.0, False, True, lambda c, s: c.f(s, "p_ffo"),
                  lambda c, s, v, m: f"{mult(v)} funds from operations{_vs(c, s, m, mult)}"),
        Component("dividend_yield", 0.6, True, True, lambda c, s: c.f(s, "dividend_yield"),
                  lambda c, s, v, m: f"Dividend yield {pct(v, 1, False)}"),
        Component("pb", 0.5, False, True, lambda c, s: c.f(s, "pb"), lambda c, s, v, m: f"{v:.2f}× book value"),
    ]
    out: dict[str, DeskResult] = {}
    for group, comps in ((industrial, comps_ind), (banks, comps_bank), (reits, comps_reit)):
        scored = score_components(ctx, group, comps)
        for s, (score, conf, parts) in scored.items():
            facts: dict[str, Any] = {k: ctx.f(s, k) for k in ("pe", "ev_ebit", "ev_sales", "p_fcf", "fcf_yield", "pb", "p_ffo",
                                                               "dividend_yield", "market_cap", "ev")}
            facts["fair_value"] = fair_value(ctx, s, rate)
            facts["implied_growth"] = None if ctx.f(s, "ev") is None or ctx.f(s, "fcf") is None else implied_growth(
                ctx.f(s, "ev") or 0, ctx.f(s, "fcf") or 0, rate + EQUITY_RISK_PREMIUM, TERMINAL_GROWTH)
            out[s] = DeskResult(score, conf, verdict(score, ("Cheap", "Reasonable", "Fair", "Pricey", "Expensive")),
                                _top_reasons(parts), facts, parts)
    return out


def _implied_text(ctx: Context, s: str, rate: float) -> str:
    ev, fcf = ctx.f(s, "ev"), ctx.f(s, "fcf")
    delivered = ctx.f(s, "revenue_cagr_5y")
    label = "over the last five years" if delivered is not None else "over the last year"
    delivered = delivered if delivered is not None else ctx.f(s, "revenue_growth")
    implied = None if ev is None or fcf is None else implied_growth(ev, fcf, rate + EQUITY_RISK_PREMIUM, TERMINAL_GROWTH)
    if implied is None or delivered is None:
        return "Price-implied growth not computable"
    return (f"The price implies {pct(implied, 0, False)} yearly cash-flow growth for ten years; revenue grew "
            f"{pct(delivered, 0, False)} a year {label}")


def fair_value(ctx: Context, s: str, rate: float) -> dict[str, Any] | None:
    """A conservative DCF range per share: base growth = delivered growth capped at 25%, fading to 2.5%."""
    fcf, shares = ctx.f(s, "fcf"), ctx.f(s, "shares")
    delivered = ctx.f(s, "revenue_cagr_5y") if ctx.f(s, "revenue_cagr_5y") is not None else ctx.f(s, "revenue_growth")
    if ctx.financial.get(s) or fcf is None or fcf <= 0 or not shares or delivered is None:
        return None
    base_g = min(max(delivered, -0.05), 0.25)
    discount = rate + EQUITY_RISK_PREMIUM
    net = (ctx.f(s, "cash") or 0.0) - (ctx.f(s, "debt") or 0.0)
    cases = {"bear": (base_g - 0.05, discount + 0.01), "base": (base_g, discount), "bull": (base_g + 0.05, discount - 0.01)}
    values = {name: max(0.0, (dcf_value(fcf, g, d, TERMINAL_GROWTH) + net) / shares) for name, (g, d) in cases.items()}
    price = ctx.f(s, "price")
    return {**values, "growth": base_g, "discount": discount, "terminal": TERMINAL_GROWTH, "risk_free": rate,
            "premium": EQUITY_RISK_PREMIUM, "fcf": fcf,
            "price_vs_base": None if not price or not values["base"] else price / values["base"] - 1}


# ── 2. quality ───────────────────────────────────────────────────────────────────────────────────
def quality(ctx: Context, symbols: Sequence[str]) -> dict[str, DeskResult]:
    industrial = [s for s in symbols if ctx.financial.get(s) in (None, "reit")]
    fin = [s for s in symbols if ctx.financial.get(s) in ("bank", "insurer")]
    comps_ind = [
        Component("roic", 1.0, True, True, lambda c, s: c.f(s, "roic"),
                  lambda c, s, v, m: f"Return on invested capital {pct(v, 0, False)}{_vs(c, s, m, lambda x: pct(x, 0, False))}"),
        Component("gross_margin", 0.5, True, True, lambda c, s: c.f(s, "gross_margin"),
                  lambda c, s, v, m: f"Gross margin {pct(v, 0, False)}{_vs(c, s, m, lambda x: pct(x, 0, False))}"),
        Component("fcf_conversion", 0.6, True, False,
                  lambda c, s: None if c.f(s, "fcf_conversion") is None else min(c.f(s, "fcf_conversion") or 0, 2.0),
                  lambda c, s, v, m: f"Free cash flow is {v:.1f}× reported profit"),
        Component("accruals", 0.6, False, False, lambda c, s: c.f(s, "accruals"),
                  lambda c, s, v, m: (f"Accruals {pct(v, 1)} of assets "
                                      f"({'profits backed by cash' if v < 0 else 'profit ahead of cash'})")),
        Component("piotroski", 0.8, True, False, lambda c, s: c.f(s, "piotroski"),
                  lambda c, s, v, m: f"Piotroski F-score {v:.0f} of 9"),
        Component("altman_z", 0.4, True, False, lambda c, s: c.f(s, "altman_z"),
                  lambda c, s, v, m: f"Altman Z {v:.1f} ({'safe' if v > 3 else 'grey zone' if v > 1.8 else 'distress zone'})"),
        Component("beneish_m", 0.4, False, False, lambda c, s: c.f(s, "beneish_m"),
                  lambda c, s, v, m: (f"Beneish M {v:.2f} "
                                      f"({'no manipulation signal' if v < -1.78 else 'earnings-manipulation flag'})")),
        Component("net_debt_ebitda", 0.5, False, False, lambda c, s: c.f(s, "net_debt_ebitda"),
                  lambda c, s, v, m: f"Net debt {v:.1f}× EBITDA"),
        Component("dilution", 0.5, False, False, lambda c, s: c.f(s, "dilution"),
                  lambda c, s, v, m: f"Share count {pct(v, 1)} in a year ({'buybacks' if v < 0 else 'dilution'})"),
    ]
    comps_fin = [
        Component("roe", 1.0, True, True, lambda c, s: c.f(s, "roe"),
                  lambda c, s, v, m: f"Return on equity {pct(v, 0, False)}{_vs(c, s, m, lambda x: pct(x, 0, False))}"),
        Component("net_margin", 0.5, True, True, lambda c, s: c.f(s, "net_margin"),
                  lambda c, s, v, m: f"Net margin {pct(v, 0, False)}"),
        Component("dilution", 0.5, False, False, lambda c, s: c.f(s, "dilution"),
                  lambda c, s, v, m: f"Share count {pct(v, 1)} in a year"),
    ]
    out: dict[str, DeskResult] = {}
    for group, comps in ((industrial, comps_ind), (fin, comps_fin)):
        for s, (score, conf, parts) in score_components(ctx, group, comps).items():
            out[s] = DeskResult(score, conf, verdict(score, ("Excellent", "Solid", "Average", "Weak", "Poor")),
                                _top_reasons(parts), {k: ctx.f(s, k) for k in ("roic", "roe", "gross_margin", "operating_margin",
                                                                               "piotroski", "altman_z", "beneish_m", "dilution")},
                                parts)
    return out


# ── 3. growth ────────────────────────────────────────────────────────────────────────────────────
def growth(ctx: Context, symbols: Sequence[str]) -> dict[str, DeskResult]:
    comps = [
        Component("revenue_growth", 1.0, True, False, lambda c, s: c.f(s, "revenue_growth"),
                  lambda c, s, v, m: f"Revenue {pct(v)} a year"),
        Component("revenue_acceleration", 0.8, True, False, lambda c, s: c.f(s, "revenue_acceleration"),
                  lambda c, s, v, m: f"Growth {'up' if v > 0 else 'down'} {abs(v) * 100:.0f} points from half a year earlier"),
        Component("earnings_growth", 0.6, True, False,
                  lambda c, s: None if c.f(s, "earnings_growth") is None else max(min(c.f(s, "earnings_growth") or 0, 3.0), -1.0),
                  lambda c, s, v, m: f"Earnings {pct(v)} a year"),
        Component("cfo_growth", 0.4, True, False,
                  lambda c, s: None if c.f(s, "cfo_growth") is None else max(min(c.f(s, "cfo_growth") or 0, 3.0), -1.0),
                  lambda c, s, v, m: f"Operating cash flow {pct(v)} a year"),
        Component("revenue_cagr_5y", 0.5, True, False, lambda c, s: c.f(s, "revenue_cagr_5y"),
                  lambda c, s, v, m: f"Revenue {pct(v)} a year over five years"),
    ]
    out: dict[str, DeskResult] = {}
    for s, (score, conf, parts) in score_components(ctx, symbols, comps).items():
        acc = ctx.f(s, "revenue_acceleration")
        if score is None:
            label = "Not enough data"
        elif score >= 70 and acc is not None and acc > 0:
            label = "Speeding up"
        else:
            label = verdict(score, ("Fast growth", "Growing", "Steady", "Slowing", "Shrinking"))
        out[s] = DeskResult(score, conf, label, _top_reasons(parts),
                            {k: ctx.f(s, k) for k in ("revenue_growth", "earnings_growth", "revenue_acceleration",
                                                      "revenue_cagr_5y")},
                            parts)
    return out


# ── 4. momentum ──────────────────────────────────────────────────────────────────────────────────
def momentum(ctx: Context, symbols: Sequence[str]) -> dict[str, DeskResult]:
    sector_ret: dict[str, list[float]] = {}
    for s in symbols:
        r = ctx.p(s, "ret_63d")
        if r is not None:
            sector_ret.setdefault(ctx.sectors.get(s, "Other"), []).append(r)
    sector_med = {k: statistics.median(v) for k, v in sector_ret.items() if len(v) >= 5}
    comps = [
        Component("mom_12_1", 1.0, True, False, lambda c, s: c.p(s, "mom_12_1"),
                  lambda c, s, v, m: f"12-month return excluding the last month {pct(v)}"),
        Component("ret_63d", 0.5, True, False, lambda c, s: c.p(s, "ret_63d"), lambda c, s, v, m: f"Three months {pct(v)}"),
        Component("dist_high", 0.6, True, False, lambda c, s: c.p(s, "dist_high_52w"),
                  lambda c, s, v, m: "At a 52-week high" if v >= -0.01 else f"{pct(-v, 0, False)} below its 52-week high"),
        Component("above_200", 0.5, True, False, lambda c, s: c.p(s, "above_sma200"),
                  lambda c, s, v, m: f"{'Above' if v > 0 else 'Below'} its 200-day average by {pct(abs(v), 0, False)}"),
        Component("above_50", 0.3, True, False, lambda c, s: c.p(s, "above_sma50"),
                  lambda c, s, v, m: f"{'Above' if v > 0 else 'Below'} its 50-day average"),
        Component("vs_sector", 0.6, True, False,
                  lambda c, s: None if c.p(s, "ret_63d") is None or c.sectors.get(s, "Other") not in sector_med
                  else (c.p(s, "ret_63d") or 0) - sector_med[c.sectors.get(s, "Other")],
                  lambda c, s, v, m: (f"{pct(abs(v), 0, False)} {'ahead of' if v > 0 else 'behind'} "
                                      f"{_sector_name(c, s)} over three months")),
    ]
    out: dict[str, DeskResult] = {}
    for s, (score, conf, parts) in score_components(ctx, symbols, comps).items():
        a50, a200 = ctx.p(s, "above_sma50"), ctx.p(s, "above_sma200")
        if score is None:
            label = "Not enough data"
        elif score >= 70 and (a200 or 0) > 0:
            label = "Strong uptrend"
        elif a50 is not None and a200 is not None and a50 > 0 > a200:
            label = "Turning up"
        elif a50 is not None and a200 is not None and a50 < 0 < a200:
            label = "Weakening"
        else:
            label = verdict(score, ("Strong uptrend", "Uptrend", "Neutral", "Weakening", "Downtrend"))
        out[s] = DeskResult(score, conf, label, _top_reasons(parts),
                            {k: ctx.p(s, k) for k in ("ret_21d", "ret_63d", "ret_252d", "mom_12_1", "dist_high_52w",
                                                      "above_sma50", "above_sma200", "rel_volume")}, parts)
    return out


# ── 5. earnings and events ───────────────────────────────────────────────────────────────────────
def earnings(ctx: Context, symbols: Sequence[str]) -> dict[str, DeskResult]:
    recent: dict[str, dict[str, Any]] = {}
    for s in symbols:
        rel = [e for e in ctx.events.get(s, {}).get("earnings", []) if e.get("abnormal") is not None]
        if rel and (ctx.asof - date.fromisoformat(rel[-1]["session"])).days <= 75:
            recent[s] = rel[-1]
    react = percentiles({s: e["abnormal"] for s, e in recent.items()}, higher_is_better=True)
    drift = percentiles({s: e["drift"] for s, e in recent.items() if e.get("drift") is not None}, higher_is_better=True)
    out: dict[str, DeskResult] = {}
    for s in symbols:
        ev = ctx.events.get(s, {})
        flags = ev.get("red_flags", [])
        parts: list[tuple[float, str]] = []
        e = recent.get(s)
        if e is not None:
            pr, pd = react.get(s, 0.5), drift.get(s)
            score = (pr * 0.5 + (pd if pd is not None else 0.5) * 0.5) * 100
            conf = 1.0 if pd is not None else 0.7
            when = date.fromisoformat(e["session"]).strftime("%b %-d")
            parts.append(((pr - 0.5) * 2, f"{pct(e['abnormal'], 1)} vs the market on results day ({when})"))
            if e.get("drift") is not None:
                parts.append((((pd or 0.5) - 0.5) * 2, f"{pct(e['drift'], 1)} vs the market since"))
        else:
            score, conf = 50.0, 0.4
            parts.append((0.0, "No results in the last 75 days"))
        for flag in flags[:2]:
            score = min(score, 25.0)
            parts.append((-1.0, f"Red flag: {flag['text']} ({flag['filed']})"))
        for m in ev.get("material", [])[:2]:
            parts.append((0.0, f"{m['filed']}: {m['text']}"))
        if flags:
            label = "Red flag"
        elif e is None:
            label = "Quiet"
        elif score >= 65 and (e.get("abnormal") or 0) > 0 and (e.get("drift") or 0) > 0:
            label = "Beat and drift"
        else:
            label = verdict(score, ("Strong reaction", "Positive reaction", "Mixed", "Negative reaction", "Sold off"))
        out[s] = DeskResult(score, conf, label, [t for _, t in sorted(parts, key=lambda x: -abs(x[0]))[:3]],
                            {"last_earnings": e, "next_window": ev.get("next_window"), "material": ev.get("material", []),
                             "red_flags": flags, "dilution_filings": ev.get("dilution_filings", 0)}, parts)
    return out


# ── 6. insiders ──────────────────────────────────────────────────────────────────────────────────
def insiders(ctx: Context, symbols: Sequence[str]) -> dict[str, DeskResult]:
    net_bps: dict[str, float | None] = {}
    for s in symbols:
        i = ctx.insiders.get(s)
        cap = ctx.f(s, "market_cap")
        if i is None or not cap:
            continue
        net_bps[s] = (i["buy_value"] - i["sell_value"]) / cap * 10_000
    ranked = percentiles(net_bps, higher_is_better=True)
    out: dict[str, DeskResult] = {}
    for s in symbols:
        i = ctx.insiders.get(s)
        parts: list[tuple[float, str]] = []
        if i is None or (i["buy_value"] == 0 and i["sell_value"] == 0):
            score, conf, label = 50.0, 0.6, "Quiet"
            parts.append((0.0, "No open-market buying or selling by insiders in 90 days"))
            if i and i["planned_sell_value"]:
                parts.append((0.0, f"{usd(i['planned_sell_value'])} sold under pre-arranged 10b5-1 plans (not counted)"))
        else:
            p = ranked.get(s, 0.5)
            score = 50 + (p - 0.5) * 60
            if i["cluster"] >= 3:
                score = max(score, 85.0)
            elif i["buyers"] >= 1 and i["buy_value"] > i["sell_value"]:
                score = max(score, 65.0)
            conf = 1.0
            if i["buy_value"]:
                parts.append((min(1.0, i["buyers"] / 3), f"{i['buyers']} insider{'s' if i['buyers'] != 1 else ''} bought "
                                                         f"{usd(i['buy_value'])} on the open market in 90 days"))
            if i["sell_value"]:
                parts.append((-min(1.0, i["sellers"] / 3), f"{i['sellers']} insider{'s' if i['sellers'] != 1 else ''} sold "
                                                           f"{usd(i['sell_value'])} outside 10b5-1 plans"))
            if i["cluster"] >= 3:
                parts.insert(0, (1.0, f"Cluster buying: {i['cluster']} insiders bought within 30 days"))
            label = ("Cluster buying" if i["cluster"] >= 3 else "Buying" if i["buy_value"] > i["sell_value"] else
                     "Heavy selling" if score < 25 else "Selling")
        out[s] = DeskResult(score, conf, label, [t for _, t in parts[:3]], {**(i or {})}, parts)
    return out


# ── 7. short interest ────────────────────────────────────────────────────────────────────────────
def shorts(ctx: Context, symbols: Sequence[str]) -> dict[str, DeskResult]:
    comps = [
        Component("short_pct", 1.0, False, False, lambda c, s: c.shorts.get(s, {}).get("short_pct"),
                  lambda c, s, v, m: f"{pct(v, 1, False)} of shares sold short"),
        Component("days_to_cover", 0.6, False, False, lambda c, s: c.shorts.get(s, {}).get("days_to_cover"),
                  lambda c, s, v, m: f"{v:.1f} days to cover"),
        Component("change", 0.4, False, False, lambda c, s: c.shorts.get(s, {}).get("change"),
                  lambda c, s, v, m: f"Short interest {pct(v, 0)} since the previous report"),
        Component("short_volume_ratio", 0.3, False, False, lambda c, s: c.shorts.get(s, {}).get("short_volume_ratio"),
                  lambda c, s, v, m: f"{pct(v, 0, False)} of reported volume was short sales (30 days)"),
        Component("fails", 0.3, False, False, lambda c, s: c.shorts.get(s, {}).get("fails_to_volume"),
                  lambda c, s, v, m: f"Failures to deliver {v:.2f}× a day's volume"),
    ]
    out: dict[str, DeskResult] = {}
    for s, (score, conf, parts) in score_components(ctx, symbols, comps).items():
        sh = ctx.shorts.get(s, {})
        squeeze = squeeze_score(ctx, s)
        out[s] = DeskResult(score, conf, verdict(score, ("Low", "Normal", "Elevated", "High", "Very high")),
                            _top_reasons(parts), {**sh, "squeeze": squeeze}, parts)
    return out


def squeeze_score(ctx: Context, s: str) -> float | None:
    """0-100: heavily shorted, hard to cover, and the price and volume already rising against the shorts."""
    sh = ctx.shorts.get(s, {})
    pct_short, dtc = sh.get("short_pct"), sh.get("days_to_cover")
    if pct_short is None or dtc is None:
        return None
    score = min(pct_short / 0.30, 1.0) * 45 + min(dtc / 8, 1.0) * 25
    r21, rv = ctx.p(s, "ret_21d"), ctx.p(s, "rel_volume")
    if r21 is not None and r21 > 0:
        score += min(r21 / 0.3, 1.0) * 20
    if rv is not None and rv > 1.5:
        score += min((rv - 1.5) / 3, 1.0) * 10
    return float(score)


# ── 8. the crowd ─────────────────────────────────────────────────────────────────────────────────
def crowd(ctx: Context, symbols: Sequence[str], hype_vs: dict[str, float | None] | None = None) -> dict[str, DeskResult]:
    attention = {s: _attention(ctx.crowd.get(s)) for s in symbols}
    ranked = percentiles({s: v for s, v in attention.items() if v is not None}, higher_is_better=True)
    out: dict[str, DeskResult] = {}
    if not ctx.crowd:            # nothing collected for the day (a rebuilt month, or the crowd feed was down): no view
        out = {}
        for s in symbols:
            meter, why = degen_meter(ctx, s)
            out[s] = DeskResult(None, 0.0, "No crowd data", ["No crowd counts were collected for this day"],
                                {"degen_meter": meter, "degen_reasons": why})
        return out
    for s in symbols:
        c = ctx.crowd.get(s, {})
        wsb, allc = c.get("wsb"), c.get("all")
        parts: list[tuple[float, str]] = []
        if not wsb and not allc:
            score, label = 10.0, "Quiet"
            parts.append((0.0, "Not in the most-mentioned stocks on Reddit"))
        else:
            p = ranked.get(s, 0.0)
            score = 30 + p * 70
            src = wsb or allc or {}
            parts.append((p, f"{src['mentions']} mentions on {'r/wallstreetbets' if wsb else 'Reddit'} in 24 hours "
                             f"(rank #{src['rank']})"))
            if src.get("mentions_24h_ago"):
                change = src["mentions"] / src["mentions_24h_ago"] - 1
                parts.append((max(-1.0, min(1.0, change)), f"{pct(change, 0)} vs the day before"))
            if src.get("avg_7d") and src.get("avg_prior_7d"):
                parts.append((0.0, f"Seven-day average {src['avg_7d']:.0f} vs {src['avg_prior_7d']:.0f} the week before"))
            label = "Heating up" if p >= 0.8 and (src.get("velocity") or 1) > 1.2 else (
                "Talked about" if p >= 0.6 else "Some chatter")
        meter, why = degen_meter(ctx, s)
        facts: dict[str, Any] = {"wsb": wsb, "all": allc, "degen_meter": meter, "degen_reasons": why}
        if hype_vs is not None and hype_vs.get(s) is not None and (wsb or allc):
            fundamentals = hype_vs[s]
            facts["wsb_vs_reality"] = {"hype": round(score),
                                       "fundamentals": None if fundamentals is None else round(fundamentals)}
        out[s] = DeskResult(score, 1.0, label, [t for _, t in parts[:3]], facts, [(0.0, t) for _, t in parts])
    return out


def _attention(c: dict[str, Any] | None) -> float | None:
    if not c:
        return None
    wsb, allc = c.get("wsb") or {}, c.get("all") or {}
    mentions = (wsb.get("mentions") or 0) * 1.5 + (allc.get("mentions") or 0)
    velocity = max((wsb or allc).get("velocity") or 1.0, 0.2)
    return math.log1p(mentions) * (1 + 0.25 * math.log(velocity))


def degen_meter(ctx: Context, s: str) -> tuple[int, list[str]]:
    """0-5 by how wild it is: volatility, short interest, crowd surge, call frenzy, penny price."""
    why: list[str] = []
    vol = ctx.p(s, "vol_20d")
    if vol is not None and vol > 0.6:
        why.append(f"moves {vol / math.sqrt(252) * 100:.1f}% a day")
    if vol is not None and vol > 1.0:
        why.append("triple-digit annualized volatility")
    sp = ctx.shorts.get(s, {}).get("short_pct")
    if sp is not None and sp > 0.15:
        why.append(f"{pct(sp, 0, False)} of shares short")
    wsb = (ctx.crowd.get(s) or {}).get("wsb") or {}
    if (wsb.get("velocity") or 0) > 2 or (wsb.get("rank") or 999) <= 10:
        why.append("mentions surging on r/wallstreetbets")
    o = ctx.options.get(s, {})
    if any(u.get("kind") == "call" for u in o.get("unusual", [])[:3]) and (o.get("put_call_volume") or 1) < 0.5:
        why.append("call buying frenzy")
    price = ctx.p(s, "close")
    if price is not None and price < 5:
        why.append("under $5")
    return min(len(why), 5), why


# ── 9. risk ──────────────────────────────────────────────────────────────────────────────────────
def risk(ctx: Context, symbols: Sequence[str]) -> dict[str, DeskResult]:
    comps = [
        Component("vol", 1.0, False, False, lambda c, s: c.p(s, "vol_1y") or c.p(s, "vol_60d"),
                  lambda c, s, v, m: (f"Moves {v / math.sqrt(252) * 100:.1f}% on a typical day "
                                      f"(market median {(m or 0) / math.sqrt(252) * 100:.1f}%)")),
        Component("beta", 0.5, False, False, lambda c, s: c.p(s, "beta_1y"),
                  lambda c, s, v, m: f"Beta {v:.2f} against the S&P 500"),
        Component("drawdown", 0.6, True, False, lambda c, s: c.p(s, "max_drawdown_1y"),
                  lambda c, s, v, m: f"Worst fall in a year {pct(v, 0)}"),
        Component("liquidity", 0.4, True, False,
                  lambda c, s: None if not c.p(s, "dollar_volume_20d") else math.log10(c.p(s, "dollar_volume_20d") or 1),
                  lambda c, s, v, m: f"{usd(10 ** v)} traded on a typical day"),
        Component("dilution", 0.4, False, False, lambda c, s: c.f(s, "dilution"),
                  lambda c, s, v, m: f"Share count {pct(v, 1)} in a year"),
    ]
    out: dict[str, DeskResult] = {}
    for s, (score, conf, parts) in score_components(ctx, symbols, comps).items():
        ev = ctx.events.get(s, {})
        alt = ctx.f(s, "altman_z")
        if score is not None:
            if alt is not None and alt < 1.8:
                score -= 15
                parts.append((-1.0, f"Altman Z {alt:.1f}: in the distress zone"))
            if ev.get("red_flags"):
                score -= 20
                parts.append((-1.0, f"Red flag: {ev['red_flags'][0]['text']}"))
            if ev.get("dilution_filings"):
                score -= 5
                parts.append((-0.5, f"{ev['dilution_filings']} share-registration or offering filing(s) in 90 days"))
            score = max(0.0, min(100.0, score))
        out[s] = DeskResult(score, conf, verdict(score, ("Low", "Below average", "Average", "Above average", "High")),
                            _top_reasons(parts),
                            {k: ctx.p(s, k) for k in ("vol_1y", "beta_1y", "max_drawdown_1y", "dollar_volume_20d")},
                            parts)
    return out


# ── 10. macro and sectors ────────────────────────────────────────────────────────────────────────
def macro(ctx: Context, symbols: Sequence[str]) -> dict[str, DeskResult]:
    sector_rank = percentiles(dict(ctx.sector_rs.items()), higher_is_better=True)
    regime = ctx.macro.get("regime", "RISK_ON")
    out: dict[str, DeskResult] = {}
    for s in symbols:
        sec = ctx.sectors.get(s, "Other")
        p = sector_rank.get(sec)
        parts: list[tuple[float, str]] = []
        if p is None:
            score, conf = 50.0, 0.3
        else:
            score, conf = 30 + p * 40, 1.0
            parts.append(((p - 0.5) * 2, f"{LABELS.get(sec, 'Its sector')} {pct(abs(ctx.sector_rs[sec]), 0, False)} "
                                         f"{'ahead of' if ctx.sector_rs[sec] > 0 else 'behind'} the S&P 500 over three months"))
        beta = ctx.p(s, "beta_1y")
        if regime == "RISK_OFF" and beta is not None:
            adj = -15 if beta > 1.3 else 10 if beta < 0.8 else 0
            score += adj
            if adj:
                parts.append((adj / 15, f"Market is risk-off and its beta is {beta:.2f}"))
        elif regime == "RISK_ON" and beta is not None and beta > 1.3:
            score += 5
        score = max(0.0, min(100.0, score))
        if ctx.macro.get("ust10y") is not None:
            parts.append((0.0, f"10-year Treasury {ctx.macro['ust10y']:.2f}%"
                               + ("" if ctx.macro.get("ust10y_change_3m") is None else
                                  f" ({ctx.macro['ust10y_change_3m']:+.2f} in three months)")))
        out[s] = DeskResult(score, conf, "Tailwind" if score >= 60 else "Headwind" if score <= 40 else "Neutral",
                            [t for _, t in sorted(parts, key=lambda x: -abs(x[0]))[:3]],
                            {"sector": LABELS.get(sec, "Other"), "sector_rs_3m": ctx.sector_rs.get(sec), "regime": regime}, parts)
    return out


# ── 11. options ──────────────────────────────────────────────────────────────────────────────────
def options(ctx: Context, symbols: Sequence[str]) -> dict[str, DeskResult]:
    def iv_vs_rv(c: Context, s: str) -> float | None:
        o, rv = c.options.get(s), c.p(s, "vol_20d")
        return None if not o or not o.get("iv30") or not rv else o["iv30"] / rv

    def rank_of(c: Context, s: str) -> float | None:
        o = c.options.get(s)
        if not o or not o.get("iv30"):
            return None
        r, _ = iv_rank(o.get("iv_history", []), o["iv30"])
        return r

    with_chain = [s for s in symbols if ctx.options.get(s, {}).get("iv30")]
    comps = [
        Component("iv_vs_rv", 1.0, False, False, iv_vs_rv,
                  lambda c, s, v, m: f"Implied volatility {v:.2f}× what the stock actually moved over 20 days"),
        Component("iv_rank", 0.8, False, False, rank_of, lambda c, s, v, m: f"IV rank {v:.0f} (0 = cheapest of its year)"),
        Component("term", 0.3, True, False, lambda c, s: c.options.get(s, {}).get("term_slope"),
                  lambda c, s, v, m: ("Term structure upward" if v > 0 else
                                      "Term structure inverted: the market expects the near term to be rougher")),
        Component("put_call", 0.3, False, False, lambda c, s: c.options.get(s, {}).get("put_call_volume"),
                  lambda c, s, v, m: f"Put/call volume {v:.2f}"),
    ]
    out: dict[str, DeskResult] = {}
    scored = score_components(ctx, with_chain, comps)
    for s in symbols:
        o = ctx.options.get(s)
        if s not in scored or not o:
            out[s] = DeskResult(None, 0.0, "No chain collected", ["Option chains are collected for the most-watched names"],
                                {}, [])
            continue
        score, conf, parts = scored[s]
        unusual = o.get("unusual", [])
        if unusual:
            u = unusual[0]
            parts.append((0.3 if u["kind"] == "call" else -0.3,
                          f"Unusual: {u['volume']:,} {u['expiration'][5:]} ${u['strike']:g} {u['kind']}s, "
                          f"{u['ratio']:.1f}× normal"))
        rank, pctile = iv_rank(o.get("iv_history", []), o["iv30"]) if o.get("iv30") else (None, None)
        labels = ("Cheap options", "Cheaper than usual", "Priced fairly", "Pricier than usual", "Expensive options")
        out[s] = DeskResult(score, conf, verdict(score, labels),
                            _top_reasons(parts), {"iv30": o.get("iv30"), "iv_rank": rank, "iv_percentile": pctile,
                                                  "iv_days": len(o.get("iv_history", [])), "term_slope": o.get("term_slope"),
                                                  "put_call_volume": o.get("put_call_volume"), "unusual": unusual[:5],
                                                  "expiries": o.get("expiries", [])}, parts)
    return out


# ── the committee ────────────────────────────────────────────────────────────────────────────────
def run_desks(ctx: Context, symbols: Sequence[str]) -> dict[str, dict[str, DeskResult]]:
    """desk → symbol → result, for every desk."""
    out: dict[str, dict[str, DeskResult]] = {}
    for name, fn in (("valuation", valuation), ("quality", quality), ("growth", growth), ("momentum", momentum),
                     ("earnings", earnings), ("insiders", insiders), ("shorts", shorts), ("risk", risk), ("macro", macro),
                     ("options", options)):
        out[name] = fn(ctx, symbols)
    fundamentals = {s: blend({d: out[d].get(s) for d in ("valuation", "quality", "growth")}, None) for s in symbols}
    out["crowd"] = crowd(ctx, symbols, fundamentals)
    return out


def blend(results: dict[str, DeskResult | None], weights: dict[str, float] | None) -> float | None:
    num = den = 0.0
    for name, r in results.items():
        if r is None or r.score is None:
            continue
        w = (weights or {}).get(name, 1.0) * max(r.confidence, 0.25)
        num += r.score * w
        den += w
    return None if den == 0 else num / den


def committee(desk_results: dict[str, DeskResult | None], weights: dict[str, float]) -> dict[str, Any]:
    score = blend({k: v for k, v in desk_results.items() if weights.get(k, 0) > 0}, weights)
    positive = [k for k, r in desk_results.items() if r is not None and r.score is not None and r.score >= 55]
    negative = [k for k, r in desk_results.items() if r is not None and r.score is not None and r.score <= 45]
    counted = [k for k, r in desk_results.items() if r is not None and r.score is not None]
    dissent = [] if score is None else [
        k for k in counted if weights.get(k, 0) > 0 and abs((desk_results[k].score or 0) - score) >= 30]   # type: ignore[union-attr]
    if score is None:
        label = "Not enough data"
    else:
        label = ("Bullish" if score >= 70 else "Bullish lean" if score >= 58 else "Neutral" if score >= 42 else
                 "Bearish lean" if score >= 30 else "Bearish")
    agreement = None if score is None or len(counted) < 3 else statistics.pstdev(
        [desk_results[k].score or 0 for k in counted])   # type: ignore[union-attr]
    return {"score": None if score is None else round(score, 1), "label": label, "positive": positive, "negative": negative,
            "desks_counted": len(counted), "dissent": dissent, "spread": None if agreement is None else round(agreement, 1)}


def bull_bear(desk_results: dict[str, DeskResult | None], n: int = 4) -> tuple[list[str], list[str]]:
    signals = [(strength, text, name) for name, r in desk_results.items() if r is not None and name not in ("crowd",)
               for strength, text in r.signals if strength != 0]
    bull = [t for s, t, _ in sorted(signals, key=lambda x: -x[0]) if s > 0.25][:n]
    bear = [t for s, t, _ in sorted(signals, key=lambda x: x[0]) if s < -0.25][:n]
    return bull, bear


def change_view(ctx: Context, s: str, desk_results: dict[str, DeskResult | None]) -> list[str]:
    """What would change the committee's view, from the thresholds that drive the current scores."""
    out = []
    fv = (desk_results.get("valuation") or DeskResult(None, 0, "", [])).facts.get("fair_value")
    if fv and fv.get("base"):
        out.append(f"A close above ${fv['base']:,.2f} (the base-case fair value): no longer cheap by these measures")
    g = ctx.f(s, "revenue_growth")
    if g is not None:
        out.append(f"The next quarterly report showing revenue growth below {pct(max(g - 0.07, 0.0), 0, False)} "
                   f"(it is {pct(g, 0, False)} now)")
    sma200 = ctx.p(s, "sma200")
    if sma200:
        out.append(f"A close below ${sma200:,.2f}, its 200-day average")
    ins = ctx.insiders.get(s)
    if ins and ins.get("buy_value"):
        out.append("Insiders selling on the open market")
    sp = ctx.shorts.get(s, {}).get("short_pct")
    if sp is not None:
        out.append(f"Short interest rising above {pct(sp * 1.5, 0, False)} of shares")
    return out[:4]


def method_content() -> dict[str, Any]:
    """The frozen methodology (versioned by its hash)."""
    return {"version": METHOD_VERSION, "data_contract": "stocks-reliability-v2",
            "desks": list(DESKS), "default_weights": DEFAULT_WEIGHTS,
            "equity_risk_premium": EQUITY_RISK_PREMIUM, "terminal_growth": TERMINAL_GROWTH,
            "verdict_bands": [75, 60, 40, 25], "valuation_in_sector": True, "min_sector_group": 10,
            "insider_window_days": 90, "short_publication_lag_days": 12, "earnings_recent_days": 75}


def method_sha256() -> str:
    return sha256_hex(canonical_json(method_content()))


__all__ = ["DEFAULT_WEIGHTS", "DESKS", "DESK_LABELS", "METHOD_VERSION", "Context", "DeskResult", "blend", "bull_bear",
           "change_view", "committee", "degen_meter", "fair_value", "method_content", "method_sha256", "percentiles", "run_desks",
           "squeeze_score"]
