"""Repeated dashboard opens reuse the server; restart identity checks exclude unrelated processes."""

from argparse import Namespace
from pathlib import Path
from unittest.mock import Mock

import pytest

from desk.core.errors import DeskError
from desk.core.paths import DeskPaths
from desk.core.worker_lock import WorkerLock
from desk.ops.dashboard_restart import is_dashboard, lock_owner
from desk.stocks.cli import run
from tests.conftest import REPO


@pytest.mark.parametrize('words,expected', [
    ([b'python', b'-m', b'desk', b'stocks', b'dashboard'], True),
    ([b'python', b'/home/neo/kestrel/.venv/bin/desk', b'stocks', b'dashboard', b'--open'], True),
    ([b'python', b'-m', b'desk', b'stocks', b'collect'], False),
    ([b'python', b'-m', b'desk', b'rh', b'dashboard'], False),
    ([b'python', b'-c', b'code', b'desk', b'stocks', b'dashboard'], False),
])
def test_restart_accepts_only_stocks_dashboard_argv(words, expected):
    assert is_dashboard(words, 'stocks') is expected


def test_existing_dashboard_opens_without_starting_or_stopping_a_worker(tmp_path, monkeypatch, capsys):
    paths = DeskPaths(tmp_path / 'home', REPO / 'config', tmp_path / 'private.env')
    monkeypatch.setattr(DeskPaths, 'from_env', lambda: paths)
    monkeypatch.setattr('desk.ops.launcher.responding', lambda url: True)
    opened = Mock()
    monkeypatch.setattr('desk.stocks.cli.webbrowser.open', opened)
    monkeypatch.setattr('desk.stocks.web.create_app', Mock(side_effect=AssertionError('no duplicate app')))
    with WorkerLock(paths, 'stocks-dashboard'):
        assert run(Namespace(stocks_command='dashboard', port=None, open=True, restart=False)) == 0
    opened.assert_called_once_with('http://127.0.0.1:8767')
    assert 'already running' in capsys.readouterr().out


def test_unresponsive_existing_dashboard_gives_specific_recovery(tmp_path, monkeypatch):
    paths = DeskPaths(tmp_path / 'home', REPO / 'config', tmp_path / 'private.env')
    monkeypatch.setattr(DeskPaths, 'from_env', lambda: paths)
    monkeypatch.setattr('desk.ops.launcher.responding', lambda url: False)
    with WorkerLock(paths, 'stocks-dashboard'), pytest.raises(DeskError, match='--restart --open'):
        run(Namespace(stocks_command='dashboard', port=None, open=True, restart=False))


def test_kernel_lock_owner_tracks_real_flock(tmp_path: Path):
    import os

    paths = DeskPaths(tmp_path / 'home', REPO / 'config', tmp_path / 'private.env')
    lock = WorkerLock(paths, 'stocks-dashboard')
    with lock:
        assert lock_owner(lock.path) == os.getpid()
    assert lock_owner(lock.path) is None


def test_restart_gracefully_stops_only_a_real_temporary_dashboard(tmp_path: Path):
    import json
    import os
    import socket
    import subprocess
    import sys
    import time

    from desk.ops.dashboard_restart import restart_dashboard
    from desk.ops.launcher import responding

    paths = DeskPaths(tmp_path / 'home', REPO / 'config', tmp_path / 'private.env')
    paths.ensure()
    # No research worker or provider request is permitted in this subprocess test.
    preference = paths.home / 'stocks-automation.json'
    preference.write_text(json.dumps({'enabled': False}))
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    env = {**os.environ, 'DESK_HOME': str(paths.home), 'DESK_CONFIG_DIR': str(paths.config_dir),
           'DESK_SECRETS_FILE': str(paths.secrets_file)}
    child = subprocess.Popen([sys.executable, '-m', 'desk', 'stocks', 'dashboard', '--port', str(port)],
                             env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        until = time.monotonic() + 10
        while not responding(f'http://127.0.0.1:{port}', timeout=0.2):
            assert child.poll() is None and time.monotonic() < until
            time.sleep(0.05)
        assert lock_owner(paths.home / 'run' / 'stocks-dashboard.lock') == child.pid
        restart_dashboard(paths, timeout=5)
        assert child.wait(timeout=5) == 0
        assert json.loads(preference.read_text()) == {'enabled': False}
        assert not WorkerLock(paths, 'stocks-worker').held()
    finally:
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=5)
