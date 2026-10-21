"""A synthetic market for KESTREL Stocks tests: companies with SEC-style filings, prices, insiders, earnings, short
interest, crowd mentions and option summaries, with a few planted traits the desks must find."""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

from desk.core.paths import DeskPaths
from desk.stocks import calendar as cal
from desk.stocks.contracts import Bar, Company, Fact, Filing, InsiderTrade, Listing, Mention, RatePoint, ShortInterest
from desk.stocks.repo import StocksDatabases, StocksMarket, StocksResearch
from tests.conftest import REPO

ASOF = date(2026, 10, 21)
NOW = datetime(2026, 10, 21, 22, 30, tzinfo=UTC)
SICS = ["3571", "7372", "2834", "3714", "5331", "4911", "1311", "2080", "3561", "4813", "6021", "6798"]


@dataclass
class Spec:
    symbol: str
    cik: int
    sic: str
    revenue: float          # annual revenue at the start of 2022
    growth: float           # yearly
    margin: float           # operating margin
    ps: float               # price-to-sales the market pays
    shares: float
    drift: float            # yearly price drift beyond fundamentals
    debt: float = 0.0
    dilution: float = 0.0   # yearly share growth
    traits: set[str] = field(default_factory=set)


def specs(n: int = 60, seed: int = 7) -> list[Spec]:
    rng = random.Random(seed)
    out = []
    for i in range(n):
        out.append(Spec(f"S{i:02d}", 1000 + i, SICS[i % len(SICS)], rng.uniform(5e8, 2e10), rng.uniform(-0.05, 0.3),
                        rng.uniform(0.02, 0.3), rng.uniform(0.8, 8.0), rng.uniform(5e7, 1e9), rng.uniform(-0.2, 0.3),
                        debt=rng.uniform(0, 3e9)))
    # planted: cheap and good / expensive / cheap for a reason / diluting
    out[1] = Spec("CHEAP", 2001, "3561", 8e9, 0.18, 0.25, 0.6, 3e8, 0.1, debt=5e8, traits={"cheap"})
    out[2] = Spec("PRICEY", 2002, "3561", 2e9, 0.02, 0.05, 25.0, 2e8, 0.0, traits={"pricey"})
    out[3] = Spec("TRAP", 2003, "3561", 6e9, -0.12, -0.04, 0.4, 3e8, -0.2, debt=6e9, traits={"trap"})
    out[4] = Spec("DILUT", 2004, "7372", 1e9, 0.1, -0.1, 4.0, 2e8, -0.1, dilution=0.25, traits={"dilute"})
    out[5] = Spec("INSDR", 2005, "2834", 3e9, 0.08, 0.15, 3.0, 2e8, 0.05, traits={"insiders"})
    out[6] = Spec("SQZ", 2006, "5331", 1.5e9, 0.0, 0.03, 0.8, 1e8, 0.0, traits={"squeeze"})
    return out


def quarter_ends(start: date, end: date) -> list[date]:
    out, y, q = [], start.year, 1
    while True:
        m = q * 3
        d = date(y, m, 31 if m in (3, 12) else 30)
        if d > end:
            return out
        if d >= start:
            out.append(d)
        q += 1
        if q == 5:
            q, y = 1, y + 1


def facts_for(sp: Spec, through: date) -> dict[str, list[Fact]]:
    """Quarterly 10-Q (3-month and year-to-date) and annual 10-K facts, filed 40 / 60 days after the period."""
    out: dict[str, list[Fact]] = {}

    def add(concept: str, unit: str, start: date | None, end: date, value: float, form: str, filed: date) -> None:
        out.setdefault(concept, []).append(Fact(concept, unit, start, end, value, form, filed, end.year, None,
                                                f"{sp.cik}-{filed.isoformat()}"))

    def quarterly_revenue(qe: date) -> float:
        years = (qe - date(2022, 1, 1)).days / 365.25
        return sp.revenue / 4 * (1 + sp.growth) ** years

    def shares_at(d: date) -> float:
        return sp.shares * (1 + sp.dilution) ** ((d - date(2022, 1, 1)).days / 365.25)

    for qe in quarter_ends(date(2021, 1, 1), through):
        q = qe.month // 3
        q_start = date(qe.year, qe.month - 2, 1)
        ytd_start = date(qe.year, 1, 1)
        is_annual = q == 4
        filed = qe + timedelta(days=60 if is_annual else 40)
        if filed > through:
            continue
        form = "10-K" if is_annual else "10-Q"
        rev_q = quarterly_revenue(qe)
        ytd_quarters = [date(qe.year, m * 3, 31 if m in (1, 4) else 30) for m in range(1, q + 1)]
        rev_ytd = sum(quarterly_revenue(x) for x in ytd_quarters)
        op = sp.margin
        for concept, factor in (("us-gaap:Revenues", 1.0), ("us-gaap:OperatingIncomeLoss", op),
                                ("us-gaap:NetIncomeLoss", op * 0.8),
                                ("us-gaap:NetCashProvidedByUsedInOperatingActivities", op * 1.05),
                                ("us-gaap:PaymentsToAcquirePropertyPlantAndEquipment", 0.04), ("us-gaap:GrossProfit", 0.45),
                                ("us-gaap:DepreciationDepletionAndAmortization", 0.03)):
            add(concept, "USD", ytd_start, qe, rev_ytd * factor, form, filed)
            if not is_annual:
                add(concept, "USD", q_start, qe, rev_q * factor, form, filed)
        if not is_annual:
            add("us-gaap:WeightedAverageNumberOfDilutedSharesOutstanding", "shares", q_start, qe, shares_at(qe), form, filed)
        annual_rev = rev_q * 4
        assets = annual_rev * 1.2 + sp.debt
        for concept, value in (("us-gaap:Assets", assets), ("us-gaap:AssetsCurrent", annual_rev * 0.5),
                               ("us-gaap:Liabilities", sp.debt + annual_rev * 0.3),
                               ("us-gaap:LiabilitiesCurrent", annual_rev * 0.25),
                               ("us-gaap:StockholdersEquity", assets - sp.debt - annual_rev * 0.3),
                               ("us-gaap:CashAndCashEquivalentsAtCarryingValue", annual_rev * 0.15),
                               ("us-gaap:LongTermDebtNoncurrent", sp.debt),
                               ("us-gaap:RetainedEarningsAccumulatedDeficit", annual_rev * 0.2),
                               ("us-gaap:AccountsReceivableNetCurrent", annual_rev * 0.12),
                               ("us-gaap:PropertyPlantAndEquipmentNet", annual_rev * 0.3)):
            add(concept, "USD", None, qe, value, form, filed)
        add("dei:EntityCommonStockSharesOutstanding", "shares", None, filed - timedelta(days=5), shares_at(qe), form, filed)
    for rows in out.values():
        rows.sort(key=lambda f: (f.filed, f.end, f.accn))
    return out


@dataclass
class StocksWorld:
    paths: DeskPaths
    dbs: StocksDatabases
    market: StocksMarket
    research: StocksResearch
    specs: list[Spec]
    days: list[date]

    def close(self) -> None:
        self.dbs.close()


def bars_for(sp: Spec, days: list[date], rng: random.Random) -> list[Bar]:
    out, noise = [], 0.0
    shares0 = sp.shares
    for i, d in enumerate(days):
        years = (d - date(2022, 1, 1)).days / 365.25
        fair = sp.ps * sp.revenue * (1 + sp.growth) ** years / (shares0 * (1 + sp.dilution) ** years)
        noise += rng.gauss(sp.drift / 252, 0.018)
        if "squeeze" in sp.traits and i > len(days) - 25:
            noise += 0.02                       # up 2% a day for a month, against heavy short interest
        close = fair * math.exp(noise)
        open_ = close * math.exp(rng.gauss(0, 0.004))
        volume = rng.uniform(1e6, 3e6) * (4 if "squeeze" in sp.traits and i > len(days) - 25 else 1)
        out.append(Bar(sp.symbol, d, open_, max(open_, close) * 1.004, min(open_, close) * 0.996, close, volume))
    return out


def make_world(tmp_path: Path, *, n: int = 60, seed: int = 7, through: date = ASOF) -> StocksWorld:
    paths = DeskPaths(tmp_path / "home", REPO / "config", tmp_path / "secrets.env")
    dbs = StocksDatabases.open(paths)
    market, research = StocksMarket(dbs.market), StocksResearch(dbs.research)
    rng = random.Random(seed)
    all_specs = specs(n, seed)
    days = cal.trading_days(through - timedelta(days=730), through)
    listings = [Listing(sp.symbol, f"{sp.symbol} Corp Common Stock", "NYSE", False, False, "common") for sp in all_specs]
    listings.append(Listing("SPY", "SPDR S&P 500 ETF", "NYSE Arca", True, False, "etf"))
    market.save_listings(listings, {sp.symbol: sp.cik for sp in all_specs}, NOW)
    spy = Spec("SPY", 0, "0", 1e10, 0.07, 0.1, 1.0, 1e8, 0.05)
    by_day: dict[date, list[Bar]] = {d: [] for d in days}
    for sp in [*all_specs, spy]:
        for b in bars_for(sp, days, rng):
            by_day[b.day].append(b)
    for d, bars in by_day.items():
        market.save_bars(d, bars, "synthetic", NOW)
    for sp in all_specs:
        filings = []
        for k, qe in enumerate(quarter_ends(date(2025, 1, 1), through)):
            filed = qe + timedelta(days=30)
            if filed >= through:
                continue
            accepted = datetime.combine(filed, time(16, 30), cal.NEW_YORK).astimezone(UTC)
            filings.append(Filing(sp.cik, f"{sp.cik}-8k-{k}", "8-K", filed, accepted, filed, ("2.02", "9.01"), "x.htm"))
        if "dilute" in sp.traits:
            filings.append(Filing(sp.cik, f"{sp.cik}-s3", "S-3", through - timedelta(days=20), None, None, (), "s3.htm"))
        market.save_company(Company(sp.cik, f"{sp.symbol} Corp", sp.sic, "synthetic", (sp.symbol,), ("NYSE",), "1231", None,
                                    tuple(filings)), None, NOW)
        market.save_facts(sp.cik, facts_for(sp, through), f"sha-{sp.symbol}", NOW)
    insiders = []
    for k in range(4):
        insiders.append((InsiderTrade(f"ins-{k}", 2005, "INSDR", f"Insider {k}", 9000 + k, k % 2 == 0, k % 2 == 1, "CFO" if k == 1
                                      else None, False, through - timedelta(days=20 - k), "P", 20000, 30.0, True, 50000, True,
                                      False), through - timedelta(days=18 - k)))
    insiders.append((InsiderTrade("ins-s", 1010, "S10", "Seller", 9100, False, True, "CEO", False, through - timedelta(days=10),
                                  "S", 500000, 20.0, False, 0, True, False), through - timedelta(days=8)))
    market.save_insider_trades(insiders)
    settlement = date(2026, 9, 30)
    market.save_short_interest([ShortInterest("SQZ", settlement, 3.2e7, 2.5e7, 4e6, 8.0)]
                               + [ShortInterest(sp.symbol, settlement, sp.shares * 0.02, sp.shares * 0.02, 2e6, 1.5)
                                  for sp in all_specs if sp.symbol != "SQZ"], NOW)
    hour = int(datetime.combine(through, time(20, 0), UTC).timestamp() // 3600)
    market.save_mentions([Mention("SQZ", "SQZ Corp", "wallstreetbets", 1, 900, 5000, 9, 120),
                          Mention("S10", "S10 Corp", "wallstreetbets", 2, 300, 900, 3, 280),
                          Mention("PRICEY", "PRICEY Corp", "wallstreetbets", 3, 250, 800, 20, 40)], hour)
    market.save_rates([RatePoint("UST10Y", d, 4.3) for d in days[-80:]] + [RatePoint("UST2Y", d, 4.0) for d in days[-80:]])
    for k, sym in enumerate(("CHEAP", "SQZ", "S10", "S11", "S12")):
        for back in range(30):
            d = days[-1 - back]
            iv = 0.3 + 0.1 * k + 0.002 * back
            unusual = ([{"occ": f"{sym}C", "kind": "call", "strike": 50.0, "expiration": "2026-11-20", "volume": 9000,
                         "open_interest": 1000, "ratio": 9.0, "mid": 1.5, "iv": iv}] if sym == "SQZ" else [])
            market.save_option_daily(sym, d, NOW, {
                "price": 50.0, "iv30": iv, "term_slope": 2.0 - k, "put_call_volume": 0.4 + 0.2 * k, "call_volume": 5000,
                "put_volume": 2000, "call_oi": 40000, "put_oi": 30000, "unusual": unusual,
                "expiries": [{"expiration": "2026-10-30", "days": 9, "atm_iv": iv, "expected_move": 0.04 + 0.01 * k,
                              "straddle": 2.0, "skew_25d": 3.0, "call_volume": 3000, "put_volume": 1000, "call_oi": 20000,
                              "put_oi": 10000, "max_pain": 50.0},
                             {"expiration": "2026-11-20", "days": 30, "atm_iv": iv, "expected_move": 0.08 + 0.01 * k,
                              "straddle": 4.0, "skew_25d": 4.0, "call_volume": 2000, "put_volume": 1000, "call_oi": 20000,
                              "put_oi": 20000, "max_pain": 50.0}]})
    return StocksWorld(paths, dbs, market, research, all_specs, days)


def third_friday(year: int, month: int) -> date:
    first = date(year, month, 1)
    return first + timedelta(days=(4 - first.weekday()) % 7 + 14)


def occ(symbol: str, expiration: date, kind: str, strike: float) -> str:
    return f"{symbol}{expiration:%y%m%d}{'C' if kind == 'call' else 'P'}{round(strike * 1000):08d}"


def add_chains(world: StocksWorld, symbols: list[str], days: list[date], *, iv: float = 0.4, spread: float = 0.06) -> None:
    """Monthly-expiry chains priced with Black-Scholes at ``iv`` around each day's close, quoted ``spread`` wide."""
    from desk.stocks import options as opt
    from desk.stocks.contracts import OptionQuote
    expirations = sorted({third_friday(y, m) for y in range(days[0].year, days[-1].year + 2) for m in range(1, 13)})
    for sym in symbols:
        closes = {b.day: b.close for b in world.market.bars(sym, days[0])}
        for d in days:
            s0 = closes.get(d)
            if s0 is None:
                continue
            step = 1.0 if s0 < 50 else 2.5 if s0 < 200 else 5.0
            strikes = [round(k * step, 2) for k in range(int(s0 * 0.6 / step), int(s0 * 1.4 / step) + 1)]
            observed = datetime.combine(d, time(21, 30), UTC)
            for e in (e for e in expirations if 2 <= (e - d).days <= 100):
                t = opt.year_fraction(d, e)
                quotes = []
                for kind in ("call", "put"):
                    for k in strikes:
                        mid = opt.bs_price(kind, s0, k, t, iv, 0.04)
                        if mid < 0.05:
                            continue
                        half = max(mid * spread / 2, 0.01)
                        quotes.append(OptionQuote(occ(sym, e, kind, k), sym, kind, k, e, round(mid - half, 2),
                                                  round(mid + half, 2), round(mid, 2), 800, 5000, 700, observed))
                world.market.save_option_chain(sym, d, e, observed, s0, quotes)


__all__ = ["ASOF", "NOW", "Spec", "StocksWorld", "add_chains", "facts_for", "make_world", "occ", "quarter_ends", "specs",
           "third_friday"]
