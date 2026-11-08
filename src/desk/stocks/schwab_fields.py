"""Which Schwab market-data fields actually arrive on the operator's account (``desk stocks verify-schwab``).

The parsers were written from Schwab's documentation; tests use documentation-shaped fixtures, not captured answers.
This asks each endpoint the app reads once (quotes for a stock and for three option contracts, the expiration list, a
chain, daily and minute price history, and movers, which the app does not read yet) and records, for every field the
app reads, whether it came back and with the expected type. Only field names, types and counts are kept and printed:
never a value, token, account or key. The summary is saved to ``stocks-schwab-fields.json`` so it can be pasted.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from typing import Any

from desk.core.errors import DeskError
from desk.stocks.sources.schwab import QUOTE_DETAIL_FIELDS, schwab_option_symbol

NUMBER, TEXT, FLAG, LIST, OBJECT = "number", "text", "bool", "list", "object"
QUOTE_KINDS = {"price": NUMBER, "signed": NUMBER, "count": NUMBER, "time": NUMBER, "date": TEXT, "text": TEXT,
               "bool": FLAG}
STOCK_QUOTE = {f"{section}.{name}": QUOTE_KINDS[kind] for section, fields in QUOTE_DETAIL_FIELDS.items()
               for name, _, kind in fields}
OPTION_QUOTE = {**{f"quote.{k}": NUMBER for k in ("bidPrice", "askPrice", "lastPrice", "mark", "totalVolume",
                                                  "openInterest", "volatility", "delta", "gamma", "theta", "vega",
                                                  "underlyingPrice", "quoteTime", "bidSize", "askSize")},
                **{f"reference.{k}": NUMBER for k in ("strikePrice", "multiplier", "expirationYear", "expirationMonth",
                                                      "expirationDay")},
                "reference.contractType": TEXT, "reference.underlying": TEXT, "assetMainType": TEXT, "symbol": TEXT}
CHAIN = {"symbol": TEXT, "isDelayed": FLAG, "underlyingPrice": NUMBER, "underlying.last": NUMBER,
         "callExpDateMap": OBJECT, "putExpDateMap": OBJECT,
         **{f"contract.{k}": NUMBER for k in ("strikePrice", "bid", "ask", "last", "totalVolume", "openInterest",
                                              "quoteTimeInLong", "bidSize", "askSize", "multiplier")},
         "contract.symbol": TEXT, "contract.nonStandard": FLAG, "contract.mini": FLAG,
         "contract.optionDeliverablesList": LIST}
HISTORY = {"symbol": TEXT, "candles": LIST, **{f"candle.{k}": NUMBER for k in ("datetime", "open", "high", "low",
                                                                              "close", "volume")}}
EXPIRATIONS = {"expirationList": LIST, "expiration.expirationDate": TEXT}
# sent only in some cases: present-or-absent is fine, and the app treats absent as unknown
OPTIONAL = {"reference.htbQuantity": "sent only for hard-to-borrow stocks"}
MOVERS = {"screeners": LIST, **{f"mover.{k}": NUMBER for k in ("lastPrice", "netChange", "netPercentChange", "volume",
                                                               "totalVolume")}, "mover.symbol": TEXT}


def _type(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return FLAG
    if isinstance(value, (int, float)):
        return NUMBER
    if isinstance(value, str):
        return TEXT
    return LIST if isinstance(value, list) else OBJECT if isinstance(value, dict) else "other"


def _get(row: Any, path: str) -> Any:
    for part in path.split("."):
        if not isinstance(row, dict):
            return None
        row = row.get(part)
    return row


def check(rows: dict[str, Any], expected: dict[str, str]) -> dict[str, Any]:
    """Each expected field: ``ok`` (present, expected type), ``absent`` or ``type`` (present, another type)."""
    out: dict[str, str] = {}
    for path, kind in expected.items():
        prefix, _, rest = path.partition(".")
        value = _get(rows[prefix], rest) if prefix in rows and rest else _get(rows.get(""), path)
        found = _type(value)
        out[path] = ((f"absent ({OPTIONAL[path]})" if path in OPTIONAL else "absent") if found is None
                     else "ok" if found == kind else f"type {found}")
    return {"fields": out, "ok": sum(v.startswith(("ok", "absent (")) for v in out.values()),
            "absent": sum(v == "absent" for v in out.values()), "mistyped": sum(v.startswith("type") for v in out.values())}


def _first_contract(value: dict[str, Any]) -> dict[str, Any] | None:
    for field in ("callExpDateMap", "putExpDateMap"):
        for strikes in (value.get(field) or {}).values():
            for contracts in (strikes or {}).values():
                if contracts:
                    return dict(contracts[0])
    return None


async def verify(schwab: Any, now: datetime, symbol: str = "SPY") -> dict[str, Any]:
    """One request per endpoint the app reads; field names and types only."""
    if schwab is None or not schwab.configured:
        return {"ok": False, "error": "Schwab is not connected; connect it in Settings, then run this again."}
    report: dict[str, Any] = {"checked_at": now.isoformat(), "symbol": symbol, "endpoints": {}}

    async def ask(name: str, endpoint: str, params: dict[str, Any], path: str | None = None) -> dict[str, Any] | None:
        try:
            body = await schwab._get(endpoint, params, path=path)
            value = json.loads(body)
            return value if isinstance(value, dict) else None
        except (DeskError, ValueError) as exc:
            report["endpoints"][name] = {"error": type(exc).__name__ + ": " + str(exc)[:120]}
            return None

    quotes = await ask("quotes (stock)", "quotes", {"symbols": symbol,
                                                    "fields": ",".join(QUOTE_DETAIL_FIELDS)})
    if quotes is not None:
        row = quotes.get(symbol) or {}
        report["endpoints"]["quotes (stock)"] = {**check({"": row, **{k: row.get(k) for k in QUOTE_DETAIL_FIELDS}},
                                                         STOCK_QUOTE), "sections": sorted(k for k in row if
                                                                                          isinstance(row[k], dict))}
    listed = await ask("expirationchain", "expirations", {"symbol": symbol})
    expiry = None
    if listed is not None:
        days = listed.get("expirationList") or []
        report["endpoints"]["expirationchain"] = check({"": listed, "expiration": days[0] if days else {}},
                                                      EXPIRATIONS)
        future = sorted(str(d.get("expirationDate", ""))[:10] for d in days if isinstance(d, dict))
        expiry = next((date.fromisoformat(d) for d in future if d and date.fromisoformat(d) >= now.date()
                       + timedelta(days=20)), None)
    chain = None
    if expiry is not None:
        chain = await ask("chains", "chains", {"symbol": symbol, "contractType": "ALL", "strategy": "SINGLE",
                                               "fromDate": expiry.isoformat(), "toDate": expiry.isoformat(),
                                               "strikeCount": 4, "includeUnderlyingQuote": "true"})
    if chain is not None:
        contract = _first_contract(chain) or {}
        report["endpoints"]["chains"] = check({"": chain, "contract": contract,
                                               "underlying": chain.get("underlying") or {}}, CHAIN)
        occs = [str(c[0].get("symbol", "")).replace(" ", "") for side in ("callExpDateMap", "putExpDateMap")
                for strikes in (chain.get(side) or {}).values() for c in strikes.values() if c][:3]
        try:
            symbols = [schwab_option_symbol(o) for o in occs]
        except ValueError:
            symbols = []
        if symbols:
            options = await ask("quotes (options)", "quotes", {"symbols": ",".join(symbols), "fields": "quote,reference"})
            if options is not None:
                first = next((v for v in options.values() if isinstance(v, dict)), {})
                report["endpoints"]["quotes (options)"] = {**check({"": first, "quote": first.get("quote") or {},
                                                                    "reference": first.get("reference") or {}},
                                                                   OPTION_QUOTE), "answered": len(options)}
    end = int(now.timestamp() * 1000)
    for name, params in (("pricehistory (daily)", {"periodType": "month", "period": 1, "frequencyType": "daily",
                                                   "frequency": 1}),
                         ("pricehistory (minute)", {"periodType": "day", "period": 1, "frequencyType": "minute",
                                                    "frequency": 1, "endDate": end})):
        history = await ask(name, "pricehistory", {"symbol": symbol, "needExtendedHoursData": "false", **params})
        if history is not None:
            candles = history.get("candles") or []
            report["endpoints"][name] = {**check({"": history, "candle": candles[0] if candles else {}}, HISTORY),
                                         "candles": len(candles)}
    movers = await ask("movers (not read by the app yet)", "movers", {"sort": "PERCENT_CHANGE_UP", "frequency": 0},
                       path="/movers/$SPX")
    if movers is not None:
        top = movers.get("screeners") or []
        expected = MOVERS if top else {"screeners": LIST}      # an empty list (market closed) has no movers to check
        report["endpoints"]["movers (not read by the app yet)"] = {
            **check({"": movers, "mover": top[0] if top else {}}, expected),
            **({} if top else {"note": "no movers listed right now (market closed); run again during market hours"})}
    report["ok"] = all("error" not in e and not e.get("absent") and not e.get("mistyped")
                       for e in report["endpoints"].values())
    return report


def summary(report: dict[str, Any]) -> list[str]:
    """Lines to read or paste: one per endpoint, then the fields that did not come back as expected."""
    if report.get("error"):
        return [report["error"]]
    lines = [f"Schwab field check on {report['symbol']}, {report['checked_at'][:16]} UTC (names and types only)"]
    for name, e in report["endpoints"].items():
        if "error" in e:
            lines.append(f"✗ {name}: {e['error']}")
            continue
        mark = "✓" if not e["absent"] and not e["mistyped"] else "!"
        lines.append(f"{mark} {name}: {e['ok']} fields as expected, {e['absent']} absent, {e['mistyped']} another type")
        lines += [f"    {path}: {state}" for path, state in e["fields"].items() if state != "ok"]
        if e.get("note"):
            lines.append(f"    {e['note']}")
    return lines


__all__ = ["check", "summary", "verify"]
