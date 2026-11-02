"""What an item is worth, what you'd keep after selling it, and whether it's worth buying: the arbitrage desk's
counterpart of a Stocks play card.

Market value is the median of what buyers paid (price plus their delivery) over the last 90 days, after dropping
outliers. From it come eBay's fees (on the buyer's total, including the sales tax eBay collects), your shipping
label, and your purchase cost with your own sales tax. A confidence meter counts five plain checks, and the verdict
needs both a profit share and a dollar profit, so a 300% return on a $1 item doesn't rank as a find.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from statistics import median
from typing import Any

from desk.arbitrage.comps import Sale

WINDOW_DAYS = 90
GRAMS_PER_OUNCE = 31.1034768
PURITY = {"10k": 10 / 24, "14k": 14 / 24, "18k": 18 / 24, "22k": 22 / 24, "24k": 0.999, "925": 0.925, "999": 0.999}
SILVER = ("925", "999")

# eBay US final value fees (most categories 13.6%; trading cards 13.25%; jewelry 15%; athletic shoes $150+ 8% with no
# order fee), charged on the buyer's total including sales tax, plus $0.40 an order ($0.30 at $10 or less).
FEE_RATES = {"general": 0.136, "cards": 0.1325, "jewelry": 0.15, "sneakers": 0.136, "lego": 0.136}
SHIPPING = {"general": 10.0, "lego": 15.0, "cards": 1.0, "sneakers": 15.0, "jewelry": 5.0}
DEFAULTS = {"purchase_tax": 0.07, "buyer_tax": 0.07, "min_profit_pct": 0.30, "min_profit_usd": 10.0,
            "scrap_payout": 0.80, "bankroll": None}


@dataclass(frozen=True)
class Market:
    value: float                     # median buyer total
    low: float                       # a quick-sale price: the 25th percentile
    high: float
    count: int                       # sales in the window
    per_week: float | None           # how often it sells (None for a market price without sales)
    spread: float                    # interquartile range over the median
    trend: float | None              # change per 30 days (fraction), from dated sales over at least 3 weeks
    first: date | None
    last: date | None
    basis: str = "sales"             # sales | market_price (a marketplace's own recent-sales price, e.g. TCGplayer)
    sales: tuple[Sale, ...] = field(default=(), repr=False)

    @property
    def yearly(self) -> float | None:
        return None if self.trend is None else (1 + self.trend) ** (365 / 30) - 1

    def as_json(self) -> dict[str, Any]:
        return {"value": round(self.value, 2), "low": round(self.low, 2), "high": round(self.high, 2),
                "count": self.count, "per_week": None if self.per_week is None else round(self.per_week, 2),
                "spread": round(self.spread, 3), "trend": None if self.trend is None else round(self.trend, 4),
                "yearly": None if self.yearly is None else round(self.yearly, 4),
                "first": self.first.isoformat() if self.first else None,
                "last": self.last.isoformat() if self.last else None, "basis": self.basis}


def _quantile(xs: Sequence[float], q: float) -> float:
    s = sorted(xs)
    pos = (len(s) - 1) * q
    lo = math.floor(pos)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def _theil_sen(points: Sequence[tuple[float, float]]) -> float:
    slopes = [(y2 - y1) / (x2 - x1) for i, (x1, y1) in enumerate(points) for x2, y2 in points[i + 1:] if x2 != x1]
    return median(slopes) if slopes else 0.0


def summarize(sales: Sequence[Sale], today: date, window: int = WINDOW_DAYS) -> Market | None:
    """The market from sold results: undated sales count only when none are dated."""
    dated = [s for s in sales if s.sold_on is not None and 0 <= (today - s.sold_on).days <= window]
    use = dated or [s for s in sales if s.sold_on is None]
    if not use:
        return None
    totals = [s.total for s in use]
    if len(totals) >= 4:
        q1, q3 = _quantile(totals, 0.25), _quantile(totals, 0.75)
        fence = 1.5 * (q3 - q1)
        kept = [s for s in use if q1 - fence <= s.total <= q3 + fence]
        use = kept or use
        totals = [s.total for s in use]
    value = median(totals)
    days = sorted(s.sold_on for s in use if s.sold_on)
    span = (days[-1] - days[0]).days if days else 0
    per_week = None
    if days:
        # 240 results can cover less than the window for a popular item: rate over the span they cover
        covered = window if len(use) < 200 else max(span, 7)
        per_week = len(use) / covered * 7
    trend = None
    if len(days) >= 8 and span >= 21:
        slope = _theil_sen([((s.sold_on - days[0]).days, math.log(s.total)) for s in use if s.sold_on and s.total > 0])
        trend = math.exp(slope * 30) - 1
    return Market(value, _quantile(totals, 0.25), _quantile(totals, 0.75), len(use), per_week,
                  (_quantile(totals, 0.75) - _quantile(totals, 0.25)) / value if value else 0.0, trend,
                  days[0] if days else None, days[-1] if days else None, "sales", tuple(use))


def market_price(price: float, as_of: date | None) -> Market:
    """A marketplace's own price from its recent sales (TCGplayer's market price), without the sales themselves."""
    return Market(price, price, price, 0, None, 0.0, None, as_of, as_of, "market_price")


def ebay_fee(gross: float, category: str, buyer_tax: float) -> float:
    taxed = gross * (1 + buyer_tax)
    if category == "sneakers" and gross >= 150:
        return taxed * 0.08
    return taxed * FEE_RATES.get(category, FEE_RATES["general"]) + (0.30 if gross <= 10 else 0.40)


def default_shipping(category: str, value: float) -> float:
    if category == "cards":
        return 1.0 if value < 20 else 5.0                # a plain envelope, or tracked with a top-loader
    return SHIPPING.get(category, SHIPPING["general"])


@dataclass(frozen=True)
class Check:
    ok: bool
    id: str
    label: str
    detail: str


def confidence(market: Market, match: float | None, comps_at: datetime | None, price_at: datetime | None,
               now: datetime) -> dict[str, Any]:
    market_price_basis = market.basis == "market_price"
    checks = [
        Check(market.count >= 10 or market_price_basis, "sales", "Enough sales",
              "TCGplayer's market price, from its recent sales" if market_price_basis else
              f"{market.count} sold in the last {WINDOW_DAYS} days (10 or more is solid)"),
        Check(match is None or match >= 0.8, "match", "Same item",
              "Matched by its exact number" if match is None else
              f"{match:.0%} of the sold titles match what you searched"),
        Check(market.spread <= 0.35, "steady", "Steady price",
              f"The middle half of sales sit within {market.spread:.0%} of the typical price"),
        Check((market.per_week or 0) >= 1, "fast", "Sells fast",
              "Sales speed isn't published for this price" if market.per_week is None else
              f"About {market.per_week:.1f} a week"),
        Check(comps_at is not None and (now - comps_at).days <= 3 and (price_at is None or (now - price_at).days <= 7),
              "fresh", "Fresh prices", "Sold prices from the last 3 days and your price from the last week"),
    ]
    score = sum(c.ok for c in checks)
    label = {5: "Very strong", 4: "Strong", 3: "Fair", 2: "Weak"}.get(score, "Very weak")
    return {"score": score, "of": 5, "label": label,
            "checks": [{"id": c.id, "label": c.label, "ok": c.ok, "detail": c.detail} for c in checks]}


def evaluate(price: float, market: Market, *, category: str, settings: dict[str, Any], quantity: int | None = None,
             shipping: float | None = None, match: float | None = None, comps_at: datetime | None = None,
             price_at: datetime | None = None, now: datetime) -> dict[str, Any]:
    """The card: what it costs, what it sells for, what you keep, how sure, and how many."""
    s = {**DEFAULTS, **settings}
    cost = price * (1 + float(s["purchase_tax"]))
    ship = default_shipping(category, market.value) if shipping is None else shipping

    def kept(gross: float) -> float:
        return gross - ebay_fee(gross, category, float(s["buyer_tax"])) - ship

    net = kept(market.value)
    profit = net - cost
    pct = profit / cost if cost > 0 else 0.0
    conf = confidence(market, match, comps_at, price_at, now)
    thin = market.basis != "market_price" and market.count < 5      # a sale or two can't vouch for a price
    if pct >= s["min_profit_pct"] and profit >= s["min_profit_usd"] and conf["score"] >= 3 and not thin:
        verdict = "buy"
    elif pct >= s["min_profit_pct"] / 2 and profit >= s["min_profit_usd"] / 2 and conf["score"] >= 2:
        verdict = "maybe"
    else:
        verdict = "skip"
    # what you can expect to sell within a month, never more than the shelf has or the bankroll allows
    can_sell = max(1, math.floor((market.per_week or 0.25) * 4))
    buy = min(can_sell, quantity or can_sell)
    if s.get("bankroll"):
        buy = min(buy, max(0, math.floor(float(s["bankroll"]) * 0.25 / cost))) if cost > 0 else buy
    return {
        "price": round(price, 2), "cost": round(cost, 2), "sells_for": round(market.value, 2),
        "fees": round(ebay_fee(market.value, category, float(s["buyer_tax"])), 2), "shipping": round(ship, 2),
        "you_keep": round(net, 2), "profit": round(profit, 2), "profit_pct": round(pct, 4),
        "profit_quick": round(kept(market.low) - cost, 2), "verdict": verdict,
        "buy": buy if verdict != "skip" else 0, "confidence": conf, "market": market.as_json(),
    }


def melt(grams: float, karat: str, spot_per_ounce: float) -> float:
    return grams / GRAMS_PER_OUNCE * PURITY[karat] * spot_per_ounce


def evaluate_metal(price: float, grams: float, karat: str, spot_per_ounce: float, settings: dict[str, Any]
                   ) -> dict[str, Any]:
    """Gold or silver against its melt value: what a scrap buyer pays (a share of melt) after your sales tax."""
    s = {**DEFAULTS, **settings}
    value = melt(grams, karat, spot_per_ounce)
    payout = value * float(s["scrap_payout"])
    cost = price * (1 + float(s["purchase_tax"]))
    profit = payout - cost
    pct = profit / cost if cost > 0 else 0.0
    verdict = ("buy" if pct >= 0.15 and profit >= 10 else "maybe" if profit > 0 else "skip")
    return {"melt": round(value, 2), "scrap_payout": round(payout, 2), "cost": round(cost, 2),
            "profit": round(profit, 2), "profit_pct": round(pct, 4), "verdict": verdict,
            "break_even_price": round(payout / (1 + float(s["purchase_tax"])), 2),
            "metal": "silver" if karat in SILVER else "gold"}


__all__ = ["DEFAULTS", "FEE_RATES", "PURITY", "Market", "confidence", "default_shipping", "ebay_fee", "evaluate",
           "evaluate_metal", "market_price", "melt", "summarize"]
