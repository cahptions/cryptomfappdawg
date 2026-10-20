"""Robinhood early participation research: five closed 15m bars, not the production 100d gate.

Coinbase volumes describe Coinbase participation, not Robinhood depth or market-wide net buying.
Actual local availability is required; stale backfilled bars cannot become historical entries.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from decimal import Decimal
from itertools import pairwise
from typing import Any

from desk.core.clock import to_epoch_millis, to_iso
from desk.core.hashing import canonical_json, fingerprint
from desk.research.opportunities import MAX_PATH_ROWS, OpportunityLab, number
from desk.rh.contracts import STABLECOINS
from desk.storage.db import transaction

D = Decimal
DEFAULT_TICKET_USD = D("50")
SPEC: dict[str, Any] = {
    "id": "rh-early-participation-v1", "version": 1, "influence": "NONE", "promotion": "DISABLED",
    "context": "five contiguous Coinbase 15m bars, all first received by decision time",
    "decision_time": "actual first receipt; bars ending >15m before receipt abstain; no synthetic past entries",
    "volume_ratio": "current base volume / median of previous four base volumes",
    "flow_watch": "volume ratio >=2, current close*base volume >=5000 USD, "
                  "0 <= 15m return <=0.10, return exceeds simultaneous BTC 15m return",
    "price_control": "15m return >=0.03 on same available universe",
    "required": "API-tradable USD pair as of decision; BTC contemporaneous bar; Robinhood quote <=120s old",
    "missing_liquidity": "depth/order imbalance unavailable, never substitute Coinbase volume for Robinhood depth",
    "point_bucket_seconds": 900, "horizons_seconds": [900, 3600, 21600, 86400],
    "label_window_seconds": 90, "path_max_rows": MAX_PATH_ROWS, "max_path_gap_seconds": 120,
    "quote_mark": "fresh exact-original-quantity BUY/SELL estimated totals; reported fees included; no actual fills",
    "experiment_usd": "50", "quote_age_seconds": 10,
}


def participation(bars: list[dict[str, Any]], btc_return: Decimal | None) -> tuple[str, bool, bool, dict[str, Any]]:
    payload: dict[str, Any] = {"bars": bars, "btc_return": None if btc_return is None else str(btc_return), "reasons": []}
    if len(bars) != 5 or any(b["start_ms"] - a["start_ms"] != 900_000 for a, b in pairwise(bars)):
        payload["reasons"].append("WARMUP_OR_GAP")
        return "ABSTAIN", False, False, payload
    closes = [number(b["close"]) for b in bars]
    volumes = [number(b["volume"]) for b in bars]
    if any(c is None or c <= 0 for c in closes) or any(v is None or v < 0 for v in volumes):
        payload["reasons"].append("INVALID_BAR")
        return "ABSTAIN", False, False, payload
    assert all(c is not None for c in closes) and all(v is not None for v in volumes)
    c0, c1 = closes[-2], closes[-1]
    assert c0 is not None and c1 is not None
    prior = sorted(v for v in volumes[:-1] if v is not None)
    median = (prior[1] + prior[2]) / 2
    current = volumes[-1]
    assert current is not None
    ret = c1 / c0 - 1
    price_watch = ret >= D("0.03")
    if median <= 0 or btc_return is None:
        payload["reasons"].append("NO_VOLUME_BASELINE_OR_BTC")
        return "ABSTAIN", False, price_watch, payload
    ratio = current / median
    payload.update(return_15m=str(ret), base_volume_ratio=str(ratio), dollar_volume_15m=str(current * c1),
                   excess_vs_btc=str(ret - btc_return))
    watch = ratio >= 2 and current * c1 >= 5000 and 0 <= ret <= D("0.10") and ret > btc_return
    return "WATCH" if watch else "OBSERVE", watch, price_watch, payload


class RhOpportunityLab(OpportunityLab):
    venue = "robinhood"
    specification = SPEC

    def __init__(self, market: sqlite3.Connection, research: sqlite3.Connection, *, ticket_lamports: int,
                 ticket_usd: Decimal = DEFAULT_TICKET_USD) -> None:
        if ticket_usd <= 0:
            raise ValueError("positive experiment ticket required")
        self.experiment_usd = ticket_usd
        self.specification = {**SPEC, "experiment_usd": format(ticket_usd.normalize(), "f")}
        super().__init__(market, research, ticket_lamports=ticket_lamports)

    def _latest_source_id(self) -> int:
        return int(self.market.execute("SELECT COUNT(*) FROM candles WHERE granularity = 900 AND source='coinbase'")
                   .fetchone()[0])

    def ingest(self, now: datetime, *, limit: int = 2000) -> dict[str, int]:
        registered = self.register(now)
        now_ms = to_epoch_millis(now)
        saved = self.research.execute("SELECT snapshot_id FROM opportunity_cursors WHERE spec_sha256 = ?",
                                       (self.sha,)).fetchone()
        cursor = 0 if saved is None else int(saved[0])
        position = self.research.execute("SELECT received_ms, product, start_ms FROM opportunity_rh_cursors"
                                         " WHERE spec_sha256 = ?", (self.sha,)).fetchone()
        watermark = tuple(position) if position else (-1, "", -1)
        stats = {"snapshots": 0, "points": 0, "future_timestamp_block": 0}
        with transaction(self.research):
            # Include listed pairs with no usable candle history in the denominator.
            for pair in self.market.execute("SELECT * FROM rh_pairs WHERE quote_code = 'USD'"):
                cid = fingerprint({"symbol": pair["symbol"], "venue": self.venue})
                first = to_epoch_millis(datetime.fromisoformat(pair["first_seen_at"].replace("Z", "+00:00")))
                if first > now_ms:
                    continue
                self.research.execute("INSERT OR IGNORE INTO opportunity_cohorts VALUES (?, ?, ?, 0, ?, ?, ?)",
                                      (cid, pair["symbol"], "robinhood:" + pair["symbol"], first,
                                       "robinhood.trading_pairs", self.venue))
            rows = self.market.execute("SELECT * FROM candles WHERE granularity = 900 AND source = 'coinbase'"
                                       " AND (first_received_at_ms, product, start_ms) > (?, ?, ?)"
                                       " ORDER BY first_received_at_ms, product, start_ms LIMIT ?",
                                       (*watermark, limit)).fetchall()
            for row in rows:
                at = int(row["first_received_at_ms"])
                if at > now_ms:
                    stats["future_timestamp_block"] = 1
                    break
                cursor += 1
                watermark = (at, row["product"], row["start_ms"])
                stats["snapshots"] += 1
                symbol = row["product"]
                cid = fingerprint({"symbol": symbol, "venue": self.venue})
                if not self.research.execute("SELECT 1 FROM opportunity_cohorts WHERE id = ?", (cid,)).fetchone():
                    continue  # starter Coinbase history alone does not establish Robinhood venue access
                # One new closed bar per asset. Batch backfills share receipt time; only the newest
                # still-current bar can be a decision, not hundreds of fictitious historical trades.
                if not 0 <= at - row["start_ms"] - 900_000 <= 900_000:
                    continue
                if self.market.execute("SELECT 1 FROM candles WHERE product = ? AND source = 'coinbase'"
                                       " AND granularity = 900 AND start_ms > ? AND first_received_at_ms <= ?"
                                       " AND start_ms + 900000 <= ? LIMIT 1",
                                       (symbol, row["start_ms"], at, at)).fetchone():
                    continue
                bucket = row["start_ms"] // 900_000
                if self.research.execute("SELECT 1 FROM opportunity_points WHERE cohort_id = ? AND spec_sha256 = ?"
                                         " AND bucket = ?", (cid, self.sha, bucket)).fetchone():
                    continue
                bars = self.market.execute(
                    "SELECT start_ms, open, high, low, close, volume, first_received_at_ms FROM candles WHERE"
                    " source = 'coinbase' AND product = ? AND granularity = 900 AND start_ms <= ?"
                    " AND first_received_at_ms <= ? ORDER BY start_ms DESC LIMIT 5", (symbol, row["start_ms"], at)).fetchall()
                btc = self.market.execute(
                    "SELECT close, start_ms, first_received_at_ms FROM candles WHERE source = 'coinbase'"
                    " AND product = 'BTC-USD' AND granularity = 900 AND start_ms BETWEEN ? AND ?"
                    " AND first_received_at_ms <= ? ORDER BY start_ms", (row["start_ms"] - 900_000, row["start_ms"], at)
                ).fetchall()
                benchmark = None
                if len(btc) == 2:
                    b0, b1 = number(btc[0]["close"]), number(btc[1]["close"])
                    if b0 is not None and b1 is not None and min(b0, b1) > 0:
                        benchmark = b1 / b0 - 1
                stance, flow, price, payload = participation([dict(b) for b in reversed(bars)], benchmark)
                when = to_iso(datetime.fromtimestamp(at / 1000, UTC))
                event = self.market.execute("SELECT content_json FROM rh_pair_events WHERE symbol = ? AND at <= ?"
                                            " ORDER BY at DESC, id DESC LIMIT 1", (symbol, when)).fetchone()
                access = json.loads(event[0]) if event else {}
                eligible = (bool(access.get("is_api_tradable")) and access.get("status") == "tradable"
                            and access.get("quote_code") == "USD" and access.get("asset_code") not in STABLECOINS)
                quote = self.market.execute("SELECT * FROM rh_quotes WHERE symbol = ? AND received_at_ms BETWEEN ? AND ?"
                                            " ORDER BY received_at_ms DESC, id DESC LIMIT 1", (symbol, at - 120_000, at)
                                           ).fetchone()
                reference = self._mid(quote) if quote else None
                if not eligible or reference is None:
                    stance, flow, price = "ABSTAIN", False, False
                    payload["reasons"].append("NO_ASOF_VENUE_ACCESS_OR_FRESH_QUOTE")
                decision = self.market.execute("SELECT id, action, reasons, spec_sha256 FROM rh_decisions WHERE symbol = ?"
                                               " AND decided_at <= ? AND decided_at >= ? ORDER BY decided_at DESC, id DESC"
                                               " LIMIT 1", (symbol, when, to_iso(datetime.fromtimestamp((at - 3_600_000)
                                                                                                        / 1000, UTC)))).fetchone()
                payload.update(authorization="NONE", source_bar_key={"source": row["source"], "product": symbol,
                               "granularity": 900, "start_ms": row["start_ms"]}, quote_id=quote["id"] if quote else None,
                               source_close_ms=row["start_ms"] + 900_000,
                               availability_delay_seconds=str(D(at - row["start_ms"] - 900_000) / 1000),
                               primary_as_of=dict(decision) if decision else None, access_as_of=access,
                               btc_bars=[dict(b) for b in btc], experiment_usd=str(self.experiment_usd))
                payload.update(asset=symbol, pool="robinhood:" + symbol, venue=self.venue,
                               spec_sha256=self.sha, decision_time_ms=at,
                               reference_quote=dict(quote) if quote else None)
                ident = fingerprint({"cohort": cid, "spec": self.sha, "bucket": bucket})
                self.research.execute("INSERT INTO opportunity_points VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                      (ident, cid, self.sha, row["start_ms"], at, bucket, to_iso(now),
                                       "PROSPECTIVE_SHADOW" if registered <= at and now_ms - at <= 120_000 else "REPLAY",
                                       "LAST_ACTION:" + decision["action"] if decision else "UNASSESSED",
                                       decision["reasons"] if decision else None, stance, int(flow), int(price),
                                       str(reference) if reference is not None else None, canonical_json(payload),
                                       fingerprint(payload)))
                stats["points"] += 1
            self.research.execute("INSERT INTO opportunity_cursors VALUES (?, ?) ON CONFLICT(spec_sha256)"
                                  " DO UPDATE SET snapshot_id = excluded.snapshot_id", (self.sha, cursor))
            self.research.execute("INSERT INTO opportunity_rh_cursors VALUES (?, ?, ?, ?) ON CONFLICT(spec_sha256)"
                                  " DO UPDATE SET received_ms=excluded.received_ms, product=excluded.product,"
                                  " start_ms=excluded.start_ms", (self.sha, *watermark))
        return stats

    @staticmethod
    def _mid(row: sqlite3.Row) -> Decimal | None:
        bid, ask = number(row["bid"]), number(row["ask"])
        return (bid + ask) / 2 if bid is not None and ask is not None and min(bid, ask) > 0 else None

    def _endpoint(self, point: sqlite3.Row, target: int) -> Any:
        row = self.market.execute("SELECT * FROM rh_quotes WHERE symbol = ? AND received_at_ms BETWEEN ? AND ?"
                                  " ORDER BY received_at_ms, id LIMIT 1", (point["asset"], target, target + 90_000)).fetchone()
        if row is None:
            return None
        mid = self._mid(row)
        return {"id": row["id"], "observed_at_ms": row["received_at_ms"], "price_usd": str(mid) if mid else None,
                "price_usd_status": "ok" if mid else "missing", "liquidity_usd": None}

    def _path(self, point: sqlite3.Row, target: int) -> list[Any]:
        rows = self.market.execute("SELECT * FROM rh_quotes WHERE symbol = ? AND received_at_ms BETWEEN ? AND ?"
                                   " ORDER BY received_at_ms, id LIMIT ?", (point["asset"], point["decided_ms"], target,
                                                                           MAX_PATH_ROWS + 1)).fetchall()
        out = [{"id": r["id"], "observed_at_ms": r["received_at_ms"], "price_usd": str(self._mid(r)),
                "price_usd_status": "ok" if self._mid(r) else "missing"} for r in rows]
        # Include the frozen as-of reference at decision time; later quotes start at the next poll.
        if not out or out[0]["observed_at_ms"] > point["decided_ms"]:
            out.insert(0, {"id": None, "observed_at_ms": point["decided_ms"],
                           "price_usd": point["reference_price"], "price_usd_status": "ok"})
        return out

    def _quote_mark(self, point: sqlite3.Row, observed_ms: int) -> dict[str, Any]:
        at = int(point["decided_ms"])
        rows = self.market.execute("SELECT * FROM rh_estimates WHERE symbol = ? AND side = 'BUY'"
                                   " AND received_at_ms BETWEEN ? AND ? ORDER BY id DESC LIMIT 100",
                                   (point["asset"], at - 10_000, at)).fetchall()
        buys = [r for r in rows if self.experiment_usd * D("0.9") <= D(r["total"]) <= self.experiment_usd]
        if not buys:
            return {"status": "NO_ENTRY_ESTIMATE_AT_EXPERIMENT_SIZE", "experiment_usd": str(self.experiment_usd),
                    "net_return": None}
        # Latest observation for each exact quantity and route, then cheapest unit cost.
        latest: dict[tuple[str, Decimal], sqlite3.Row] = {}
        for r in buys:
            latest.setdefault((r["route"], D(r["quantity"])), r)
        buy = min(latest.values(), key=lambda r: D(r["total"]) / D(r["quantity"]))
        sells = self.market.execute("SELECT * FROM rh_estimates WHERE symbol = ? AND side = 'SELL'"
                                    " AND received_at_ms BETWEEN ? AND ? ORDER BY id DESC LIMIT 100",
                                    (point["asset"], observed_ms - 10_000, observed_ms)).fetchall()
        exact: dict[str, sqlite3.Row] = {}
        for row in sells:
            if D(row["quantity"]) == D(buy["quantity"]):
                exact.setdefault(row["route"], row)
        base = {"buy_estimate_id": buy["id"], "original_token_quantity": buy["quantity"],
                "entry_total_usd": buy["total"], "buy_route": buy["route"], "buy_received_ms": buy["received_at_ms"],
                "net_return": None, "confirmed_fill": False}
        if not exact:
            return {**base, "status": "NO_EXACT_QUANTITY_EXIT_ESTIMATE"}
        sell = max(exact.values(), key=lambda r: D(r["total"]))
        return {**base, "status": "HYPOTHETICAL_QUOTE_MARK", "sell_estimate_id": sell["id"],
                "exit_total_usd": sell["total"], "sell_route": sell["route"], "sell_received_ms": sell["received_at_ms"],
                "estimated_return_after_reported_fees": str(D(sell["total"]) / D(buy["total"]) - 1)}
