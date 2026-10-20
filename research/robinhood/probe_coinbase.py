"""Measure Coinbase Exchange public candles (keyless): ordering, bucket time, partial latest bar, coverage.

Two public requests per run. Writes research/evidence/<UTC date>/coinbase_candles_probe.json. Used as the
price-history source for the Robinhood desk because Robinhood's Crypto Trading API has no history endpoint.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

BASE = "https://api.exchange.coinbase.com"


def main() -> None:
    now = time.time()
    with httpx.Client(timeout=20, trust_env=True, headers={"User-Agent": "desk/0.1 (private local research tool)"}) as c:
        daily = c.get(f"{BASE}/products/BTC-USD/candles", params={"granularity": 86400})
        hourly = c.get(f"{BASE}/products/BTC-USD/candles", params={"granularity": 3600})
    d, h = daily.json(), hourly.json()
    report = {
        "measured_at": datetime.fromtimestamp(now, UTC).isoformat(),
        "requests": 2,
        "daily": {"status": daily.status_code, "count": len(d), "first": d[0], "second": d[1], "last": d[-1],
                  "newest_first": d[0][0] > d[1][0], "first_bar_end_after_now": d[0][0] + 86400 > now},
        "hourly": {"status": hourly.status_code, "count": len(h), "first": h[0], "newest_first": h[0][0] > h[1][0],
                   "first_bar_end_after_now": h[0][0] + 3600 > now},
        "row_layout": "[bucket_start_epoch_s, low, high, open, close, volume_base] (docs: time is the bucket start)",
        "conclusion": "newest first; the newest bar is still forming (its end is after the request), so only bars "
                      "whose start + granularity <= receipt time are stored; at most 300 bars per request",
    }
    out = Path(__file__).resolve().parents[1] / "evidence" / datetime.fromtimestamp(now, UTC).strftime("%Y-%m-%d")
    out.mkdir(parents=True, exist_ok=True)
    (out / "coinbase_candles_probe.json").write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
