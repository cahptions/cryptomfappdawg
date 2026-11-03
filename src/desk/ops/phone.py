"""KESTREL on your phone, privately, through Tailscale.

The apps listen on this computer only (127.0.0.1). Tailscale Serve, running on the same computer, answers at the
computer's private address (``appstuff.tail1234.ts.net``) with a real HTTPS certificate, only for devices signed in
to your own Tailscale account (Serve, not Funnel: nothing is put on the public internet), and passes each request to
the app here. The apps accept that one extra address: this computer's own Tailscale name, read from
``tailscale status``. Any other name is still refused, which keeps DNS rebinding out.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import time
from collections.abc import Callable
from typing import Any

from desk.core.errors import DeskError

HTTPS_PORTS = {"stocks": 443, "arbitrage": 8443}
RECHECK_SECONDS = 60.0

INSTALL = """Tailscale isn't installed on this computer. It's free for personal use and keeps KESTREL private to your
own devices. Once, on this computer:
  1. curl -fsSL https://tailscale.com/install.sh | sh
  2. sudo tailscale up --operator=$USER      (prints a sign-in link; any Google, Microsoft, Apple or GitHub account)
  3. kestrel phone"""
SIGN_IN = """Tailscale is installed but not signed in here. Once, on this computer:
  sudo tailscale up --operator=$USER      (prints a sign-in link)
then run kestrel phone again."""
NOT_SERVED = """Tailscale didn't start serving KESTREL.
  If it said access denied: run once  sudo tailscale set --operator=$USER  then kestrel phone again.
  If it printed a link to turn on HTTPS: open it, allow HTTPS certificates, then kestrel phone again."""
READY = """KESTREL {app} is ready for your phone at

    {url}

On the phone, once: install the Tailscale app (App Store or Google Play), sign in with the same account, leave it
on. Then open that address and add it to your home screen (iPhone: Share > Add to Home Screen; Android: the
three dots > Add to Home screen).

Only devices signed in to your Tailscale account can open it; it is not on the public internet. This computer has
to be on and awake. To stop: kestrel phone{arg} off"""


def tailnet_name(timeout: float = 3) -> str | None:
    """This computer's full Tailscale name while Tailscale is running and signed in, else None."""
    exe = shutil.which("tailscale")
    if not exe:
        return None
    try:
        done = subprocess.run([exe, "status", "--json"], capture_output=True, text=True, timeout=timeout,  # noqa: S603
                              check=False)
        status = json.loads(done.stdout or "{}")
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    name = str((status.get("Self") or {}).get("DNSName") or "").rstrip(".").lower()
    return name if status.get("BackendState") == "Running" and name.endswith(".ts.net") else None


class TailnetHost:
    """The one Tailscale name the apps accept. Looked up when the first ``.ts.net`` request arrives, and again at most
    once a minute when a request names a different one (Tailscale started after the app, or the name changed)."""

    def __init__(self, lookup: Callable[[], str | None] = tailnet_name) -> None:
        self.lookup = lookup
        self.name: str | None = None
        self.next_check = 0.0

    async def accepts(self, hostname: str) -> bool:
        if self.name and hostname == self.name:
            return True
        if not hostname.endswith(".ts.net") or time.monotonic() < self.next_check:
            return False
        self.next_check = time.monotonic() + RECHECK_SECONDS
        self.name = await asyncio.to_thread(self.lookup)
        return bool(self.name) and hostname == self.name


def serve(app: str, port: int, *, off: bool = False, run: Callable[..., Any] = subprocess.run,
          lookup: Callable[[], str | None] = tailnet_name) -> str:
    """Serve one app to your Tailscale devices (``tailscale serve --bg``), or stop serving it. Tailscale's own output
    and any link it asks you to open are shown as they come."""
    exe = shutil.which("tailscale")
    if not exe:
        raise DeskError(INSTALL)
    https = HTTPS_PORTS[app]
    arg = "" if app == "stocks" else f" {app}"
    if off:
        run([exe, "serve", f"--https={https}", "off"], check=False)
        return f"KESTREL {app.title()} is no longer served to your phone."
    name = lookup()
    if not name:
        raise DeskError(SIGN_IN)
    if run([exe, "serve", "--bg", f"--https={https}", f"http://127.0.0.1:{port}"], check=False).returncode:
        raise DeskError(NOT_SERVED)
    url = f"https://{name}/" if https == 443 else f"https://{name}:{https}/"
    return READY.format(app=app.title(), url=url, arg=arg)


__all__ = ["HTTPS_PORTS", "TailnetHost", "serve", "tailnet_name"]
