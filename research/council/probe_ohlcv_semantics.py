"""Observed check of GeckoTerminal OHLCV bar-time semantics (research evidence; not part of the desk runtime).

Three independent checks on the SOL/USDC reference pool the collector already reads:

1. Revision: the newest 1-minute bar of a first request is read again after 65 s (another `limit`, so another
   cache key). A bar that changed although it was received after its own timestamp was still accumulating after
   that time, so its timestamp marks the interval START.
2. Trades: the USD volume traded in each fully observed minute [m, m+60) of the pool's recent trades is compared
   with the bar labelled m (start labelling) and the bar labelled m+60 (end labelling).
3. Aggregation: each completed 5-minute bar labelled t is compared with the five 1-minute bars labelled
   t ... t+240 (start labelling) and t-240 ... t (end labelling).

Four keyless public requests in total; nothing is written to any desk database.
Usage: uv run python research/council/probe_ohlcv_semantics.py [output.json]
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx

BASE = "https://api.geckoterminal.com/api/v2"
POOL = "Czfq3xZZDmsdGdUyrNLtRhGc47cXcZtLG4crryfu44zE"     # Orca SOL/USDC, the desk's context.sol_reference_pool
HEADERS = {"Accept": "application/json;version=20230302", "User-Agent": "kestrel-research/1 (interval-semantics probe)"}


def _now() -> datetime:
    return datetime.now(UTC)


def fetch(client: httpx.Client, path: str, params: dict[str, Any], log: list[dict[str, Any]]) -> dict[str, Any]:
    requested = _now()
    response = client.get(BASE + path, params=params, headers=HEADERS)
    received = _now()
    log.append({"path": path, "params": params, "requested_at": requested.isoformat(), "received_at": received.isoformat(),
                "http_status": response.status_code, "bytes": len(response.content),
                "sha256": hashlib.sha256(response.content).hexdigest(),
                "cache_headers": {k: v for k, v in response.headers.items() if k.lower() in ("age", "cache-control", "date")}})
    response.raise_for_status()
    body: dict[str, Any] = response.json()
    body["_received_at"] = received.isoformat()
    return body


def bars(body: dict[str, Any]) -> dict[int, list[Decimal]]:
    return {int(row[0]): [Decimal(str(v)) for v in row[1:]] for row in body["data"]["attributes"]["ohlcv_list"]}


def main(out: Path) -> int:
    log: list[dict[str, Any]] = []
    with httpx.Client(timeout=20.0) as client:
        first = fetch(client, f"/networks/solana/pools/{POOL}/ohlcv/minute", {"aggregate": 1, "limit": 10}, log)
        trades = fetch(client, f"/networks/solana/pools/{POOL}/trades", {}, log)
        five = fetch(client, f"/networks/solana/pools/{POOL}/ohlcv/minute", {"aggregate": 5, "limit": 12}, log)
        time.sleep(65)
        second = fetch(client, f"/networks/solana/pools/{POOL}/ohlcv/minute", {"aggregate": 1, "limit": 40}, log)
    one_a, one_b, fives = bars(first), bars(second), bars(five)
    received_a = datetime.fromisoformat(first["_received_at"]).timestamp()

    # 1. revision of the newest bar of the first request
    newest = max(one_a)
    revision = {"bar_time": newest, "first_received_epoch": round(received_a, 3),
                "received_minus_bar_seconds": round(received_a - newest, 3),
                "first": [str(v) for v in one_a[newest]], "second": [str(v) for v in one_b.get(newest, [])],
                "changed": newest in one_b and one_b[newest] != one_a[newest]}

    # 2. which bar carries the trades of one fully observed minute [m, m+60): the bar labelled m or the one labelled m+60?
    rows = [t["attributes"] for t in trades["data"]]
    stamps = [(datetime.fromisoformat(r["block_timestamp"].replace("Z", "+00:00")).timestamp(), Decimal(str(r["volume_in_usd"])))
              for r in rows]
    earliest, latest = min(s for s, _ in stamps), max(s for s, _ in stamps)
    comparison = []
    for m in range(int(earliest // 60 + 1) * 60, int(latest), 60):
        if m + 60 > latest or m not in one_b or m + 60 not in one_b:
            continue
        traded = sum((v for s, v in stamps if m <= s < m + 60), Decimal(0))
        comparison.append({"minute_start": m, "trades_usd": str(traded),
                           "bar_labelled_minute_start_usd": str(one_b[m][4]),
                           "bar_labelled_minute_end_usd": str(one_b[m + 60][4])})

    def error(key: str) -> Decimal:
        return sum((abs(Decimal(c[key]) - Decimal(c["trades_usd"])) for c in comparison), Decimal(0))

    # 3. 5-minute bars against 1-minute aggregation (completed 5-minute bars only)
    aggregation = []
    for t, values in sorted(fives.items()):
        start_keys = [t + 60 * i for i in range(5)]
        end_keys = [t - 60 * i for i in range(5)]
        if all(k in one_b for k in start_keys) and all(k in one_b for k in end_keys) and t + 300 <= received_a:
            aggregation.append({"bar_time": t, "five_minute_volume_usd": str(values[4]),
                                "sum_1m_start_labelled": str(sum(one_b[k][4] for k in start_keys)),
                                "sum_1m_end_labelled": str(sum(one_b[k][4] for k in end_keys))})

    start_error, end_error = error("bar_labelled_minute_start_usd"), error("bar_labelled_minute_end_usd")
    aggregated = bool(aggregation) and all(
        abs(Decimal(a["five_minute_volume_usd"]) - Decimal(a["sum_1m_start_labelled"]))
        < abs(Decimal(a["five_minute_volume_usd"]) - Decimal(a["sum_1m_end_labelled"])) for a in aggregation)
    verdict = ("START" if revision["changed"] and revision["received_minus_bar_seconds"] >= 0 and comparison
               and start_error < end_error and aggregated else "UNRESOLVED")
    evidence = {
        "kind": "ohlcv_interval_semantics_probe", "pool": POOL, "provider": "geckoterminal (keyless public API)",
        "requests": log, "revision_check": revision,
        "trade_volume_check": {"trade_window_epoch": [earliest, latest], "minutes_compared": len(comparison),
                               "abs_error_start_labelling": str(start_error), "abs_error_end_labelling": str(end_error),
                               "rows": comparison,
                               "note": "the provider's 300 most recent trades; its volume may differ slightly from the bar's"},
        "aggregation_check": aggregation,
        "verdict": verdict,
        "documentation": [
            {"url": "https://coingecko-api-v3.readme.io/v3.0.1/reference/pool-ohlcv-contract-address",
             "quote": "The epoch/unix timestamp representing the start of the time interval."},
            {"url": "https://docs.coingecko.com/websocket/onchainohlcv", "quote": "Candle open time, UNIX timestamp (s)."},
            {"url": "https://coingecko-api-v3.readme.io/v3.0.1/reference/pool-ohlcv-contract-address",
             "quote": "specific timeframe intervals (e.g. minutely) with no recorded swaps are excluded from the response."}],
        "limitations": "one pool, one short window; the trade list is the provider's own recent-trade record, not a "
                       "chain scan; a 60 s response cache can delay the newest bar",
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(evidence, indent=1) + "\n")
    print(json.dumps({"verdict": verdict, "revision_changed": revision["changed"],
                      "minutes_compared": len(comparison), "start_error": str(start_error), "end_error": str(end_error),
                      "five_minute_bars_compared": len(aggregation)}, indent=1))
    return 0 if verdict == "START" else 1


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1]) if len(sys.argv) > 1 else
                  Path("research/evidence/2026-10-20/council_ohlcv_interval_semantics.json")))
