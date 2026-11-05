"""Stocks on the HP is an always-on user service: started at power-on (lingering) and restarted after it stops.
It ran as a plain background process before, so a failed restart left it off until something opened it again."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from desk.core.paths import REPO_ROOT, DeskPaths
from desk.ops import launcher, services


def paths(tmp_path: Path) -> DeskPaths:
    return DeskPaths(tmp_path / "data", REPO_ROOT / "config", tmp_path / "secrets.env")


def test_the_unit_restarts_stocks_whenever_it_stops_and_starts_with_the_computer(tmp_path: Path) -> None:
    unit = services.render_stocks_unit(paths(tmp_path), "/home/neo/kestrel/.venv/bin/python", 8767)
    assert 'ExecStart="/home/neo/kestrel/.venv/bin/python" -m desk stocks dashboard --port 8767' in unit
    assert "Restart=always\nRestartSec=10" in unit and "WantedBy=default.target" in unit
    assert f"WorkingDirectory={REPO_ROOT}\n" in unit and "MemoryMax" not in unit   # the research worker lives here too
    assert f'Environment="DESK_HOME={tmp_path / "data"}"' in unit
    assert launcher.DESKS["stocks"]["service"] == services.STOCKS_UNIT                 # what a restart starts
    with pytest.raises(ValueError):
        services.render_stocks_unit(paths(tmp_path), "python", 8767)


class Systemd:
    def __init__(self, *, linger: str = "no", lingering_allowed: bool = False, refuse: str | None = None) -> None:
        self.calls: list[list[str]] = []
        self.linger, self.lingering_allowed, self.refuse = linger, lingering_allowed, refuse

    def __call__(self, command: list[str], **_: object) -> SimpleNamespace:
        self.calls.append(command)
        if self.refuse and self.refuse in command:
            return SimpleNamespace(returncode=1, stdout="", stderr="Failed to connect to bus")
        if command[:2] == ["loginctl", "show-user"]:
            return SimpleNamespace(returncode=0, stdout=self.linger + "\n", stderr="")
        if command[:2] == ["loginctl", "enable-linger"]:
            return SimpleNamespace(returncode=0 if self.lingering_allowed else 1, stdout="", stderr="denied")
        return SimpleNamespace(returncode=0, stdout="", stderr="")


def test_updates_install_and_enable_it_once_and_say_how_to_start_it_at_power_on(tmp_path: Path,
                                                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(services.shutil, "which", lambda _: "/usr/bin/systemctl")
    units = tmp_path / "units"
    systemd = Systemd()
    notes = services.keep_stocks_running(paths(tmp_path), "/usr/bin/python3", 8767, unit_dir=units, run=systemd)
    target = units / services.STOCKS_UNIT
    assert target.read_text() == services.render_stocks_unit(paths(tmp_path), "/usr/bin/python3", 8767)
    assert oct(target.stat().st_mode & 0o777) == "0o600"
    assert ["systemctl", "--user", "enable", services.STOCKS_UNIT] in systemd.calls
    assert len(notes) == 1 and "sudo loginctl enable-linger" in notes[0]                 # lingering needs an admin
    # the hourly backups run on the computer that holds the Stocks databases (desk.ops.backups)
    assert ["systemctl", "--user", "enable", "--now", services.BACKUP_TIMER] in systemd.calls
    for name, content in services.render_backup_units(paths(tmp_path), "/usr/bin/python3").items():
        assert (units / name).read_text() == content and (units / name).stat().st_mode & 0o777 == 0o600
    assert '"/usr/bin/python3" -m desk backups run' in (units / "desk-backup.service").read_text()
    no_timer = services.keep_stocks_running(paths(tmp_path), "/usr/bin/python3", 8767, unit_dir=units,
                                            run=Systemd(linger="yes", refuse=services.BACKUP_TIMER))
    assert len(no_timer) == 1 and "backups are not scheduled" in no_timer[0]             # Stocks itself still on
    written = target.stat().st_mtime_ns
    assert services.keep_stocks_running(paths(tmp_path), "/usr/bin/python3", 8767, unit_dir=units,
                                        run=Systemd(linger="yes")) == []                 # already lingering: quiet
    assert target.stat().st_mtime_ns == written                                          # unchanged: not rewritten
    assert services.keep_stocks_running(paths(tmp_path), "/usr/bin/python3", 8767, unit_dir=units,
                                        run=Systemd(lingering_allowed=True)) == []
    refused = services.keep_stocks_running(paths(tmp_path), "/usr/bin/python3", 8767, unit_dir=units,
                                           run=Systemd(refuse="enable"))
    assert "plain process" in refused[0] and "Failed to connect to bus" in refused[0]   # never stops an update
    monkeypatch.setattr(services.shutil, "which", lambda _: None)
    assert "No systemd" in services.keep_stocks_running(paths(tmp_path), "/usr/bin/python3", 8767, unit_dir=units)[0]


def test_a_service_start_that_never_answers_shows_its_journal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(launcher, "responding", lambda *_a, **_k: False)
    monkeypatch.setattr(launcher, "_start_dashboard", lambda desk, port, log=None: None)   # systemd took it
    monkeypatch.setattr(launcher, "SLOW_START_SECONDS", 0)
    monkeypatch.setattr(launcher, "journal_tail", lambda unit, p, lines=15: f"journal of {unit}")
    with pytest.raises(launcher.DeskError, match=f"(?s)did not answer.*journal of {services.STOCKS_UNIT}"):
        launcher.ensure_running("stocks", 8767, paths(tmp_path), wait_seconds=0)


def test_kestrel_reports_whether_stocks_stays_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(services.shutil, "which", lambda _: "/usr/bin/systemctl")

    def answers(enabled: str, active: str, linger: str):
        def run(command: list[str], **_: object) -> SimpleNamespace:
            reply = {"is-enabled": enabled, "is-active": active}.get(command[2], linger)
            return SimpleNamespace(returncode=0, stdout=reply + "\n")
        return run

    assert services.always_on(answers("enabled", "active", "yes")) == (
        True, "restarts within seconds if it stops; starts at power-on")
    on, text = services.always_on(answers("enabled", "active", "no"))
    assert not on and "sudo loginctl enable-linger" in text
    assert services.always_on(answers("disabled", "", "yes"))[1].startswith("not set up yet · kestrel update")
    assert services.always_on(answers("enabled", "activating", "yes")) == (
        False, "service is activating; starts at power-on")
