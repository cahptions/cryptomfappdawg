"""`kestrel`: the one-word command file and the plain-English status report."""

from __future__ import annotations

import os
import subprocess
from datetime import timedelta
from pathlib import Path

from desk.core.config import load_config
from desk.ops.launcher import UPDATE_BRANCH, install_command
from desk.stocks import status
from tests.conftest import REPO
from tests.stocks_world import NOW, make_world


def test_kestrel_command_is_a_private_valid_script_with_status_update_and_open(tmp_path: Path) -> None:
    path = install_command(Path("/home/neo/kestrel"), tmp_path / "bin", uv="/home/neo/.local/bin/uv")
    text = path.read_text()
    assert path.name == "kestrel" and os.stat(path).st_mode & 0o777 == 0o700
    assert "cd /home/neo/kestrel" in text and "/home/neo/.local/bin/uv run desk stocks status" in text
    assert f'git pull --ff-only origin "${{KESTREL_BRANCH:-{UPDATE_BRANCH}}}"' in text
    assert "run desk stocks restart" in text and "run desk open --desk stocks" in text
    assert subprocess.run(["sh", "-n", str(path)], check=False).returncode == 0       # parses as POSIX sh
    usage = subprocess.run(["sh", str(path), "nonsense"], capture_output=True, text=True, check=False,
                           cwd=tmp_path, env={**os.environ, "PATH": "/usr/bin:/bin"})
    assert usage.returncode != 0


def test_status_reads_this_desk_in_plain_words(tmp_path: Path) -> None:
    w = make_world(tmp_path, n=12)
    try:
        found = status.lines(w.paths, load_config(REPO / "config"), NOW + timedelta(hours=1), online=False)
        by = {label: (ok, text) for ok, label, text in found}
        assert by["App"][0] is False and "kestrel open" in by["App"][1]
        assert by["Collector"] == (False, "paused · use Start PAPER in the app")
        assert by["Prices"][0] and by["Prices"][1].startswith("through Wed Oct 21")
        assert by["Schwab"][0] is False and "Open Schwab authorization" in by["Schwab"][1]
        assert by["Last close"] == (False, "nothing analysed yet")
        assert "Live quote" not in by
        text = status.render(found, NOW)
        assert text.splitlines()[0].startswith("KESTREL · ") and "✗ App" in text
        assert text.endswith("need attention.")
    finally:
        w.close()
