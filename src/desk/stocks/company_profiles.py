"""Company context from SEC submissions and the issuer's annual business section.

Display only, outside research features. A separate bounded task serves viewed symbols, preserving the last
good profile on failure and sharing existing SEC rate limits. No additional paid API or language model needed.
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timedelta
from html.parser import HTMLParser
from typing import Any

from desk.core.clock import to_iso, utc_now
from desk.core.config import LoadedConfig
from desk.core.paths import DeskPaths
from desk.core.secrets import load_secrets
from desk.stocks.repo import MARKET_FILE, StocksMarket
from desk.stocks.suite import build_stocks_suite
from desk.storage.db import DbKind, connect


class Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "ix:hidden"}:
            self.hidden += 1
        if tag in {"p", "div", "br", "tr", "h1", "h2", "h3", "h4"}:
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "ix:hidden"}:
            self.hidden = max(0, self.hidden - 1)
        self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            self.parts.append(data)


def business_excerpt(body: bytes, form: str = "10-K") -> str | None:
    parser = Text()
    parser.feed(body.decode("utf-8", "replace"))
    text = re.sub(r"\s+", " ", "".join(parser.parts))
    start = r"\bITEM\s*1\s*[.:—-]?\s*BUSINESS\b" if form == "10-K" else r"\bITEM\s*4\s*[.:—-]?\s*INFORMATION ON THE COMPANY\b"
    end = r"\bITEM\s*1\s*A\s*[.:—-]?\s*RISK FACTORS\b" if form == "10-K" else r"\bITEM\s*(?:4\s*A|5)\s*[.:—-]"
    for match in re.finditer(start, text, flags=re.I):
        following = text[match.end():]
        stop = re.search(end, following, flags=re.I)
        # Reject table-of-contents entries and unidentified boundaries instead of publishing unrelated boilerplate.
        if stop and stop.start() >= 500:
            section = following[:stop.start()].strip(" .:")
            if len(section) > 2000:
                section = section[:2000].rsplit(" ", 1)[0] + "…"
            return section
    return None


def request(conn: sqlite3.Connection, symbol: str, now: datetime) -> None:
    row = conn.execute("SELECT cik,etf FROM stk_listings WHERE symbol=?", (symbol,)).fetchone()
    if row and row["cik"] and not row["etf"]:
        conn.execute("INSERT INTO stk_company_profiles(cik,requested_at) VALUES (?,?) "
                     "ON CONFLICT(cik) DO UPDATE SET requested_at=excluded.requested_at", (row["cik"], to_iso(now)))


def read(conn: sqlite3.Connection, symbol: str) -> dict[str, Any]:
    row = conn.execute("SELECT c.cik,c.name,c.sector,c.sic_description,c.fiscal_year_end,c.category,c.submissions_at,"
                       "p.metadata_json,p.about,p.source_url,p.filing_date,p.updated_at,p.error,"
                       "p.requested_at FROM stk_listings l LEFT JOIN stk_companies c ON c.cik=l.cik "
                       "LEFT JOIN stk_company_profiles p ON p.cik=l.cik WHERE l.symbol=?", (symbol,)).fetchone()
    if row is None:
        return {"status": "unavailable", "message": "No company profile is available for this symbol."}
    result = dict(row)
    try:
        result["metadata"] = json.loads(result.pop("metadata_json") or "{}")
    except (TypeError, ValueError):
        result["metadata"] = {}
    result["status"] = "available" if row["about"] else "pending" if row["requested_at"] else "metadata"
    return result


async def refresh(paths: DeskPaths, loaded: LoadedConfig) -> None:
    conn = connect(paths.db_dir / MARKET_FILE, DbKind.STOCKS_MARKET)
    runtime = connect(paths.db_dir / "runtime.db", DbKind.RUNTIME)
    suite = None
    try:
        now = utc_now()
        row = conn.execute("SELECT * FROM stk_company_profiles WHERE requested_at IS NOT NULL "
                           "AND (updated_at IS NULL OR updated_at<?) AND (attempted_at IS NULL OR attempted_at<?) "
                           "ORDER BY attempted_at, requested_at DESC LIMIT 1",
                           (to_iso(now - timedelta(days=30)), to_iso(now - timedelta(minutes=10)))).fetchone()
        if row is None:
            return
        cik = row["cik"]
        conn.execute("UPDATE stk_company_profiles SET attempted_at=? WHERE cik=?", (to_iso(now), cik))
        suite = build_stocks_suite(paths, loaded, load_secrets(paths.secrets_file), runtime)
        if "sec" in suite.problems:
            conn.execute("UPDATE stk_company_profiles SET error=? WHERE cik=?",
                         ("Add your SEC contact email in Settings → Data sources to load the business profile.", cik))
            return
        try:
            company = await suite.sec.submissions(cik)
            market = StocksMarket(conn)
            old = market.company(cik)
            market.save_company(company, old["sector"] if old else None, now)
            annual = next((f for f in sorted(company.filings, key=lambda f: f.filed, reverse=True)
                           if f.form in {"10-K", "20-F"} and f.primary_document), None)
            if annual is None:
                conn.execute("UPDATE stk_company_profiles SET updated_at=?,error=? WHERE cik=?",
                             (to_iso(now), "Company metadata loaded; no supported annual business filing was found.", cik))
                return
            about = business_excerpt(await suite.sec.document(cik, annual.accession, annual.primary_document), annual.form)
            url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{annual.accession.replace('-', '')}/{annual.primary_document}"
            if about:
                conn.execute("UPDATE stk_company_profiles SET about=?,source_url=?,filing_date=?,updated_at=?,error=NULL "
                             "WHERE cik=?", (about, url, annual.filed.isoformat(), to_iso(now), cik))
            else:
                conn.execute("UPDATE stk_company_profiles SET updated_at=?,error=? WHERE cik=?",
                             (to_iso(now), "Metadata loaded; the annual business section could not be reliably extracted. "
                              "The previous description, if present, has been preserved.", cik))
        except Exception:
            conn.execute("UPDATE stk_company_profiles SET error=? WHERE cik=?",
                         ("SEC profile refresh could not finish. Existing company information is preserved; "
                          "retry is automatic.", cik))
    finally:
        if suite is not None:
            await suite.aclose()
        conn.close()
        runtime.close()
