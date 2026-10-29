"""The daily lists (Robinhood-style). Each is a frozen rule with a version; every day's list is appended to
stocks-research.db once and never changed, so its track record cannot be rewritten (docs/STOCKS_METHOD.md)."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from desk.core.hashing import canonical_json, sha256_hex
from desk.stocks.activity import activity_failure, market_activity, options_activity
from desk.stocks.analysis import Day
from desk.stocks.desks import DeskResult, pct, usd

LIST_SIZE = 25


@dataclass(frozen=True)
class ListSpec:
    id: str
    title: str
    blurb: str
    rule: str
    version: int
    badge: str                 # the UI's icon key
    tracked: bool              # does the list make a claim worth a track record?
    select: Callable[[Day], list[dict[str, Any]]]
    claim: str = "describe"    # outperform / underperform (tested, Holm-adjusted) or describe (what happened next)
    history: bool = True       # can it be rebuilt for past months? (crowd and options data exist only from today on)

    def content(self) -> dict[str, Any]:
        return {"id": self.id, "version": self.version, "rule": self.rule, "size": LIST_SIZE, "claim": self.claim}

    @property
    def spec_sha256(self) -> str:
        return sha256_hex(canonical_json(self.content()))


def _r(day: Day, desk: str, s: str) -> DeskResult | None:
    return day.results.get(desk, {}).get(s)


def _score(day: Day, desk: str, s: str) -> float | None:
    r = _r(day, desk, s)
    return None if r is None else r.score


def _reason(day: Day, desk: str, s: str, n: int = 1) -> str:
    r = _r(day, desk, s)
    return "" if r is None or not r.reasons else "; ".join(r.reasons[:n])


def _px(day: Day, s: str, key: str) -> float | None:
    return day.ctx.price.get(s, day.market_price.get(s, {})).get(key)


def _verdict(day: Day, desk: str, s: str) -> str:
    r = _r(day, desk, s)
    return "?" if r is None else r.verdict.lower()


def _confident(day: Day, desk: str, s: str, minimum: float = 0.5) -> bool:
    r = _r(day, desk, s)
    return r is not None and r.confidence >= minimum


def _row(day: Day, s: str, value: float | None, reason: str) -> dict[str, Any]:
    return {"symbol": s, "value": None if value is None or math.isnan(value) else round(float(value), 4), "reason": reason,
            "price": _px(day, s, "close")}


def _top(day: Day, keyed: list[tuple[float, str, str]], reverse: bool = True) -> list[dict[str, Any]]:
    keyed = [k for k in keyed if k[0] is not None and not math.isnan(k[0])]
    keyed.sort(key=lambda k: (-k[0], k[1]) if reverse else (k[0], k[1]))
    return [_row(day, s, v, why) for v, s, why in keyed[:LIST_SIZE]]


def _no_red_flags(day: Day, s: str) -> bool:
    return not day.ctx.events.get(s, {}).get("red_flags")


# ── selections ───────────────────────────────────────────────────────────────────────────────────
def trending(day: Day) -> list[dict[str, Any]]:
    """Market attention only. Missing options do not require a social score or manufacture a zero observation."""
    prices = day.market_price or day.ctx.price
    observed = {}
    for s in prices:
        o = day.ctx.options.get(s, {})
        a = o.get("activity") or {}
        if o.get("day") == day.asof.isoformat() and activity_failure(a, day.asof) is None:
            observed[s] = a
    premiums = [float(a["premium_proxy"]) for a in observed.values()]
    out = []
    for s, price in prices.items():
        rv, r1, vol = (price.get(k) for k in ("rel_volume", "ret_1d", "vol_20d"))
        rv = float(rv) if rv is not None and math.isfinite(rv) and rv >= 0 else None
        r1 = float(r1) if r1 is not None and math.isfinite(r1) else None
        z = abs(r1)/(float(vol)/math.sqrt(252)) if r1 is not None and vol and math.isfinite(vol) and vol>0 else None
        if (rv is None or rv < 1.5) and (z is None or z < 2):
            continue
        components = []
        bits = []
        if rv is not None:
            components.append((.45,min(rv/5,1)))
            bits.append(f"volume {rv:.1f}× normal")
        if z is not None:
            components.append((.35,min(z/4,1)))
        if r1 is not None:
            bits.append(f"{pct(r1, 1)} today"+(f" ({z:.1f}σ)" if z is not None else ""))
        if s in observed:
            a = observed[s]
            premium = float(a["premium_proxy"])
            percentile = (sum(p<premium for p in premiums)+.5*sum(p==premium for p in premiums))/len(premiums)
            components.append((.20,percentile))
            bits.append(f"{a['volume']:,} sampled option contracts; {usd(premium)} midpoint premium proxy")
        else:
            bits.append("qualified same-session options unavailable; ranked on stock data")
        value = 100*sum(weight*signal for weight,signal in components)/sum(weight for weight,_ in components)
        out.append((value,s,"; ".join([*bits,"Market activity, not direction; no social input"])))
    return _top(day, out)


def wsb(day: Day) -> list[dict[str, Any]]:
    """The source's observed rolling-24h WSB counts, separate from market ranking and sentiment forecasts."""
    out = []
    for s in day.market_price or day.ctx.price:
        source = (day.ctx.crowd.get(s) or {}).get("wsb") or {}
        mentions = source.get("mentions")
        if mentions is None or not math.isfinite(mentions) or mentions <= 0:
            continue
        before = source.get("mentions_24h_ago")
        change = mentions/before-1 if before and before>0 else None
        bits = [f"{mentions:,} mentions on r/wallstreetbets in 24 hours"]
        if source.get("rank") is not None:
            bits.append(f"source rank #{source['rank']}")
        if change is not None:
            bits.append(f"{pct(change,0)} vs prior 24 hours")
        elif before == 0:
            bits.append("previously zero mentions")
        if source.get("upvotes") is not None:
            bits.append(f"{source['upvotes']:,} reported upvotes")
        row = _row(day,s,float(mentions),"; ".join(bits))
        row.update(mentions=mentions,mention_change=change,source_rank=source.get("rank"),
                   upvotes=source.get("upvotes"),observed_hour=source.get("observed_hour"))
        out.append(row)
    return sorted(out,key=lambda r:(-r["mentions"],r["source_rank"] if r["source_rank"] is not None else math.inf,
                                   r["symbol"]))[:LIST_SIZE]


def gainers(day: Day) -> list[dict[str, Any]]:
    out = []
    for s in day.ctx.symbols:
        r1, rv = _px(day, s, "ret_1d"), _px(day, s, "rel_volume")
        if r1 is not None:
            out.append((r1, s, f"{pct(r1, 1)} today" + (f"; volume {rv:.1f}× normal" if (rv or 0) >= 1.5 else "")))
    return _top(day, out)

def losers(day: Day) -> list[dict[str, Any]]:
    out = [(r1, s, f"{pct(r1, 1)} today") for s in day.ctx.symbols if (r1 := _px(day, s, "ret_1d")) is not None]
    return _top(day, out, reverse=False)

def undervalued(day: Day) -> list[dict[str, Any]]:
    out = []
    for s in day.ctx.symbols:
        v, q = _score(day, "valuation", s) or 0, _score(day, "quality", s) or 0
        if v >= 70 and q >= 40 and _no_red_flags(day, s) and _confident(day, "valuation", s):
            out.append((v + 0.1 * q, s, f"{_reason(day, 'valuation', s)}; quality {_verdict(day, 'quality', s)}"))
    return _top(day, out)

def overvalued(day: Day) -> list[dict[str, Any]]:
    out = []
    for s in day.ctx.symbols:
        v = _score(day, "valuation", s)
        if v is not None and v <= 25 and _confident(day, "valuation", s):
            out.append((v, s, _reason(day, "valuation", s, 2)))
    return _top(day, out, reverse=False)

def quality_fair(day: Day) -> list[dict[str, Any]]:
    out = []
    for s in day.ctx.symbols:
        q, v = _score(day, "quality", s) or 0, _score(day, "valuation", s) or 0
        if q >= 70 and v >= 45 and _no_red_flags(day, s):
            out.append((q + v, s, f"{_reason(day, 'quality', s)}; valuation {_verdict(day, 'valuation', s)}"))
    return _top(day, out)

def momentum_leaders(day: Day) -> list[dict[str, Any]]:
    out = []
    for s in day.ctx.symbols:
        m = _score(day, "momentum", s) or 0
        if m >= 75 and (_px(day, s, "above_sma200") or -1) > 0:
            out.append((m, s, _reason(day, "momentum", s, 2)))
    return _top(day, out)

def earnings_week(day: Day) -> list[dict[str, Any]]:
    horizon = day.asof + timedelta(days=7)
    out = []
    for s in day.ctx.symbols:
        window = day.ctx.events.get(s, {}).get("next_window")
        if not window:
            continue
        start, end = date.fromisoformat(window[0]), date.fromisoformat(window[1])
        center = start + (end - start) / 2
        if start <= horizon and end >= day.asof and day.asof <= center <= horizon + timedelta(days=4):
            move = _expected_move(day, s, center)
            why = f"Expected {start:%b %-d}–{end:%b %-d} (estimated from its past reports)"
            if move:
                why += f"; options price a {pct(move, 1, False)} move"
            out.append((float(-(center - day.asof).days), s, why))
    return _top(day, out)

def _expected_move(day: Day, s: str, after: date) -> float | None:
    for e in day.ctx.options.get(s, {}).get("expiries", []):
        if date.fromisoformat(e["expiration"]) >= after and e.get("expected_move"):
            return float(e["expected_move"])
    return None


def just_reported(day: Day) -> list[dict[str, Any]]:
    cutoff = day.asof - timedelta(days=8)
    out = []
    for s in day.ctx.symbols:
        rel = day.ctx.events.get(s, {}).get("earnings", [])
        if rel and date.fromisoformat(rel[-1]["session"]) >= cutoff and rel[-1].get("abnormal") is not None:
            e = rel[-1]
            out.append((abs(e["abnormal"]), s, f"{pct(e['abnormal'], 1)} vs the market on results day "
                                               f"({date.fromisoformat(e['session']):%b %-d})"))
    return _top(day, out)


def insider_buying(day: Day) -> list[dict[str, Any]]:
    out = []
    for s in day.ctx.symbols:
        i = day.ctx.insiders.get(s)
        if not i or i["buy_value"] <= 0 or i["buy_value"] <= i["sell_value"]:
            continue
        cap = day.ctx.fund.get(s, {}).get("market_cap")
        value = i["buy_value"] / cap if cap else 0.0
        out.append((value + (1 if i["cluster"] >= 3 else 0), s, _reason(day, "insiders", s, 2)))
    return _top(day, out)


def squeeze_watch(day: Day) -> list[dict[str, Any]]:
    out = []
    for s in day.ctx.symbols:
        sq = day.ctx.shorts.get(s) or {}
        r = _r(day, "shorts", s)
        score = None if r is None else r.facts.get("squeeze")
        if score is None or score < 50:
            continue
        why = f"{pct(sq.get('short_pct'), 1, False)} of shares short, {sq.get('days_to_cover') or 0:.1f} days to cover"
        r21 = _px(day, s, "ret_21d")
        out.append((score, s, why + (f"; {pct(r21, 0)} in a month" if r21 is not None else "")))
    return _top(day, out)

def unusual_options(day: Day) -> list[dict[str, Any]]:
    out = []
    for s in day.ctx.symbols:
        unusual = day.ctx.options.get(s, {}).get("unusual", [])
        if not unusual:
            continue
        u = unusual[0]
        premium = u["volume"] * (u.get("mid") or 0) * 100
        out.append((premium, s, f"{u['volume']:,} {u['expiration'][5:]} ${u['strike']:g} {u['kind']}s "
                                f"(volume / {u.get('ratio_basis', 'reference unavailable')} {u['ratio']:.1f}×; "
                                f"{usd(premium)} midpoint premium proxy; not trade direction)"))
    return _top(day, out)


def cheapest_iv(day: Day) -> list[dict[str, Any]]:
    out = []
    for s in day.ctx.symbols:
        o, rv = day.ctx.options.get(s, {}), _px(day, s, "vol_20d")
        if not o.get("iv30") or not rv:
            continue
        ratio = o["iv30"] / rv
        out.append((ratio, s, f"IV {pct(o['iv30'], 0, False)} vs {pct(rv, 0, False)} realized over 20 days ({ratio:.2f}×)"))
    return _top(day, out, reverse=False)


def biggest_moves_week(day: Day) -> list[dict[str, Any]]:
    out = []
    for s in day.ctx.symbols:
        for e in day.ctx.options.get(s, {}).get("expiries", []):
            if e.get("days") is not None and e["days"] <= 10 and e.get("expected_move"):
                out.append((e["expected_move"], s, f"Options price a ±{pct(e['expected_move'], 1, False)} move by "
                                                   f"{date.fromisoformat(e['expiration']):%b %-d}"))
                break
    return _top(day, out)


def value_traps(day: Day) -> list[dict[str, Any]]:
    out = []
    for s in day.ctx.symbols:
        v, q, g = _score(day, "valuation", s), _score(day, "quality", s), _score(day, "growth", s)
        flags = day.ctx.events.get(s, {}).get("red_flags")
        if v is None or v < 70 or not ((q is not None and q <= 35) or (g is not None and g <= 25) or flags):
            continue
        if flags:
            why = f"Red flag: {flags[0]['text']}"
        elif q is not None and q <= 35:
            why = f"Quality {_verdict(day, 'quality', s)}: {_reason(day, 'quality', s)}"
        else:
            why = f"Growth {_verdict(day, 'growth', s)}: {_reason(day, 'growth', s)}"
        out.append((v - (q or 50), s, f"Cheap for a reason. {why}"))
    return _top(day, out)

def dilution_watch(day: Day) -> list[dict[str, Any]]:
    out = []
    for s in day.ctx.symbols:
        dil = day.ctx.fund.get(s, {}).get("dilution")
        filings = day.ctx.events.get(s, {}).get("dilution_filings", 0)
        if (dil is None or dil < 0.05) and not filings:
            continue
        bits = []
        if dil is not None and dil >= 0.05:
            bits.append(f"share count {pct(dil, 1)} in a year")
        if filings:
            bits.append(f"{filings} registration or offering filing{'s' if filings > 1 else ''} in 90 days")
        out.append(((dil or 0) + 0.05 * filings, s, "; ".join(bits).capitalize()))
    return _top(day, out)


def wsb_vs_reality(day: Day) -> list[dict[str, Any]]:
    out = []
    for s in day.ctx.symbols:
        r = _r(day, "crowd", s)
        gap = None if r is None else r.facts.get("wsb_vs_reality")
        if not gap or gap.get("fundamentals") is None or gap["hype"] < 50:
            continue
        diff = gap["hype"] - gap["fundamentals"]
        if abs(diff) < 25:
            continue
        verdict = ("The crowd loves it; the numbers don't" if diff > 0 else "The numbers are better than the hype")
        out.append((abs(diff), s, f"{verdict} (hype {gap['hype']}, fundamentals {gap['fundamentals']})"))
    return _top(day, out)


def new_highs(day: Day) -> list[dict[str, Any]]:
    out = []
    for s in day.ctx.symbols:
        if (_px(day, s, "dist_high_52w") or -1) >= -0.003 and (_px(day, s, "bars_year") or 0) >= 240:
            r = _px(day, s, "ret_252d") or 0
            out.append((r, s, f"Closed at a 52-week high; {pct(r, 0)} in a year"))
    return _top(day, out)

def new_lows(day: Day) -> list[dict[str, Any]]:
    out = []
    for s in day.ctx.symbols:
        if (_px(day, s, "dist_low_52w") or 1) <= 0.003 and (_px(day, s, "bars_year") or 0) >= 240:
            r = _px(day, s, "ret_252d") or 0
            out.append((r, s, f"Closed at a 52-week low; {pct(r, 0)} in a year"))
    return _top(day, out, reverse=False)

def committee_reason(day: Day, s: str, score: float) -> str:
    """Which voting desks lift the committee score and which hold it back (desks with no weight are left out, so the
    reason always agrees with the score it explains)."""
    voting = [(name, sc) for name in day.weights if day.weights[name] > 0 and (sc := _score(day, name, s)) is not None]
    lifts = sorted((x for x in voting if x[1] >= 55), key=lambda x: -x[1])
    drags = sorted((x for x in voting if x[1] <= 45), key=lambda x: x[1])
    text = f"Score {score:.0f}: "
    text += (", ".join(f"{n} {v:.0f}" for n, v in lifts[:3]) + " lift it") if lifts else "no desk lifts it"
    text += ("; " + ", ".join(f"{n} {v:.0f}" for n, v in drags[:2]) + " hold it back") if drags else "; nothing holds it back"
    neutral = len(voting) - len(lifts) - len(drags)
    return text + (f"; {neutral} neutral" if neutral else "")


def committee_top(day: Day) -> list[dict[str, Any]]:
    out = []
    for s in day.ctx.symbols:
        c = day.committee.get(s) or {}
        if c.get("score") is None or c.get("desks_counted", 0) < 6:
            continue
        out.append((c["score"], s, committee_reason(day, s, c["score"])))
    return _top(day, out)


LISTS: tuple[ListSpec, ...] = (
    ListSpec("market_activity", "Market & options activity", "Measured options and price activity; no social input",
             "v1: same-session observed standard options, DTE 2..90, bid>0, spread/mid<=25%, >=4 contracts, "
             "aggregate volume>=100, OI>=100, volume*mid*100>=10000; percentile weights premium proxy .25, "
             "volume .20, capped volume/OI .10, quote quality .10, relative stock volume .15, "
             "absolute volatility-scaled daily move .10, absolute SPY-relative 63d strength .10; "
             "rank among covered names only; descriptive, no social or committee inputs", 1, "wave", False,
             market_activity, history=False),
    ListSpec("options_activity", "Options activity", "Liquid sampled contracts, ranked by midpoint premium proxy",
             "market_activity v1 quality gates; rank all qualified covered names by sum(volume*mid*100), descending; "
             "sampled expiries only, no directional or opening-trade inference", 1, "zap", False,
             options_activity, history=False),
    ListSpec("trending", "Trending today", "Unusual volume and moves today, from market data only",
             "relative volume>=1.5 or absolute daily move>=2 times 20-day annualized volatility/sqrt(252); "
             "weights relative volume/5 (capped at 1) .45, standardized move/4 (capped at 1) .35, qualified "
             "same-session midpoint options premium proxy percentile .20; normalize over available components; "
             "options use market-activity quote/liquidity gates; no social or committee inputs", 2, "rocket", True, trending,
             history=False),
    ListSpec("wsb", "WallStreetBets", "Most-mentioned covered tickers on r/wallstreetbets in the source's last 24 hours",
             "positive observed WSB 24-hour mentions among covered tickers, descending; ties source rank then symbol; "
             "show mention change and reported upvotes without treating attention as sentiment or a direction forecast",
             1, "people", True, wsb, history=False),
    ListSpec("gainers", "Top gainers", "Biggest gains today", "1-day return, highest", 1, "up", True, gainers),
    ListSpec("losers", "Top losers", "Biggest drops today", "1-day return, lowest", 1, "down", True, losers),
    ListSpec("undervalued", "Undervalued", "Cheap by these measures, and not junk", "valuation >= 70, quality >= 40, no red "
             "flag, valuation confidence >= 0.5; ranked by valuation + 0.1 x quality", 1, "diamond", True, undervalued,
             claim="outperform"),
    ListSpec("overvalued", "Overvalued", "Expensive by these measures", "valuation <= 25 with confidence >= 0.5; cheapest-scored "
             "first", 1, "bear", True, overvalued, claim="underperform"),
    ListSpec("quality_fair", "Quality at a fair price", "Great businesses not priced for perfection", "quality >= 70, valuation "
             ">= 45, no red flag; ranked by quality + valuation", 1, "diamond", True, quality_fair, claim="outperform"),
    ListSpec("momentum", "Momentum leaders", "Strongest trends", "momentum >= 75 and above the 200-day average", 1, "bull",
             True, momentum_leaders, claim="outperform"),
    ListSpec("earnings_week", "Earnings this week", "Reports expected in the next 7 days", "next report window (median gap of "
             "past 8-K 2.02 filings, ±7 days) centred within 7 days", 1, "calendar", False, earnings_week),
    ListSpec("just_reported", "Just reported", "Results in the last week, by the size of the reaction", "8-K 2.02 within 8 days; "
             "ranked by |abnormal return on results day|", 1, "doc", False, just_reported),
    ListSpec("insider_buying", "Insider buying", "Officers and directors buying with their own money", "open-market buys (P) > "
             "sales outside 10b5-1 plans in 90 days; ranked by buys / market value, cluster buys first", 1, "people", True,
             insider_buying, claim="outperform"),
    ListSpec("squeeze", "Squeeze watch", "Heavily shorted and moving against the shorts", "squeeze score >= 50 (short % of "
             "shares, days to cover, 21-day return, relative volume)", 1, "flame", True, squeeze_watch),
    ListSpec("unusual_options", "High option volume ratios", "Volume against average volume, or open interest when unavailable",
             "largest midpoint premium proxy among contracts with volume>=500 and volume/reference>=3; reference is "
             "provider average volume when supplied, otherwise open interest; these are different denominators", 2,
             "zap", True, unusual_options, history=False),
    ListSpec("cheap_iv", "Cheapest IV", "Options priced below what the stock actually moves", "lowest 30-day implied volatility "
             "/ 20-day realized volatility", 1, "tag", True, cheapest_iv, history=False),
    ListSpec("big_moves", "Biggest expected moves this week", "Where options price the biggest swings", "expected move "
             "(at-the-money straddle / price) of the first expiry within 10 days", 1, "wave", False, biggest_moves_week,
             history=False),
    ListSpec("value_traps", "Value traps to avoid", "Cheap for a reason", "valuation >= 70 with quality <= 35, growth <= 25 or "
             "a red flag", 1, "alert", True, value_traps, claim="underperform"),
    ListSpec("dilution", "Dilution watch", "Shareholders getting diluted", "diluted shares >= +5% in a year or a registration / "
             "offering filing in 90 days", 1, "drop", True, dilution_watch, claim="underperform"),
    ListSpec("wsb_vs_reality", "WSB vs reality", "Where the hype and the numbers disagree", "crowd score >= 50 and |crowd - "
             "fundamentals (valuation, quality, growth)| >= 25", 1, "eyes", True, wsb_vs_reality, history=False),
    ListSpec("new_highs", "New 52-week highs", "Closing at a one-year high", "close within 0.3% of the 52-week high", 1,
             "up", True, new_highs),
    ListSpec("new_lows", "New 52-week lows", "Closing at a one-year low", "close within 0.3% of the 52-week low", 1, "down",
             True, new_lows),
    ListSpec("committee", "The committee's picks", "Where the desks agree most", "highest committee score with at least 6 "
             "desks counted", 1, "star", True, committee_top, claim="outperform"),
)
BY_ID = {spec.id: spec for spec in LISTS}


def build_lists(day: Day) -> dict[str, list[dict[str, Any]]]:
    return {spec.id: spec.select(day) for spec in LISTS}


__all__ = ["BY_ID", "LISTS", "LIST_SIZE", "ListSpec", "build_lists"]
