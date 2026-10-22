"""Diagnostics must be useful when broken and never create databases, print credentials or start work."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock

from desk.core.paths import DeskPaths
from desk.stocks.cli import run
from desk.stocks.diagnostics import diagnose
from tests.conftest import REPO
from tests.stocks_world import NOW, make_world


def test_diagnostics_on_uninstalled_desk_create_nothing(tmp_path: Path) -> None:
    paths = DeskPaths(tmp_path / 'missing', REPO / 'config', tmp_path / 'none.env')
    result = diagnose(paths)
    assert result['read_only'] and result['orders_enabled'] is False
    assert all(row['status'] == 'not_created_yet' for row in result['databases'].values())
    assert not paths.home.exists()


def test_diagnostics_omit_secrets_and_retained_errors_without_mutating_data(tmp_path: Path) -> None:
    world = make_world(tmp_path, n=12)
    try:
        sentinel = 'PRIVATE-SENTINEL-DO-NOT-PRINT'
        world.paths.secrets_file.write_text(f'MASSIVE_API_KEY={sentinel}\nSEC_CONTACT_EMAIL={sentinel}@example.org\n')
        world.paths.secrets_file.chmod(0o600)
        world.market.job('prices', NOW,
                         error=f'HTTP 401 {sentinel}')
        world.paths.logs_dir.mkdir(exist_ok=True)
        (world.paths.logs_dir / 'stocks-research.log').write_text(f'ValueError: {sentinel}\n')
        ledger = list(world.dbs.ledger.iterdump())
        result = diagnose(world.paths)
        assert sentinel not in json.dumps(result)
        assert result['source_health']['prices']['error_category'] == 'authorization'
        assert result['worker']['last_logged_exception_type'] == 'ValueError'
        assert result['history']['sessions'] > 60 and result['configured_inputs']['MASSIVE_API_KEY']
        assert list(world.dbs.ledger.iterdump()) == ledger
    finally:
        world.close()


def test_one_corrupt_database_and_reports_do_not_hide_other_diagnostics(tmp_path: Path) -> None:
    paths = DeskPaths(tmp_path / 'home', REPO / 'config', tmp_path / 'none.env')
    paths.ensure()
    (paths.db_dir / 'stocks-market.db').write_bytes(b'not sqlite')
    (paths.home / 'stocks-worker.json').write_text('{broken')
    (paths.home / 'stocks-progress.json').write_text('{"phase": ["malformed"], "updated_at": "2026-10-21"}')
    result = diagnose(paths)
    assert result['databases']['stocks-market.db']['status'] == 'unavailable_or_check_incomplete'
    assert result['databases']['stocks-ledger-paper.db']['status'] == 'not_created_yet'
    assert result['warnings']


def test_diagnostic_cli_skips_bootstrap_and_runtime_mutations(tmp_path: Path, monkeypatch, capsys) -> None:
    from argparse import Namespace

    paths = DeskPaths(tmp_path / 'missing', tmp_path / 'no-config', tmp_path / 'none.env')
    monkeypatch.setattr(DeskPaths, 'from_env', lambda: paths)
    bootstrap = Mock(side_effect=AssertionError('diagnose must not load or initialize databases'))
    monkeypatch.setattr('desk.stocks.cli.StocksDatabases.open', bootstrap)
    assert run(Namespace(stocks_command='diagnose')) == 0
    assert json.loads(capsys.readouterr().out)['read_only']
    assert not paths.home.exists()
