"""Opt-in systemd *user* units. Rendering never installs or starts anything.

``desk up`` installs and ENABLES the units, so the desks start again whenever the operator logs in; the Solana
engine runs in 4-hour segments (each ends cleanly and is restarted), so switching the laptop off loses at most the
piece in progress. ``desk down`` stops and disables the engines and dashboards; the exit guardian and backups stay.
"""

from __future__ import annotations

import getpass
import os
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from desk.core.errors import DeskError
from desk.core.paths import REPO_ROOT, DeskPaths


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
INHIBITOR = "/usr/bin/systemd-inhibit --what=idle:sleep --mode=block --who=KESTREL "
BACKUP_TIMER = "desk-backup.timer"


def _environment(paths: DeskPaths) -> str:
    return "\n".join(
        "Environment=" + _quote(f"{key}={value}")
        for key, value in (("DESK_HOME", paths.home), ("DESK_CONFIG_DIR", paths.config_dir),
                           ("DESK_SECRETS_FILE", paths.secrets_file))
    )


def render_backup_units(paths: DeskPaths, python_executable: str) -> dict[str, str]:
    """The hourly online-backup timer (desk.ops.backups). Shared by every desk: the Stocks computer installs the
    same two files, so a computer running both writes identical units."""
    if not Path(python_executable).is_absolute():
        raise ValueError("an absolute Python executable is required")
    return {
        "desk-backup.service": (
            "[Unit]\nDescription=KESTREL online database backups\n\n[Service]\nType=oneshot\n"
            f"{_environment(paths)}\nExecStart={_quote(python_executable, command=True)} -m desk backups run\n"
            "UMask=0077\nMemoryMax=200M\nNoNewPrivileges=true\n"
        ),
        BACKUP_TIMER: (
            "[Unit]\nDescription=KESTREL hourly backup schedule\n\n[Timer]\nOnCalendar=hourly\n"
            "Persistent=true\nRandomizedDelaySec=30\n\n[Install]\nWantedBy=timers.target\n"
        ),
    }


def render_units(paths: DeskPaths, python_executable: str, port: int = 8765, rh_port: int = 8766) -> dict[str, str]:
    """Render memory-bounded units using the existing app-owned private secrets loader."""
    if not Path(python_executable).is_absolute():
        raise ValueError("an absolute Python executable is required")
    if not (1024 <= port <= 65535 and 1024 <= rh_port <= 65535) or port == rh_port:
        raise ValueError("dashboard ports must differ and be between 1024 and 65535")
    python = _quote(python_executable, command=True)
    inhibitor = INHIBITOR
    environment = _environment(paths)

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
        **render_backup_units(paths, python_executable),
    }
    return units


STOCKS_UNIT = "desk-stocks-dashboard.service"


def render_stocks_unit(paths: DeskPaths, python_executable: str, port: int) -> str:
    """Stocks on the computer that holds the Schwab sign-in, always on: started when the computer starts (with
    lingering, before anyone logs in) and restarted 10 seconds after it stops for any reason. No memory cap: the
    research worker it starts runs in the same service.

    Like the crypto engines, it runs under ``systemd-inhibit --what=idle:sleep``: while Stocks is running the
    computer does not go to sleep when it is left idle, which would stop collection and the PAPER books overnight.
    Closing a laptop's lid is a separate system setting (logind's HandleLidSwitch) that this does not change."""
    if not Path(python_executable).is_absolute():
        raise ValueError("an absolute Python executable is required")
    environment = _environment(paths)
    root = str(REPO_ROOT)
    folder = f"WorkingDirectory={root.replace('%', '%%')}\n" if not any(c.isspace() for c in root) else ""
    why = _quote("--why=KESTREL Stocks collects market data and runs PAPER research", command=True)
    return ("[Unit]\nDescription=KESTREL Stocks (always on)\nStartLimitIntervalSec=600\nStartLimitBurst=20\n"
            f"\n[Service]\nType=simple\n{environment}\n{folder}"
            f"ExecStart={INHIBITOR}{why} {_quote(python_executable, command=True)} -m desk stocks dashboard "
            f"--port {int(port)}\n"
            "Restart=always\nRestartSec=10\nTimeoutStopSec=60\nUMask=0077\nNoNewPrivileges=true\n"
            "\n[Install]\nWantedBy=default.target\n")


def keep_stocks_running(paths: DeskPaths, python_executable: str, port: int, *, unit_dir: Path | None = None,
                        run: Callable[..., Any] = subprocess.run) -> list[str]:
    """Install or refresh the always-on Stocks service and enable it; turn on lingering so it starts at power-on.
    The hourly backup timer is installed and started too, so the Stocks databases are backed up on the computer
    that holds them (desk.ops.backups), whether or not the crypto desks were ever set up there.
    Returns what the person should know; never raises, so an update still restarts the app the old way."""
    if not shutil.which("systemctl"):
        return ["No systemd here: Stocks runs as a plain background process and stays off after it stops."]
    directory = unit_dir or Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "systemd" / "user"
    files = {STOCKS_UNIT: render_stocks_unit(paths, python_executable, port),
             **render_backup_units(paths, python_executable)}
    try:
        for name, content in files.items():
            target = directory / name
            if not target.exists() or target.read_text(encoding="utf-8") != content:
                directory.mkdir(parents=True, exist_ok=True)
                with target.open("w", encoding="utf-8") as output:
                    os.fchmod(output.fileno(), 0o600)
                    output.write(content)
    except OSError as exc:
        return [f"Could not write the always-on service ({type(exc).__name__}); Stocks runs as a plain process."]
    notes = []
    for step in (["daemon-reload"], ["enable", STOCKS_UNIT]):
        done = run(["systemctl", "--user", *step], check=False, capture_output=True, text=True)
        if done.returncode:
            return [f"systemd did not accept the always-on service ({' '.join(step)}: "
                    f"{(done.stderr or '').strip()[:160]}); Stocks runs as a plain process this time."]
    backups = run(["systemctl", "--user", "enable", "--now", BACKUP_TIMER], check=False, capture_output=True, text=True)
    if backups.returncode:
        notes.append(f"Hourly backups are not scheduled (systemd: {(backups.stderr or '').strip()[:160]}); "
                     "run `uv run desk backups run` by hand until the next update sets them up.")
    user = getpass.getuser()
    linger = run(["loginctl", "show-user", user, "--property=Linger", "--value"], check=False,
                 capture_output=True, text=True)
    if (linger.stdout or "").strip() != "yes":
        tried = run(["loginctl", "enable-linger", user], check=False, capture_output=True, text=True)
        if tried.returncode:
            notes.append("Always on while you are logged in. So it also starts at power-on before anyone logs in, "
                         f"run once: sudo loginctl enable-linger {user}")
    return notes


def always_on(run: Callable[..., Any] = subprocess.run) -> tuple[bool, str]:
    """For the health report: is Stocks set to stay on (enabled, running, and starting at power-on)?"""
    if not shutil.which("systemctl"):
        return False, "no systemd here: stays off after it stops until something opens it"
    def ask(*command: str) -> str:
        try:
            return (run(list(command), check=False, capture_output=True, text=True, timeout=5).stdout or "").strip()
        except (OSError, subprocess.SubprocessError):
            return ""
    enabled = ask("systemctl", "--user", "is-enabled", STOCKS_UNIT)
    if enabled != "enabled":
        return False, "not set up yet · kestrel update sets it up"
    active = ask("systemctl", "--user", "is-active", STOCKS_UNIT)
    lingering = ask("loginctl", "show-user", getpass.getuser(), "--property=Linger", "--value") == "yes"
    state = "restarts within seconds if it stops" if active == "active" else f"service is {active or 'unknown'}"
    if not lingering:
        return False, (f"{state}; starts at power-on only after you log in · once: sudo loginctl enable-linger "
                       f"{getpass.getuser()}")
    return active == "active", f"{state}; starts at power-on"


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
