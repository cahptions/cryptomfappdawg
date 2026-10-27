"""Schwab market data, normalized to the same stock/options contracts as Tradier. No account/order methods.

Only standard 100-share options are admitted; adjusted deliverables require an explicit contract model.
Schwab option symbols contain spaces; the ledger's OCC identifiers do not. Missing averages stay missing.
"""

from __future__ import annotations

import json
import math
import re
from datetime import UTC, date, datetime
from typing import Any

from desk.core.errors import ProviderError, ProviderErrorKind, SchemaError
from desk.providers.http import ProviderHttp
from desk.stocks import calendar as cal
from desk.stocks.contracts import Bar, IntradayBar, OptionQuote, OptionTick, StockQuote
from desk.stocks.schwab_auth import SchwabTokens

OCC = re.compile(r"^[A-Z0-9.]{1,6}\d{6}[CP]\d{8}$")


def _f(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)
        return out if math.isfinite(out) and out >= 0 else None
    except (ValueError, TypeError):
        return None


def _time(value: Any) -> datetime | None:
    n = _f(value)
    if n is None or n <= 0:
        return None
    try:
        return datetime.fromtimestamp(n / 1000, UTC)
    except (ValueError, OverflowError, OSError):
        return None


def _body(body: bytes) -> dict[str, Any]:
    try:
        value = json.loads(body)
        if not isinstance(value, dict) or "errors" in value or value.get("status") == "FAILED":
            raise ValueError
        return dict(value)
    except (ValueError, TypeError) as exc:
        raise SchemaError("schwab market data: invalid response (details suppressed)") from exc


def parse_expirations(body: bytes) -> list[date]:
    value = _body(body)
    try:
        return sorted({date.fromisoformat(row["expirationDate"][:10]) for row in value["expirationList"]})
    except (KeyError, ValueError, TypeError) as exc:
        raise SchemaError("schwab expirations: invalid expiration list") from exc


def _standard_deliverable(row: dict[str, Any], symbol: str) -> bool:
    """Schwab also describes *standard* contracts with an explicit 100-stock deliverable.

    A nonempty list is not itself an adjustment. Cash, baskets, other underlyings and unknown
    deliverables still need a separate contract model and are excluded.
    """
    if row.get("nonStandard", False) or row.get("mini", False) or _f(row.get("multiplier")) != 100:
        return False
    items = row.get("optionDeliverablesList")
    if items is None or items == []:
        return True
    return (isinstance(items, list) and len(items) == 1 and isinstance(items[0], dict)
            and items[0].get("symbol") == symbol and items[0].get("assetType") == "STOCK"
            and _f(items[0].get("deliverableUnits")) == 100)


def parse_chain(body: bytes, symbol: str, expiration: date) -> list[OptionQuote]:
    value = _body(body)
    out: dict[str, OptionQuote] = {}
    try:
        if value.get("symbol", symbol) != symbol:
            raise ValueError
        if value.get("isDelayed") is True:
            raise SchemaError("schwab chains: delayed data requires a separate delayed-execution experiment")
        for field, kind in (("callExpDateMap", "call"), ("putExpDateMap", "put")):
            for key, strikes in (value.get(field) or {}).items():
                exp = date.fromisoformat(key.split(":")[0])
                if exp != expiration:
                    continue
                for contracts in strikes.values():
                    for row in contracts:
                        occ = str(row.get("symbol", "")).replace(" ", "")
                        strike = _f(row.get("strikePrice"))
                        if (not OCC.fullmatch(occ) or strike is None or strike <= 0
                                or not _standard_deliverable(row, symbol)):
                            continue
                        # Contract identity must agree with its map, not just have a plausible-looking name.
                        if (occ[:-15] != symbol or occ[-15:-9] != exp.strftime("%y%m%d") or occ[-9] != kind[0].upper()
                                or int(occ[-8:]) != round(strike * 1000)):
                            continue
                        out[occ] = OptionQuote(occ, symbol, kind, strike, exp, _f(row.get("bid")), _f(row.get("ask")),
                                               _f(row.get("last")), int(_f(row.get("totalVolume")) or 0),
                                               int(_f(row.get("openInterest")) or 0), None,
                                               _time(row.get("quoteTimeInLong")))
        return list(out.values())
    except (ValueError, TypeError, AttributeError) as exc:
        raise SchemaError("schwab chains: invalid expiration/strike map") from exc


def parse_quotes(body: bytes) -> list[StockQuote]:
    value = _body(body)
    out = []
    try:
        for symbol, row in value.items():
            q = row.get("quote")
            if not isinstance(q, dict):
                continue
            out.append(StockQuote(str(row.get("symbol") or symbol), _f(q.get("lastPrice")), _f(q.get("bidPrice")),
                                  _f(q.get("askPrice")), _f(q.get("closePrice")), _f(q.get("totalVolume")),
                                  _time(q.get("quoteTime"))))
        return out
    except (ValueError, TypeError, AttributeError) as exc:
        raise SchemaError("schwab quotes: invalid quote map") from exc


def schwab_option_symbol(occ: str) -> str:
    """Stored OCC identifiers have no spaces; Schwab pads the root to six characters (``AAPL  261120C00250000``)."""
    if not OCC.fullmatch(occ):
        raise ValueError(f"not an OCC option symbol: {occ!r}")
    return occ[:-15].ljust(6) + occ[-15:]


def _signed(value: Any) -> float | None:
    """Greeks can be negative; Schwab reports unavailable ones as -999 or NaN."""
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (ValueError, TypeError):
        return None
    return out if math.isfinite(out) and abs(out) < 999 else None


def parse_option_quotes(body: bytes, wanted: set[str]) -> list[OptionTick]:
    """Option quotes for the requested contracts only. A row whose reference data disagrees with its symbol (strike,
    call/put, expiration) is dropped rather than trusted."""
    value = _body(body)
    out: dict[str, OptionTick] = {}
    try:
        for key, row in value.items():
            if not isinstance(row, dict):
                continue
            occ = str(row.get("symbol") or key).replace(" ", "")
            q = row.get("quote")
            if occ not in wanted or not OCC.fullmatch(occ) or not isinstance(q, dict):
                continue
            if row.get("assetMainType") not in (None, "OPTION"):
                continue
            raw_ref = row.get("reference")
            ref: dict[str, Any] = raw_ref if isinstance(raw_ref, dict) else {}
            strike = _f(ref.get("strikePrice"))
            if strike is not None and int(occ[-8:]) != round(strike * 1000):
                continue
            kind = str(ref.get("contractType") or "")[:1].upper()
            if kind in ("C", "P") and kind != occ[-9]:
                continue
            # an adjusted contract (split, merger) changes its multiplier or deliverable: not this contract any more
            multiplier = _f(ref.get("multiplier", q.get("multiplier")))
            if multiplier is not None and multiplier != 100:
                continue
            if ref.get("underlying") not in (None, occ[:-15]):
                continue
            if all(ref.get(k) for k in ("expirationYear", "expirationMonth", "expirationDay")):
                expiry = date(int(ref["expirationYear"]), int(ref["expirationMonth"]), int(ref["expirationDay"]))
                if expiry.strftime("%y%m%d") != occ[-15:-9]:
                    continue
            vol = _f(q.get("volatility"))
            iv = None if vol is None or vol <= 0 or vol / 100 > 10 else vol / 100
            out[occ] = OptionTick(occ, _f(q.get("bidPrice")), _f(q.get("askPrice")), _f(q.get("lastPrice")),
                                  _f(q.get("mark")),
                                  None if _f(q.get("totalVolume")) is None else int(_f(q.get("totalVolume")) or 0),
                                  None if _f(q.get("openInterest")) is None else int(_f(q.get("openInterest")) or 0),
                                  iv, _signed(q.get("delta")), _signed(q.get("gamma")), _signed(q.get("theta")),
                                  _signed(q.get("vega")), _f(q.get("underlyingPrice")), _time(q.get("quoteTime")))
        return list(out.values())
    except (ValueError, TypeError, AttributeError) as exc:
        raise SchemaError("schwab option quotes: invalid quote map") from exc


def parse_history(body: bytes, symbol: str) -> list[Bar]:
    """Keep Schwab's candles separate from unadjusted bulk bars; never adjust them twice."""
    value = _body(body)
    try:
        if value.get("symbol") != symbol or not isinstance(value.get("candles"), list):
            raise ValueError
        if len(value["candles"]) > 10000:
            raise ValueError
        out: dict[date, Bar] = {}
        for row in value["candles"]:
            stamp = _time(row["datetime"])
            op, hi, lo, cl, volume = (_f(row[k]) for k in ("open", "high", "low", "close", "volume"))
            if (stamp is None or any(v is None for v in (op, hi, lo, cl, volume))
                    or op is None or hi is None or lo is None or cl is None or volume is None
                    or min(op, hi, lo, cl) <= 0 or hi < max(op, lo, cl) or lo > min(op, hi, cl)):
                raise ValueError
            day = cal.new_york(stamp).date()
            if day in out:
                raise ValueError  # a daily request must not return intraday/duplicate candles
            out[day] = Bar(symbol, day, op, hi, lo, cl, volume)
        return [out[d] for d in sorted(out)]
    except (KeyError, ValueError, TypeError, AttributeError) as exc:
        raise SchemaError("schwab price history: invalid daily candles; existing history preserved") from exc


def parse_intraday(body: bytes, symbol: str) -> list[IntradayBar]:
    value = _body(body)
    try:
        if value.get("symbol") != symbol or not isinstance(value.get("candles"), list) or len(value["candles"]) > 10000:
            raise ValueError
        out: dict[datetime, IntradayBar] = {}
        for row in value["candles"]:
            stamp = _time(row["datetime"])
            op, hi, lo, cl, volume = (_f(row[k]) for k in ("open", "high", "low", "close", "volume"))
            if (stamp is None or stamp.second or stamp.microsecond or stamp in out
                    or op is None or hi is None or lo is None or cl is None or volume is None
                    or min(op, hi, lo, cl) <= 0 or hi < max(op, lo, cl) or lo > min(op, hi, cl)):
                raise ValueError
            out[stamp] = IntradayBar(symbol, stamp, op, hi, lo, cl, volume)
        return [out[at] for at in sorted(out)]
    except (KeyError, ValueError, TypeError, AttributeError) as exc:
        raise SchemaError("schwab price history: invalid minute candles; existing intraday chart preserved") from exc


class SchwabClient:
    def __init__(self, http: ProviderHttp, tokens: SchwabTokens) -> None:
        self.http, self.tokens = http, tokens
        self.http.auth_headers = tokens.headers
        self.chain_diagnostics: dict[str, dict[str, int]] = {}

    @property
    def configured(self) -> bool:
        return self.tokens.configured

    async def _get(self, endpoint: str, params: dict[str, Any]) -> bytes:
        if not self.configured:
            raise ProviderError("schwab", ProviderErrorKind.HTTP_ERROR, "Schwab is not connected; reconnect in Settings")
        response = await self.http.request(endpoint, params=params)
        if response.status != 200:
            raise ProviderError("schwab", ProviderErrorKind.HTTP_ERROR, f"{endpoint}: HTTP {response.status}", response.status)
        return response.body

    async def expirations(self, symbol: str) -> list[date]:
        self.chain_diagnostics[symbol] = {"raw_contracts": 0, "parsed_contracts": 0, "volume_fields_present": 0,
                                         "positive_volume_contracts": 0, "reported_volume": 0}
        return parse_expirations(await self._get("expirations", {"symbol": symbol}))

    async def chain(self, symbol: str, expiration: date) -> list[OptionQuote]:
        body = await self._get("chains", {"symbol": symbol, "contractType": "ALL", "strategy": "SINGLE",
                                                       "fromDate": expiration.isoformat(), "toDate": expiration.isoformat(),
                                                       "includeUnderlyingQuote": "true"})
        parsed = parse_chain(body, symbol, expiration)
        value = _body(body)
        stats = self.chain_diagnostics.setdefault(symbol, {"raw_contracts": 0, "parsed_contracts": 0,
              "volume_fields_present": 0, "positive_volume_contracts": 0, "reported_volume": 0})
        for field in ("callExpDateMap", "putExpDateMap"):
            for strikes in (value.get(field) or {}).values():
                for contracts in strikes.values():
                    for row in contracts:
                        volume = _f(row.get("totalVolume"))
                        stats["raw_contracts"] += 1
                        stats["volume_fields_present"] += volume is not None
                        stats["positive_volume_contracts"] += bool(volume)
                        stats["reported_volume"] += int(volume or 0)
        stats["parsed_contracts"] += len(parsed)
        return parsed

    async def quotes(self, symbols: list[str]) -> list[StockQuote]:
        if not 1 <= len(symbols) <= 100:
            raise ValueError("Schwab quote batches contain 1..100 symbols")
        return parse_quotes(await self._get("quotes", {"symbols": ",".join(symbols), "fields": "quote"}))

    async def option_quotes(self, occs: list[str]) -> list[OptionTick]:
        """Live quotes for up to 100 option contracts in one request (stored OCC symbols in, OCC symbols out)."""
        if not 1 <= len(occs) <= 100:
            raise ValueError("Schwab quote batches contain 1..100 symbols")
        symbols = [schwab_option_symbol(o) for o in occs]
        return parse_option_quotes(await self._get("quotes", {"symbols": ",".join(symbols), "fields": "quote,reference"}),
                                   set(occs))

    async def price_history(self, symbol: str) -> list[Bar]:
        return parse_history(await self._get("pricehistory", {
            "symbol": symbol, "periodType": "year", "period": 20, "frequencyType": "daily", "frequency": 1,
            "needExtendedHoursData": "false", "needPreviousClose": "false"}), symbol)

    async def daily_since(self, symbol: str, since: date, today: date) -> list[Bar]:
        """Daily candles back to ``since`` with the smallest documented period that reaches it (gap repair)."""
        span = (today - since).days
        period_type, period = next(((kind, n) for kind, n, days in (("month", 1, 27), ("month", 3, 88), ("month", 6, 180),
                                                                    ("year", 1, 360), ("year", 5, 1820)) if span <= days),
                                   ("year", 20))
        return parse_history(await self._get("pricehistory", {
            "symbol": symbol, "periodType": period_type, "period": period, "frequencyType": "daily", "frequency": 1,
            "needExtendedHoursData": "false", "needPreviousClose": "false"}), symbol)

    async def intraday_history(self, symbol: str) -> list[IntradayBar]:
        # Several sessions allow a real previous session at weekends, holidays and before today's open.
        return parse_intraday(await self._get("pricehistory", {
            "symbol": symbol, "periodType": "day", "period": 5, "frequencyType": "minute", "frequency": 1,
            "needExtendedHoursData": "false", "needPreviousClose": "false"}), symbol)
