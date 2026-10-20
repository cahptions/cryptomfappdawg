"""App icons for the two dashboards, and ``desk open``: start what is needed and open an app-style window.

The launchers are ordinary desktop entries in ``~/.local/share/applications`` (the Mint menu and panel pick them
up). Clicking one runs ``desk open``: if the dashboard is not answering it starts its user service (or, without
services, a detached dashboard process), waits for it, and opens it in a Chromium-family "app" window when such a
browser is installed (no tabs or address bar), otherwise in the default browser. Nothing here touches a wallet.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from desk.core.errors import DeskError
from desk.core.paths import DeskPaths

APP_BROWSERS = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "brave-browser",
                "microsoft-edge")
DESKS = {
    "solana": {"name": "KESTREL", "comment": "Solana memecoin desk (paper trading)", "service": "desk-dashboard.service",
               "icon": "kestrel", "dashboard": ["dashboard", "--port"]},
    "robinhood": {"name": "KESTREL Crypto", "comment": "Robinhood crypto desk (paper trading)",
                  "service": "desk-rh-dashboard.service", "icon": "kestrel-crypto",
                  "dashboard": ["rh", "dashboard", "--port"]},
}
ICON = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 40"><rect width="40" height="40" rx="9" fill="{bg}"/>'
        '<path d="m5 10 15 6L35 5 25 24l-5 11-5-12Z" fill="{fg}"/></svg>\n')


def _data_home() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))


_RESERVED = set(" \t\"'\\><~|&;$*?#()`")


def _exec_arg(value: str) -> str:
    """One Exec argument: double-quoted when it has spaces or shell characters, every % doubled. The four characters
    that would need the spec's two-layer backslash escaping are refused instead (no normal path contains them)."""
    if any(char in value for char in ('\n', '\r', '\0', '"', '`', '$', '\\')):
        raise ValueError(f"cannot put {value!r} in a launcher; use a folder path without quotes, $, ` or backslashes")
    value = value.replace("%", "%%")
    return f'"{value}"' if any(char in _RESERVED for char in value) else value


def desktop_entry(paths: DeskPaths, python_executable: str, desk: str, port: int) -> str:
    spec = DESKS[desk]
    env = [f"{k}={v}" for k, v in (("DESK_HOME", paths.home), ("DESK_CONFIG_DIR", paths.config_dir),
                                   ("DESK_SECRETS_FILE", paths.secrets_file))]
    command = " ".join(_exec_arg(a) for a in ["env", *env, python_executable, "-m", "desk", "open", "--desk", desk,
                                               "--port", str(port)])
    return ("[Desktop Entry]\nType=Application\nVersion=1.0\n"
            f"Name={spec['name']}\nComment={spec['comment']}\nExec={command}\nIcon={spec['icon']}\n"
            "Terminal=false\nCategories=Office;Finance;\nStartupNotify=true\n")


def install_launchers(paths: DeskPaths, python_executable: str, *, port: int = 8765, rh_port: int = 8766,
                      data_home: Path | None = None) -> list[Path]:
    """Write the two menu entries and their icons (user-only files); returns what was written."""
    if not Path(python_executable).is_absolute():
        raise ValueError("an absolute Python executable is required")
    home = data_home or _data_home()
    apps, icons = home / "applications", home / "icons" / "hicolor" / "scalable" / "apps"
    apps.mkdir(parents=True, exist_ok=True)
    icons.mkdir(parents=True, exist_ok=True)
    written = []
    for desk, desk_port, colours in (("solana", port, ("#0c1016", "#3cd2ff")), ("robinhood", rh_port, ("#000000", "#00c805"))):
        entry = apps / f"{DESKS[desk]['icon']}.desktop"
        entry.write_text(desktop_entry(paths, python_executable, desk, desk_port), encoding="utf-8")
        os.chmod(entry, 0o700)
        icon = icons / f"{DESKS[desk]['icon']}.svg"
        icon.write_text(ICON.format(bg=colours[0], fg=colours[1]), encoding="utf-8")
        written += [entry, icon]
    return written


def responding(url: str, timeout: float = 1.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310 - fixed loopback URL
            return bool(200 <= int(response.status) < 500)
    except (urllib.error.URLError, OSError, ValueError):
        return False


def _start_dashboard(desk: str, port: int) -> None:
    spec = DESKS[desk]
    if shutil.which("systemctl"):
        result = subprocess.run(["systemctl", "--user", "start", str(spec["service"])], check=False,  # noqa: S603,S607
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if result.returncode == 0:
            return
    subprocess.Popen([sys.executable, "-m", "desk", *spec["dashboard"], str(port)],  # noqa: S603
                     start_new_session=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL)


def open_window(url: str) -> list[str]:
    for browser in APP_BROWSERS:
        if shutil.which(browser):
            command = [browser, f"--app={url}"]
            break
    else:
        if not shutil.which("xdg-open"):
            return []                      # no desktop here: the caller prints the address
        command = ["xdg-open", url]
    subprocess.Popen(command, start_new_session=True, stdin=subprocess.DEVNULL,  # noqa: S603
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return command


def open_desk(desk: str, port: int, *, wait_seconds: float = 30) -> str:
    """Make sure the dashboard answers, then open it; returns the URL."""
    url = f"http://127.0.0.1:{port}/"
    if not responding(url):
        _start_dashboard(desk, port)
        deadline = time.monotonic() + wait_seconds
        while not responding(url):
            if time.monotonic() > deadline:
                raise DeskError(f"the dashboard did not start on {url}; run `desk up` or check `desk services status`")
            time.sleep(0.5)
    open_window(url)
    return url


__all__ = ["APP_BROWSERS", "DESKS", "desktop_entry", "install_launchers", "open_desk", "open_window", "responding"]
