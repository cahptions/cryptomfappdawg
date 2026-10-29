"""Wikidata company facts (found by SEC CIK) and Wikipedia page summaries: keyless, display only.

They fill gaps the SEC filings and Massive leave on the stock page (founded year, a summary when Massive is not
configured). A chief executive from Wikidata is a last resort: the issuer's own Form 4 officer titles come first.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote, unquote, urlsplit

from desk.core.errors import ProviderError, ProviderErrorKind
from desk.providers.http import ProviderHttp
from desk.stocks.sources.validation import clean_text, iso_day, json_object, schema_boundary, web_url

QID = re.compile(r"^Q\d+$")
_QUERY = """SELECT ?kind ?value ?valueLabel ?when WHERE {
  ?item wdt:P5531 "%s" .
  { BIND("founded" AS ?kind) ?item wdt:P571 ?value . }
  UNION { BIND("employees" AS ?kind) ?item p:P1128 ?statement . ?statement ps:P1128 ?value .
          OPTIONAL { ?statement pq:P585 ?when } }
  UNION { BIND("headquarters" AS ?kind) ?item wdt:P159 ?value . }
  UNION { BIND("ceo" AS ?kind) ?item p:P169 ?statement . ?statement ps:P169 ?value .
          FILTER NOT EXISTS { ?statement pq:P582 ?ended } OPTIONAL { ?statement pq:P580 ?when } }
  UNION { BIND("website" AS ?kind) ?item wdt:P856 ?value . FILTER(REGEX(STR(?value), "^https?://[^/]+/?$")) }
  UNION { BIND("article" AS ?kind) ?value schema:about ?item ; schema:isPartOf <https://en.wikipedia.org/> . }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
}"""


def company_query(cik: int) -> str:
    if not 0 < cik < 10**10:
        raise ValueError("CIK out of range")
    return _QUERY % f"{cik:010d}"


def _label(row: dict[str, Any]) -> str | None:
    label = clean_text((row.get("valueLabel") or {}).get("value"), 120)
    return None if label is None or QID.match(label) else label


@schema_boundary("wikidata company facts")
def parse_company_facts(body: bytes) -> dict[str, Any]:
    rows = json_object(body)["results"]["bindings"]
    if not isinstance(rows, list):
        raise ValueError("bindings")
    founded: list[str] = []
    employees: list[tuple[str, int]] = []
    ceos: list[tuple[str, str]] = []
    out: dict[str, Any] = {}
    websites: list[str] = []
    for row in rows:
        kind, value = (row.get("kind") or {}).get("value"), (row.get("value") or {}).get("value")
        when = iso_day((row.get("when") or {}).get("value")) or ""
        if kind == "founded" and (day := iso_day(value)):
            founded.append(day)
        elif kind == "employees":
            try:
                employees.append((when, int(float(str(value)))))
            except (TypeError, ValueError):
                continue
        elif kind == "headquarters" and (label := _label(row)):
            out.setdefault("headquarters", label)
        elif kind == "ceo" and (label := _label(row)):
            ceos.append((when, label))
        elif kind == "website" and (url := web_url(value)):
            websites.append(url)
        elif kind == "article" and (url := web_url(value)) and url.startswith("https://en.wikipedia.org/wiki/"):
            out["article"] = url
    if founded:
        out["founded"] = min(founded)[:4]
    if employees:
        when, count = max(employees)
        if count > 0:
            out["employees"], out["employees_asof"] = count, when[:4] or None
    if ceos:
        out["ceo"] = max(ceos)[1]
    if websites:
        out["website"] = min(websites, key=len)
    return out


def article_title(url: str) -> str | None:
    """``Apple_Inc.`` from ``https://en.wikipedia.org/wiki/Apple_Inc.`` (only English Wikipedia articles)."""
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.hostname != "en.wikipedia.org" or not parts.path.startswith("/wiki/"):
        return None
    title = unquote(parts.path[len("/wiki/"):])
    return title if title and len(title) <= 255 and ":" not in title else None


@schema_boundary("wikipedia summary")
def parse_summary(body: bytes) -> dict[str, Any]:
    d = json_object(body)
    if d.get("type") != "standard":
        raise ValueError("not an article summary")
    urls = d.get("content_urls")
    desktop = urls.get("desktop") if isinstance(urls, dict) else None
    page = web_url(desktop.get("page")) if isinstance(desktop, dict) else None
    extract = clean_text(d.get("extract"), 2000)
    if extract is None:
        raise ValueError("empty extract")
    return {"title": clean_text(d.get("title"), 200), "description": clean_text(d.get("description"), 200),
            "extract": extract, "url": page if page and page.startswith("https://en.wikipedia.org/") else None}


async def _get(http: ProviderHttp, endpoint: str, *, path: str | None = None, params: dict[str, Any] | None = None) -> bytes:
    response = await http.request(endpoint, path=path, params=params)
    if response.status != 200:
        raise ProviderError(http.name, ProviderErrorKind.HTTP_ERROR, f"{endpoint}: HTTP {response.status}", response.status)
    return response.body


class WikimediaClient:
    def __init__(self, wikidata: ProviderHttp, wikipedia: ProviderHttp) -> None:
        self.wikidata, self.wikipedia = wikidata, wikipedia

    async def company(self, cik: int) -> dict[str, Any]:
        return parse_company_facts(await _get(self.wikidata, "sparql", params={"query": company_query(cik),
                                                                                "format": "json"}))

    async def summary(self, title: str) -> dict[str, Any]:
        return parse_summary(await _get(self.wikipedia, "summary",
                                        path=f"/api/rest_v1/page/summary/{quote(title, safe='')}"))


__all__ = ["WikimediaClient", "article_title", "company_query", "parse_company_facts", "parse_summary"]
