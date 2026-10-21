"""App icons for the two dashboards, and ``desk open``: start what is needed and open an app-style window.

The launchers are ordinary desktop entries in ``~/.local/share/applications`` (the Mint menu and panel pick them
up). Clicking one runs ``desk open``: if the dashboard is not answering it starts its user service (or, without
services, a detached dashboard process), waits for it, and opens it in a Chromium-family "app" window when such a
browser is installed (no tabs or address bar), otherwise in a new Firefox window or the default browser.

Chrome names an app window after its host without the port, so both dashboards were one app, ``127.0.0.1_/``,
and the dots in that name keep Chrome from finding the window's saved place again (see ``saved_place``): every
window opened at the same default spot, on top of the other. Now each desk's window runs in its own small browser
profile under ``DESK_HOME/windows`` (a separate window and panel entry), opens on its half of the main screen the
first time, and comes back where it was last left after that.
Nothing here touches a wallet.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Sequence
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
    "stocks": {"name": "KESTREL Stocks", "comment": "Stock and options research hub (PAPER)",
               "service": "desk-stocks-dashboard.service", "icon": "kestrel-stocks",
               "dashboard": ["stocks", "dashboard", "--port"]},
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
            f"Terminal=false\nCategories=Office;Finance;\nStartupNotify=true\nStartupWMClass={spec['icon']}\n")


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


Area = tuple[int, int, int, int]                 # x, y, width, height in screen pixels
FALLBACK_AREA: Area = (0, 0, 1366, 728)         # the smallest common laptop screen, less a panel


def _command_output(command: list[str]) -> str:
    if not shutil.which(command[0]):
        return ""
    try:
        result = subprocess.run(command, check=False, capture_output=True, text=True, timeout=3)  # noqa: S603
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout if result.returncode == 0 else ""


def monitors(xrandr: str) -> list[Area]:
    """Connected monitors from ``xrandr --current``, the primary first."""
    found = re.findall(r"^\S+ connected (primary )?(\d+)x(\d+)\+(\d+)\+(\d+)", xrandr, re.MULTILINE)
    return [(int(x), int(y), int(w), int(h)) for _, w, h, x, y in sorted(found, key=lambda m: not m[0])]


def _overlap(a: Area, b: Area) -> tuple[int, int]:
    return min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0]), min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])


def screen_area(xrandr: str, workarea: str) -> Area | None:
    """The main monitor less the panels (``xprop -root _NET_WORKAREA``), when known."""
    screens = monitors(xrandr)
    if not screens:
        return None
    main = screens[0]
    found = re.search(r"=\s*(\d+),\s*(\d+),\s*(\d+),\s*(\d+)", workarea)
    if found:
        work = (int(found[1]), int(found[2]), int(found[3]), int(found[4]))
        width, height = _overlap(main, work)
        if width >= 400 and height >= 300:
            return max(main[0], work[0]), max(main[1], work[1]), width, height
    return main


def window_slot(desk: str, area: Area) -> Area:
    """Solana on the left half, Robinhood on the right, so the two first open side by side."""
    x, y, w, h = area
    half = w // 2
    return (x if desk == "solana" else x + half), y, half, h


def saved_place(profile: Path) -> tuple[Area, bool] | None:
    """Where this desk's window was last left, and whether it was maximized, from its own profile.

    Chrome records it under the app's name, ``127.0.0.1_/``; its preferences treat the dots as a path, store the
    place under nested keys and then never find it again, so on its own Chrome would reopen every window at the
    same default spot. KESTREL reads the place back and opens the window there."""
    try:
        prefs = json.loads((profile / "Default" / "Preferences").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    stack = [prefs.get("browser", {}).get("app_window_placement")] if isinstance(prefs, dict) else []
    while stack:
        node = stack.pop()
        if not isinstance(node, dict):
            continue
        edges = [edge for key in ("left", "top", "right", "bottom") if isinstance(edge := node.get(key), int)]
        if len(edges) == 4:
            left, top, right, bottom = edges
            if right - left >= 200 and bottom - top >= 150:
                return (left, top, right - left, bottom - top), node.get("maximized") is True
            continue
        stack.extend(node.values())
    return None


def browser_command(browser: str, url: str, desk: str, profile: Path, area: Area | None,
                    screens: Sequence[Area] = ()) -> list[str]:
    command = [browser, f"--app={url}", f"--user-data-dir={profile}", f"--class={DESKS[desk]['icon']}",
               "--no-first-run", "--no-default-browser-check"]
    saved = saved_place(profile)
    if saved and screens and not any(min(_overlap(saved[0], screen)) >= 100 for screen in screens):
        saved = None                       # left on a monitor that is no longer connected
    x, y, w, h = saved[0] if saved else window_slot(desk, area or FALLBACK_AREA)
    command += [f"--window-position={x},{y}", f"--window-size={w},{h}"]
    if saved and saved[1]:
        command.append("--start-maximized")
    return command


def open_window(url: str, desk: str, profiles: Path) -> list[str]:
    for browser in APP_BROWSERS:
        if shutil.which(browser):
            profile = profiles / desk
            profile.mkdir(parents=True, exist_ok=True, mode=0o700)
            xrandr = _command_output(["xrandr", "--current"])
            area = screen_area(xrandr, _command_output(["xprop", "-root", "_NET_WORKAREA"]))
            command = browser_command(browser, url, desk, profile, area, monitors(xrandr))
            break
    else:
        if shutil.which("firefox"):
            command = ["firefox", "--new-window", url]     # its own window, not a tab hidden behind the other
        elif shutil.which("xdg-open"):
            command = ["xdg-open", url]
        else:
            return []                      # no desktop here: the caller prints the address
    subprocess.Popen(command, start_new_session=True, stdin=subprocess.DEVNULL,  # noqa: S603
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return command


def open_desk(desk: str, port: int, paths: DeskPaths, *, wait_seconds: float = 30) -> str:
    """Make sure the dashboard answers, then open it in its own window; returns the URL."""
    url = f"http://127.0.0.1:{port}/"
    if not responding(url):
        _start_dashboard(desk, port)
        deadline = time.monotonic() + wait_seconds
        while not responding(url):
            if time.monotonic() > deadline:
                raise DeskError(f"the dashboard did not start on {url}; run `desk up` or check `desk services status`")
            time.sleep(0.5)
    open_window(url, desk, paths.home / "windows")
    return url


__all__ = ["APP_BROWSERS", "DESKS", "browser_command", "desktop_entry", "install_launchers", "monitors", "open_desk",
           "open_window", "responding", "saved_place", "screen_area", "window_slot"]
