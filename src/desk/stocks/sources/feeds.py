"""Keyless public feeds: Nasdaq Trader symbol lists, FINRA short data, Treasury and FRED rates, ApeWisdom, OpenFIGI."""

from __future__ import annotations

import csv
import io
import json
import re
from datetime import date, timedelta
from typing import Any

from desk.core.errors import ProviderError, ProviderErrorKind, SchemaError
from desk.providers.http import ProviderHttp
from desk.stocks.contracts import Listing, Mention, RatePoint, ShortInterest, ShortVolume

OTHER_EXCHANGES = {"A": "NYSE American", "N": "NYSE", "P": "NYSE Arca", "Z": "Cboe BZX", "V": "IEX"}

_KIND_RULES = [
    ("common", re.compile(r"limited partnership units|common units", re.I)),
    ("warrant", re.compile(r"\bwarrants?\b", re.I)),
    ("right", re.compile(r"\brights?\b", re.I)),
    ("unit", re.compile(r"\bunits?\b", re.I)),
    ("preferred", re.compile(r"\bpreferred\b|\bpfd\b", re.I)),
    ("note", re.compile(r"\bnotes? due\b|\bdebentures?\b|% (senior|subordinated)|\bbonds?\b", re.I)),
    ("fund", re.compile(r"\bfund\b|\btrust units?\b|\bclosed[- ]end\b", re.I)),
    ("adr", re.compile(r"american depositary|\bads\b|\badr\b", re.I)),
    ("common", re.compile(r"common stock|common shares|ordinary shares|capital stock|class [a-c] (common )?shares?|"
                          r"\bshares of beneficial interest\b|\bcommon\b", re.I)),
]


def classify(name: str, *, etf: bool) -> str:
    if etf:
        return "etf"
    for kind, rule in _KIND_RULES:
        if rule.search(name):
            return kind
    return "other"


def _rows(text: str) -> list[list[str]]:
    lines = [line for line in text.splitlines() if line and not line.startswith("File Creation Time")]
    return [line.split("|") for line in lines]


def parse_nasdaq_listed(text: str) -> list[Listing]:
    rows = _rows(text)
    if not rows or rows[0][:2] != ["Symbol", "Security Name"]:
        raise SchemaError("nasdaqlisted: unexpected header")
    out = []
    for r in rows[1:]:
        if len(r) < 8:
            continue
        etf = r[6] == "Y"
        out.append(Listing(r[0].strip(), r[1].strip(), "Nasdaq", etf, r[3] == "Y", classify(r[1], etf=etf)))
    return out


def parse_other_listed(text: str) -> list[Listing]:
    rows = _rows(text)
    if not rows or rows[0][:3] != ["ACT Symbol", "Security Name", "Exchange"]:
        raise SchemaError("otherlisted: unexpected header")
    out = []
    for r in rows[1:]:
        if len(r) < 8:
            continue
        etf = r[4] == "Y"
        out.append(Listing(r[0].strip(), r[1].strip(), OTHER_EXCHANGES.get(r[2], r[2]), etf, r[6] == "Y",
                           classify(r[1], etf=etf)))
    return out


def parse_short_interest(body: bytes) -> list[ShortInterest]:
    """FINRA consolidated short interest (Query API JSON)."""
    try:
        rows = json.loads(body)
        def opt(r: dict[str, Any], key: str) -> float | None:
            return None if r.get(key) is None else float(r[key])

        return [ShortInterest(str(r["symbolCode"]).replace("/", "."), date.fromisoformat(r["settlementDate"]),
                              float(r["currentShortPositionQuantity"]), opt(r, "previousShortPositionQuantity"),
                              opt(r, "averageDailyVolumeQuantity"), opt(r, "daysToCoverQuantity"))
                for r in rows if r.get("symbolCode") and r.get("currentShortPositionQuantity") is not None]
    except (ValueError, KeyError, TypeError) as exc:
        raise SchemaError(f"finra short interest: {exc}") from exc


def parse_reg_sho(text: str) -> list[ShortVolume]:
    """FINRA consolidated daily short sale volume (``CNMSshvolYYYYMMDD.txt``)."""
    out = []
    lines = text.splitlines()
    if not lines or not lines[0].startswith("Date|Symbol|ShortVolume"):
        raise SchemaError("finra reg sho: unexpected header")
    for line in lines[1:]:
        p = line.split("|")
        if len(p) < 5 or not p[0].isdigit():
            continue
        d = p[0]
        out.append(ShortVolume(p[1].replace("/", "."), date(int(d[:4]), int(d[4:6]), int(d[6:])), float(p[2]), float(p[3]),
                               float(p[4])))
    return out


TREASURY_COLUMNS = {"3 Mo": "UST3M", "2 Yr": "UST2Y", "10 Yr": "UST10Y", "30 Yr": "UST30Y"}


def parse_treasury_curve(text: str) -> list[RatePoint]:
    reader = csv.DictReader(io.StringIO(text))
    out = []
    for row in reader:
        try:
            month, day, year = (int(x) for x in row["Date"].split("/"))
        except (KeyError, ValueError):
            continue
        for column, series in TREASURY_COLUMNS.items():
            value = (row.get(column) or "").strip()
            if value:
                out.append(RatePoint(series, date(year, month, day), float(value)))
    return out


def parse_fred_csv(text: str, series: str) -> list[RatePoint]:
    reader = csv.reader(io.StringIO(text))
    header = next(reader, None)
    if not header or len(header) < 2:
        raise SchemaError(f"fred {series}: unexpected header")
    out = []
    for row in reader:
        if len(row) < 2 or not row[1].strip() or row[1].strip() == ".":
            continue
        out.append(RatePoint(series, date.fromisoformat(row[0]), float(row[1])))
    return out


def parse_apewisdom(body: bytes, source: str) -> tuple[list[Mention], int]:
    """One page of mentions and the number of pages."""
    try:
        d = json.loads(body)

        def num(value: Any) -> int | None:
            return None if value in (None, "") else int(value)

        return ([Mention(str(r["ticker"]).upper().replace("-", "."), str(r.get("name") or ""), source, int(r["rank"]),
                         int(r["mentions"]), int(r.get("upvotes") or 0), num(r.get("rank_24h_ago")),
                         num(r.get("mentions_24h_ago"))) for r in d["results"]], int(d.get("pages") or 1))
    except (ValueError, KeyError, TypeError) as exc:
        raise SchemaError(f"apewisdom {source}: {exc}") from exc


def parse_openfigi(body: bytes, cusips: list[str]) -> dict[str, str | None]:
    """CUSIP → US common-stock ticker (None when OpenFIGI has no equity match)."""
    try:
        results = json.loads(body)
    except ValueError as exc:
        raise SchemaError(f"openfigi: {exc}") from exc
    out: dict[str, str | None] = {}
    for cusip, result in zip(cusips, results, strict=True):
        ticker = None
        for item in result.get("data", []):
            if item.get("exchCode") == "US" and item.get("marketSector") == "Equity" and item.get("ticker"):
                ticker = str(item["ticker"]).replace("/", ".")
                break
        out[cusip] = ticker
    return out


async def _ok(http: ProviderHttp, endpoint: str, *, empty: bytes | None = None, **kwargs: Any) -> bytes:
    resp = await http.request(endpoint, **kwargs)
    if resp.status == 204 and empty is not None:
        return empty                       # FINRA: no rows for that partition (not yet published)
    if resp.status == 404:
        raise ProviderError(http.name, ProviderErrorKind.HTTP_ERROR, f"{endpoint}: not found", 404)
    if resp.status != 200:
        raise ProviderError(http.name, ProviderErrorKind.HTTP_ERROR, f"{endpoint}: HTTP {resp.status}", resp.status)
    return resp.body


class FeedsClient:
    def __init__(self, nasdaq: ProviderHttp, finra_api: ProviderHttp, finra_cdn: ProviderHttp, treasury: ProviderHttp,
                 fred: ProviderHttp, apewisdom: ProviderHttp, openfigi: ProviderHttp) -> None:
        self.nasdaq, self.finra_api, self.finra_cdn = nasdaq, finra_api, finra_cdn
        self.treasury, self.fred, self.apewisdom, self.openfigi = treasury, fred, apewisdom, openfigi

    async def listings(self) -> list[Listing]:
        nasdaq = parse_nasdaq_listed((await _ok(self.nasdaq, "nasdaq_listed")).decode())
        other = parse_other_listed((await _ok(self.nasdaq, "other_listed")).decode())
        return nasdaq + other

    async def short_interest(self, settlement: date, symbols: list[str] | None = None) -> list[ShortInterest]:
        """Every symbol's short interest for one settlement date (paged 5,000 at a time; none if not published)."""
        out: list[ShortInterest] = []
        offset = 0
        while True:
            body: dict[str, Any] = {"limit": 5000, "offset": offset, "compareFilters": [
                {"compareType": "EQUAL", "fieldName": "settlementDate", "fieldValue": settlement.isoformat()}]}
            if symbols:
                body["domainFilters"] = [{"fieldName": "symbolCode", "values": symbols}]
            page = parse_short_interest(await _ok(self.finra_api, "consolidated_short_interest", method="POST",
                                                  json_body=body, empty=b"[]"))
            out += page
            if len(page) < 5000:
                return out
            offset += 5000

    async def latest_short_interest(self, today: date) -> tuple[date | None, list[ShortInterest]]:
        """The newest published settlement (FINRA sorts only within one settlement date, so try them newest first)."""
        for candidate in settlement_candidates(today):
            probe = await _ok(self.finra_api, "consolidated_short_interest", method="POST", empty=b"[]", json_body={
                "limit": 1, "compareFilters": [{"compareType": "EQUAL", "fieldName": "settlementDate",
                                                "fieldValue": candidate.isoformat()}]})
            if parse_short_interest(probe):
                return candidate, await self.short_interest(candidate)
        return None, []

    async def reg_sho(self, day: date) -> list[ShortVolume]:
        body = await _ok(self.finra_cdn, "reg_sho_daily", path=f"/equity/regsho/daily/CNMSshvol{day:%Y%m%d}.txt")
        return parse_reg_sho(body.decode())

    async def treasury_curve(self, year: int) -> list[RatePoint]:
        params = {"type": "daily_treasury_yield_curve", "field_tdr_date_value": str(year), "page": "", "_format": "csv"}
        path = f"/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/{year}/all"
        body = await _ok(self.treasury, "yield_curve", path=path, params=params)
        return parse_treasury_curve(body.decode())

    async def fred_series(self, series: str, since: date) -> list[RatePoint]:
        body = await _ok(self.fred, "graph_csv", params={"id": series, "cosd": since.isoformat()})
        return parse_fred_csv(body.decode(), series)

    async def mentions(self, source: str, pages: int = 10) -> list[Mention]:
        out: list[Mention] = []
        page = 1
        while page <= pages:
            rows, total = parse_apewisdom(await _ok(self.apewisdom, "mentions", path=f"/api/v1.0/filter/{source}/page/{page}"),
                                          source)
            out += rows
            if page >= total:
                break
            page += 1
        return out

    async def cusip_tickers(self, cusips: list[str]) -> dict[str, str | None]:
        """OpenFIGI without a key takes 10 identifiers per request (25 requests a minute)."""
        out: dict[str, str | None] = {}
        for i in range(0, len(cusips), 10):
            chunk = cusips[i:i + 10]
            body = await _ok(self.openfigi, "mapping", method="POST",
                             json_body=[{"idType": "ID_CUSIP", "idValue": c} for c in chunk])
            out.update(parse_openfigi(body, chunk))
        return out


def settlement_candidates(today: date, months: int = 3) -> list[date]:
    """FINRA short interest settles mid-month and at month end, moved back to a weekday (and one more day when that
    is a holiday): candidate dates newest first."""
    out: set[date] = set()
    year, month = today.year, today.month
    for _ in range(months):
        month_end = date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1)
        for anchor in (date(year, month, 15), month_end):
            d = anchor
            while d.weekday() >= 5:
                d -= timedelta(days=1)
            for c in (d, d - timedelta(days=1 if d.weekday() else 3)):
                if c <= today:
                    out.add(c)
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    return sorted(out, reverse=True)


__all__ = ["FeedsClient", "classify", "parse_apewisdom", "parse_fred_csv", "parse_nasdaq_listed",
           "parse_openfigi", "parse_other_listed", "parse_reg_sho", "parse_short_interest", "parse_treasury_curve",
           "settlement_candidates"]
