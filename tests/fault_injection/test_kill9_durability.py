"""M1-13: ``kill -9`` during a write loop loses no committed rows and leaves an intact database."""

from __future__ import annotations

import os
import signal
import sqlite3
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from desk.storage.db import DbKind

WRITER = textwrap.dedent(
    """
    import sys
    from pathlib import Path
    from desk.storage.db import DbKind, connect, migrate, transaction
    conn = connect(Path(sys.argv[1]), DbKind(sys.argv[2]))
    conn.execute("CREATE TABLE IF NOT EXISTS t (i INTEGER PRIMARY KEY, payload TEXT NOT NULL)")
    i = 0
    while True:
        i += 1
        with transaction(conn):
            conn.execute("INSERT INTO t(i, payload) VALUES (?, ?)", (i, "x" * 200))
        print(i, flush=True)       # only printed AFTER the commit returned
    """
)


@pytest.mark.slow
@pytest.mark.parametrize("kind", [DbKind.LEDGER, DbKind.MARKET])
def test_kill9_loses_no_committed_rows(tmp_path: Path, kind: DbKind) -> None:
    db_path = tmp_path / f"{kind.value}.db"
    proc = subprocess.Popen([sys.executable, "-c", WRITER, str(db_path), kind.value],
                            stdout=subprocess.PIPE, text=True, env=os.environ.copy())
    assert proc.stdout is not None
    last_committed = 0
    deadline = time.monotonic() + 30
    while last_committed < 300 and time.monotonic() < deadline:
        line = proc.stdout.readline()
        if not line:
            break
        last_committed = int(line)
    os.kill(proc.pid, signal.SIGKILL)
    proc.wait(timeout=10)
    assert last_committed >= 300, "writer did not make progress"

    conn = sqlite3.connect(db_path)
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    stored = conn.execute("SELECT COUNT(*), MAX(i) FROM t").fetchone()
    conn.close()
    assert stored[0] >= last_committed          # every acknowledged commit survived
    assert stored[0] == stored[1]               # no gaps, no partial rows
