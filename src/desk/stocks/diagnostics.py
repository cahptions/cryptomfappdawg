"""Bounded local diagnostics: read-only SQLite, no migrations, credential values, provider calls or worker control."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import stat
import subprocess
import time
from pathlib import Path
from typing import Any

from desk.core.clock import utc_now
from desk.core.paths import REPO_ROOT, DeskPaths
from desk.stocks.analysis import MIN_BARS
from desk.stocks.repo import LEDGER_FILE, MARKET_FILE, RESEARCH_FILE

KEYS = ("MASSIVE_API_KEY", "SEC_CONTACT_EMAIL", "TRADIER_TOKEN", "SCHWAB_APP_KEY", "SCHWAB_APP_SECRET")


def error_category(message: str) -> str:
    """Stored/provider errors may echo secrets. Return an allowlisted diagnosis, never the original text."""
    text = message.lower()
    for words, label in ((('401', '403', 'authorization', 'credential'), 'authorization'),
                         (('429', 'rate limit'), 'rate_limit'), (('timeout', 'timed out'), 'timeout'),
                         (('network', 'connecterror', 'connectivity'), 'network'),
                         (('schema', 'html', 'header', 'csv', 'json', 'malformed'), 'response_format'),
                         (('disk', 'space', 'full'), 'storage'), (('locked', 'busy'), 'database_busy')):
        if any(word in text for word in words):
            return label
    return 'error_details_suppressed'


def _document(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        with path.open('rb') as handle:
            raw = handle.read(1_000_001)
        if len(raw) > 1_000_000:
            raise ValueError
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError
        return dict(value)
    except (OSError, ValueError):
        return {'diagnostic_error': 'unreadable_or_oversized'}


def _held(path: Path) -> bool | None:
    import fcntl

    if not path.exists():
        return False
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            return False
        finally:
            os.close(descriptor)
    except OSError:
        return None


def diagnose(paths: DeskPaths) -> dict[str, Any]:
    result: dict[str, Any] = {'generated_at': utc_now().isoformat(), 'mode': 'PAPER', 'orders_enabled': False,
                              'read_only': True, 'databases': {}, 'source_health': {}, 'warnings': []}
    try:
        version = subprocess.run(['git', 'rev-parse', '--short', 'HEAD'], cwd=REPO_ROOT, capture_output=True,  # noqa: S607
                                 text=True, timeout=2, check=False)
        result['version'] = version.stdout.strip() if re.fullmatch(r'[0-9a-f]{7,40}\n?', version.stdout) else 'unknown'
    except (OSError, subprocess.TimeoutExpired):
        result['version'] = 'unknown'
    present = {key: bool(os.environ.get(key)) for key in KEYS}
    result['settings'] = {'config_present': (paths.config_dir / 'default.toml').is_file(),
                          'private_file_present': paths.secrets_file.is_file()}
    if paths.secrets_file.is_file():
        try:
            info = paths.secrets_file.stat()
            result['settings']['private_file_permissions_ok'] = (info.st_uid == os.getuid()
                                                                                and not stat.S_IMODE(info.st_mode) & 0o077)
            with paths.secrets_file.open('rb') as handle:
                raw = handle.read(262145)
            if len(raw) > 262144:
                raise ValueError
            for line in raw.decode('utf-8').splitlines():
                key, separator, setting = line.partition('=')
                if separator and key.strip() in present:
                    present[key.strip()] |= bool(setting.strip().strip('"\''))
        except (OSError, ValueError):
            result['warnings'].append('Private configuration presence could not be checked')
    result['configured_inputs'] = present
    result['worker'] = {'running': _held(paths.home / 'run' / 'stocks-worker.lock'),
                         'dashboard_running': _held(paths.home / 'run' / 'stocks-dashboard.lock')}
    log_path = paths.logs_dir / 'stocks-research.log'
    if log_path.is_file():
        try:
            with log_path.open('rb') as handle:
                handle.seek(max(0, log_path.stat().st_size - 32768))
                tail = handle.read(32768).decode('utf-8', 'replace')
            types = re.findall(r'^(ZeroDivisionError|ValueError|TypeError|SchemaError|StorageError|OperationalError|'
                               r'IntegrityError|MemoryError|OverflowError|SchwabAuthError):', tail, re.MULTILINE)
            result['worker']['last_logged_exception_type'] = types[-1] if types else None
        except OSError:
            result['warnings'].append('Worker log could not be read')
    preference = _document(paths.home / 'stocks-automation.json')
    result['worker']['automation_enabled'] = preference.get('enabled') if preference else True
    for name in ('worker', 'paper', 'research', 'progress'):
        value = _document(paths.home / f'stocks-{name}.json')
        if value and value.get('diagnostic_error'):
            result['warnings'].append(f'Stored {name} report is unreadable or oversized')
        if value:
            result[f'{name}_record'] = {key: value[key] for key in ('asof', 'started_at', 'finished_at', 'updated_at')
                                        if isinstance(value.get(key), str) and re.fullmatch(r'[0-9T:+.Z-]+', value[key])}
            phases = {'starting', 'listings', 'prices', 'corporate_actions', 'companies', 'filings', 'mentions',
                      'rates', 'short_interest', 'fails', 'short_volume', 'options', 'analysis', 'idle'}
            if name == 'progress' and isinstance(value.get('phase'), str) and value['phase'] in phases:
                result['progress_record']['phase'] = value['phase']
    for filename in (MARKET_FILE, RESEARCH_FILE, LEDGER_FILE):
        path = paths.db_dir / filename
        if not path.exists():
            result['databases'][filename] = {'status': 'not_created_yet'}
            continue
        conn = None
        try:
            conn = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=0.1)
            conn.row_factory = sqlite3.Row
            conn.execute('PRAGMA query_only=ON')
            deadline = time.monotonic() + 0.5
            def expired(limit: float = deadline) -> int:
                return int(time.monotonic() > limit)
            conn.set_progress_handler(expired, 1000)
            check = conn.execute('PRAGMA quick_check(1)').fetchone()[0]
            result['databases'][filename] = {'integrity': 'ok' if check == 'ok' else 'failed',
                                             'journal_mode': conn.execute('PRAGMA journal_mode').fetchone()[0]}
            if filename == MARKET_FILE:
                row = conn.execute('SELECT COUNT(*),SUM(rows),MAX(day),MAX(received_at) '
                                   'FROM stk_bar_days WHERE rows>0').fetchone()
                result['history'] = {'sessions': row[0], 'bar_rows_recorded': row[1] or 0,
                                      'minimum_sessions': MIN_BARS, 'last_receipt': row[3],
                                      'note': 'Each stock still needs its own history, liquidity and strategy inputs'}
                for row in conn.execute('SELECT name,last_ok_at,last_run_at,last_error FROM stk_jobs LIMIT 50'):
                    name = row['name'] if re.fullmatch(r'[a-z_]{1,40}', row['name']) else 'unknown_job'
                    result['source_health'][name] = {'last_success': row['last_ok_at'], 'last_attempt': row['last_run_at'],
                                                     'error_category': (error_category(row['last_error'])
                                                                        if row['last_error'] else None)}
            if filename == LEDGER_FILE:
                row = conn.execute("SELECT value FROM ledger_meta WHERE key='mode'").fetchone()
                result['databases'][filename]['paper_mode_verified'] = bool(row and row[0] == 'PAPER')
        except sqlite3.Error as exc:
            result['databases'][filename] = {'status': 'unavailable_or_check_incomplete',
                                             'error_category': error_category(str(exc))}
        finally:
            if conn is not None:
                conn.close()
    return result
