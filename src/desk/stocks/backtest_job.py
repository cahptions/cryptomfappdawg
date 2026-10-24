"""One owned/cancellable historical replay, automatically refreshed on a new stored daily close."""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import date, datetime, timedelta
from typing import Any

from desk.core.clock import utc_now
from desk.core.config import load_config
from desk.core.errors import DeskError
from desk.core.paths import DeskPaths
from desk.core.worker_lock import WorkerLock
from desk.stocks.analysis import MIN_BARS
from desk.stocks.backtest import VERSION, replay
from desk.stocks.connection import _atomic_private
from desk.stocks.evidence_job import EvidenceWorker, publish
from desk.stocks.repo import MARKET_FILE
from desk.stocks.runner import _memory_guard


class BacktestWorker(EvidenceWorker):
    kind = "backtest"
    module = "desk.stocks.backtest_job"
    from_automation = False

    def preferences(self) -> dict[str, Any]:
        path = self.paths.home / "stocks-backtest-preferences.json"
        if not path.exists():
            return {"enabled": True, "sessions": 0, "last_input": None}
        try:
            if path.stat().st_size > 4096:
                raise ValueError
            value = json.loads(path.read_text())
            if (
                not isinstance(value, dict)
                or not isinstance(value.get("enabled"), bool)
                or type(value.get("sessions")) is not int
                or value.get("sessions") not in (0, 63, 126, 252)
            ):
                raise ValueError
            if value.get("attempted_at"):
                if not isinstance(value["attempted_at"], str):
                    raise ValueError
                attempted = datetime.fromisoformat(value["attempted_at"])
                if attempted.tzinfo is None:
                    raise ValueError
            if value.get("last_input") is not None and not isinstance(value["last_input"], str):
                raise ValueError
            if "window_mode" not in value:
                value["sessions"] = 0  # upgrade the former fixed-window default to earliest eligible stored history
            return dict(value)
        except (OSError, ValueError) as exc:
            raise DeskError("Historical replay preferences need repair. Save the automatic replay setting here again.") from exc

    def save_preferences(self, value: dict[str, Any]) -> None:
        value["window_mode"] = "all_available" if value.get("sessions") == 0 else "recent_sessions"
        _atomic_private(self.paths.home / "stocks-backtest-preferences.json", json.dumps(value))

    def coverage(self) -> dict[str, Any]:
        conn = sqlite3.connect((self.paths.db_dir / MARKET_FILE).as_uri() + "?mode=ro", uri=True, timeout=2)
        try:
            row = conn.execute("SELECT COUNT(*),MAX(day),MIN(day),SUM(rows) FROM stk_bar_days WHERE rows>0").fetchone()
            return {
                "sessions": row[0],
                "latest": date.fromordinal(row[1]).isoformat() if row[1] else None,
                "warmup_sessions": MIN_BARS,
                "eligible_sessions": max(0, row[0] - MIN_BARS),
                "earliest": date.fromordinal(row[2]).isoformat() if row[2] else None,
                "rows": row[3] or 0,
            }
        finally:
            conn.close()

    def status(self) -> dict[str, Any]:
        value = super().status()
        value["auto_started"] = self.from_automation and value["owned"]
        try:
            prefs = self.preferences()
            value.update(auto_enabled=prefs["enabled"], requested_sessions=prefs["sessions"], coverage=self.coverage())
            eligible = value["coverage"]["eligible_sessions"]
            if not value["running"] and value["status"] == "idle":
                value["phase"] = (
                    f"Ready to replay {eligible} eligible sessions"
                    if eligible >= (prefs["sessions"] or 2)
                    else "Collecting earlier price history and strategy warm-up; replay starts automatically"
                )
            if prefs.get("attempted_at") and value.get("status") in {"failed", "interrupted"}:
                value["retry_at"] = (datetime.fromisoformat(prefs["attempted_at"]) + timedelta(minutes=10)).isoformat()
        except (DeskError, sqlite3.Error):
            value.update(auto_enabled=False, requested_sessions=0, preference_error="Check and save historical replay settings")
        return value

    def extra_env(self) -> dict[str, str]:
        return {"DESK_STOCKS_BACKTEST_SESSIONS": str(self.preferences()["sessions"])}

    def start(self, sessions: int = 0, *, automatic: bool = False) -> dict[str, Any]:
        if sessions not in (0, 63, 126, 252):
            raise DeskError("Choose all available history, 63, 126 or 252 historical sessions")
        with self.lock:
            if self.status()["running"]:
                raise DeskError("A historical replay is already running. Its progress is shown here.")
            if WorkerLock(self.paths, "stocks-evidence").held():
                raise DeskError("Evidence evaluation is running. Finish or cancel it in Evidence first.")
            coverage = self.coverage()
            needed = (sessions or 2) + MIN_BARS
            if coverage["sessions"] < needed:
                raise DeskError(
                    f"Replay needs {needed} stored sessions including warm-up; "
                    f"{coverage['sessions']} are available. Collection continues independently."
                )
            try:
                prefs = self.preferences()
            except DeskError:
                prefs = {"enabled": False}
            prefs.update(sessions=sessions, last_input=self.input_key(sessions, coverage), attempted_at=utc_now().isoformat())
            self.save_preferences(prefs)
            self.from_automation = automatic
            return super().start()

    def pause_automatic(self) -> None:
        with self.lock:
            status = self.status()
            if status["owned"] and status["auto_started"] and not status["stopping"]:
                self.cancel()
                prefs = self.preferences()
                prefs["last_input"] = None  # resuming PAPER may restart this interrupted automatic replay
                prefs.pop("attempted_at", None)
                self.save_preferences(prefs)

    def automatic(self, enabled: bool, sessions: int) -> dict[str, Any]:
        if sessions not in (0, 63, 126, 252):
            raise DeskError("Choose all available history, 63, 126 or 252 historical sessions")
        with self.lock:
            try:
                prefs = self.preferences()
            except DeskError:
                prefs = {}
            self.save_preferences({**prefs, "enabled": enabled, "sessions": sessions})
            return self.status()

    def maintain(self) -> None:
        with self.lock:
            try:
                prefs = self.preferences()
                if not prefs["enabled"] or self.status()["running"]:
                    return
                coverage = self.coverage()
                key = self.input_key(prefs["sessions"], coverage)
                last_attempt = prefs.get("attempted_at")
                failed = self.status().get("status") in {"failed", "interrupted"}
                cooling_down = last_attempt and utc_now() - datetime.fromisoformat(last_attempt) < timedelta(minutes=10)
                if (
                    coverage["eligible_sessions"] >= (prefs["sessions"] or 2)
                    and (prefs.get("last_input") != key or failed)
                    and not cooling_down
                ):
                    self.start(prefs["sessions"], automatic=True)
            except (DeskError, sqlite3.Error):
                return  # preferences/source diagnosis is visible; no crash/restart loop

    @staticmethod
    def input_key(sessions: int, coverage: dict[str, Any]) -> str:
        return f"{VERSION}:{sessions}:{coverage['latest']}:{coverage['sessions']}:{coverage['rows']}"


def run(paths: DeskPaths, sessions: int) -> None:
    with WorkerLock(paths, "stocks-backtest"):
        try:
            _memory_guard()

            def progress(day: date, done: int, total: int) -> None:
                _memory_guard()
                publish(
                    paths,
                    kind="backtest",
                    status="running",
                    phase=f"Replaying {day} · {done}/{total} sessions",
                    completed=done,
                    total=total,
                )

            result = replay(paths, load_config(paths.config_dir).settings.stocks, sessions, progress=progress)
            result["generated_at"] = utc_now().isoformat()
            payload = json.dumps(result, allow_nan=False)
            if len(payload.encode()) > 4_000_000:
                raise DeskError("The replay report is too large. Choose a shorter window; the previous report was preserved.")
            _atomic_private(paths.home / "stocks-backtest-report.json", payload)
            publish(
                paths,
                kind="backtest",
                status="completed",
                phase="Historical replay complete",
                completed=result["requested_sessions"],
                total=result["requested_sessions"],
            )
        except DeskError as exc:
            publish(paths, kind="backtest", status="failed", phase=str(exc))
        except Exception:
            publish(paths, kind="backtest", status="failed", phase="Replay could not finish. Check Data health and retry.")
            raise


if __name__ == "__main__":
    os.nice(10)
    count = int(os.environ.get("DESK_STOCKS_BACKTEST_SESSIONS", "0"))
    if count not in (0, 63, 126, 252):
        raise SystemExit("Unsupported replay window")
    run(DeskPaths.from_env(), count)
