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
        assert by["Schwab"] == (False, "not signed in · sign in again: KESTREL → Settings → Start browser authorization")
        assert by["Last close"] == (False, "nothing analysed yet")
        assert "Live quote" not in by
        text = status.render(found, NOW)
        assert text.splitlines()[0].startswith("KESTREL · ") and "✗ App" in text
        assert text.endswith("need attention.")
    finally:
        w.close()


def test_schwab_line_gives_the_times_to_compare_across_computers() -> None:
    ok, _, text = status._schwab({"connected": True, "relogin_required": False, "login_days_left": 6.8,
                                  "signed_in_at": "2026-10-30T13:12:00+00:00"})
    assert ok and text == "signed in here Fri Oct 30, 9:12 AM ET · 6.8 days left"
    ok, _, text = status._schwab({"connected": False, "relogin_required": True, "ended_early": True,
                                  "ended_after_hours": 4.9, "error": "invalid_grant",
                                  "last_ok_at": "2026-10-30T17:44:00+00:00", "ended_at": "2026-10-30T18:14:00+00:00"})
    assert not ok and text.startswith("ended 4.9 h after signing in here, between Fri Oct 30, 1:44 PM ET and "
                                      "Fri Oct 30, 2:14 PM ET (invalid_grant)")
