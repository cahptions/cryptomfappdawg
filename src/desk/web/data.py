"""Bounded dashboard views over existing databases. GETs never create, migrate or write a database."""

from __future__ import annotations

import contextlib
import dataclasses
import json
import sqlite3
import tomllib
from collections.abc import Iterator
from datetime import datetime
from decimal import Decimal
from typing import Any

from desk.contracts.features import FeatureSnapshot, FeatureValue
from desk.contracts.observed import DataStatus
from desk.core.clock import parse_iso8601, to_epoch_millis, utc_now
from desk.core.config import LoadedConfig
from desk.core.logging import redaction_filter
from desk.core.paths import DeskPaths
from desk.ops.system import battery_info, disk_free_gb, memory_info, process_rss_mb
from desk.paper.ledger import PaperLedger, as_dict
from desk.regime.classifier import RegimeReference, classify_regime
from desk.regime.context import INPUTS, build_reference, latest_context
from desk.security.policy import SOL_MINT
from desk.storage.databases import Databases
from desk.storage.discovery_repo import DiscoveryRepository
from desk.web.paper_view import paper_snapshot


def _safe(value: Any, key: str = "") -> Any:
    """Keep exact quantities exact when crossing JavaScript's 53-bit integer boundary."""
    if isinstance(value, dict):
        return {k: _safe(v, k) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [_safe(v, key) for v in value]
    if isinstance(value, str):
        return redaction_filter().redact(value)
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value) if "lamports" in key or key.endswith("_raw") or abs(value) > 2 ** 53 - 1 else value
    if isinstance(value, Decimal):
        return str(value)
    return value


def _json_row(row: sqlite3.Row, fields: tuple[str, ...]) -> dict[str, Any]:
    result = dict(row)
    for key in fields:
        raw = result.pop(key, None)
        result[key.removesuffix("_json")] = None if raw is None else json.loads(raw)
    return result


class DashboardData:
    def __init__(self, paths: DeskPaths, loaded: LoadedConfig) -> None:
        self.paths, self.loaded = paths, loaded
        self._reference_hour: int | None = None
        self._reference: RegimeReference | None = None

    @contextlib.contextmanager
    def _databases(self) -> Iterator[Databases | None]:
        files = [self.paths.db_dir / f"{name}.db" for name in ("market", "research", "runtime", "ledger-paper")]
        if not all(path.is_file() for path in files):
            yield None
            return
        connections = []
        try:
            for path in files:
                conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, isolation_level=None, timeout=2)
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA query_only=ON")
                conn.execute("PRAGMA busy_timeout=2000")
                conn.execute("BEGIN")
                connections.append(conn)
            yield Databases(market=connections[0], research=connections[1], runtime=connections[2],
                            paper_ledger=connections[3], paths=self.paths)
        finally:
            for conn in connections:
                conn.close()

    @staticmethod
    def _confidence(dbs: Databases) -> dict[str, Any]:
        row = dbs.research.execute("SELECT id, created_at, payload_json FROM experiments "
                                  "WHERE kind = 'CONFIDENCE_MATRIX' ORDER BY created_at DESC, id DESC LIMIT 1").fetchone()
        if row is None:
            return {"generated_at": None, "closed_trades": 0, "matrices": [],
                    "note": "No recorded confidence report. Run desk confidence after closing PAPER trades."}
        result: dict[str, Any] = json.loads(row["payload_json"])
        result["experiment_id"] = row["id"]
        return result

    @staticmethod
    def _scanner(dbs: Databases) -> list[dict[str, Any]]:
        rows = dbs.market.execute(
            "SELECT d.id, d.mint, d.pool_address, t.symbol, t.name, d.reason, d.state, d.stage, d.last_rule_id, "
            "d.last_observed_at AS observed_at, s.price_usd, s.liquidity_usd, s.windows_json, "
            "s.observed_at AS market_observed_at, s.price_usd_status, s.liquidity_status "
            "FROM discoveries d JOIN tokens t ON t.mint = d.mint LEFT JOIN market_snapshots s ON s.id = "
            "(SELECT x.id FROM market_snapshots x WHERE x.base_mint = d.mint "
            "AND (d.pool_address = '' OR x.pool_address = d.pool_address) "
            "ORDER BY x.observed_at_ms DESC, x.id DESC LIMIT 1) ORDER BY d.last_observed_at DESC, d.id LIMIT 100")
        result = []
        for row in rows:
            item = dict(row)
            windows = json.loads(item.pop("windows_json") or "{}")
            item["volume_h1"] = (windows.get("h1") or {}).get("volume_usd")
            result.append(item)
        return result

    @staticmethod
    def _workers(dbs: Databases, now: datetime) -> list[dict[str, Any]]:
        workers = []
        for row in dbs.runtime.execute("SELECT process, pid, at FROM heartbeats ORDER BY process LIMIT 20"):
            age = (now - parse_iso8601(row["at"])).total_seconds()
            max_age = 90 if row["process"] in {"collector", "paper-engine"} else 30
            workers.append({"name": row["process"], "pid": row["pid"], "age_seconds": round(age, 1),
                            "active": 0 <= age <= max_age, "managed": False})
        return workers

    @staticmethod
    def _equity(dbs: Databases) -> list[dict[str, Any]]:
        ledger = PaperLedger(dbs.paper_ledger)
        if not ledger.initialized():
            return []
        meta = ledger.meta()
        rows = list(dbs.paper_ledger.execute("SELECT closed_at, realized_pnl_lamports FROM positions "
                                            "WHERE state = 'CLOSED' ORDER BY closed_at DESC, id DESC LIMIT 499"))
        total = ledger.realized_pnl_lamports()
        balance = int(meta["deposit_lamports"]) + total - sum(row["realized_pnl_lamports"] for row in rows)
        result = []
        if len(rows) < 499:
            result.append({"at": meta["book_started_at"], "value_sol": str(Decimal(balance) / 10 ** 9),
                           "label": "realized_cash", "kind": "realized_cash"})
        for row in reversed(rows):
            balance += row["realized_pnl_lamports"]
            result.append({"at": row["closed_at"], "value_sol": str(Decimal(balance) / 10 ** 9),
                           "label": "realized_cash", "kind": "realized_cash"})
        return result

    def _system(self) -> dict[str, Any]:
        memory, battery, rss = memory_info(), battery_info(), process_rss_mb()
        databases = {}
        for name in ("market", "research", "runtime", "ledger-paper"):
            path = self.paths.db_dir / f"{name}.db"
            wal = path.with_name(path.name + "-wal")
            databases[name] = {"bytes": path.stat().st_size if path.exists() else 0,
                               "wal_bytes": wal.stat().st_size if wal.exists() else 0}
        return {"memory": None if memory is None else dataclasses.asdict(memory),
                "battery": None if battery is None else dataclasses.asdict(battery),
                "process_rss_mb": None if rss is None else rss[0], "disk_free_gb": disk_free_gb(self.paths.home),
                "databases": databases, "note": "Measurements describe the machine serving this dashboard."}

    def _regime(self, dbs: Databases, now: datetime) -> dict[str, Any]:
        context = latest_context(dbs.market, now)
        if context is None:
            return {"label": None, "status": "missing", "reason": "No recorded market context."}
        hour = to_epoch_millis(now) // 3_600_000
        if hour != self._reference_hour:
            # Reuse the strategy core's measured reference, cached by decision hour. This reads
            # recorded observations only; the dashboard never inserts context or calibration data.
            self._reference = build_reference(dbs.market, now)
            self._reference_hour = hour
        at = parse_iso8601(context["observed_at"])
        age = (now - at).total_seconds()
        values = {name: FeatureValue(value=None if context[name] is None else Decimal(context[name]),
                                    status=DataStatus.MISSING if context[name] is None else (
                                        DataStatus.STALE if age > 120 else DataStatus.OK), unit="context",
                                    source="market_context:" + context["version"], observed_at=at, age_seconds=age)
                  for name in INPUTS}
        features = FeatureSnapshot(mint=SOL_MINT, pool_address=SOL_MINT, as_of=now,
                                   feature_set_version="dashboard-market-context-v1", values=values)
        regime = classify_regime(features, self._reference)
        return {"observed_at": context["observed_at"], "label": None if regime.label is None else regime.label.value,
                "status": regime.status.value, "reason": "; ".join(regime.reasons),
                "coverage": json.loads(context["coverage_json"]), "version": regime.version,
                **{name: context[name] for name in INPUTS}}

    def _live(self, dbs: Databases, now: datetime, halted: str | None) -> dict[str, Any]:
        from desk.live.gates import evaluate_gates, status_context
        from desk.paper.engine import heartbeat_age
        from desk.stats.champion import current_champion

        with (self.paths.config_dir / "safety_ceilings.toml").open("rb") as handle:
            ceiling = Decimal(str(tomllib.load(handle)["max"]["live.max_capital_sol"]))
        version = current_champion(dbs.research) or "none"
        age = heartbeat_age(dbs.runtime, "guardian", now)
        context = status_context(dbs.research, self.loaded, version, signer_unlocked=False, ceiling=ceiling,
                                 halted=halted, guardian_fresh=age is not None and 0 <= age <= 20)
        return {"armed": False, "buy_enabled": False, "strategy_version": version,
                "max_capital_sol": str(self.loaded.settings.live.max_capital_sol),
                "capital_ceiling_sol": str(ceiling),
                "gates": [dataclasses.asdict(gate) for gate in evaluate_gates(context)],
                "reason": "LIVE entry execution is unavailable. Runtime signer status is checked separately; "
                          "per-entry quote, fee and token preflight remain unverified."}

    def snapshot(self) -> dict[str, Any]:
        now = utc_now()
        result: dict[str, Any] = {
            "generated_at": now.isoformat(), "mode": self.loaded.settings.desk.mode.value,
            "status": {"label": "IDLE", "halted": False, "reason": "No PAPER book has started."},
            "paper": {"initialized": False, "note": "Start desk paper to create the PAPER learning book."},
            "scanner": [], "funnel": {"by_state": {}, "by_reason": {}, "rejection_reasons": {}},
            "health": [], "workers": [], "confidence": {"generated_at": None, "closed_trades": 0, "matrices": []},
            "audit": [], "equity": [], "system": self._system(),
            "live": {"armed": False, "buy_enabled": False, "gates": [],
                     "reason": "LIVE entry execution is unavailable. Arming remains CLI-only."},
            "regime": {"label": None, "status": "missing", "reason": "No recorded market context."},
        }
        with self._databases() as dbs:
            if dbs is None:
                return _safe(result)  # type: ignore[no-any-return]
            paper = paper_snapshot(dbs, self.loaded, now)
            for row in paper.get("open", []):
                detail = dbs.paper_ledger.execute("SELECT mint, last_mark_at FROM positions WHERE id = ?",
                                                 (row["id"],)).fetchone()
                if detail is not None:
                    row.update(dict(detail))
                    row["mark_at"] = detail["last_mark_at"]
                    token = dbs.market.execute("SELECT symbol FROM tokens WHERE mint = ?", (detail["mint"],)).fetchone()
                    row["symbol"] = None if token is None else token["symbol"]
            result["paper"], result["scanner"] = paper, self._scanner(dbs)
            report = DiscoveryRepository(dbs.market, self.loaded.settings.discovery).report()
            result["funnel"] = {"by_state": report.get("by_state", {}), "by_reason": report.get("by_reason", {}),
                                "rejection_reasons": report.get("rejection_reasons", report.get("rejections", {}))}
            result["health"] = [dict(row) for row in dbs.runtime.execute(
                "SELECT provider, state, last_success_at, last_error FROM provider_health ORDER BY provider LIMIT 30")]
            result["workers"] = self._workers(dbs, now)
            if result["mode"] != "LIVE" and any(
                worker["active"] and worker["name"] == "paper-engine" for worker in result["workers"]
            ):
                result["mode"] = "PAPER"
            result["confidence"] = self._confidence(dbs)
            from desk.ops.readiness import latest_recorded

            result["readiness"] = latest_recorded(dbs.research, now)
            result["audit"] = [_json_row(row, ("payload_json",)) for row in dbs.paper_ledger.execute(
                "SELECT * FROM paper_events ORDER BY id DESC LIMIT 100")]
            # Replay contexts and quote arrays stay in the evidence store and detail routes. Re-sending
            # hundreds of full feature dossiers every two seconds would waste the laptop's memory.
            for event in result["audit"]:
                event["payload"] = {key: value for key, value in event["payload"].items()
                                    if key not in {"context", "ranking", "quotes", "profiles"}}
            result["equity"] = self._equity(dbs)
            halted = paper.get("halted") or ("STOP" if self.paths.stop_file.exists() else None)
            active = any(worker["active"] and worker["name"] == "collector" for worker in result["workers"])
            result["status"] = {"label": "HALTED" if halted else "ACTIVE" if active else "IDLE",
                                "halted": bool(halted), "reason": halted or (
                                    "Collection heartbeat is fresh." if active else "No fresh collection heartbeat.")}
            result["regime"] = self._regime(dbs, now)
            result["live"] = self._live(dbs, now, halted)
        return _safe(result)  # type: ignore[no-any-return]

    def candidate(self, candidate_id: str) -> dict[str, Any] | None:
        with self._databases() as dbs:
            if dbs is None:
                return None
            row = dbs.market.execute("SELECT d.*, t.symbol, t.name FROM discoveries d JOIN tokens t ON t.mint = d.mint "
                                     "WHERE d.id = ?", (candidate_id,)).fetchone()
            if row is None:
                return None
            result = dict(row)
            result["stages"] = [_json_row(stage, ("observed_values_json", "thresholds_json")) for stage in dbs.market.execute(
                "SELECT * FROM stage_results WHERE candidate_id = ? ORDER BY id DESC LIMIT 100", (candidate_id,))]
            security = dbs.market.execute("SELECT payload_json FROM security_assessments WHERE candidate_id = ? "
                                          "ORDER BY id DESC LIMIT 1", (candidate_id,)).fetchone()
            result["security"] = None if security is None else json.loads(security["payload_json"])
            result["observations"] = [dict(item) for item in dbs.market.execute(
                "SELECT source, reason, observed_at FROM discovery_observations WHERE candidate_id = ? "
                "ORDER BY observed_at DESC LIMIT 100", (candidate_id,))]
            return _safe(result)  # type: ignore[no-any-return]

    def paper(self) -> dict[str, Any]:
        """The Paper trading tab: one bounded read of the learning book (see desk.web.paper_tab)."""
        from desk.web.paper_tab import paper_tab

        with self._databases() as dbs:
            if dbs is None:
                return {"initialized": False, "status": {"headline": "No databases yet", "tone": "neutral",
                                                         "detail": "start desk collect, desk discover or desk paper"}}
            return _safe(paper_tab(dbs, self.loaded, utc_now()))  # type: ignore[no-any-return]

    def position(self, position_id: str) -> dict[str, Any] | None:
        with self._databases() as dbs:
            if dbs is None:
                return None
            row = dbs.paper_ledger.execute("SELECT * FROM positions WHERE id = ?", (position_id,)).fetchone()
            if row is None:
                return None
            result = as_dict(row)
            result["timeline"] = [_json_row(event, ("payload_json",)) for event in dbs.paper_ledger.execute(
                "SELECT * FROM paper_events WHERE position_id = ? ORDER BY id DESC LIMIT 200", (position_id,))]
            result["marks"] = [dict(mark) for mark in dbs.paper_ledger.execute(
                "SELECT at, kind, value_lamports FROM marks WHERE position_id = ? ORDER BY id DESC LIMIT 500",
                (position_id,))]
            return _safe(result)  # type: ignore[no-any-return]

    def strategy(self, strategy_id: str) -> dict[str, Any] | None:
        with self._databases() as dbs:
            if dbs is None:
                return None
            versions = [_json_row(row, ("content_json",)) for row in dbs.market.execute(
                "SELECT * FROM strategy_versions WHERE strategy_id = ? OR spec_hash = ? ORDER BY registered_at DESC LIMIT 20",
                (strategy_id, strategy_id))]
            if not versions:
                return None
            matrices = self._confidence(dbs)["matrices"]
            result = {"id": strategy_id, "versions": versions, "confidence": [matrix for matrix in matrices
                      if matrix["strategy_id"] == strategy_id or matrix["strategy_version"] == strategy_id]}
            return _safe(result)  # type: ignore[no-any-return]

    def trades(self) -> list[dict[str, Any]]:
        with self._databases() as dbs:
            if dbs is None:
                return []
            result = [dict(row) for row in dbs.paper_ledger.execute(
                "SELECT id, mint, pool_address, strategy_id, strategy_version, state, opened_at, closed_at, "
                "ticket_lamports, tokens_raw, entry_fees_lamports, exit_lamports, exit_fees_lamports, "
                "realized_pnl_lamports, exit_reason FROM positions ORDER BY opened_at DESC, id DESC LIMIT 10000")]
            return _safe(result)  # type: ignore[no-any-return]
