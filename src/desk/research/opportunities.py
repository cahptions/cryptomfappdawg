"""Point-in-time early-opportunity research. Read-only market.db; research.db records only.

No provider requests, production hooks, learned weights, order methods, promotion or ledger writes.
All observed pool identities enter the denominator, including pools never refreshed by DexScreener.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal, InvalidOperation
from itertools import pairwise
from typing import Any

from desk.core.clock import to_epoch_millis, to_iso
from desk.core.hashing import canonical_json, fingerprint
from desk.security.policy import SOL_MINT
from desk.storage.db import StorageError, transaction
from desk.storage.market_repo import HAZARD_SOURCE

D = Decimal
MAX_PATH_ROWS = 2000
SPEC: dict[str, Any] = {
    "id": "early-opportunities-v1", "influence": "NONE", "version": 1,
    "source": HAZARD_SOURCE, "population": "all locally observed mint/pool identities; not the entire market",
    "point_bucket_seconds": 300, "episode_seconds": 86400, "prior_window_seconds": [300, 390],
    "min_history_observations": 4, "max_gap_seconds": 120,
    "flow_watch": {"volume_ratio_min": "2", "volume_m5_usd_min": "5000", "trade_count_min": 50,
                   "buy_trade_share_min": "0.60", "price_return_range": ["0", "0.10"],
                   "liquidity_usd_min": "40000", "liquidity_ratio_min": "0.95"},
    "price_control": {"price_return_min": "0.03", "liquidity_usd_min": "40000"},
    "horizons_seconds": [900, 3600, 21600, 86400], "label_window_seconds": 90,
    "path_max_rows": MAX_PATH_ROWS, "quote_age_seconds": 10,
    "executable_mark": "original exact token quantity; contemporaneous persisted quotes only; fees not imputed",
    "net_returns": "not estimated; quote-to-quote marks exclude unrecorded network costs and are not fills",
    "missingness": "retain missing, zero-liquidity, insufficient-path and unquoted outcomes; never impute",
}


def number(value: Any) -> Decimal | None:
    try:
        result = D(str(value))
        return result if result.is_finite() else None
    except InvalidOperation:
        return None


def compact(row: sqlite3.Row) -> dict[str, Any]:
    windows = json.loads(row["windows_json"])
    return {"id": row["id"], "at_ms": row["observed_at_ms"], "source": row["source"],
            "price": row["price_usd"], "price_status": row["price_usd_status"],
            "liquidity": row["liquidity_usd"], "liquidity_status": row["liquidity_status"],
            "m5": windows.get("m5", {}), "raw_sha256": row["raw_sha256"]}


def features(history: list[dict[str, Any]]) -> tuple[str, bool, bool, dict[str, Any]]:
    """Uncalibrated research WATCH predicates, never a BUY recommendation or security assessment."""
    out: dict[str, Any] = {"observations": history, "reasons": []}
    if len(history) < 4:
        out["reasons"].append("SHORT_HISTORY")
        return "ABSTAIN", False, False, out
    prior, current = history[0], history[-1]
    if not 300_000 <= current["at_ms"] - prior["at_ms"] <= 390_000 or any(
            b["at_ms"] - a["at_ms"] > 120_000 for a, b in pairwise(history)):
        out["reasons"].append("GAP_OR_OVERLAPPING_WINDOWS")
        return "ABSTAIN", False, False, out
    if prior["raw_sha256"] is not None and prior["raw_sha256"] == current["raw_sha256"]:
        out["reasons"].append("UNCHANGED_PAYLOAD")
        return "ABSTAIN", False, False, out
    p0, p1, l0, l1 = (number(r[k]) for r, k in ((prior, "price"), (current, "price"),
                                               (prior, "liquidity"), (current, "liquidity")))
    valid = all(r["price_status"] == "ok" and r["liquidity_status"] == "ok" for r in (prior, current))
    if not valid or p0 is None or p1 is None or l0 is None or l1 is None or min(p0, p1, l0, l1) <= 0:
        out["reasons"].append("INVALID_PRICE_OR_LIQUIDITY")
        return "ABSTAIN", False, False, out
    ret, retention = p1 / p0 - 1, l1 / l0
    out.update(price_return=str(ret), liquidity_ratio=str(retention), liquidity_usd=str(l1))
    price_watch = ret >= D("0.03") and l1 >= 40_000
    v0, v1 = (number(r["m5"].get("volume_usd")) for r in (prior, current))
    buys, sells = number(current["m5"].get("buys")), number(current["m5"].get("sells"))
    if v0 is None or v1 is None or v0 <= 0 or v1 < 0 or buys is None or sells is None or min(buys, sells) < 0:
        out["reasons"].append("MISSING_FLOW_INPUTS")
        return "ABSTAIN", False, price_watch, out
    count = buys + sells
    share = buys / count if count else D(0)
    ratio = v1 / v0
    out.update(volume_ratio=str(ratio), volume_m5_usd=str(v1), buy_trade_share=str(share), trade_count=str(count))
    watch = (ratio >= 2 and v1 >= 5000 and count >= 50 and share >= D("0.60")
             and 0 <= ret <= D("0.10") and l1 >= 40_000 and retention >= D("0.95"))
    return "WATCH" if watch else "OBSERVE", watch, price_watch, out


class OpportunityLab:
    venue = "solana"
    specification = SPEC

    def __init__(self, market: sqlite3.Connection, research: sqlite3.Connection, *, ticket_lamports: int) -> None:
        self.market, self.research, self.ticket = market, research, ticket_lamports
        self.specification = {**self.specification, "quote_ticket_lamports": ticket_lamports}
        self.sha = fingerprint(self.specification)

    def register(self, now: datetime) -> int:
        ident = "opportunity_spec_" + self.sha
        self.research.execute("INSERT OR IGNORE INTO experiments(id, created_at, kind, payload_json) VALUES (?, ?, ?, ?)",
                              (ident, to_iso(now), "OPPORTUNITY_SPEC", canonical_json(self.specification)))
        row = self.research.execute("SELECT created_at FROM experiments WHERE id = ?", (ident,)).fetchone()
        assert row is not None
        return to_epoch_millis(datetime.fromisoformat(row[0].replace("Z", "+00:00")))

    def _history(self, row: sqlite3.Row) -> list[dict[str, Any]]:
        ms = int(row["observed_at_ms"])
        previous = self.market.execute(
            "SELECT * FROM market_snapshots WHERE pool_address = ? AND base_mint = ? AND source = ?"
            " AND observed_at_ms BETWEEN ? AND ? AND id <= ? ORDER BY observed_at_ms DESC, id DESC LIMIT 1",
            (row["pool_address"], row["base_mint"], HAZARD_SOURCE, ms - 390_000, ms - 300_000, row["id"])).fetchone()
        if previous is None:
            return [compact(row)]
        rows = self.market.execute(
            "SELECT * FROM market_snapshots WHERE pool_address = ? AND base_mint = ? AND source = ?"
            " AND observed_at_ms BETWEEN ? AND ? AND id <= ? ORDER BY observed_at_ms, id LIMIT 30",
            (row["pool_address"], row["base_mint"], HAZARD_SOURCE, previous["observed_at_ms"], ms, row["id"])).fetchall()
        # One timestamp is one observation, regardless of duplicated source rows.
        dedup = {int(r["observed_at_ms"]): compact(r) for r in rows}
        return [dedup[t] for t in sorted(dedup)]

    def ingest(self, now: datetime, *, limit: int = 2000) -> dict[str, int]:
        registered = self.register(now)
        sha, now_ms = self.sha, to_epoch_millis(now)
        saved = self.research.execute("SELECT snapshot_id FROM opportunity_cursors WHERE spec_sha256 = ?", (sha,)).fetchone()
        cursor = 0 if saved is None else int(saved[0])
        rows = self.market.execute("SELECT * FROM market_snapshots WHERE id > ? ORDER BY id LIMIT ?",
                                   (cursor, limit)).fetchall()
        stats = {"snapshots": 0, "points": 0, "future_timestamp_block": 0}
        with transaction(self.research):
            for row in rows:
                ms = int(row["observed_at_ms"])
                if ms > now_ms:
                    stats["future_timestamp_block"] = 1
                    break  # do not skip an unavailable row then advance the cursor beyond it
                cursor = int(row["id"])
                stats["snapshots"] += 1
                cid = fingerprint({"mint": row["base_mint"], "pool": row["pool_address"], "venue": self.venue})
                self.research.execute(
                    "INSERT OR IGNORE INTO opportunity_cohorts VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (cid, row["base_mint"], row["pool_address"], row["id"], ms, row["source"], self.venue))
                cohort = self.research.execute("SELECT * FROM opportunity_cohorts WHERE id = ?", (cid,)).fetchone()
                assert cohort is not None
                if row["source"] != HAZARD_SOURCE or not 0 <= ms - cohort["first_seen_ms"] <= 86_400_000:
                    continue
                bucket = ms // 300_000
                if self.research.execute("SELECT 1 FROM opportunity_points WHERE cohort_id = ? AND spec_sha256 = ?"
                                         " AND bucket = ?", (cid, sha, bucket)).fetchone():
                    continue
                history = self._history(row)
                stance, flow, price, payload = features(history)
                stage = self.market.execute(
                    "SELECT r.id, r.outcome, r.rule_id, r.config_hash FROM stage_results r JOIN discoveries d"
                    " ON d.id = r.candidate_id WHERE d.mint = ? AND d.pool_address = ? AND r.decided_at <= ?"
                    " ORDER BY r.decided_at DESC, r.id DESC LIMIT 1",
                    (row["base_mint"], row["pool_address"], row["observed_at"])).fetchone()
                payload["primary_as_of"] = dict(stage) if stage else None
                payload["authorization"] = "NONE"
                payload["discovery_delay_seconds"] = str(D(ms - cohort["first_seen_ms"]) / 1000)
                payload["ticket_lamports"] = self.ticket
                payload.update(asset=row["base_mint"], pool=row["pool_address"], venue=self.venue,
                               spec_sha256=sha, decision_time_ms=ms)
                ident = fingerprint({"cohort": cid, "spec": sha, "bucket": bucket})
                self.research.execute("INSERT INTO opportunity_points VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                      (ident, cid, sha, row["id"], ms, bucket, to_iso(now),
                                       "PROSPECTIVE_SHADOW" if registered <= ms and now_ms - ms <= 120_000 else "REPLAY",
                                       stage["outcome"] if stage else "UNASSESSED", stage["rule_id"] if stage else None,
                                       stance, int(flow), int(price), row["price_usd"], canonical_json(payload),
                                       fingerprint(payload)))
                stats["points"] += 1
            self.research.execute("INSERT INTO opportunity_cursors VALUES (?, ?) ON CONFLICT(spec_sha256)"
                                  " DO UPDATE SET snapshot_id = excluded.snapshot_id", (sha, cursor))
        return stats

    def _latest_source_id(self) -> int:
        return int(self.market.execute("SELECT COALESCE(MAX(id), 0) FROM market_snapshots").fetchone()[0])

    def _endpoint(self, point: sqlite3.Row, target: int) -> Any:
        return self.market.execute(
            "SELECT * FROM market_snapshots WHERE pool_address = ? AND base_mint = ? AND source = ?"
            " AND observed_at_ms BETWEEN ? AND ? ORDER BY observed_at_ms, id LIMIT 1",
            (point["pool"], point["asset"], HAZARD_SOURCE, target, target + 90_000)).fetchone()

    def _path(self, point: sqlite3.Row, target: int) -> list[Any]:
        return self.market.execute(
            "SELECT id, observed_at_ms, price_usd, price_usd_status FROM market_snapshots"
            " WHERE pool_address = ? AND base_mint = ? AND source = ? AND observed_at_ms BETWEEN ? AND ?"
            " ORDER BY observed_at_ms, id LIMIT ?", (point["pool"], point["asset"], HAZARD_SOURCE,
                                                    point["decided_ms"], target, MAX_PATH_ROWS + 1)).fetchall()

    def _quote_mark(self, point: sqlite3.Row, observed_ms: int) -> dict[str, Any]:
        at, mint = int(point["decided_ms"]), point["asset"]
        ticket = int(json.loads(point["features_json"])["ticket_lamports"])
        buy = self.market.execute(
            "SELECT * FROM quotes WHERE input_mint = ? AND output_mint = ? AND in_amount_raw = ?"
            " AND received_at_ms BETWEEN ? AND ? ORDER BY received_at_ms DESC, id DESC LIMIT 1",
            (SOL_MINT, mint, str(ticket), at - 10_000, at)).fetchone()
        if buy is None:
            return {"status": "NO_ENTRY_QUOTE", "net_return": None}
        if buy["outcome"] != "quote" or not buy["out_amount_raw"] or int(buy["out_amount_raw"]) <= 0:
            return {"status": "NO_BUY_ROUTE", "buy_quote_id": buy["id"], "net_return": None}
        sell = self.market.execute(
            "SELECT * FROM quotes WHERE input_mint = ? AND output_mint = ? AND in_amount_raw = ?"
            " AND received_at_ms BETWEEN ? AND ? ORDER BY received_at_ms DESC, id DESC LIMIT 1",
            (mint, SOL_MINT, buy["out_amount_raw"], observed_ms - 10_000, observed_ms)).fetchone()
        out: dict[str, Any] = {"buy_quote_id": buy["id"], "original_token_quantity": buy["out_amount_raw"],
                               "entry_lamports": buy["in_amount_raw"], "buy_received_ms": buy["received_at_ms"],
                               "net_return": None, "confirmed_fill": False}
        if sell is None:
            return {**out, "status": "NO_EXACT_QUANTITY_EXIT_QUOTE"}
        out["sell_quote_id"] = sell["id"]
        out.update(exit_lamports=sell["out_amount_raw"], sell_received_ms=sell["received_at_ms"])
        if sell["outcome"] != "quote" or not sell["out_amount_raw"] or int(sell["out_amount_raw"]) <= 0:
            return {**out, "status": "NO_SELL_ROUTE"}
        return {**out, "status": "HYPOTHETICAL_QUOTE_MARK", "quoted_return_before_network_fees": str(
            D(sell["out_amount_raw"]) / D(buy["in_amount_raw"]) - 1)}

    def label(self, now: datetime, *, limit: int = 200) -> int:
        now_ms, written = to_epoch_millis(now), 0
        horizons = self.specification["horizons_seconds"]
        for i, horizon in enumerate(horizons):
            # Reserve work for long horizons; a busy 15m queue must not starve 24h outcomes.
            quota = max(0, (limit - written) // (len(horizons) - i))
            rows = self.research.execute(
                "SELECT p.*, c.asset, c.pool FROM opportunity_points p JOIN opportunity_cohorts c ON c.id = p.cohort_id"
                " WHERE p.spec_sha256 = ? AND p.decided_ms + ? <= ? AND NOT EXISTS (SELECT 1 FROM"
                " opportunity_outcomes o WHERE o.point_id = p.id AND o.horizon_seconds = ?)"
                " ORDER BY p.decided_ms LIMIT ?", (self.sha, (horizon + 90) * 1000, now_ms, horizon,
                                                    quota)).fetchall()
            with transaction(self.research):
                for point in rows:
                    target = point["decided_ms"] + horizon * 1000
                    end = self._endpoint(point, target)
                    payload: dict[str, Any] = {"net_return": None, "features_used_future_data": False}
                    ret, status = None, "NO_DATA"
                    reference = number(point["reference_price"])
                    if reference is None or reference <= 0:
                        status = "NO_REFERENCE_PRICE"
                    elif end is not None:
                        price = number(end["price_usd"])
                        liquidity = number(end["liquidity_usd"])
                        status = "CHART_ONLY"
                        if price is not None and price > 0 and end["price_usd_status"] == "ok":
                            ret = str(price / reference - 1)
                        else:
                            status = "INVALID_PRICE"
                        if liquidity is not None and liquidity <= 0:
                            status = "ZERO_LIQUIDITY"
                        payload.update(endpoint_snapshot_id=end["id"], observed_ms=end["observed_at_ms"],
                                       price_usd=end["price_usd"], liquidity_usd=end["liquidity_usd"],
                                       quote=self._quote_mark(point, end["observed_at_ms"]))
                        path = self._path(point, target)
                        valid = [number(r["price_usd"]) for r in path if r["price_usd_status"] == "ok"]
                        prices = [p for p in valid if p is not None and p > 0]
                        complete = (0 < len(path) <= MAX_PATH_ROWS and len(prices) == len(path)
                                    and path[0]["observed_at_ms"] == point["decided_ms"]
                                    and target - path[-1]["observed_at_ms"] <= 120_000
                                    and all(b["observed_at_ms"] - a["observed_at_ms"] <= 120_000
                                            for a, b in pairwise(path)))
                        payload.update(path_complete=complete, path_rows=min(len(path), MAX_PATH_ROWS),
                                       observed_mfe=str(max(prices) / reference - 1) if complete else None,
                                       observed_mae=str(min(prices) / reference - 1) if complete else None)
                    self.research.execute("INSERT INTO opportunity_outcomes VALUES (?, ?, ?, ?, ?, ?)",
                                          (point["id"], horizon, to_iso(now), status, ret, canonical_json(payload)))
                    written += 1
        return written

    def report(self, now: datetime) -> dict[str, Any]:
        rows = self.research.execute(
            "SELECT p.stance, p.flow_watch, p.price_watch, p.primary_state, p.evidence_kind, o.* FROM opportunity_outcomes o"
            " JOIN opportunity_points p ON p.id = o.point_id WHERE p.spec_sha256 = ? ORDER BY o.rowid DESC LIMIT 10000",
            (self.sha,)).fetchall()
        groups: dict[str, dict[str, Any]] = {}
        predicates: tuple[tuple[str, Callable[[sqlite3.Row], bool]], ...] = (
            ("ALL", lambda r: True), ("FLOW", lambda r: bool(r["flow_watch"])),
            ("PRICE", lambda r: bool(r["price_watch"])),
            ("BOTH", lambda r: bool(r["flow_watch"] and r["price_watch"])))
        for name, predicate in predicates:
            groups[name] = {}
            for horizon in self.specification["horizons_seconds"]:
                selected = [r for r in rows if r["horizon_seconds"] == horizon and predicate(r)]
                prices = [D(r["chart_return"]) for r in selected if r["chart_return"] is not None]
                quote_status = Counter(json.loads(r["payload_json"]).get("quote", {}).get("status", "NOT_OBSERVED")
                                       for r in selected)
                groups[name][str(horizon)] = {"matured": len(selected), "charted": len(prices),
                    "status": dict(Counter(r["status"] for r in selected)), "quote_status": dict(quote_status),
                    "charted_up_50pct": sum(p >= D("0.5") for p in prices),
                    "primary_state": dict(Counter(r["primary_state"] for r in selected)),
                    "by_evidence_kind": dict(Counter(r["evidence_kind"] for r in selected)),
                    "net_expectancy": None}
        total = int(self.research.execute("SELECT COUNT(*) FROM opportunity_outcomes o JOIN opportunity_points p"
                                         " ON p.id = o.point_id WHERE p.spec_sha256 = ?", (self.sha,)).fetchone()[0])
        cohorts = int(self.research.execute("SELECT COUNT(*) FROM opportunity_cohorts WHERE venue = ?",
                                            (self.venue,)).fetchone()[0])
        covered = int(self.research.execute("SELECT COUNT(DISTINCT cohort_id) FROM opportunity_points"
                                           " WHERE spec_sha256 = ?", (self.sha,)).fetchone()[0])
        cursor = self.research.execute("SELECT snapshot_id FROM opportunity_cursors WHERE spec_sha256 = ?",
                                       (self.sha,)).fetchone()
        max_id = self._latest_source_id()
        return {"as_of": to_iso(now), "venue": self.venue, "spec_sha256": self.sha, "influence": "NONE", "promotion": "DISABLED",
                "verdict": "INSUFFICIENT EVIDENCE: descriptive SHADOW/replay records, no validated net edge",
                "cohorts": cohorts, "cohorts_with_comparable_points": covered,
                "cohorts_without_comparable_points": cohorts - covered,
                "points_by_evidence_kind": dict(self.research.execute("SELECT evidence_kind, COUNT(*)"
                    " FROM opportunity_points WHERE spec_sha256 = ? GROUP BY evidence_kind", (self.sha,))),
                "source_rows_pending_approx": max(0, max_id - (int(cursor[0]) if cursor else 0)),
                "matured_outcomes": total, "summary_outcomes": len(rows), "summary_truncated": total > len(rows),
                "groups": groups, "caveats": ["Locally observed pools, not market-complete coverage",
                    "Snapshots are provider claims; buys/sells are trades, not unique wallets",
                    "Charts and hypothetical quoted marks are not realized or net returns",
                    "Points overlap within assets and share market regimes; counts are not independent trials",
                    "Replay and prospective records are reported separately; neither establishes profitability",
                    "No collector, filter, sizing, ledger or authorization is changed"]}


def readonly_market(path: Any) -> sqlite3.Connection:
    if not path.is_file():
        raise StorageError("market.db is missing; run the existing collector first")
    conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn
