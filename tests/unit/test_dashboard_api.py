from __future__ import annotations

import asyncio
import csv
import io
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from desk.core.paths import DeskPaths
from desk.storage.databases import Databases
from desk.web.app import SnapshotCache, create_app
from desk.web.control import WorkerManager
from desk.web.data import DashboardData


@pytest.fixture
def paths(desk_home: Path, config_dir: Path, tmp_path: Path) -> DeskPaths:
    return DeskPaths(desk_home, config_dir, tmp_path / "secrets.env")


def test_empty_api_and_reads_do_not_initialize_paper(paths: DeskPaths) -> None:
    with TestClient(create_app(paths), base_url="http://127.0.0.1") as client:
        response = client.get("/api/snapshot")
        assert response.status_code == 200
        snapshot = response.json()
        assert snapshot["paper"]["initialized"] is False
        assert snapshot["live"]["buy_enabled"] is False
        assert snapshot["scanner"] == []
        mint = "So11111111111111111111111111111111111111112"
        assert client.get(f"/api/charts/{mint}").json()["points"] == []
        assert client.get(f"/api/charts/{mint}?limit=501").status_code == 422
        assert client.get("/api/charts/not-a-mint").status_code == 422
        assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
        assert response.headers["cache-control"] == "no-store"
        for route in ("candidates", "positions", "strategies"):
            assert client.get(f"/api/{route}/missing").status_code == 404
    dbs = Databases.open(paths)
    try:
        assert dbs.paper_ledger.execute("SELECT COUNT(*) FROM positions").fetchone()[0] == 0
        assert dbs.paper_ledger.execute("SELECT COUNT(*) FROM cash_entries").fetchone()[0] == 0
    finally:
        dbs.close()


def test_cross_origin_dns_rebinding_and_csrf_are_refused(paths: DeskPaths) -> None:
    with TestClient(create_app(paths), base_url="http://127.0.0.1") as client:
        assert client.get("/api/session", headers={"Host": "attacker.example"}).status_code == 403
        assert client.get("/api/session", headers={"Origin": "https://attacker.example"}).status_code == 403
        assert client.get("/api/session", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
        assert client.post("/api/control/stop").status_code == 403
        token = client.get("/api/session").json()["csrf_token"]
        headers = {"X-CSRF-Token": token}
        assert client.post("/api/control/stop", headers={**headers, "Origin": "http://localhost"}).status_code == 403
        assert not paths.stop_file.exists()
        response = client.post("/api/control/stop", headers={**headers, "Origin": "http://127.0.0.1"})
        assert response.status_code == 200
        assert response.json()["halted"] is True
        assert paths.stop_file.exists()
        assert client.post("/api/control/resume", headers=headers).status_code == 422
        assert client.post("/api/control/arm", headers=headers).status_code == 422


def test_control_only_accepts_bounded_fixed_actions(paths: DeskPaths, monkeypatch: pytest.MonkeyPatch) -> None:
    starts: list[tuple[str, float]] = []

    def start(_: WorkerManager, kind: str, minutes: float = 4320) -> dict[str, Any]:
        starts.append((kind, minutes))
        return {"name": kind}

    monkeypatch.setattr(WorkerManager, "start", start)
    with TestClient(create_app(paths), base_url="http://127.0.0.1") as client:
        token = client.get("/api/session").json()["csrf_token"]
        headers = {"X-CSRF-Token": token}
        for bad in ({"minutes": 0}, {"minutes": 10081}, {"argv": ["rm"]}, {"mode": "LIVE"}):
            assert client.post("/api/control/start-paper", headers=headers, json=bad).status_code == 422
        assert not starts
        assert client.post("/api/control/start-paper", headers=headers, json={"minutes": 3}).status_code == 200
        assert starts == [("paper", 3)]


def test_exports_exact_units_and_neutralize_spreadsheet_formulas(paths: DeskPaths,
                                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    def trades(_: DashboardData) -> list[dict[str, Any]]:
        return [{"id": "p1", "mint": "=HYPERLINK(\"https://example.invalid\")", "state": "CLOSED",
                 "ticket_lamports": "99999999999999999999", "exit_reason": "safe\nline"}]

    monkeypatch.setattr(DashboardData, "trades", trades)
    with TestClient(create_app(paths), base_url="http://127.0.0.1") as client:
        response = client.get("/api/export/trades.csv")
        rows = list(csv.DictReader(io.StringIO(response.text)))
        assert rows[0]["mint"].startswith("'=")
        assert rows[0]["ticket_lamports"] == "99999999999999999999"
        assert rows[0]["exit_reason"] == "safe\nline"
        assert response.headers["x-export-limit"] == "10000"
        assert "attachment" in response.headers["content-disposition"]
        assert client.get("/api/export/trades.json").json()[0]["ticket_lamports"] == "99999999999999999999"


async def test_snapshot_concurrent_readers_share_one_fetch() -> None:
    count = 0

    def fetch() -> dict[str, Any]:
        nonlocal count
        count += 1
        return {"count": count}

    cache = SnapshotCache(fetch, ttl=30)
    values = await asyncio.gather(*(cache.get() for _ in range(25)))
    assert count == 1
    assert all(value == {"count": 1} for value in values)
    cache.invalidate()
    assert await cache.get() == {"count": 2}
