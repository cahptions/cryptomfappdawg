"""Read-only facts about the machine (Linux). Every function returns ``None`` when a fact is unavailable."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class MemoryInfo:
    total_mb: int
    available_mb: int
    swap_total_mb: int
    swap_used_mb: int


@dataclass(frozen=True)
class BatteryInfo:
    name: str
    status: str          # Charging / Discharging / Full / Not charging
    capacity_pct: int | None


def memory_info(meminfo: Path = Path("/proc/meminfo")) -> MemoryInfo | None:
    try:
        fields = {}
        for line in meminfo.read_text().splitlines():
            key, _, rest = line.partition(":")
            fields[key] = int(rest.split()[0]) // 1024
    except (OSError, ValueError, IndexError):
        return None
    swap_total = fields.get("SwapTotal", 0)
    return MemoryInfo(fields.get("MemTotal", 0), fields.get("MemAvailable", 0), swap_total,
                      swap_total - fields.get("SwapFree", swap_total))


def process_rss_mb(status_file: Path = Path("/proc/self/status")) -> tuple[float, float] | None:
    """(current RSS, peak RSS) in MB for this process."""
    try:
        values = {}
        for line in status_file.read_text().splitlines():
            if line.startswith(("VmRSS:", "VmHWM:")):
                key, _, rest = line.partition(":")
                values[key] = int(rest.split()[0]) / 1024
        return values["VmRSS"], values["VmHWM"]
    except (OSError, KeyError, ValueError, IndexError):
        return None


def battery_info(root: Path = Path("/sys/class/power_supply")) -> BatteryInfo | None:
    try:
        candidates = sorted(p for p in root.iterdir() if p.name.startswith("BAT"))
    except OSError:
        return None
    for bat in candidates:
        try:
            status = (bat / "status").read_text().strip()
            capacity_text = (bat / "capacity").read_text().strip() if (bat / "capacity").exists() else ""
            return BatteryInfo(bat.name, status, int(capacity_text) if capacity_text.isdigit() else None)
        except OSError:
            continue
    return None


def ntp_synchronized() -> bool | None:
    if shutil.which("timedatectl") is None:
        return None
    try:
        out = subprocess.run(["timedatectl", "show", "-p", "NTPSynchronized", "--value"],  # noqa: S607
                             capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    value = out.stdout.strip().lower()
    return {"yes": True, "no": False}.get(value)


def disk_free_gb(path: Path) -> float | None:
    probe = path
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    try:
        return shutil.disk_usage(probe).free / 1e9
    except OSError:
        return None
