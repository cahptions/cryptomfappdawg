"""Opt-in systemd *user* units. Rendering never installs or starts anything.

``desk up`` installs and ENABLES the units, so the desks start again whenever the operator logs in; the Solana
engine runs in 4-hour segments (each ends cleanly and is restarted), so switching the laptop off loses at most the
piece in progress. ``desk down`` stops and disables the engines and dashboards; the exit guardian and backups stay.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from desk.core.errors import DeskError
from desk.core.paths import DeskPaths


def _quote(value: str, *, command: bool = False) -> str:
    if any(char in value for char in ("\n", "\r", "\0")):
        raise ValueError("unit values must occupy one line")
    value = value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
    if command:
        value = value.replace("$", "$$")
    return f'"{value}"'


SEGMENT_MINUTES = 240                       # one piece of the segmented 24-hour test (desk.ops.acceptance)
ENGINES = ("desk-engine.service", "desk-rh-engine.service")
DASHBOARDS = ("desk-dashboard.service", "desk-rh-dashboard.service")


def render_units(paths: DeskPaths, python_executable: str, port: int = 8765, rh_port: int = 8766) -> dict[str, str]:
    """Render memory-bounded units using the existing app-owned private secrets loader."""
    if not Path(python_executable).is_absolute():
        raise ValueError("an absolute Python executable is required")
    if not (1024 <= port <= 65535 and 1024 <= rh_port <= 65535) or port == rh_port:
        raise ValueError("dashboard ports must differ and be between 1024 and 65535")
    python = _quote(python_executable, command=True)
    inhibitor = "/usr/bin/systemd-inhibit --what=idle:sleep --mode=block --who=KESTREL "
    environment = "\n".join(
        "Environment=" + _quote(f"{key}={value}")
        for key, value in (("DESK_HOME", paths.home), ("DESK_CONFIG_DIR", paths.config_dir),
                           ("DESK_SECRETS_FILE", paths.secrets_file))
    )

    def unit(description: str, command: str, memory: str, *, restart: str = "on-failure",
             extra_unit: str = "") -> str:
        return (
            f"[Unit]\nDescription=KESTREL {description}\n{extra_unit}"
            "StartLimitIntervalSec=120\nStartLimitBurst=5\n\n[Service]\nType=simple\n"
            f"{environment}\nExecStart={command}\nRestart={restart}\nRestartSec=5\n"
            f"MemoryMax={memory}\nUMask=0077\nNoNewPrivileges=true\nTimeoutStopSec=45\n"
            "\n[Install]\nWantedBy=default.target\n"
        )

    units = {
        "desk-guardian.service": unit("independent PAPER exit guardian",
                                      inhibitor + _quote("--why=PAPER guardian supervision", command=True)
                                      + f" {python} -m desk guardian", "200M", restart="always"),
        "desk-engine.service": unit(
            "PAPER collector and entry engine (4-hour segments)",
            inhibitor + _quote("--why=PAPER session in progress", command=True)
            + f" {python} -m desk paper --minutes {SEGMENT_MINUTES}",
            "600M", restart="always", extra_unit="After=desk-guardian.service\n"),
        "desk-dashboard.service": unit("local dashboard", f"{python} -m desk dashboard --port {port}", "200M"),
        "desk-rh-engine.service": unit("Robinhood desk PAPER engine", f"{python} -m desk rh paper", "400M",
                                       restart="always"),
        "desk-rh-dashboard.service": unit("Robinhood desk dashboard", f"{python} -m desk rh dashboard --port {rh_port}",
                                          "200M"),
        "desk-backup.service": (
            "[Unit]\nDescription=KESTREL online database backups\n\n[Service]\nType=oneshot\n"
            f"{environment}\nExecStart={python} -m desk backups run\nUMask=0077\nMemoryMax=200M\n"
            "NoNewPrivileges=true\n"
        ),
        "desk-backup.timer": (
            "[Unit]\nDescription=KESTREL hourly backup schedule\n\n[Timer]\nOnCalendar=hourly\n"
            "Persistent=true\nRandomizedDelaySec=30\n\n[Install]\nWantedBy=timers.target\n"
        ),
    }
    return units


def control_services(action: str, *, paper: bool = False) -> None:
    """Control only known user services. Stopping entries always leaves the guardian alive.

    start: enable and start now (they return at every login); with ``paper`` the engines and the guardian too.
    stop: disable and stop the engines and dashboards; the guardian keeps supervising open positions.
    """
    if action not in {"start", "stop", "status"}:
        raise ValueError("unsupported service action")
    if action == "start":
        units = [*DASHBOARDS, "desk-backup.timer"]
        if paper:
            # A guardian is independently supervised; it is never PartOf/BindsTo the engine.
            _systemctl(["enable", "--now", "desk-guardian.service"])
            units[:0] = ENGINES
        _systemctl(["enable", "--now", *units])
    elif action == "stop":
        _systemctl(["disable", "--now", *ENGINES, *DASHBOARDS])
    else:
        _systemctl(["status", "--no-pager", *ENGINES, "desk-guardian.service", *DASHBOARDS, "desk-backup.timer"])


def _systemctl(arguments: list[str]) -> None:
    try:
        subprocess.run(["systemctl", "--user", *arguments], check=True)  # noqa: S603,S607
    except (OSError, subprocess.CalledProcessError) as exc:
        raise DeskError("systemd user operation failed; check the service status and your desktop user session") from exc


def install_services(paths: DeskPaths, python_executable: str, *, port: int = 8765,
                     unit_dir: Path | None = None, start: bool = False, paper: bool = False) -> list[Path]:
    """Install explicitly, reload the user manager, and start only when requested."""
    units = render_units(paths, python_executable, port)
    directory = unit_dir or Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "systemd" / "user"
    directory.mkdir(parents=True, exist_ok=True)
    installed = []
    for filename, content in units.items():
        target = directory / filename
        with target.open("w", encoding="utf-8") as output:
            os.fchmod(output.fileno(), 0o600)
            output.write(content)
        installed.append(target)
    _systemctl(["daemon-reload"])
    if start:
        control_services("start", paper=paper)
    return installed
