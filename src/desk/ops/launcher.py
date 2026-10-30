"""App icons for the dashboards, and ``desk open``: start what is needed and open an app-style window.

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
    "arbitrage": {"name": "KESTREL Arbitrage", "comment": "Product sourcing and selling-route research (read only)",
                  "service": "desk-arbitrage-dashboard.service", "icon": "kestrel-arbitrage",
                  "dashboard": ["arbitrage", "dashboard", "--port"]},
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


def desktop_entry(paths: DeskPaths, python_executable: str, desk: str, port: int, remote: str | None = None) -> str:
    spec = DESKS[desk]
    comment = f"{spec['comment']} · on {remote}" if remote else spec["comment"]
    env = [f"{k}={v}" for k, v in (("DESK_HOME", paths.home), ("DESK_CONFIG_DIR", paths.config_dir),
                                   ("DESK_SECRETS_FILE", paths.secrets_file))]
    command = " ".join(_exec_arg(a) for a in ["env", *env, python_executable, "-m", "desk", "open", "--desk", desk,
                                               "--port", str(port)])
    return ("[Desktop Entry]\nType=Application\nVersion=1.0\n"
            f"Name={spec['name']}\nComment={comment}\nExec={command}\nIcon={spec['icon']}\n"
            f"Terminal=false\nCategories=Office;Finance;\nStartupNotify=true\nStartupWMClass={spec['icon']}\n")


def install_launchers(paths: DeskPaths, python_executable: str, *, port: int = 8765, rh_port: int = 8766,
                      stocks_port: int = 8767, arbitrage_port: int = 8768, desks: Sequence[str] | None = None,
                      data_home: Path | None = None) -> list[Path]:
    """Write selected menu entries and icons (all three by default); never install or start services."""
    if not Path(python_executable).is_absolute():
        raise ValueError("an absolute Python executable is required")
    selected = ('solana', 'robinhood', 'stocks') if desks is None else tuple(dict.fromkeys(desks))
    if any(desk not in DESKS for desk in selected):
        raise ValueError("unknown desk")
    ports = {"solana": port, "robinhood": rh_port, "stocks": stocks_port, "arbitrage": arbitrage_port}
    colours = {"solana": ("#0c1016", "#3cd2ff"), "robinhood": ("#000000", "#00c805"),
               "stocks": ("#08060c", "#89cff0"), "arbitrage": ('#080b08', '#c2f43a')}
    if any(not 1024 <= ports[desk] <= 65535 for desk in selected):
        raise ValueError("dashboard ports must be between 1024 and 65535")
    home = data_home or _data_home()
    apps, icons = home / "applications", home / "icons" / "hicolor" / "scalable" / "apps"
    apps.mkdir(parents=True, exist_ok=True)
    icons.mkdir(parents=True, exist_ok=True)
    written = []
    for desk in selected:
        entry = apps / f"{DESKS[desk]['icon']}.desktop"
        remote = remote_host(paths) if desk == "stocks" else None
        entry.write_text(desktop_entry(paths, python_executable, desk, ports[desk], remote), encoding="utf-8")
        os.chmod(entry, 0o700)
        icon = icons / f"{DESKS[desk]['icon']}.svg"
        icon.write_text(ICON.format(bg=colours[desk][0], fg=colours[desk][1]), encoding="utf-8")
        written += [entry, icon]
    return written


STOCKS_REMOTE_OFFSET = 10000      # another computer's Stocks port appears here 10000 higher: 8767 → 18767
_HOST = re.compile(r"^(?:[A-Za-z0-9._-]+@)?[A-Za-z0-9][A-Za-z0-9.-]*$")
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8"]


def remote_path(paths: DeskPaths) -> Path:
    return paths.home / "stocks-remote"


def check_host(host: str) -> str:
    if not _HOST.fullmatch(host):
        raise DeskError("Give the other computer as user@address, for example neo@192.168.0.10")
    return host


def remote_host(paths: DeskPaths) -> str | None:
    """The computer whose Stocks desk this one shows (e.g. ``neo@192.168.0.10``), or None for its own."""
    try:
        host = remote_path(paths).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return host if _HOST.fullmatch(host) else None


def set_remote_host(paths: DeskPaths, host: str | None) -> None:
    path = remote_path(paths)
    if host is None:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(check_host(host) + "\n", encoding="utf-8")


def _quiet(command: list[str], timeout: float = 30) -> int:
    """Run without a terminal or captured output (a backgrounded ``ssh -f`` keeps any pipe open)."""
    try:
        return subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,  # noqa: S603
                              stderr=subprocess.DEVNULL, check=False, timeout=timeout, start_new_session=True).returncode
    except (OSError, subprocess.TimeoutExpired):
        return 1


def reachable(host: str) -> bool:
    return _quiet([*SSH, host, "true"]) == 0


def ssh_key_setup(host: str) -> None:
    """Once, in a terminal: a key so the menu launcher reaches ``host`` without a password prompt."""
    if reachable(host):
        return
    key = Path.home() / ".ssh" / "id_ed25519"
    if not key.exists():
        print(f"Creating an SSH key ({key}) so this computer can reach {host} without a password.")
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True)  # noqa: S603,S607
    print(f"Adding it to {host}: type {host.split('@')[0]}'s password on {host.split('@')[-1]} once.")
    subprocess.run(["ssh-copy-id", "-i", f"{key}.pub", host], check=False)  # noqa: S603,S607
    if not reachable(host):
        raise DeskError(f"Could not reach {host} with a key. Check it is on and the address is right: ssh {host}")


def _listening(port: int) -> bool:
    import socket

    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _answers(url: str, seconds: float) -> bool:
    until = time.monotonic() + seconds
    while not responding(url):
        if time.monotonic() > until:
            return False
        time.sleep(0.5)
    return True


# Run on the other computer: is its Stocks app answering? Then: start it, with the kestrel command or without it.
_PING = ("py=$(command -v python3 || echo ~/kestrel/.venv/bin/python); \"$py\" -c "
         "'import urllib.request as u; u.urlopen(\"http://127.0.0.1:{port}/\", timeout=5)'")
_START = "~/.local/bin/kestrel restart || (cd ~/kestrel && ~/.local/bin/uv run desk stocks restart)"


def remote_window(host: str, port: int, paths: DeskPaths) -> str:
    """Open ``host``'s Stocks desk here through an SSH tunnel.

    A tunnel that already answers is reused. Otherwise the other computer is asked directly whether its app is
    running (and it is started there if not), then a new tunnel takes the first free local port from 18767 up, so
    an old, stuck connection holding 18767 is stepped over rather than waited on."""
    ports = range(port + STOCKS_REMOTE_OFFSET, port + STOCKS_REMOTE_OFFSET + 5)
    for local in ports:
        if responding(f"http://127.0.0.1:{local}/"):
            return _show(f"http://127.0.0.1:{local}/", paths)
    print(f"Connecting to {host}…", flush=True)
    ping = [*SSH, host, _PING.format(port=port)]
    answered = _quiet(ping, timeout=20)
    if answered == 255:                                  # ssh's own failure: off, asleep or another network
        raise DeskError(f"Could not reach {host}. Is it on and on this network? Check with: ssh {host}")
    if answered != 0:
        print(f"KESTREL Stocks is not running on {host}; starting it there (up to a minute)…", flush=True)
        notify(f"Starting KESTREL Stocks on {host}…")
        _quiet([*SSH, host, _START], timeout=120)
        if _quiet(ping, timeout=20) != 0:
            raise DeskError(f"KESTREL Stocks is not running on {host} and did not start. Update it there once: "
                            f"ssh {host}, then: cd ~/kestrel && git pull origin {UPDATE_BRANCH} && uv sync && "
                            "uv run desk open --desk stocks --install")
    free = next((p for p in ports if not _listening(p)), None)
    if free is None:
        raise DeskError(f"Local ports {ports[0]}–{ports[-1]} are all held by old connections; restart this computer")
    tunnel = [*SSH, "-o", "ExitOnForwardFailure=yes", "-o", "ServerAliveInterval=30", "-o", "ServerAliveCountMax=3",
              "-f", "-N", "-L", f"{free}:127.0.0.1:{port}", host]
    url = f"http://127.0.0.1:{free}/"
    if _quiet(tunnel) != 0 or not _answers(url, 10):
        raise DeskError(f"{host}'s KESTREL is running, but the connection to it from here did not open. "
                        f"Check with: ssh {host}")
    return _show(url, paths)


def _show(url: str, paths: DeskPaths) -> str:
    open_window(url, "stocks", paths.home / "windows")
    return url


def notify(text: str) -> None:
    """A desktop notification, for errors from menu launchers that have no terminal."""
    if shutil.which("notify-send"):
        subprocess.Popen(["notify-send", "KESTREL Stocks", text], start_new_session=True,  # noqa: S603,S607
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


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
    """Solana/Robinhood share halves; the Stocks research hub initially uses the full work area."""
    if desk in {"stocks", "arbitrage"}:
        return area
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


def open_desk(desk: str, port: int, paths: DeskPaths, *, wait_seconds: float = 30, restart: bool = False) -> str:
    """Make sure the dashboard answers, then open it in its own window; returns the URL."""
    url = ensure_running(desk, port, paths, wait_seconds=wait_seconds, restart=restart)
    open_window(url, desk, paths.home / "windows")
    return url


def ensure_running(desk: str, port: int, paths: DeskPaths, *, wait_seconds: float = 30, restart: bool = False) -> str:
    """Start (or, with ``restart``, safely restart) the dashboard in the background until it answers; no window."""
    url = f"http://127.0.0.1:{port}/"
    if desk == "stocks" and not restart and responding(url):
        from desk.stocks.runtime import server_is_current

        restart = not server_is_current(url)
    if restart:
        from desk.arbitrage.repo import isolated_paths
        from desk.ops.dashboard_restart import restart_dashboard

        restart_dashboard(isolated_paths(paths) if desk == "arbitrage" else paths, desk=desk)
    if not responding(url):
        _start_dashboard(desk, port)
        deadline = time.monotonic() + wait_seconds
        while not responding(url):
            if time.monotonic() > deadline:
                if desk == 'arbitrage':
                    raise DeskError(f'Arbitrage dashboard did not start on {url}; run '
                                    '`uv run desk arbitrage dashboard --open` to see its local error')
                raise DeskError(f"the dashboard did not start on {url}; run `desk up` or check `desk services status`")
            time.sleep(0.5)
    if desk == "stocks":
        from desk.stocks.runtime import server_is_current

        if not server_is_current(url):
            raise DeskError("Stocks is responding from a different or older installation. No unrelated process was stopped. "
                            "Open the updated installation with: uv run desk stocks dashboard --restart --open")
    return url


UPDATE_BRANCH = "claude/funny-rubin-gq99vj"   # where KESTREL's updates are published
COMMAND = """#!/bin/sh
# KESTREL on this computer. Written by `uv run desk open --desk stocks --install`.
#   kestrel           health: app, collector, last close, Schwab sign-in, a live test quote, jobs
#   kestrel update    pull the latest version, install it, restart the app in the background
#   kestrel open      open the app window
#   kestrel signin    the weekly Schwab sign-in from this terminal, over SSH too: open the printed link in any
#                     browser, log in, then paste the final address (it may fail to load) at the hidden prompt
#   kestrel restart   restart the app in the background
cd {repo} || exit 1
case "${{1:-status}}" in
  status) exec {uv} run desk stocks status ;;
  update) git pull --ff-only origin "${{KESTREL_BRANCH:-{branch}}}" && {uv} sync && exec {uv} run desk stocks restart ;;
  open) exec {uv} run desk open --desk stocks ;;
  signin) exec {uv} run desk stocks connect --manual ;;
  restart) exec {uv} run desk stocks restart ;;
  *) echo "usage: kestrel [status | update | open | signin | restart]"; exit 2 ;;
esac
"""
REMOTE_COMMAND = """#!/bin/sh
# KESTREL Stocks on {host}, used from this computer. Written by
# `uv run desk open --desk stocks --install --remote {host}`; `--remote off` brings back this computer's own.
#   kestrel           health of the KESTREL on {host}
#   kestrel update    update it there and restart it
#   kestrel signin    its weekly Schwab sign-in, from this terminal
#   kestrel restart   restart it there
#   kestrel open      open it in a window here
action="${{1:-status}}"
case "$action" in
  status|update|signin|restart) exec ssh -t {host} "~/.local/bin/kestrel $action" ;;
  open) cd {repo} || exit 1; exec {uv} run desk open --desk stocks ;;
  *) echo "usage: kestrel [status | update | signin | restart | open]  (runs on {host})"; exit 2 ;;
esac
"""


def install_command(repo: Path, bin_dir: Path, uv: str | None = None, remote: str | None = None) -> Path:
    """Write the ``kestrel`` command into ``bin_dir`` (usually ~/.local/bin): for this computer's desk, or with
    ``remote`` for that computer's, over SSH.

    Written beside and renamed over the old file, so a ``kestrel update`` that is still running is never cut short."""
    import shlex

    bin_dir.mkdir(parents=True, exist_ok=True)
    path = bin_dir / "kestrel"
    fresh = bin_dir / ".kestrel.new"
    template = REMOTE_COMMAND if remote else COMMAND
    fresh.write_text(template.format(repo=shlex.quote(str(repo)), uv=shlex.quote(uv or shutil.which("uv") or "uv"),
                                     branch=UPDATE_BRANCH, host=shlex.quote(check_host(remote) if remote else "")),
                     encoding="utf-8")
    os.chmod(fresh, 0o700)                 # yours to run; nobody else on the computer
    os.replace(fresh, path)
    return path


__all__ = ["APP_BROWSERS", "DESKS", "browser_command", "check_host", "desktop_entry", "ensure_running",
           "install_command", "install_launchers", "monitors", "notify", "open_desk", "open_window", "remote_host",
           "remote_window", "responding", "saved_place", "screen_area", "set_remote_host", "ssh_key_setup",
           "window_slot"]
