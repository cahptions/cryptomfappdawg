"""The PAPER strategies, preregistered: each states its hypothesis, its rule and its exits before it trades, is frozen
by version, and trades its own account. Baselines (hold SPY, random stocks, random calls) are what the others must
beat. Options strategies are defined-risk only: long calls, puts and straddles, and debit spreads; nothing is ever
sold naked.

A rule reads one day's analysis (``desk.stocks.analysis.Day``) and returns candidates; ``desk.stocks.paper.engine``
sizes them, turns them into orders and manages the exits.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from statistics import median
from typing import Any

from desk.core.hashing import canonical_json, sha256_hex
from desk.stocks import options as opt
from desk.stocks.analysis import Day
from desk.stocks.contracts import OptionQuote
from desk.stocks.lists import BY_ID
from desk.stocks.paper.fills import fair_spread

MIN_OPEN_INTEREST = 100


@dataclass(frozen=True)
class Strategy:
    id: str                          # the paper account's name too
    version: int
    title: str
    asset: str                       # stock | option
    style: str                       # hold | rebalance | event
    hypothesis: str
    params: tuple[tuple[str, Any], ...]
    baseline: bool = False
    learned: bool = False

    @property
    def p(self) -> dict[str, Any]:
        return dict(self.params)

    def content(self) -> dict[str, Any]:
        return {"id": self.id, "version": self.version, "asset": self.asset, "style": self.style,
                "hypothesis": self.hypothesis, "params": self.p, "baseline": self.baseline, "learned": self.learned}

    @property
    def spec_sha256(self) -> str:
        return sha256_hex(canonical_json(self.content()))


@dataclass
class Candidate:
    symbol: str
    score: float
    reasons: list[str]
    evidence: dict[str, Any]


Rule = Callable[[Day, dict[str, Any]], list[Candidate]]


def _s(day: Day, desk: str, s: str) -> float | None:
    r = day.results.get(desk, {}).get(s)
    return None if r is None else r.score


def _px(day: Day, s: str, key: str) -> float | None:
    return day.ctx.price.get(s, {}).get(key)


def _evidence(day: Day, s: str) -> dict[str, Any]:
    c = day.committee.get(s) or {}
    return {"committee": c.get("score"), "close": _px(day, s, "close"),
            **{d: _s(day, d, s) for d in ("valuation", "quality", "growth", "momentum", "earnings", "shorts", "options")}}


# ── stock rules ──────────────────────────────────────────────────────────────────────────────────
def from_list(day: Day, p: dict[str, Any]) -> list[Candidate]:
    rows = BY_ID[p["list"]].select(day)
    return [Candidate(r["symbol"], float(len(rows) - i), [r["reason"]], _evidence(day, r["symbol"]))
            for i, r in enumerate(rows)]


def combo(day: Day, p: dict[str, Any]) -> list[Candidate]:
    """Names clearing every desk's floor, ranked by the weighted sum of those desks (``desks``: {desk: floor},
    ``weights``: {desk: weight})."""
    out = []
    for s in day.ctx.symbols:
        scores = {d: _s(day, d, s) for d in p["desks"]}
        if any(v is None or v < floor for (d, floor), v in zip(p["desks"].items(), scores.values(), strict=True)):
            continue
        if p.get("no_red_flags", True) and day.ctx.events.get(s, {}).get("red_flags"):
            continue
        total = sum(p.get("weights", {}).get(d, 1.0) * (v or 0) for d, v in scores.items())
        out.append(Candidate(s, total, [", ".join(f"{d} {v:.0f}" for d, v in scores.items())], _evidence(day, s)))
    return sorted(out, key=lambda c: (-c.score, c.symbol))


def blend(day: Day, p: dict[str, Any]) -> list[Candidate]:
    """The lab's rule: ranked by the average of ``desks`` (names missing any of them are left out)."""
    out = []
    for s in day.ctx.symbols:
        scores = [_s(day, d, s) for d in p["desks"]]
        if any(v is None for v in scores):
            continue
        avg = sum(v or 0 for v in scores) / len(scores)
        out.append(Candidate(s, avg, [", ".join(f"{d} {v:.0f}" for d, v in zip(p["desks"], scores, strict=True))],
                             _evidence(day, s)))
    return sorted(out, key=lambda c: (-c.score, c.symbol))


def committee_rank(day: Day, p: dict[str, Any]) -> list[Candidate]:
    out = []
    for s in day.ctx.symbols:
        c = day.committee.get(s) or {}
        if c.get("score") is None or c.get("desks_counted", 0) < p.get("min_desks", 6):
            continue
        out.append(Candidate(s, float(c["score"]), [f"Committee {c['score']:.0f} ({c['label']})"], _evidence(day, s)))
    return sorted(out, key=lambda c: (-c.score, c.symbol))


def random_names(day: Day, p: dict[str, Any]) -> list[Candidate]:
    """A reproducible random draw (seeded by the strategy and the day) from the day's universe."""
    rng = random.Random(f"{p['seed']}:{day.asof.isoformat()}")      # noqa: S311 - a reproducible draw, not a secret
    pool = sorted(day.ctx.symbols)
    picks = rng.sample(pool, min(p["n"], len(pool)))
    return [Candidate(s, 0.0, ["Random pick (baseline)"], _evidence(day, s)) for s in picks]


def benchmark(day: Day, p: dict[str, Any]) -> list[Candidate]:
    return [Candidate(p["symbol"], 1.0, [f"Hold {p['symbol']} (baseline)"], {})]


def earnings_drift(day: Day, p: dict[str, Any]) -> list[Candidate]:
    """Bought after results the market liked: post-earnings-announcement drift."""
    out = []
    for s in day.ctx.symbols:
        rel = day.ctx.events.get(s, {}).get("earnings", [])
        if not rel or rel[-1].get("abnormal") is None:
            continue
        e = rel[-1]
        age = (day.asof - date.fromisoformat(e["session"])).days
        if age <= p["within_days"] and e["abnormal"] >= p["min_abnormal"] and (_s(day, "earnings", s) or 0) >= p["min_desk"]:
            out.append(Candidate(s, e["abnormal"], [f"{e['abnormal']:+.1%} vs the market on results day ({e['session']})"],
                                 {**_evidence(day, s), "reaction": e}))
    return sorted(out, key=lambda c: (-c.score, c.symbol))


def insider_cluster(day: Day, p: dict[str, Any]) -> list[Candidate]:
    out = []
    for s in day.ctx.symbols:
        i = day.ctx.insiders.get(s)
        cap = (day.ctx.fund.get(s) or {}).get("market_cap")
        if not i or i["buy_value"] <= i["sell_value"]:
            continue
        bps = i["buy_value"] / cap * 1e4 if cap else 0.0
        if i["cluster"] >= p["min_buyers"] or bps >= p["min_bps"]:
            out.append(Candidate(s, bps + 10 * i["cluster"], [f"{i['cluster']} insiders bought; {bps:.1f} bp of market value"],
                                 {**_evidence(day, s), "insiders": {k: i[k] for k in ("buy_value", "sell_value", "cluster")}}))
    return sorted(out, key=lambda c: (-c.score, c.symbol))


def breakout(day: Day, p: dict[str, Any]) -> list[Candidate]:
    out = []
    for s in day.ctx.symbols:
        dist, rv, m = _px(day, s, "dist_high_52w"), _px(day, s, "rel_volume"), _s(day, "momentum", s)
        if dist is not None and dist >= -0.003 and (rv or 0) >= p["min_rel_volume"] and (m or 0) >= p["min_momentum"]:
            out.append(Candidate(s, m or 0, [f"New 52-week high on {rv:.1f}× volume; momentum {m:.0f}"], _evidence(day, s)))
    return sorted(out, key=lambda c: (-c.score, c.symbol))


def squeeze(day: Day, p: dict[str, Any]) -> list[Candidate]:
    out = []
    for s in day.ctx.symbols:
        r = day.results.get("shorts", {}).get(s)
        sq = None if r is None else r.facts.get("squeeze")
        if sq is not None and sq >= p["min_squeeze"] and (_px(day, s, "ret_5d") or 0) > 0:
            out.append(Candidate(s, float(sq), [f"Squeeze score {sq:.0f}, up {_px(day, s, 'ret_5d') or 0:.1%} in a week"],
                                 _evidence(day, s)))
    return sorted(out, key=lambda c: (-c.score, c.symbol))


# ── options rules (candidates; the contracts are chosen from the day's chain) ───────────────────
def _iv_ratio(day: Day, s: str) -> float | None:
    o, rv = day.ctx.options.get(s) or {}, _px(day, s, "vol_20d")
    return None if not o.get("iv30") or not rv else float(o["iv30"]) / rv


def cheap_iv_bulls(day: Day, p: dict[str, Any]) -> list[Candidate]:
    out = []
    for s in day.ctx.symbols:
        ratio, c, m = _iv_ratio(day, s), (day.committee.get(s) or {}).get("score"), _s(day, "momentum", s)
        if ratio is None or c is None or m is None:
            continue
        if ratio <= p["max_iv_ratio"] and c >= p["min_committee"] and m >= p["min_momentum"]:
            out.append(Candidate(s, c - 20 * ratio, [f"Committee {c:.0f}, momentum {m:.0f}, IV {ratio:.2f}× realized"],
                                 {**_evidence(day, s), "iv_ratio": ratio}))
    return sorted(out, key=lambda c: (-c.score, c.symbol))


def momentum_bulls(day: Day, p: dict[str, Any]) -> list[Candidate]:
    out = []
    for s in day.ctx.symbols:
        m, above = _s(day, "momentum", s), _px(day, s, "above_sma200")
        if (day.ctx.options.get(s) or {}).get("iv30") and m is not None and m >= p["min_momentum"] and (above or -1) > 0:
            out.append(Candidate(s, m, [f"Momentum {m:.0f}, {above:.0%} above its 200-day average"], _evidence(day, s)))
    return sorted(out, key=lambda c: (-c.score, c.symbol))


def bears(day: Day, p: dict[str, Any]) -> list[Candidate]:
    out = []
    for s in day.ctx.symbols:
        v, m, above = _s(day, "valuation", s), _s(day, "momentum", s), _px(day, s, "above_sma200")
        if not (day.ctx.options.get(s) or {}).get("iv30") or v is None or m is None or above is None:
            continue
        if v <= p["max_valuation"] and m <= p["max_momentum"] and above < 0:
            out.append(Candidate(s, (100 - v) + (100 - m), [f"Valuation {v:.0f}, momentum {m:.0f}, below its 200-day"],
                                 _evidence(day, s)))
    return sorted(out, key=lambda c: (-c.score, c.symbol))


def earnings_vol(day: Day, p: dict[str, Any]) -> list[Candidate]:
    """Results due soon, and the options price a smaller move than this stock has usually made on results day."""
    out = []
    for s in day.ctx.symbols:
        ev = day.ctx.events.get(s, {})
        window = ev.get("next_window")
        past = [abs(e["reaction"]) for e in ev.get("earnings", []) if e.get("reaction") is not None]
        o = day.ctx.options.get(s) or {}
        if not window or len(past) < p["min_reports"] or not o.get("expiries"):
            continue
        start, end = date.fromisoformat(window[0]), date.fromisoformat(window[1])
        centre = start + (end - start) / 2
        if not p["min_days"] <= (centre - day.asof).days <= p["max_days"]:
            continue
        expiry = next((e for e in o["expiries"] if date.fromisoformat(e["expiration"]) > end and e.get("expected_move")), None)
        typical = median(past[-8:])
        if expiry and expiry["expected_move"] <= typical * p["max_priced_vs_typical"]:
            out.append(Candidate(s, typical / expiry["expected_move"],
                                 [f"Results expected {centre:%b %-d}; options price ±{expiry['expected_move']:.1%}, "
                                  f"it has moved a median {typical:.1%} on results days"],
                                 {**_evidence(day, s), "expected_move": expiry["expected_move"], "typical_move": typical,
                                  "window": window, "after": expiry["expiration"]}))
    return sorted(out, key=lambda c: (-c.score, c.symbol))


def squeeze_options(day: Day, p: dict[str, Any]) -> list[Candidate]:
    return [c for c in squeeze(day, p) if (day.ctx.options.get(c.symbol) or {}).get("iv30")]


def unusual_calls(day: Day, p: dict[str, Any]) -> list[Candidate]:
    out = []
    for s in day.ctx.symbols:
        for u in (day.ctx.options.get(s) or {}).get("unusual", [])[:3]:
            premium = u["volume"] * (u.get("mid") or 0) * 100
            if (u["kind"] == "call" and u["volume"] >= p["min_volume"] and u["ratio"] >= p["min_ratio"]
                    and premium >= p["min_premium"]):
                out.append(Candidate(s, premium, [f"{u['volume']:,} {u['expiration']} ${u['strike']:g} calls, "
                                                  f"{u['ratio']:.1f}× normal (${premium:,.0f})"],
                                     {**_evidence(day, s), "follow": u}))
                break
    return sorted(out, key=lambda c: (-c.score, c.symbol))


def random_optioned(day: Day, p: dict[str, Any]) -> list[Candidate]:
    rng = random.Random(f"{p['seed']}:{day.asof.isoformat()}")      # noqa: S311 - a reproducible draw, not a secret
    pool = sorted(s for s in day.ctx.symbols if (day.ctx.options.get(s) or {}).get("iv30"))
    return [Candidate(s, 0.0, ["Random pick (baseline)"], _evidence(day, s)) for s in rng.sample(pool, min(p["n"], len(pool)))]


RULES: dict[str, Rule] = {
    "list": from_list, "combo": combo, "blend": blend, "committee": committee_rank, "random": random_names,
    "benchmark": benchmark,
    "earnings_drift": earnings_drift, "insider_cluster": insider_cluster, "breakout": breakout, "squeeze": squeeze,
    "cheap_iv_bulls": cheap_iv_bulls, "momentum_bulls": momentum_bulls, "bears": bears, "earnings_vol": earnings_vol,
    "squeeze_options": squeeze_options, "unusual_calls": unusual_calls, "random_optioned": random_optioned,
}


def candidates(strategy: Strategy, day: Day) -> list[Candidate]:
    p = strategy.p
    return RULES[p["rule"]](day, p)


# ── choosing contracts ───────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Pick:
    quote: OptionQuote
    iv: float
    delta: float


def _expiry(chains: dict[date, list[OptionQuote]], today: date, lo: int, hi: int, after: date | None = None) -> date | None:
    choices = [e for e in chains if lo <= (e - today).days <= hi and (after is None or e > after)]
    if not choices:
        return None
    return min(choices, key=lambda e: abs((e - today).days - (lo + hi) / 2))


def contract(chains: dict[date, list[OptionQuote]], s: float, today: date, r: float, kind: str, delta: float,
             lo: int, hi: int, *, expiration: date | None = None) -> Pick | None:
    """The fairly-quoted contract with open interest >= 100 whose delta is nearest ``delta`` (absolute), in the expiry
    nearest the middle of ``lo``..``hi`` days (or ``expiration``)."""
    e = expiration or _expiry(chains, today, lo, hi)
    if e is None or e not in chains:
        return None
    t = opt.year_fraction(today, e)
    best: tuple[float, Pick] | None = None
    for q in chains[e]:
        mid = q.mid
        if q.kind != kind or mid is None or not fair_spread(q) or q.open_interest < MIN_OPEN_INTEREST:
            continue
        iv = opt.implied_vol(kind, mid, s, q.strike, t, r)
        if iv is None:
            continue
        d = opt.greeks(kind, s, q.strike, t, iv, r).delta
        gap = abs(abs(d) - delta)
        if best is None or gap < best[0]:
            best = (gap, Pick(q, iv, d))
    return None if best is None else best[1]


def leg(pick: Pick, quantity: int) -> dict[str, Any]:
    q = pick.quote
    price = q.ask if quantity > 0 else q.bid
    return {"occ": q.occ, "kind": q.kind, "strike": q.strike, "expiration": q.expiration.isoformat(), "quantity": quantity,
            "decision_price": price, "decision_mid": q.mid, "entry_iv": round(pick.iv, 4), "entry_delta": round(pick.delta, 3)}


def build_structure(structure: str, chains: dict[date, list[OptionQuote]], s: float, today: date, r: float,
                    p: dict[str, Any], evidence: dict[str, Any]) -> list[dict[str, Any]] | None:
    """One unit of a structure (quantity 1 per long leg); None when the chain can't supply it fairly."""
    lo, hi = p["dte"]
    if structure in ("long_call", "long_put"):
        follow = evidence.get("follow")
        if follow:                                    # the unusual contract itself, when it's fairly quoted
            e = date.fromisoformat(follow["expiration"])
            q = next((x for x in chains.get(e, []) if x.occ == follow["occ"]), None)
            if q is None or not fair_spread(q) or (e - today).days < lo:
                return None
            iv = opt.implied_vol(q.kind, q.mid or 0, s, q.strike, opt.year_fraction(today, e), r)
            if iv is None:
                return None
            return [leg(Pick(q, iv, opt.greeks(q.kind, s, q.strike, opt.year_fraction(today, e), iv, r).delta), 1)]
        pick = contract(chains, s, today, r, "call" if structure == "long_call" else "put", p["delta"], lo, hi)
        return None if pick is None else [leg(pick, 1)]
    if structure in ("bull_call_spread", "bear_put_spread"):
        kind = "call" if structure == "bull_call_spread" else "put"
        near = contract(chains, s, today, r, kind, p["delta"], lo, hi)
        if near is None:
            return None
        far = contract(chains, s, today, r, kind, p["short_delta"], lo, hi, expiration=near.quote.expiration)
        if far is None or far.quote.strike == near.quote.strike or (
                (far.quote.strike < near.quote.strike) if kind == "call" else (far.quote.strike > near.quote.strike)):
            return None
        return [leg(near, 1), leg(far, -1)]
    if structure == "long_straddle":
        after = date.fromisoformat(evidence["window"][1]) if evidence.get("window") else None
        straddle_expiry = _expiry(chains, today, lo, hi, after)
        if straddle_expiry is None:
            return None
        e = straddle_expiry
        call = contract(chains, s, today, r, "call", 0.5, lo, hi, expiration=e)
        put = contract(chains, s, today, r, "put", 0.5, lo, hi, expiration=e)
        if call is None or put is None:
            return None
        if call.quote.strike != put.quote.strike:
            same = next((x for x in chains[e] if x.kind == "put" and x.strike == call.quote.strike and fair_spread(x)), None)
            if same is None or same.mid is None:
                return None
            iv = opt.implied_vol("put", same.mid, s, same.strike, opt.year_fraction(today, e), r)
            if iv is None:
                return None
            put = Pick(same, iv, opt.greeks("put", s, same.strike, opt.year_fraction(today, e), iv, r).delta)
        return [leg(call, 1), leg(put, 1)]
    raise ValueError(f"unknown structure {structure}")


def _P(**kw: Any) -> tuple[tuple[str, Any], ...]:
    return tuple(sorted(kw.items()))


STOCK_EXITS = {"stop": 0.15}
STRATEGIES: tuple[Strategy, ...] = (
    # baselines
    Strategy("spy_hold", 1, "Hold SPY", "stock", "hold", "The market itself: what every stock strategy must beat",
             _P(rule="benchmark", symbol="SPY", weight=1.0), baseline=True),
    Strategy("random_10", 1, "10 random stocks", "stock", "rebalance",
             "Ten liquid names drawn at random each month: what picking must beat", _P(rule="random", n=10, seed="random_10"),
             baseline=True),
    # the desk's own lists and committee, monthly
    Strategy("committee_top", 1, "Committee's top 10", "stock", "rebalance",
             "The ten names the desks agree on most outperform the market", _P(rule="committee", n=10, min_desks=6)),
    Strategy("undervalued", 1, "Undervalued list", "stock", "rebalance",
             "Cheap, not-junk stocks outperform (value with a quality floor)", _P(rule="list", list="undervalued", n=10)),
    Strategy("quality_fair", 1, "Quality at a fair price", "stock", "rebalance",
             "Great businesses not priced for perfection outperform", _P(rule="list", list="quality_fair", n=10)),
    Strategy("momentum", 1, "Momentum leaders", "stock", "rebalance",
             "The strongest 12-month trends keep going for a while", _P(rule="list", list="momentum", n=10)),
    # combinations (novel)
    Strategy("value_momentum", 1, "Value + momentum", "stock", "rebalance",
             "Cheap stocks that have started to work beat either signal alone",
             _P(rule="combo", desks={"valuation": 60, "momentum": 60}, n=10)),
    Strategy("quality_momentum", 1, "Quality + momentum", "stock", "rebalance",
             "Strong businesses in strong trends beat the market with smaller drawdowns",
             _P(rule="combo", desks={"quality": 60, "momentum": 60}, n=10)),
    Strategy("cheap_catalyst", 1, "Cheap with a catalyst", "stock", "rebalance",
             "Cheap stocks whose last results the market liked re-rate faster",
             _P(rule="combo", desks={"valuation": 65, "earnings": 60, "quality": 40}, n=10)),
    # events
    Strategy("earnings_drift", 1, "Post-earnings drift", "stock", "event",
             "Stocks that beat on results day keep drifting the same way for weeks",
             _P(rule="earnings_drift", within_days=5, min_abnormal=0.03, min_desk=60, hold=60, stop=0.12, size=0.1,
                max_open=10)),
    Strategy("insider_cluster", 1, "Insider clusters", "stock", "event",
             "Several insiders buying with their own money is information",
             _P(rule="insider_cluster", min_buyers=3, min_bps=5.0, hold=126, stop=0.20, size=0.1, max_open=10)),
    Strategy("breakout", 1, "52-week-high breakouts", "stock", "event",
             "New highs on heavy volume keep running (with a trailing stop)",
             _P(rule="breakout", min_rel_volume=1.5, min_momentum=70, hold=42, trail=0.10, size=0.1, max_open=10)),
    Strategy("squeeze_runner", 1, "Squeeze runners", "stock", "event",
             "Heavily shorted stocks already rising keep squeezing for a couple of weeks",
             _P(rule="squeeze", min_squeeze=60, hold=10, stop=0.10, size=0.05, max_open=5)),
    # options: baselines first
    Strategy("random_calls", 1, "Random calls", "option", "event",
             "At-the-money calls on random optioned names: what any call-buying strategy must beat",
             _P(rule="random_optioned", n=1, seed="random_calls", structure="long_call", delta=0.5, dte=[30, 60],
                premium=0.02, take_profit=1.0, stop=-0.5, exit_dte=21, max_open=5), baseline=True),
    Strategy("cheap_iv_calls", 1, "Calls when IV is cheap", "option", "event",
             "Calls on names the desks like, bought when options price less movement than the stock delivers",
             _P(rule="cheap_iv_bulls", max_iv_ratio=1.05, min_committee=60, min_momentum=55, structure="long_call",
                delta=0.5, dte=[30, 60], premium=0.02, take_profit=1.0, stop=-0.5, exit_dte=21, max_open=5)),
    Strategy("momentum_call_spreads", 1, "Momentum call spreads", "option", "event",
             "Bull call spreads on trend leaders capture the trend at a defined, smaller cost",
             _P(rule="momentum_bulls", min_momentum=80, structure="bull_call_spread", delta=0.55, short_delta=0.30,
                dte=[30, 60], premium=0.02, take_profit=0.75, stop=-0.5, exit_dte=14, max_open=5)),
    Strategy("bear_put_spreads", 1, "Bear put spreads", "option", "event",
             "Expensive stocks in downtrends keep falling; put spreads profit at a defined cost",
             _P(rule="bears", max_valuation=30, max_momentum=35, structure="bear_put_spread", delta=0.5, short_delta=0.25,
                dte=[30, 60], premium=0.02, take_profit=0.75, stop=-0.5, exit_dte=14, max_open=5)),
    Strategy("earnings_straddles", 1, "Cheap earnings straddles", "option", "event",
             "When options price a smaller results-day move than the stock usually makes, the straddle is cheap",
             _P(rule="earnings_vol", min_reports=4, min_days=2, max_days=10, max_priced_vs_typical=0.85,
                structure="long_straddle", dte=[3, 30], premium=0.02, take_profit=1.0, stop=-0.6, exit_after_window=1,
                exit_dte=1, max_open=5)),
    Strategy("squeeze_calls", 1, "Squeeze calls", "option", "event",
             "Out-of-the-money calls on squeezing stocks pay off big when the squeeze runs",
             _P(rule="squeeze_options", min_squeeze=60, structure="long_call", delta=0.30, dte=[14, 45], premium=0.01,
                take_profit=2.0, stop=-0.6, exit_dte=7, max_open=3)),
    Strategy("unusual_follow", 1, "Follow unusual call buying", "option", "event",
             "Large, unusual call buying knows something; buying the same contract follows it",
             _P(rule="unusual_calls", min_volume=1000, min_ratio=5.0, min_premium=100_000, structure="long_call", dte=[10, 90],
                premium=0.01, take_profit=1.0, stop=-0.5, exit_dte=5, max_open=3)),
)
BY_STRATEGY = {s.id: s for s in STRATEGIES}

__all__ = ["BY_STRATEGY", "RULES", "STRATEGIES", "Candidate", "Pick", "Strategy", "build_structure", "candidates",
           "contract", "leg"]
