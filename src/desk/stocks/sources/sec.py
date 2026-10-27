"""SEC EDGAR: company list, submissions (filings and 8-K items), XBRL company facts, daily index, Form 4.

Two hosts: ``data.sec.gov`` (submissions, company facts) and ``www.sec.gov`` (company list, daily index, filing
documents, fails-to-deliver). The SEC's fair-access policy asks for at most 10 requests a second and a User-Agent
that names a contact; www.sec.gov refuses requests without one (verified 2026-10-21), so the desk sends
``SEC_CONTACT_EMAIL`` from secrets.env and never a built-in address.
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from collections.abc import Iterable
from datetime import UTC, date, datetime
from xml.etree import ElementTree

from desk.core.errors import ProviderError, ProviderErrorKind, SchemaError
from desk.providers.http import ProviderHttp
from desk.stocks.contracts import Company, Fact, Fail, Filing, IndexEntry, InsiderTrade
from desk.stocks.sources.validation import json_object, number, schema_boundary

FACT_FORMS = frozenset({"10-K", "10-Q", "10-K/A", "10-Q/A", "10-KT", "20-F", "40-F"})


def canonical_symbol(symbol: str) -> str:
    """SEC writes class shares with a dash (BRK-B); the desk uses the exchange's dot (BRK.B)."""
    return symbol.strip().upper().replace("-", ".")


def _day(text: str | None) -> date | None:
    return date.fromisoformat(text[:10]) if text else None


# ── parsers (pure) ───────────────────────────────────────────────────────────────────────────────
@schema_boundary("sec tickers_exchange")
def parse_tickers_exchange(body: bytes) -> list[tuple[int, str, str, str]]:
    """``company_tickers_exchange.json`` → (cik, name, symbol, exchange)."""
    try:
        data = json_object(body)
        fields = data["fields"]
        idx = {name: fields.index(name) for name in ("cik", "name", "ticker", "exchange")}
        return [(int(r[idx["cik"]]), str(r[idx["name"]]), canonical_symbol(str(r[idx["ticker"]])),
                 str(r[idx["exchange"]] or "")) for r in data["data"] if r[idx["ticker"]]]
    except (ValueError, KeyError, TypeError) as exc:
        raise SchemaError(f"sec company tickers: {exc}") from exc


@schema_boundary("sec submissions")
def parse_submissions(body: bytes) -> Company:
    try:
        d = json_object(body)
        recent = d["filings"]["recent"]
        n = len(recent["accessionNumber"])
        filings = []
        for i in range(n):
            accepted = recent["acceptanceDateTime"][i]
            filings.append(Filing(
                cik=int(d["cik"]), accession=recent["accessionNumber"][i], form=recent["form"][i],
                filed=date.fromisoformat(recent["filingDate"][i]),
                accepted=datetime.fromisoformat(accepted.replace("Z", "+00:00")).astimezone(UTC) if accepted else None,
                report_date=_day(recent["reportDate"][i] or None),
                items=tuple(x.strip() for x in (recent.get("items", [""] * n)[i] or "").split(",") if x.strip()),
                primary_document=recent["primaryDocument"][i] or ""))
        return Company(cik=int(d["cik"]), name=d["name"], sic=d.get("sic") or None,
                       sic_description=d.get("sicDescription") or None,
                       tickers=tuple(canonical_symbol(t) for t in d.get("tickers", [])),
                       exchanges=tuple(e or "" for e in d.get("exchanges", [])),
                       fiscal_year_end=d.get("fiscalYearEnd") or None, category=d.get("category") or None,
                       filings=tuple(filings), profile={
                           "website": str(d.get("website") or ""), "phone": str(d.get("phone") or ""),
                           "headquarters": ", ".join(str(v) for k in ("street1", "street2", "city", "stateOrCountry", "zipCode")
                               if (v := ((d.get("addresses") or {}).get("business") or {}).get(k)))})
    except (ValueError, KeyError, TypeError, IndexError) as exc:
        raise SchemaError("sec submissions: invalid filing response") from exc


@schema_boundary("sec companyfacts")
def parse_companyfacts(body: bytes, wanted: Iterable[str]) -> dict[str, list[Fact]]:
    """Keep the wanted ``taxonomy:Concept`` values from 10-K/10-Q-type filings, oldest filing first."""
    keep = set(wanted)
    try:
        d = json_object(body)
        out: dict[str, list[Fact]] = {}
        for taxonomy, concepts in d.get("facts", {}).items():
            for concept, content in concepts.items():
                key = f"{taxonomy}:{concept}"
                if key not in keep:
                    continue
                rows = out.setdefault(key, [])
                for unit, values in content["units"].items():
                    for v in values:
                        if v.get("form") not in FACT_FORMS or v.get("val") is None:
                            continue
                        rows.append(Fact(key, unit, _day(v.get("start")), date.fromisoformat(v["end"]), number(v["val"]),
                                         v["form"], date.fromisoformat(v["filed"]), v.get("fy"), v.get("fp"), v["accn"]))
                rows.sort(key=lambda f: (f.filed, f.end, f.accn))
        return out
    except (ValueError, KeyError, TypeError) as exc:
        raise SchemaError("sec company facts: invalid fact response") from exc


_INDEX_ROW = re.compile(r"^(?P<form>\S(?:.*?\S)?)\s{2,}(?P<company>\S.*?)\s{2,}(?P<cik>\d+)\s+(?P<filed>\d{8})\s+"
                        r"(?P<path>edgar/data/\S+)\s*$")


@schema_boundary("sec daily_index")
def parse_daily_index(body: bytes) -> list[IndexEntry]:
    """``daily-index/YYYY/QTRn/form.YYYYMMDD.idx`` (fixed-width) → entries."""
    text = body.decode("latin-1")
    lines = text.splitlines()
    try:
        start = next(i for i, line in enumerate(lines) if line.startswith("-----")) + 1
    except StopIteration as exc:
        raise SchemaError("sec daily index: no header rule") from exc
    out = []
    for line in lines[start:]:
        m = _INDEX_ROW.match(line)
        if m:
            filed = m["filed"]
            out.append(IndexEntry(m["form"], m["company"], int(m["cik"]),
                                  date(int(filed[:4]), int(filed[4:6]), int(filed[6:])), m["path"]))
    return out


def _text(node: ElementTree.Element | None, path: str) -> str | None:
    if node is None:
        return None
    found = node.find(path)
    if found is None:
        return None
    value = found.find("value")
    raw = (value.text if value is not None else found.text) or ""
    return raw.strip() or None


def _flag(node: ElementTree.Element | None, path: str) -> bool:
    return (_text(node, path) or "0").lower() in ("1", "true")


def _num(text: str | None) -> float | None:
    if text is None:
        return None
    try:
        return number(text.replace(",", ""), nonnegative=True)
    except ValueError:
        return None


@schema_boundary("sec form4")
def parse_form4(xml: bytes | str, accession: str) -> list[InsiderTrade]:
    """Non-derivative transactions of a Form 4 (one row per transaction per reporting owner)."""
    try:
        # SEC ownership XML: ElementTree never resolves external entities, and the bundled expat (2.5+) limits
        # entity expansion, so the attacks defusedxml guards against do not apply.
        root = ElementTree.fromstring(xml)  # noqa: S314
    except ElementTree.ParseError as exc:
        raise SchemaError(f"form 4 {accession}: {exc}") from exc
    if root.tag != "ownershipDocument":
        raise SchemaError(f"form 4 {accession}: not an ownership document")
    issuer = root.find("issuer")
    issuer_cik = int(_text(issuer, "issuerCik") or 0)
    symbol = canonical_symbol(_text(issuer, "issuerTradingSymbol") or "")
    planned = _flag(root, "aff10b5One")
    owners = []
    for owner in root.findall("reportingOwner"):
        rel = owner.find("reportingOwnerRelationship")
        owners.append((_text(owner, "reportingOwnerId/rptOwnerName") or "",
                       int(_text(owner, "reportingOwnerId/rptOwnerCik") or 0), _flag(rel, "isDirector"),
                       _flag(rel, "isOfficer"), _text(rel, "officerTitle"), _flag(rel, "isTenPercentOwner")))
    trades = []
    for tx in root.findall("nonDerivativeTable/nonDerivativeTransaction"):
        day = _day(_text(tx, "transactionDate"))
        code = _text(tx, "transactionCoding/transactionCode")
        shares = _num(_text(tx, "transactionAmounts/transactionShares"))
        if day is None or code is None or shares is None:
            continue
        price = _num(_text(tx, "transactionAmounts/transactionPricePerShare"))
        acquired = (_text(tx, "transactionAmounts/transactionAcquiredDisposedCode") or "A") == "A"
        after = _num(_text(tx, "postTransactionAmounts/sharesOwnedFollowingTransaction"))
        direct = (_text(tx, "ownershipNature/directOrIndirectOwnership") or "D") == "D"
        for name, cik, director, officer, title, ten in owners:
            trades.append(InsiderTrade(accession, issuer_cik, symbol, name, cik, director, officer, title, ten, day, code,
                                       shares, price, acquired, after, direct, planned))
    return trades


_MONTHS = {m: i + 1 for i, m in enumerate(("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV",
                                           "DEC"))}


def _sec_day(text: str) -> date | None:
    """The insider data sets write dates as 31-JUL-2026."""
    try:
        d, m, y = text.strip().split("-")
        return date(int(y), _MONTHS[m.upper()], int(d))
    except (ValueError, KeyError):
        return None


@schema_boundary("sec insider_dataset")
def parse_insider_dataset(body: bytes) -> list[tuple[InsiderTrade, date]]:
    """A quarterly Insider Transactions Data Set (Forms 3, 4, 5): Form 4 non-derivative transactions with the date
    each was filed, one row per transaction per reporting owner (as ``parse_form4``)."""
    def table(zf: zipfile.ZipFile, name: str) -> list[dict[str, str]]:
        lines = zf.read(name).decode("utf-8", "replace").splitlines()
        head = lines[0].split("\t")
        return [dict(zip(head, line.split("\t"), strict=False)) for line in lines[1:] if line]

    try:
        with zipfile.ZipFile(io.BytesIO(body)) as zf:
            if sum(info.file_size for info in zf.infolist()) > 128 * 1024 * 1024:
                raise SchemaError('insider data set: uncompressed archive exceeds 128 MiB')
            subs = {r["ACCESSION_NUMBER"]: r for r in table(zf, "SUBMISSION.tsv") if r.get("DOCUMENT_TYPE") in ("4", "4/A")}
            owners: dict[str, list[dict[str, str]]] = {}
            for r in table(zf, "REPORTINGOWNER.tsv"):
                owners.setdefault(r["ACCESSION_NUMBER"], []).append(r)
            rows = table(zf, "NONDERIV_TRANS.tsv")
    except (zipfile.BadZipFile, KeyError, IndexError) as exc:
        raise SchemaError(f"insider data set: {exc}") from exc
    out = []
    for r in rows:
        sub = subs.get(r["ACCESSION_NUMBER"])
        if sub is None or not sub.get("ISSUERTRADINGSYMBOL"):
            continue
        filed, day, shares = _sec_day(sub["FILING_DATE"]), _sec_day(r.get("TRANS_DATE", "")), _num(r.get("TRANS_SHARES"))
        if filed is None or day is None or shares is None or not r.get("TRANS_CODE"):
            continue
        for o in owners.get(r["ACCESSION_NUMBER"], []):
            rel = {x.strip() for x in o.get("RPTOWNER_RELATIONSHIP", "").split(",")}
            trade = InsiderTrade(r["ACCESSION_NUMBER"], int(sub["ISSUERCIK"] or 0), canonical_symbol(sub["ISSUERTRADINGSYMBOL"]),
                                 o.get("RPTOWNERNAME", ""), int(o.get("RPTOWNERCIK") or 0), "Director" in rel, "Officer" in rel,
                                 o.get("RPTOWNER_TITLE") or None, "TenPercentOwner" in rel, day, r["TRANS_CODE"], shares,
                                 _num(r.get("TRANS_PRICEPERSHARE")), r.get("TRANS_ACQUIRED_DISP_CD", "A") == "A",
                                 _num(r.get("SHRS_OWND_FOLWNG_TRANS")), r.get("DIRECT_INDIRECT_OWNERSHIP", "D") == "D",
                                 sub.get("AFF10B5ONE", "0").strip() in ("1", "true"))
            out.append((trade, filed))
    return out


_ACCEPTED = re.compile(r"<ACCEPTANCE-DATETIME>(\d{14})")
_XML = re.compile(r"<XML>\s*(.*?)\s*</XML>", re.S)


@schema_boundary("sec submission_text")
def parse_submission_text(text: str) -> tuple[datetime | None, str | None]:
    """A full submission (``edgar/data/<cik>/<accession>.txt``): its acceptance time (New York time, naive) and its
    first XML document."""
    m = _ACCEPTED.search(text)
    accepted = datetime.strptime(m.group(1), "%Y%m%d%H%M%S") if m else None
    x = _XML.search(text)
    return accepted, (x.group(1) if x else None)


@schema_boundary("sec fails")
def parse_fails(text: str) -> list[Fail]:
    """SEC fails-to-deliver text (pipe-separated, one settlement date per row)."""
    out = []
    for line in text.splitlines()[1:]:
        parts = line.split("|")
        if len(parts) < 6 or not parts[0].isdigit():
            continue
        d = parts[0]
        qty = _num(parts[3])
        if qty is None or not parts[2].strip():
            continue
        out.append(Fail(date(int(d[:4]), int(d[4:6]), int(d[6:])), parts[1].strip(), canonical_symbol(parts[2]), qty,
                        _num(parts[5])))
    return out


@schema_boundary("sec fails_zip")
def parse_fails_zip(body: bytes) -> list[Fail]:
    with zipfile.ZipFile(io.BytesIO(body)) as zf:
        if not zf.infolist() or sum(info.file_size for info in zf.infolist()) > 128 * 1024 * 1024:
            raise SchemaError("sec fails archive: empty or exceeds unpacked size limit")
        return parse_fails(zf.read(zf.namelist()[0]).decode("latin-1"))


# ── client ───────────────────────────────────────────────────────────────────────────────────────
class SecClient:
    def __init__(self, data_http: ProviderHttp, www_http: ProviderHttp) -> None:
        self.data = data_http
        self.www = www_http

    @property
    def contact_configured(self) -> bool:
        return bool(self.www.user_agent)

    async def _get(self, http: ProviderHttp, endpoint: str, path: str, *, label: str | None = None) -> bytes:
        if http is self.www and not self.contact_configured:
            raise ProviderError(http.name, ProviderErrorKind.HTTP_ERROR,
                                "SEC_CONTACT_EMAIL is not in secrets.env (www.sec.gov requires a contact)")
        resp = await http.request(endpoint, path=path, label=label or endpoint)
        if resp.status == 404:
            raise ProviderError(http.name, ProviderErrorKind.HTTP_ERROR, f"{endpoint}: not found", 404)
        if resp.status != 200:
            raise ProviderError(http.name, ProviderErrorKind.HTTP_ERROR, f"{endpoint}: HTTP {resp.status}", resp.status)
        return resp.body

    async def tickers(self) -> list[tuple[int, str, str, str]]:
        return parse_tickers_exchange(await self._get(self.www, "company_tickers", "/files/company_tickers_exchange.json"))

    async def submissions(self, cik: int) -> Company:
        return parse_submissions(await self._get(self.data, "submissions", f"/submissions/CIK{cik:010d}.json"))

    async def companyfacts_raw(self, cik: int) -> bytes:
        return await self._get(self.data, "companyfacts", f"/api/xbrl/companyfacts/CIK{cik:010d}.json")

    async def daily_index(self, day: date) -> list[IndexEntry]:
        quarter = (day.month - 1) // 3 + 1
        path = f"/Archives/edgar/daily-index/{day.year}/QTR{quarter}/form.{day:%Y%m%d}.idx"
        return parse_daily_index(await self._get(self.www, "daily_index", path))

    async def filing_documents(self, cik: int, accession: str) -> list[str]:
        body = await self._get(self.www, "filing_index", f"/Archives/edgar/data/{cik}/{accession.replace('-', '')}/index.json")
        try:
            return [str(i["name"]) for i in json.loads(body)["directory"]["item"]]
        except (ValueError, KeyError, TypeError) as exc:
            raise SchemaError(f"filing index {accession}: {exc}") from exc

    async def document(self, cik: int, accession: str, name: str) -> bytes:
        return await self._get(self.www, "filing_document",
                               f"/Archives/edgar/data/{cik}/{accession.replace('-', '')}/{name}")

    async def form4(self, cik: int, accession: str) -> list[InsiderTrade]:
        xmls = [n for n in await self.filing_documents(cik, accession) if n.endswith(".xml") and not n.startswith("xsl")]
        if not xmls:
            return []
        return parse_form4(await self.document(cik, accession, xmls[0]), accession)

    async def submission_text(self, path: str) -> str:
        """``path`` as the daily index lists it (``edgar/data/<cik>/<accession>.txt``)."""
        return (await self._get(self.www, "filing_document", "/Archives/" + path, label="submission_text")).decode(
            "utf-8", "replace")

    async def insider_dataset_paths(self) -> list[str]:
        page = (await self._get(self.www, "insider_datasets", "/data-research/sec-markets-data/insider-transactions-data-sets")
                ).decode("utf-8", "replace")
        return sorted(set(re.findall(r'href="(/files/[^"]+_form345\.zip)"', page)), reverse=True)

    async def insider_dataset(self, path: str) -> list[tuple[InsiderTrade, date]]:
        return parse_insider_dataset(await self._get(self.www, "insider_dataset", path))

    async def fails(self, name: str) -> list[Fail]:
        return parse_fails_zip(await self._get(self.www, "fails_to_deliver", f"/files/data/fails-deliver-data/{name}"))


def ftd_file_names(day: date) -> list[str]:
    """Fails-to-deliver files are published twice a month: ``a`` covers the first half, ``b`` the second."""
    return [f"cnsfails{day:%Y%m}a.zip", f"cnsfails{day:%Y%m}b.zip"]


def sec_user_agent(contact: str | None) -> str | None:
    """The agent the SEC asks for: who is asking, and how to reach them."""
    if not contact or "@" not in contact:
        return None
    return f"KESTREL personal research desk {contact.strip()}"


__all__ = ["SecClient", "canonical_symbol", "ftd_file_names", "parse_companyfacts", "parse_daily_index", "parse_fails",
           "parse_fails_zip", "parse_form4", "parse_insider_dataset", "parse_submission_text", "parse_submissions",
           "parse_tickers_exchange", "sec_user_agent"]
