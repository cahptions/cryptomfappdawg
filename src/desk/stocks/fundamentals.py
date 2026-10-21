"""Point-in-time fundamentals from SEC company facts.

A value counts only from the day after its filing date (``filed < as_of``): the SEC gives filing days, not times,
and a filing can arrive after the close. Restatements count from their own filing date. Trailing-twelve-month
(TTM) flows are the latest annual value plus the year-to-date since, minus the same period a year earlier.
Nothing is filled: a metric without its inputs is None and every desk lowers its confidence accordingly.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta

from desk.stocks.contracts import Fact

FLOWS: dict[str, tuple[str, ...]] = {
    "revenue": ("us-gaap:Revenues", "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax", "us-gaap:SalesRevenueNet",
                "us-gaap:RevenueFromContractWithCustomerIncludingAssessedTax"),
    "cost_of_revenue": ("us-gaap:CostOfRevenue", "us-gaap:CostOfGoodsAndServicesSold"),
    "gross_profit": ("us-gaap:GrossProfit",),
    "operating_income": ("us-gaap:OperatingIncomeLoss",),
    "net_income": ("us-gaap:NetIncomeLoss",),
    "cfo": ("us-gaap:NetCashProvidedByUsedInOperatingActivities",),
    "capex": ("us-gaap:PaymentsToAcquirePropertyPlantAndEquipment",),
    "dna": ("us-gaap:DepreciationDepletionAndAmortization", "us-gaap:DepreciationAndAmortization"),
    "sbc": ("us-gaap:ShareBasedCompensation",),
    "interest_expense": ("us-gaap:InterestExpense", "us-gaap:InterestExpenseNonoperating"),
    "tax": ("us-gaap:IncomeTaxExpenseBenefit",),
    "pretax": ("us-gaap:IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",),
    "sga": ("us-gaap:SellingGeneralAndAdministrativeExpense",),
    "buybacks": ("us-gaap:PaymentsForRepurchaseOfCommonStock",),
    "dividends_paid": ("us-gaap:PaymentsOfDividends", "us-gaap:PaymentsOfDividendsCommonStock"),
    "net_interest_income": ("us-gaap:InterestIncomeExpenseNet",),
    "noninterest_income": ("us-gaap:NoninterestIncome",),
    "interest_income": ("us-gaap:InterestAndDividendIncomeOperating",),
    "re_gains": ("us-gaap:GainLossOnSaleOfRealEstateInvestmentProperty",),
    "diluted_shares": ("us-gaap:WeightedAverageNumberOfDilutedSharesOutstanding",),
}
INSTANTS: dict[str, tuple[str, ...]] = {
    "assets": ("us-gaap:Assets",),
    "current_assets": ("us-gaap:AssetsCurrent",),
    "liabilities": ("us-gaap:Liabilities",),
    "current_liabilities": ("us-gaap:LiabilitiesCurrent",),
    "equity": ("us-gaap:StockholdersEquity",),
    "cash": ("us-gaap:CashAndCashEquivalentsAtCarryingValue",),
    "st_investments": ("us-gaap:MarketableSecuritiesCurrent", "us-gaap:ShortTermInvestments"),
    "lt_debt": ("us-gaap:LongTermDebtNoncurrent", "us-gaap:LongTermDebt"),
    "current_debt": ("us-gaap:LongTermDebtCurrent", "us-gaap:DebtCurrent"),
    "st_borrowings": ("us-gaap:ShortTermBorrowings", "us-gaap:CommercialPaper"),
    "retained_earnings": ("us-gaap:RetainedEarningsAccumulatedDeficit",),
    "receivables": ("us-gaap:AccountsReceivableNetCurrent",),
    "ppe": ("us-gaap:PropertyPlantAndEquipmentNet",),
    "real_estate": ("us-gaap:RealEstateInvestmentPropertyNet",),
    "shares_out": ("dei:EntityCommonStockSharesOutstanding",),
    "public_float": ("dei:EntityPublicFloat",),
}
EPS = ("us-gaap:EarningsPerShareDiluted",)
CONCEPTS: tuple[str, ...] = tuple(sorted({c for group in (*FLOWS.values(), *INSTANTS.values(), EPS) for c in group}))

ANNUAL = (350, 380)
STALE_FLOW_DAYS = 470          # a TTM ending more than ~15 months ago is too old to price today
STALE_INSTANT_DAYS = 400


def _known(facts: Sequence[Fact], as_of: date) -> list[Fact]:
    return [f for f in facts if f.filed < as_of]


def _latest_per_period(facts: Sequence[Fact]) -> dict[tuple[date | None, date], Fact]:
    """For each (start, end) the most recently filed value (restatements replace earlier values from their date)."""
    out: dict[tuple[date | None, date], Fact] = {}
    for f in sorted(facts, key=lambda x: (x.filed, x.accn)):
        out[(f.start, f.end)] = f
    return out


@dataclass(frozen=True)
class Flow:
    value: float
    end: date
    source: str        # how it was built: annual, annual+ytd, quarters


def ttm(facts: Sequence[Fact], as_of: date, *, end_before: date | None = None) -> Flow | None:
    periods = _latest_per_period([f for f in _known(facts, as_of) if f.start is not None and f.unit in ("USD", "shares")
                                  and (end_before is None or f.end <= end_before)])
    durations = [(f, f.days) for f in periods.values()]
    annuals = sorted((f for f, d in durations if ANNUAL[0] <= d <= ANNUAL[1]), key=lambda f: f.end)
    if annuals:
        a = annuals[-1]
        assert a.start is not None
        ytds = sorted((f for f, d in durations if 80 <= d <= 290 and f.start is not None
                       and abs((f.start - (a.end + timedelta(days=1))).days) <= 7 and f.end > a.end), key=lambda f: f.end)
        if ytds:
            y = ytds[-1]
            prior = [f for f, d in durations if f.start is not None and abs(d - y.days) <= 10
                     and abs((f.end - (y.end - timedelta(days=365))).days) <= 10]
            if prior:
                p = max(prior, key=lambda f: f.filed)
                return Flow(a.value + y.value - p.value, y.end, "annual+ytd")
        return Flow(a.value, a.end, "annual")
    quarters = sorted((f for f, d in durations if 80 <= d <= 100), key=lambda f: f.end)
    if len(quarters) >= 4:
        last4 = quarters[-4:]
        if (last4[-1].end - last4[0].end).days <= 290:
            return Flow(sum(q.value for q in last4), last4[-1].end, "quarters")
    return None


def latest_quarter(facts: Sequence[Fact], as_of: date) -> Fact | None:
    periods = _latest_per_period([f for f in _known(facts, as_of) if f.start is not None])
    quarters = [f for f in periods.values() if 80 <= f.days <= 100]
    return max(quarters, key=lambda f: f.end) if quarters else None


def instant(facts: Sequence[Fact], as_of: date, *, near: date | None = None, tolerance: int = 45) -> Fact | None:
    """The latest balance-sheet value known by ``as_of`` (or the one closest to ``near``). Several values for the
    same date in one filing (share classes) are summed."""
    known = [f for f in _known(facts, as_of) if f.start is None]
    if near is not None:
        known = [f for f in known if abs((f.end - near).days) <= tolerance]
    if not known:
        return None
    end = max(known, key=lambda f: f.end).end if near is None else min(known, key=lambda f: abs((f.end - near).days)).end
    same = [f for f in known if f.end == end]
    newest = max(f.filed for f in same)
    latest = [f for f in same if f.filed == newest]
    by_accn: dict[str, list[Fact]] = {}
    for f in latest:
        by_accn.setdefault(f.accn, []).append(f)
    group = max(by_accn.values(), key=len)
    values = {f.value for f in group}
    total = sum(values) if len(values) > 1 else group[0].value
    first = group[0]
    return Fact(first.concept, first.unit, None, first.end, total, first.form, first.filed, first.fy, first.fp, first.accn)


@dataclass
class Fundamentals:
    """A company's facts grouped by meaning (the first alias with recent data wins)."""

    facts: dict[str, list[Fact]] = field(default_factory=dict)

    def series(self, name: str, as_of: date) -> list[Fact]:
        aliases = FLOWS.get(name) or INSTANTS.get(name) or (EPS if name == "eps" else ())
        best: list[Fact] = []
        best_end: date | None = None
        for concept in aliases:
            known = _known(self.facts.get(concept, []), as_of)
            if not known:
                continue
            end = max(f.end for f in known)
            if best_end is None or end > best_end + timedelta(days=60):
                best, best_end = known, end
        return best

    def flow(self, name: str, as_of: date, *, end_before: date | None = None) -> Flow | None:
        found = ttm(self.series(name, as_of), as_of, end_before=end_before)
        if found is None or (as_of - found.end).days > STALE_FLOW_DAYS + (0 if end_before is None else 10**6):
            return None
        return found

    def value(self, name: str, as_of: date, *, near: date | None = None) -> float | None:
        fact = instant(self.series(name, as_of), as_of, near=near)
        if fact is None or (near is None and (as_of - fact.end).days > STALE_INSTANT_DAYS):
            return None
        return fact.value

    def year_ago_flow(self, name: str, as_of: date, current: Flow) -> Flow | None:
        prior = ttm(self.series(name, as_of), as_of, end_before=current.end - timedelta(days=330))
        if prior is None or abs((prior.end - (current.end - timedelta(days=365))).days) > 40:
            return None
        return prior

    def annual_values(self, name: str, as_of: date) -> list[Fact]:
        """Fiscal-year values known by ``as_of``, oldest first (latest filing for each year)."""
        periods = _latest_per_period([f for f in self.series(name, as_of) if f.start is not None])
        return sorted((f for f in periods.values() if ANNUAL[0] <= f.days <= ANNUAL[1]), key=lambda f: f.end)


def _div(a: float | None, b: float | None) -> float | None:
    if a is None or b is None or b == 0 or math.isnan(a) or math.isnan(b):
        return None
    return a / b


def _pos_div(a: float | None, b: float | None) -> float | None:
    """A multiple only means something when the denominator is positive (a P/E on a loss is undefined)."""
    if b is None or b <= 0:
        return None
    return _div(a, b)


def _sum(*values: float | None) -> float | None:
    present = [v for v in values if v is not None]
    return sum(present) if present else None


def metrics(f: Fundamentals, as_of: date, price: float | None, *, financial: str | None = None) -> dict[str, float | None]:
    """Every fundamental number the desks use, as known on ``as_of`` at ``price``. None = not computable."""
    flows = {name: f.flow(name, as_of) for name in FLOWS}
    v = {name: f.value(name, as_of) for name in INSTANTS}
    m: dict[str, float | None] = {}
    revenue = flows["revenue"].value if flows["revenue"] else None
    if revenue is None and flows["net_interest_income"] and flows["noninterest_income"]:
        revenue = flows["net_interest_income"].value + flows["noninterest_income"].value       # banks
    ni = flows["net_income"].value if flows["net_income"] else None
    ebit = flows["operating_income"].value if flows["operating_income"] else None
    if ebit is None and flows["pretax"] and flows["interest_expense"]:
        ebit = flows["pretax"].value + flows["interest_expense"].value
    cfo = flows["cfo"].value if flows["cfo"] else None
    capex = flows["capex"].value if flows["capex"] else None
    fcf = None if cfo is None else cfo - (capex or 0.0)
    gross = flows["gross_profit"].value if flows["gross_profit"] else (
        revenue - flows["cost_of_revenue"].value if revenue is not None and flows["cost_of_revenue"] else None)
    dna = flows["dna"].value if flows["dna"] else None
    shares = v["shares_out"]
    if shares is None:
        q = latest_quarter(f.series("diluted_shares", as_of), as_of)
        shares = q.value if q is not None and (as_of - q.end).days <= STALE_INSTANT_DAYS else None
    mcap = None if price is None or shares is None else price * shares
    debt = _sum(v["lt_debt"], v["current_debt"], v["st_borrowings"])
    cash = _sum(v["cash"], v["st_investments"])
    ev = None if mcap is None else mcap + (debt or 0.0) - (cash or 0.0)
    equity = v["equity"]
    m.update(revenue=revenue, net_income=ni, ebit=ebit, cfo=cfo, capex=capex, fcf=fcf, gross_profit=gross, dna=dna,
             shares=shares, market_cap=mcap, debt=debt, cash=cash, ev=ev, equity=equity, assets=v["assets"],
             sbc=flows["sbc"].value if flows["sbc"] else None,
             ttm_end=float(flows["revenue"].end.toordinal()) if flows["revenue"] else None)
    m["pe"] = _pos_div(mcap, ni)
    m["earnings_yield"] = _div(ni, mcap)
    m["ev_ebit"] = None if financial else _pos_div(ev, ebit)
    m["ev_sales"] = None if financial else _pos_div(ev, revenue)
    m["p_fcf"] = None if financial else _pos_div(mcap, fcf)
    m["fcf_yield"] = None if financial else _div(fcf, mcap)
    m["pb"] = _pos_div(mcap, equity)
    m["gross_margin"] = _div(gross, revenue)
    m["operating_margin"] = _div(ebit, revenue)
    m["net_margin"] = _div(ni, revenue)
    m["roe"] = _div(ni, equity) if equity and equity > 0 else None
    m["roa"] = _div(ni, v["assets"])
    tax_rate = None
    if flows["tax"] and flows["pretax"] and flows["pretax"].value > 0:
        tax_rate = min(max(flows["tax"].value / flows["pretax"].value, 0.0), 0.35)
    invested = None if equity is None else equity + (debt or 0.0) - (cash or 0.0)
    m["roic"] = None if financial or ebit is None or not invested or invested <= 0 else ebit * (1 - (tax_rate or 0.21)) / invested
    m["fcf_conversion"] = _pos_div(fcf, ni)
    m["accruals"] = _div(None if ni is None or cfo is None else ni - cfo, v["assets"])
    m["debt_to_equity"] = _div(debt, equity) if equity and equity > 0 and financial is None else None
    ebitda = None if ebit is None else ebit + (dna or 0.0)
    m["net_debt_ebitda"] = None if financial else _pos_div(None if debt is None else debt - (cash or 0.0), ebitda)
    interest = flows["interest_expense"].value if flows["interest_expense"] else None
    m["interest_cover"] = None if financial else _pos_div(ebit, interest)
    m["current_ratio"] = _div(v["current_assets"], v["current_liabilities"])
    m["buyback_yield"] = _div(flows["buybacks"].value if flows["buybacks"] else None, mcap)
    m["dividend_yield"] = _div(flows["dividends_paid"].value if flows["dividends_paid"] else None, mcap)
    m["sbc_to_revenue"] = _div(m["sbc"], revenue)
    if financial == "reit" and ni is not None:
        ffo = ni + (dna or 0.0) - (flows["re_gains"].value if flows["re_gains"] else 0.0)
        m["ffo"] = ffo
        m["p_ffo"] = _pos_div(mcap, ffo)
    # growth and its trend
    for name, key in (("revenue", "revenue_growth"), ("net_income", "earnings_growth"), ("cfo", "cfo_growth")):
        cur = flows[name]
        prior = None if cur is None else f.year_ago_flow(name, as_of, cur)
        m[key] = None if cur is None or prior is None or prior.value <= 0 else cur.value / prior.value - 1
    cur_rev = flows["revenue"]
    if cur_rev is not None:
        half = f.flow("revenue", as_of, end_before=cur_rev.end - timedelta(days=150))
        half_prior = None if half is None else f.year_ago_flow("revenue", as_of, half)
        if half is not None and half_prior is not None and half_prior.value > 0 and m["revenue_growth"] is not None:
            m["revenue_acceleration"] = m["revenue_growth"] - (half.value / half_prior.value - 1)
        else:
            m["revenue_acceleration"] = None
    else:
        m["revenue_acceleration"] = None
    annual_rev = f.annual_values("revenue", as_of)
    m["revenue_cagr_5y"] = None
    m["history_years"] = float(len(annual_rev))
    if len(annual_rev) >= 2:
        last = annual_rev[-1]
        base = min(annual_rev, key=lambda x: abs((x.end - (last.end - timedelta(days=365 * 5))).days))
        years = (last.end - base.end).days / 365.25
        if years >= 2.5 and base.value > 0 and last.value > 0:
            m["revenue_cagr_5y"] = (last.value / base.value) ** (1 / years) - 1
    # dilution: diluted share count now vs a year earlier
    q = latest_quarter(f.series("diluted_shares", as_of), as_of)
    m["dilution"] = None
    if q is not None:
        periods = _latest_per_period([x for x in f.series("diluted_shares", as_of) if x.start is not None])
        prior_q = [x for x in periods.values() if 80 <= x.days <= 100 and abs((x.end - (q.end - timedelta(days=365))).days) <= 20]
        if prior_q and prior_q[0].value > 0:
            m["dilution"] = q.value / prior_q[0].value - 1
    if financial in ("bank", "insurer"):
        m.update({"piotroski": None, "altman_z": None, "beneish_m": None})   # built for industrial companies
    else:
        m.update(scores(f, as_of, mcap, financial=financial))
    return m


def scores(f: Fundamentals, as_of: date, mcap: float | None, *, financial: str | None = None) -> dict[str, float | None]:
    """Piotroski F-score (0-9), Altman Z and Beneish M on the two latest fiscal years known by ``as_of``."""
    out: dict[str, float | None] = {"piotroski": None, "altman_z": None, "beneish_m": None}

    def annual(name: str) -> tuple[Fact, Fact] | None:
        rows = f.annual_values(name, as_of)
        if len(rows) < 2 or (rows[-1].end - rows[-2].end).days > 400:
            return None
        return rows[-2], rows[-1]

    def bs(name: str, end: date) -> float | None:
        fact = instant(f.series(name, as_of), as_of, near=end, tolerance=20)
        return None if fact is None else fact.value

    ni, cfo, rev = annual("net_income"), annual("cfo"), annual("revenue")
    if ni is None or rev is None:
        return out
    prev_end, end = rev[0].end, rev[1].end
    ta, ta0 = bs("assets", end), bs("assets", prev_end)
    if not ta or not ta0:
        return out
    gross = annual("gross_profit")
    cogs = annual("cost_of_revenue")
    gm = gm0 = None
    if gross:
        gm, gm0 = gross[1].value / rev[1].value, gross[0].value / rev[0].value
    elif cogs:
        gm, gm0 = 1 - cogs[1].value / rev[1].value, 1 - cogs[0].value / rev[0].value
    ltd, ltd0 = bs("lt_debt", end), bs("lt_debt", prev_end)
    ca, ca0, cl, cl0 = bs("current_assets", end), bs("current_assets", prev_end), bs("current_liabilities", end), bs(
        "current_liabilities", prev_end)
    shares = annual("diluted_shares")
    tests = [ni[1].value > 0, cfo is not None and cfo[1].value > 0,
             ni[1].value / ta > ni[0].value / ta0, cfo is not None and cfo[1].value > ni[1].value,
             ltd is not None and ltd0 is not None and ltd / ta < ltd0 / ta0,
             ca is not None and cl is not None and ca0 is not None and cl0 is not None and cl and cl0
             and ca / cl > ca0 / cl0,
             shares is not None and shares[1].value <= shares[0].value * 1.005,
             gm is not None and gm0 is not None and gm > gm0, rev[1].value / ta > rev[0].value / ta0]
    out["piotroski"] = float(sum(bool(t) for t in tests))
    if financial is None:
        tl = bs("liabilities", end)
        re_ = bs("retained_earnings", end)
        ebit = annual("operating_income")
        if ca is not None and cl is not None and tl and re_ is not None and ebit is not None and mcap:
            out["altman_z"] = (1.2 * (ca - cl) / ta + 1.4 * re_ / ta + 3.3 * ebit[1].value / ta + 0.6 * mcap / tl
                               + 1.0 * rev[1].value / ta)
        recv, recv0 = bs("receivables", end), bs("receivables", prev_end)
        ppe, ppe0 = bs("ppe", end), bs("ppe", prev_end)
        dep, sga = annual("dna"), annual("sga")
        if all(x is not None for x in (recv, recv0, gm, gm0, ca, ca0, ppe, ppe0, dep, cfo, tl)) and recv0 and gm and ppe and ppe0:
            assert recv is not None and recv0 is not None and gm is not None and gm0 is not None and ca is not None
            assert ca0 is not None and ppe is not None and ppe0 is not None and dep is not None and cfo is not None
            tl0 = bs("liabilities", prev_end)
            dsri = (recv / rev[1].value) / (recv0 / rev[0].value)
            gmi = gm0 / gm if gm else 1.0
            aqi = (1 - (ca + ppe) / ta) / (1 - (ca0 + ppe0) / ta0) if (1 - (ca0 + ppe0) / ta0) else 1.0
            sgi = rev[1].value / rev[0].value
            depi = ((dep[0].value / (dep[0].value + ppe0)) / (dep[1].value / (dep[1].value + ppe))
                    if dep[1].value and (dep[0].value + ppe0) and (dep[1].value + ppe) else 1.0)
            sgai = ((sga[1].value / rev[1].value) / (sga[0].value / rev[0].value)) if sga and sga[0].value else 1.0
            lvgi = ((tl / ta) / (tl0 / ta0)) if tl and tl0 else 1.0
            tata = (ni[1].value - cfo[1].value) / ta
            out["beneish_m"] = (-4.84 + 0.92 * dsri + 0.528 * gmi + 0.404 * aqi + 0.892 * sgi + 0.115 * depi
                                - 0.172 * sgai + 4.679 * tata - 0.327 * lvgi)
    return out


def implied_growth(ev: float, fcf: float, discount: float, terminal: float = 0.025, years: int = 10) -> float | None:
    """The yearly cash-flow growth for ``years`` that makes a DCF equal today's enterprise value (reverse DCF)."""
    if fcf <= 0 or ev <= 0 or discount <= terminal:
        return None

    def value(g: float) -> float:
        total, cash = 0.0, fcf
        for t in range(1, years + 1):
            cash *= 1 + g
            total += cash / (1 + discount) ** t
        return total + cash * (1 + terminal) / (discount - terminal) / (1 + discount) ** years

    lo, hi = -0.5, 1.0
    if value(lo) > ev or value(hi) < ev:
        return None
    for _ in range(80):
        mid = (lo + hi) / 2
        if value(mid) < ev:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def dcf_value(fcf: float, growth: float, discount: float, terminal: float = 0.025, years: int = 10) -> float:
    """Enterprise value with growth fading linearly from ``growth`` to ``terminal`` over ``years``."""
    total, cash = 0.0, fcf
    for t in range(1, years + 1):
        g = growth + (terminal - growth) * (t - 1) / (years - 1)
        cash *= 1 + g
        total += cash / (1 + discount) ** t
    return total + cash * (1 + terminal) / (discount - terminal) / (1 + discount) ** years


__all__ = ["CONCEPTS", "FLOWS", "INSTANTS", "Flow", "Fundamentals", "dcf_value", "implied_growth", "instant", "latest_quarter",
           "metrics", "scores", "ttm"]
