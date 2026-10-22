"""One day's analysis: the research universe, point-in-time context, every desk, the committee, and what each stock
page shows. ``build`` works for today and for any past day (validation rebuilds lists exactly the same way)."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from typing import Any

from desk.core.config import StocksSection
from desk.core.hashing import canonical_json, sha256_hex
from desk.stocks import calendar as cal
from desk.stocks import desks as dk
from desk.stocks import features as feat
from desk.stocks import signals as sig
from desk.stocks.fundamentals import Fundamentals, base, with_price
from desk.stocks.prices import Panel
from desk.stocks.repo import StocksMarket, StocksResearch
from desk.stocks.sectors import LABELS, financial_type, sector
from desk.stocks.universe import research_listings

PRICE_FEATURES = ("close", "ret_1d", "ret_5d", "ret_21d", "ret_63d", "ret_252d", "mom_12_1", "sma50", "sma200", "high_52w",
                  "low_52w", "dist_high_52w", "dist_low_52w", "above_sma50", "above_sma200", "vol_20d", "vol_60d", "vol_1y",
                  "max_drawdown_1y", "dollar_volume_20d", "rel_volume", "gap", "beta_1y", "rs_63d", "bars_year")
MIN_BARS = 60
HISTORY_MONTHS = 24


@dataclass
class Day:
    asof: date
    ctx: dk.Context
    results: dict[str, dict[str, dk.DeskResult]]
    committee: dict[str, dict[str, Any]]
    weights: dict[str, float]
    universe_size: int
    market_price: dict[str, dict[str, Any]] = field(default_factory=dict)


def month_ends(days: Sequence[date], before: date, months: int) -> list[date]:
    """The last trading day of each of the ``months`` months before ``before``."""
    out: dict[tuple[int, int], date] = {}
    for d in days:
        if d < before:
            out[(d.year, d.month)] = d
    return sorted(out.values())[-months - 1:-1] if out else []


class Analyzer:
    def __init__(self, market: StocksMarket, research: StocksResearch, settings: StocksSection) -> None:
        self.market = market
        self.research = research
        self.settings = settings
        self._facts: dict[int, Fundamentals] = {}
        self._revisions: dict[int, str | None] = {}

    # ── fundamentals ───────────────────────────────────────────────────────────────────────────────
    def facts(self, cik: int) -> Fundamentals:
        row = self.market.conn.execute("SELECT facts_sha256 FROM stk_companies WHERE cik=?", (cik,)).fetchone()
        revision = row[0] if row else None
        if cik not in self._facts or self._revisions.get(cik) != revision:
            self._facts[cik] = Fundamentals(self.market.facts(cik))
            self._revisions[cik] = revision
        return self._facts[cik]

    def bases(self, ciks: dict[int, str | None], asof: date, *, cache: bool) -> dict[int, dict[str, Any]]:
        """Price-independent fundamentals per company on ``asof``; past days come from (and go to) the cache."""
        revisions = {r["cik"]: r["facts_sha256"] for r in self.market.conn.execute("SELECT cik, facts_sha256 FROM stk_companies")}
        hashes = {c: sha256_hex(canonical_json(["fundamentals-v2", revisions.get(c), fin])) for c, fin in ciks.items()}
        found = self.research.fund_cached(list(ciks), asof, hashes) if cache else {}
        missing = {c: fin for c, fin in ciks.items() if c not in found}
        computed = {c: base(self.facts(c), asof, financial=fin) for c, fin in missing.items()}
        if cache and computed:
            self.research.cache_fund(computed, asof, hashes)
        return {**found, **computed}

    # ── the day ────────────────────────────────────────────────────────────────────────────────────
    def build(self, panel: Panel, asof: date, *, now: datetime | None = None, cache_today: bool = False,
              crowd_and_options: bool = True, weights: dict[str, float] | None = None) -> Day:
        t = panel.day_index(asof)
        if t < 0:
            raise ValueError(f"no prices on or before {asof}")
        pf = feat.compute(panel, t)
        listings = {r["symbol"]: r for r in research_listings(self.market)}
        companies = self.market.companies()
        idx = pf.index
        symbols: list[str] = []
        stale = (asof - panel.days[t]).days > 5          # no prices for a week: score nothing rather than old prices
        for s in ([] if stale else panel.symbols):
            if s not in listings:
                continue
            i = idx[s]
            close, dv, bars = (float(pf.values[k][i]) for k in ("close", "dollar_volume_20d", "bars_year"))
            if math.isnan(close) or close < float(self.settings.min_price_usd) or math.isnan(dv) or (
                    dv < float(self.settings.min_median_dollar_volume_usd)) or bars < MIN_BARS:
                continue
            symbols.append(s)
        ciks = {s: int(listings[s]["cik"]) for s in symbols if listings[s]["cik"]}
        sic = {s: (companies[c]["sic"] if c in companies else None) for s, c in ciks.items()}
        sectors = {s: sector(sic.get(s)) for s in symbols}
        fin = {s: financial_type(sic.get(s)) for s in symbols}
        price = {s: {k: _num(pf.values[k][idx[s]]) for k in PRICE_FEATURES} for s in symbols}
        bases = self.bases({c: fin.get(s) for s, c in ciks.items()}, asof, cache=cache_today or asof < cal.new_york(
            now or datetime.now(UTC)).date())
        fund = {s: with_price(bases[ciks[s]], price[s]["close"]) if s in ciks else {} for s in symbols}
        history = self.valuation_history(panel, symbols, ciks, fin, asof)
        sector_rs = sig.sector_strength([price[s]["rs_63d"] or float("nan") for s in symbols], [sectors[s] for s in symbols])
        spy = panel.index.get("SPY")
        spy_above = None if spy is None else _num(pf.values["above_sma200"][spy])
        end_of_day = datetime.combine(asof, time(23, 59), cal.NEW_YORK)
        ctx = dk.Context(
            asof=asof, symbols=symbols, names={s: str(listings[s]["name"]) for s in symbols}, sectors=sectors, financial=fin,
            price=price, fund=fund,
            insiders=sig.insiders(self.market, asof),
            events=sig.events(self.market, asof, ciks, panel),
            shorts=sig.shorts(self.market, asof, {s: fund[s].get("shares") for s in symbols},
                              {s: (price[s]["dollar_volume_20d"] or 0) / (price[s]["close"] or 1) for s in symbols}),
            crowd=sig.crowd(self.market, end_of_day) if crowd_and_options else {},
            options=sig.options(self.market, asof) if crowd_and_options else {},
            macro=sig.macro(self.market, asof, spy_above),
            sector_rs=sector_rs, valuation_history=history)
        results = dk.run_desks(ctx, symbols)
        w = weights or self.weights()
        committee = {s: dk.committee({d: results[d].get(s) for d in dk.DESKS}, w) for s in symbols}
        # ETFs have useful market data but do not belong in the company-fundamental/PAPER universe.
        etfs = {r[0] for r in self.market.conn.execute("SELECT symbol FROM stk_listings WHERE etf=1 AND active=1 AND test=0")}
        market_price = dict(price)
        for s in ([] if stale else panel.symbols):
            if s not in etfs:
                continue
            values = {k: _num(pf.values[k][idx[s]]) for k in PRICE_FEATURES}
            if ((values["close"] or 0) >= float(self.settings.min_price_usd)
                    and (values["dollar_volume_20d"] or 0) >= float(self.settings.min_median_dollar_volume_usd)
                    and (values["bars_year"] or 0) >= MIN_BARS):
                market_price[s] = values
        return Day(asof, ctx, results, committee, w, len(symbols), market_price)

    def weights(self) -> dict[str, float]:
        """Learned weights from the newest validation when it produced them, else the documented defaults."""
        latest = self.research.latest_validation()
        learned = (latest or {}).get("committee_weights")
        return dict(learned) if learned else dict(dk.DEFAULT_WEIGHTS)

    def valuation_history(self, panel: Panel, symbols: Sequence[str], ciks: dict[str, int], fin: dict[str, str | None],
                          asof: date) -> dict[str, list[float]]:
        """Each stock's EV/sales (P/B for banks and insurers) at each of the last months' ends, point in time."""
        out: dict[str, list[float]] = {s: [] for s in symbols}
        for d in month_ends(panel.days, asof, HISTORY_MONTHS):
            j = panel.day_index(d)
            month_bases = self.bases({c: fin.get(s) for s, c in ciks.items()}, d, cache=True)
            for s, c in ciks.items():
                i = panel.index.get(s)
                if i is None:
                    continue
                px = panel.close[i, j]
                if math.isnan(px):
                    continue
                m = with_price(month_bases[c], float(px))
                v = m.get("pb") if fin.get(s) in ("bank", "insurer") else m.get("ev_sales")
                if v is not None:
                    out[s].append(float(v))
        return out


def _num(v: Any) -> float | None:
    if v is None:
        return None
    x = float(v)
    return None if math.isnan(x) or math.isinf(x) else x


# ── what is saved ────────────────────────────────────────────────────────────────────────────────
def stock_detail(day: Day, s: str) -> dict[str, Any]:
    ctx = day.ctx
    results = {d: day.results[d].get(s) for d in dk.DESKS}
    bull, bear = dk.bull_bear(results)
    fund = {k: v for k, v in ctx.fund.get(s, {}).items() if isinstance(v, (int, float)) or v is None}
    return {
        "symbol": s, "method_version": dk.METHOD_VERSION, "name": ctx.names.get(s), "asof": day.asof.isoformat(),
        "sector": LABELS.get(ctx.sectors.get(s, "Other")),
        "financial_type": ctx.financial.get(s), "price": ctx.price.get(s, {}), "fundamentals": fund,
        "desks": {d: (r.as_json() if r else None) for d, r in results.items()},
        "committee": day.committee.get(s), "weights": day.weights, "bull": bull, "bear": bear,
        "change_view": dk.change_view(ctx, s, results),
        "insiders": ctx.insiders.get(s), "events": ctx.events.get(s), "shorts": ctx.shorts.get(s), "crowd": ctx.crowd.get(s),
        "options": {k: v for k, v in ctx.options.get(s, {}).items() if k != "iv_history"},
        "macro": ctx.macro,
    }


def compact(day: Day, s: str) -> dict[str, Any]:
    return {"c": None if day.committee.get(s, {}).get("score") is None else day.committee[s]["score"],
            **{d: (None if day.results[d].get(s) is None or day.results[d][s].score is None
                   else round(day.results[d][s].score or 0, 1)) for d in dk.DESKS},
            "px": day.ctx.price.get(s, {}).get("close"), "mcap": day.ctx.fund.get(s, {}).get("market_cap")}


__all__ = ["Analyzer", "Day", "compact", "month_ends", "stock_detail"]
