"""Reopen a real stale Stocks process through the icon's code path, retaining the actual paused data directory."""

import json
import os
import shutil
import socket
import subprocess
import sys
import time

from desk.ops import launcher
from desk.ops.dashboard_restart import lock_owner
from desk.stocks.paper.book import PaperBook
from desk.stocks.runtime import server_is_current
from tests.conftest import REPO
from tests.stocks_world import NOW, make_world


def test_application_icon_replaces_real_stale_dashboard_without_changing_saved_paper_or_pause(tmp_path, monkeypatch):
    world = make_world(tmp_path / "data", n=12)
    paths = world.paths
    preference = paths.home / "stocks-automation.json"
    preference.write_text(json.dumps({"enabled": False}))
    PaperBook(world.dbs.ledger).open_account("preserved_test", 12345, NOW, NOW.date())
    before = list(world.dbs.ledger.iterdump())
    # An isolated prior installation serves its own assets and owns its real kernel lock.
    previous = tmp_path / "previous" / "src"
    shutil.copytree(REPO / "src" / "desk", previous / "desk", ignore=shutil.ignore_patterns("__pycache__"))
    version = previous / "desk" / "stocks" / "runtime.py"
    version.write_text(version.read_text().replace("return digest.hexdigest()", "return 'prior-installation'"))
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    url = f"http://127.0.0.1:{port}/"
    children = []

    def spawn(source):
        child = subprocess.Popen(
            [sys.executable, "-m", "desk", "stocks", "dashboard", "--port", str(port)],
            cwd=source.parent,
            env={**os.environ, "PYTHONPATH": str(source), "DESK_HOME": str(paths.home),
                 "DESK_CONFIG_DIR": str(paths.config_dir), "DESK_SECRETS_FILE": str(paths.secrets_file)},
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
        children.append(child)
        return child

    try:
        old = spawn(previous)
        deadline = time.monotonic() + 15
        while not launcher.responding(url, timeout=.2):
            assert old.poll() is None and time.monotonic() < deadline
            time.sleep(.05)
        assert not server_is_current(url)
        assert lock_owner(paths.home / "run" / "stocks-dashboard.lock") == old.pid
        opened = []
        monkeypatch.setattr(launcher, "_start_dashboard", lambda desk, selected_port: spawn(REPO / "src"))
        monkeypatch.setattr(launcher, "open_window", lambda *args: opened.append(args))
        assert launcher.open_desk("stocks", port, paths, wait_seconds=15) == url
        assert len(children) == 2 and old.poll() is not None
        assert server_is_current(url)
        assert lock_owner(paths.home / "run" / "stocks-dashboard.lock") == children[1].pid
        assert len(opened) == 1
        assert json.loads(preference.read_text()) == {"enabled": False}
        assert list(world.dbs.ledger.iterdump()) == before
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
            child.communicate(timeout=5)
        world.close()
