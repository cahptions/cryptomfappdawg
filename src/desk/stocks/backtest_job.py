"""One owned/cancellable historical replay, automatically refreshed on a new stored daily close."""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import date
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
            return {"enabled": True, "sessions": 63, "last_input": None}
        try:
            if path.stat().st_size > 4096:
                raise ValueError
            value = json.loads(path.read_text())
            if (
                not isinstance(value, dict)
                or not isinstance(value.get("enabled"), bool)
                or value.get("sessions") not in (63, 126, 252)
            ):
                raise ValueError
            return dict(value)
        except (OSError, ValueError) as exc:
            raise DeskError("Historical replay preferences need repair. Save the automatic replay setting here again.") from exc

    def save_preferences(self, value: dict[str, Any]) -> None:
        _atomic_private(self.paths.home / "stocks-backtest-preferences.json", json.dumps(value))

    def coverage(self) -> dict[str, Any]:
        conn = sqlite3.connect((self.paths.db_dir / MARKET_FILE).as_uri() + "?mode=ro", uri=True, timeout=2)
        try:
            row = conn.execute("SELECT COUNT(*),MAX(day) FROM stk_bar_days WHERE rows>0").fetchone()
            return {
                "sessions": row[0],
                "latest": date.fromordinal(row[1]).isoformat() if row[1] else None,
                "warmup_sessions": MIN_BARS,
            }
        finally:
            conn.close()

    def status(self) -> dict[str, Any]:
        value = super().status()
        value["auto_started"] = self.from_automation and value["owned"]
        try:
            prefs = self.preferences()
            value.update(auto_enabled=prefs["enabled"], requested_sessions=prefs["sessions"], coverage=self.coverage())
        except (DeskError, sqlite3.Error):
            value.update(auto_enabled=False, requested_sessions=63, preference_error="Check and save historical replay settings")
        return value

    def extra_env(self) -> dict[str, str]:
        return {"DESK_STOCKS_BACKTEST_SESSIONS": str(self.preferences()["sessions"])}

    def start(self, sessions: int = 63, *, automatic: bool = False) -> dict[str, Any]:
        if sessions not in (63, 126, 252):
            raise DeskError("Choose 63, 126 or 252 historical sessions")
        with self.lock:
            if self.status()["running"]:
                raise DeskError("A historical replay is already running. Its progress is shown here.")
            if WorkerLock(self.paths, "stocks-evidence").held():
                raise DeskError("Evidence evaluation is running. Finish or cancel it in Evidence first.")
            coverage = self.coverage()
            if coverage["sessions"] < sessions + MIN_BARS:
                raise DeskError(
                    f"Replay needs {sessions + MIN_BARS} stored sessions including warm-up; "
                    f"{coverage['sessions']} are available. Collection continues independently."
                )
            try:
                prefs = self.preferences()
            except DeskError:
                prefs = {"enabled": False}
            prefs.update(sessions=sessions, last_input=f"{VERSION}:{sessions}:{coverage['latest']}")
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
                self.save_preferences(prefs)

    def automatic(self, enabled: bool, sessions: int) -> dict[str, Any]:
        if sessions not in (63, 126, 252):
            raise DeskError("Choose 63, 126 or 252 historical sessions")
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
                key = f"{VERSION}:{prefs['sessions']}:{coverage['latest']}"
                if coverage["sessions"] >= prefs["sessions"] + MIN_BARS and prefs.get("last_input") != key:
                    self.start(prefs["sessions"], automatic=True)
            except (DeskError, sqlite3.Error):
                return  # preferences/source diagnosis is visible; no crash/restart loop


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
                paths, kind="backtest", status="completed", phase="Historical replay complete", completed=sessions, total=sessions
            )
        except DeskError as exc:
            publish(paths, kind="backtest", status="failed", phase=str(exc))
        except Exception:
            publish(paths, kind="backtest", status="failed", phase="Replay could not finish. Check Data health and retry.")
            raise


if __name__ == "__main__":
    os.nice(10)
    count = int(os.environ.get("DESK_STOCKS_BACKTEST_SESSIONS", "63"))
    if count not in (63, 126, 252):
        raise SystemExit("Unsupported replay window")
    run(DeskPaths.from_env(), count)
