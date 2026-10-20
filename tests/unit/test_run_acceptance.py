"""Recorded run acceptance must reject short, interrupted, or zero-work reports."""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from desk.core.paths import DeskPaths
from desk.ops import launcher
from desk.ops.acceptance import discovery_run_failures, segmented_progress
from desk.ops.launcher import desktop_entry, install_launchers


def complete_run():
    return {"started_at": "2026-10-18T00:00:00+00:00", "ended_at": "2026-10-19T00:00:00+00:00", "stats": {
        "discovery": {"orphans": 0}, "unhandled_exceptions": 0, "suspend_gaps": 0, "peak_rss_mb": 60,
        "completed_requested_duration": True,
        "tasks": {name: {"scheduled": 1440, "succeeded": 1440} for name in (
            "geckoterminal_new_pools", "jupiter_recent", "dexscreener_refresh", "discovery_evaluation")}}}


def test_complete_recorded_run_meets_duration_gate() -> None:
    assert discovery_run_failures(complete_run()) == []


def test_short_smoke_test_does_not_close_m2() -> None:
    run = complete_run()
    run["ended_at"] = "2026-10-18T00:02:00+00:00"
    assert any("below 24 hours" in reason for reason in discovery_run_failures(run))


def test_small_ntp_adjustment_does_not_shorten_measured_monotonic_runtime() -> None:
    run = complete_run()
    run["ended_at"] = "2026-10-18T23:59:59+00:00"
    run["stats"]["elapsed_monotonic_seconds"] = 86400.1
    assert discovery_run_failures(run) == []


@pytest.mark.parametrize("case", ["missing", "active", "not_m2", "exceptions", "orphans", "zero_work", "sleep"])
def test_incomplete_or_broken_evidence_is_rejected(case: str) -> None:
    run = deepcopy(complete_run())
    if case == "missing":
        run = None
    elif case == "active":
        run["ended_at"] = None
    elif case == "not_m2":
        run["stats"]["discovery"] = None
    elif case == "exceptions":
        run["stats"]["unhandled_exceptions"] = 1
    elif case == "orphans":
        run["stats"]["discovery"]["orphans"] = 1
    elif case == "zero_work":
        run["stats"]["tasks"] = {}
    elif case == "sleep":
        run["stats"]["suspend_gaps"] = 1
    assert discovery_run_failures(run)


# ── segmented test (operator decision 2026-10-20) ──────────────────────────────────────────────────
NOW = datetime(2026, 10, 25, 12, tzinfo=UTC)


def piece(run_id: str, start_hours_ago: float, hours: float | None, **stats: object) -> dict:
    started = NOW - timedelta(hours=start_hours_ago)
    run = deepcopy(complete_run())
    run.update(id=run_id, started_at=started.isoformat(),
               ended_at=None if hours is None else (started + timedelta(hours=hours)).isoformat())
    run["stats"]["completed_requested_duration"] = False
    run["stats"]["elapsed_monotonic_seconds"] = None if hours is None else hours * 3600
    run["stats"].update(stats)
    return run


def test_pieces_add_up_to_the_24_hour_test() -> None:
    runs = [piece(f"r{i}", 30 * (i + 1), 4) for i in range(5)] + [piece("short", 200, 3.5)]
    result = segmented_progress(runs, NOW)
    assert result["counted_hours"] == 23.5 and not result["passed"]
    result = segmented_progress([*runs, piece("last", 10, 0.5)], NOW)
    assert result["passed"] and result["counted_hours"] == 24.0 and result["longest_segment_hours"] == 4


def test_a_crash_loses_only_its_own_piece_and_sleep_is_not_counted() -> None:
    runs = [piece("ok", 50, 4), piece("crash", 40, None), piece("slept", 30, 2.5, suspend_gaps=1)]
    result = segmented_progress(runs, NOW)
    states = {s["run_id"]: s["state"] for s in result["segments"]}
    assert states == {"ok": "COUNTED", "crash": "INTERRUPTED", "slept": "COUNTED"}
    assert result["counted_hours"] == 6.5                         # active (monotonic) time only
    running = segmented_progress([*runs, piece("now", 1, None)], NOW, running_run_id="now")
    assert {s["run_id"]: s["state"] for s in running["segments"]}["now"] == "RUNNING"
    assert running["counted_hours"] == 6.5                       # a running piece counts once it ends cleanly


def test_broken_old_or_short_pieces_cannot_pass() -> None:
    assert not segmented_progress([piece(f"r{i}", 10 * (i + 1), 3.9) for i in range(8)], NOW)["passed"]  # no 4 h piece
    old = segmented_progress([piece(f"r{i}", 24 * 15 + i * 5, 4) for i in range(7)], NOW)
    assert old["counted_hours"] == 0                             # older than the 14-day window
    bad = segmented_progress([piece("x", 10, 4, unhandled_exceptions=2)], NOW)
    assert bad["segments"][0]["state"] == "NOT_COUNTED" and "unhandled exceptions" in bad["segments"][0]["problems"]
    shadow = segmented_progress([piece("s", 10, 4, discovery=None)], NOW)
    assert shadow["segments"] == []                              # not a discovery run: not part of the test


def test_launchers_open_the_dashboards_without_a_terminal(tmp_path: Path) -> None:
    paths = DeskPaths(tmp_path / "desk data", tmp_path / "config", tmp_path / "secrets.env")
    written = install_launchers(paths, "/home/neo/kestrel/.venv/bin/python", data_home=tmp_path / "share")
    names = sorted(p.name for p in written)
    assert names == ["kestrel-crypto.desktop", "kestrel-crypto.svg", "kestrel.desktop", "kestrel.svg"]
    entry = (tmp_path / "share" / "applications" / "kestrel-crypto.desktop").read_text()
    assert "Terminal=false" in entry and "-m desk open --desk robinhood --port 8766" in entry
    assert "StartupWMClass=kestrel-crypto" in entry                  # its own panel entry, matching --class
    assert f'"DESK_HOME={tmp_path / "desk data"}"' in entry          # spaces quoted the Desktop Entry way
    with pytest.raises(ValueError, match="launcher"):
        desktop_entry(DeskPaths(Path("/x/$HOME"), tmp_path, tmp_path / "s.env"), "/usr/bin/python3", "solana", 8765)


XRANDR = """Screen 0: minimum 320 x 200, current 3840 x 1080, maximum 16384 x 16384
HDMI-1 connected 1920x1080+1920+0 (normal left inverted right x axis y axis) 527mm x 296mm
eDP-1 connected primary 1920x1080+0+0 (normal left inverted right x axis y axis) 344mm x 194mm
DP-1 disconnected (normal left inverted right x axis y axis)
"""


def test_the_main_screen_less_the_panel_is_split_between_the_two_desks() -> None:
    area = launcher.screen_area(XRANDR, "_NET_WORKAREA(CARDINAL) = 0, 0, 3840, 1040, 0, 0, 3840, 1040")
    assert area == (0, 0, 1920, 1040)                                # the primary monitor, above a 40 px panel
    assert launcher.window_slot("solana", area) == (0, 0, 960, 1040)
    assert launcher.window_slot("robinhood", area) == (960, 0, 960, 1040)
    assert launcher.screen_area(XRANDR, "") == (0, 0, 1920, 1080)
    assert launcher.screen_area("HDMI-1 connected 2560x1440+0+0 (normal)\n", "") == (0, 0, 2560, 1440)
    assert launcher.screen_area("", "") is None


def remember(profile: Path, **place: object) -> None:
    """A placement as Chrome writes it for app ``127.0.0.1_/``: the dots become nested keys."""
    prefs = profile / "Default" / "Preferences"
    prefs.parent.mkdir(parents=True, exist_ok=True)
    edges = {"left": 100, "top": 50, "right": 900, "bottom": 650, "maximized": False} | place
    prefs.write_text(json.dumps({"browser": {"app_window_placement": {"127": {"0": {"0": {"1_/": edges}}}}}}))


def test_each_desk_gets_its_own_window_that_keeps_where_it_was_moved(tmp_path: Path) -> None:
    area, screens = (0, 0, 1920, 1040), launcher.monitors(XRANDR)
    solana = launcher.browser_command("chromium", "http://127.0.0.1:8765/", "solana", tmp_path / "solana", area,
                                      screens)
    crypto = launcher.browser_command("chromium", "http://127.0.0.1:8766/", "robinhood", tmp_path / "robinhood",
                                      area, screens)
    assert f"--user-data-dir={tmp_path / 'solana'}" in solana and f"--user-data-dir={tmp_path / 'robinhood'}" in crypto
    assert "--class=kestrel" in solana and "--class=kestrel-crypto" in crypto
    assert solana[-2:] == ["--window-position=0,0", "--window-size=960,1040"]        # side by side the first time
    assert crypto[-2:] == ["--window-position=960,0", "--window-size=960,1040"]
    remember(tmp_path / "robinhood")
    again = launcher.browser_command("chromium", "http://127.0.0.1:8766/", "robinhood", tmp_path / "robinhood",
                                     area, screens)
    assert again[-2:] == ["--window-position=100,50", "--window-size=800,600"]       # back where it was left
    remember(tmp_path / "robinhood", left=2000, right=3000, maximized=True)           # on the second monitor
    assert launcher.browser_command("chromium", "u", "robinhood", tmp_path / "robinhood", area, screens)[-3:] == [
        "--window-position=2000,50", "--window-size=1000,600", "--start-maximized"]
    unplugged = launcher.monitors("eDP-1 connected primary 1920x1080+0+0 (normal)\n")
    assert launcher.browser_command("chromium", "u", "robinhood", tmp_path / "robinhood", area, unplugged)[-2:] == [
        "--window-position=960,0", "--window-size=960,1040"]                          # never off-screen
    unknown = launcher.browser_command("chromium", "http://127.0.0.1:8765/", "solana", tmp_path / "solana", None)
    assert unknown[-2:] == ["--window-position=0,0", "--window-size=683,728"]
    assert launcher.saved_place(tmp_path / "nothing") is None


def test_without_chrome_each_desk_opens_a_new_firefox_window(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    opened = []
    monkeypatch.setattr(launcher.shutil, "which", lambda name: "/usr/bin/firefox" if name == "firefox" else None)
    monkeypatch.setattr(launcher.subprocess, "Popen", lambda command, **_: opened.append(command))
    assert launcher.open_window("http://127.0.0.1:8766/", "robinhood", tmp_path) == [
        "firefox", "--new-window", "http://127.0.0.1:8766/"]
    assert opened == [["firefox", "--new-window", "http://127.0.0.1:8766/"]]
