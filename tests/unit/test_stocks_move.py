"""`kestrel move-from USER@HOST`: another computer's Stocks data comes here through SQLite online-backup copies,
checked before anything here changes; this computer's own data is kept aside, never deleted. SSH and scp are fakes
that act on a second data folder; the copies are made by the real script with the real backup code."""

from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from desk.core.errors import DeskError
from desk.core.paths import DeskPaths
from desk.ops import launcher, move
from desk.stocks import learning
from desk.stocks.paper.book import PaperBook
from desk.stocks.repo import StocksDatabases, day_of
from tests.conftest import REPO

HOST = "neo@192.168.0.10"
NOW = datetime(2026, 11, 12, 18, 0, tzinfo=UTC)


def desk(root: Path, *, sessions: int, trades: int, learning_trades: int, tracked: int) -> DeskPaths:
    paths = DeskPaths(root, REPO / "config", root / "secrets.env")
    dbs = StocksDatabases.open(paths)
    try:
        for k in range(sessions):
            dbs.market.execute("INSERT INTO stk_bar_days(day, source, rows, received_at) VALUES (?,?,?,?)",
                               (day_of(date(2026, 10, 1)) + k, "synthetic", 10, NOW.isoformat()))
        book = PaperBook(dbs.ledger)
        for k in range(trades):
            decision = book.decide(NOW, date(2026, 11, 11), "momentum", "sha", f"S{k}", "ENTER", ["x"], {})
            book.open_position(account="momentum", spec_sha256="sha", asset="stock", symbol=f"S{k}",
                               structure="stock", legs=[], decision_id=decision, at=NOW, day=date(2026, 11, 11),
                               cost=100.0, fees=0.0, max_loss=None, stop_price=None, expires=None, plan={})
        dbs.research.execute("INSERT INTO stk_play_ideas VALUES ('i1','2026-11-10T21:00:00+00:00',1,'c','k','{}','m')")
        for k in range(tracked):
            dbs.research.execute("INSERT INTO stk_play_selections VALUES (?,?,'i1','2026-11-10T21:00:00+00:00',"
                                 "'2026-11-11T14:30:00+00:00',1)", (f"s{k}", f"r{k}"))
    finally:
        dbs.close()
    book_conn = learning.open_book(paths)
    try:
        book = PaperBook(book_conn)
        for k in range(learning_trades):
            decision = book.decide(NOW, date(2026, 11, 11), "uncapped_research", "sha", f"L{k}", "ENTER", ["x"], {})
            book.open_position(account="uncapped_research", spec_sha256="sha", asset="stock", symbol=f"L{k}",
                               structure="stock", legs=[], decision_id=decision, at=NOW, day=date(2026, 11, 11),
                               cost=100.0, fees=0.0, max_loss=None, stop_price=None, expires=None, plan={})
    finally:
        book_conn.close()
    (paths.home / "stocks-plays.json").write_text("{}")
    return paths


class FakeNetwork:
    """ssh runs commands as if on the other computer (its data folder is ``remote``); scp copies from it."""

    def __init__(self, remote: DeskPaths, *, reachable: bool = True, damage: str | None = None) -> None:
        self.remote, self.reachable, self.damage = remote, reachable, damage
        self.calls: list[list[str]] = []

    def __call__(self, command: list[str], **_: Any) -> SimpleNamespace:
        self.calls.append(command)
        if command[0] == "ssh":
            assert "BatchMode=yes" in command
            if not self.reachable:
                return SimpleNamespace(returncode=255, stdout="", stderr="")
            remote = command[-1]
            if remote == "true" or remote.startswith("systemctl --user disable --now"):
                return SimpleNamespace(returncode=0, stdout="", stderr="")
            assert remote == move.remote_command("KESTREL_MOVE_DRY=1" in remote)
            env = {**os.environ, "DESK_HOME": str(self.remote.home),
                   **({"KESTREL_MOVE_DRY": "1"} if "KESTREL_MOVE_DRY=1" in remote else {})}
            done = subprocess.run([sys.executable, "-c", move._SCRIPT], capture_output=True, text=True, env=env,
                                  check=False, cwd=REPO)
            return SimpleNamespace(returncode=done.returncode, stdout=done.stdout, stderr=done.stderr)
        if command[0] == "scp":
            source, target = command[-2], Path(command[-1])
            assert "BatchMode=yes" in command and source.startswith(f"{HOST}:/")
            shutil.copyfile(source.split(":", 1)[1], target)
            if self.damage and target.name == self.damage:
                with target.open("r+b") as handle:
                    handle.seek(200)
                    handle.write(b"\xff" * 8)
            return SimpleNamespace(returncode=0)
        return SimpleNamespace(returncode=0, stdout="", stderr="")


@pytest.fixture
def computers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[DeskPaths, DeskPaths]:
    monkeypatch.setattr(move, "this_computer", lambda host: host.endswith("127.0.0.1"))
    monkeypatch.setattr(move.shutil, "which", lambda name: None)          # no systemd in the test
    hp = desk(tmp_path / "hp", sessions=15, trades=7, learning_trades=4, tracked=3)
    dell = desk(tmp_path / "dell", sessions=2, trades=1, learning_trades=0, tracked=0)
    return hp, dell


def test_the_other_computers_data_moves_here_and_this_computers_is_kept(computers: tuple[DeskPaths, DeskPaths]) -> None:
    hp, dell = computers
    network, started, said = FakeNetwork(hp), [], []
    result = move.move_from(HOST, dell, port=8767, run=network,
                            start=lambda paths, port: started.append(port) or "running", out=said.append)
    assert result["moved"] == {"price sessions": 15, "Plays sessions": 0, "tracked plays": 3, "PAPER trades": 7,
                               "Learning positions": 4}
    assert move.counts(dell.db_dir) == result["moved"] and started == [8767]
    kept = Path(result["kept"])
    assert kept.parent == dell.backups_dir / "before-move"
    assert move.counts(kept) == {"price sessions": 2, "Plays sessions": 0, "tracked plays": 0, "PAPER trades": 1,
                                 "Learning positions": 0}                  # the Dell's own data, untouched
    assert (kept / "stocks-plays.json").exists() and not (dell.home / "stocks-plays.json").exists()
    with sqlite3.connect(dell.db_dir / "stocks-research.db") as conn:          # migrations ran on the moved file
        assert conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] >= 16
    text = "\n".join(said)
    assert "kestrel signin" in text and "Not copied: secrets.env" in text and "(this computer had 1)" in text
    remote = [c[-1] for c in network.calls if c[0] == "ssh"]
    assert remote[0] == "true" and remote[1] == move.remote_command(False)
    manifests = list((hp.backups_dir / "manual").glob("*/manifest.json"))     # the other computer keeps its copy
    assert len(manifests) == 1 and result["remote_copy"] == str(manifests[0].parent)
    assert not (dell.home / "moving-in").exists()


def test_a_dry_run_lists_the_files_and_changes_nothing(computers: tuple[DeskPaths, DeskPaths]) -> None:
    hp, dell = computers
    before = move.counts(dell.db_dir)
    said: list[str] = []
    result = move.move_from(HOST, dell, port=8767, dry_run=True, run=FakeNetwork(hp), out=said.append,
                            start=lambda *_: pytest.fail("a dry run never restarts anything"))
    assert set(result["files"]) == {"stocks-market", "stocks-research", "stocks-ledger-paper", "stocks-learning-paper"}
    assert move.counts(dell.db_dir) == before and not (hp.backups_dir / "manual").exists()
    assert "Dry run: nothing was copied" in said[-1]


def test_a_damaged_copy_is_refused_before_anything_here_changes(computers: tuple[DeskPaths, DeskPaths],
                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    hp, dell = computers
    before = move.counts(dell.db_dir)
    monkeypatch.setattr(move, "stop_local", lambda *_: pytest.fail("Stocks here must not be stopped"))
    with pytest.raises(DeskError, match="did not pass their checks"):
        move.move_from(HOST, dell, port=8767, run=FakeNetwork(hp, damage="stocks-ledger-paper.db"),
                       start=lambda *_: "", out=lambda _: None)
    assert move.counts(dell.db_dir) == before and not (dell.home / "moving-in").exists()


def test_a_ledger_not_marked_paper_is_refused(computers: tuple[DeskPaths, DeskPaths]) -> None:
    hp, dell = computers
    with sqlite3.connect(hp.db_dir / "stocks-ledger-paper.db") as conn:
        conn.execute("DELETE FROM ledger_meta WHERE key='mode'")
    with pytest.raises(DeskError, match="not marked PAPER"):
        move.move_from(HOST, dell, port=8767, run=FakeNetwork(hp), start=lambda *_: "", out=lambda _: None)
    assert move.counts(dell.db_dir)["PAPER trades"] == 1


def test_it_refuses_this_computer_and_an_unreachable_one(computers: tuple[DeskPaths, DeskPaths]) -> None:
    hp, dell = computers
    with pytest.raises(DeskError, match="is this computer"):
        move.move_from("neo@127.0.0.1", dell, port=8767, run=FakeNetwork(hp), out=lambda _: None)
    network = FakeNetwork(hp, reachable=False)
    with pytest.raises(DeskError, match="ssh-copy-id"):
        move.move_from(HOST, dell, port=8767, run=network, out=lambda _: None)
    assert len(network.calls) == 1
    with pytest.raises(DeskError, match="user@address"):
        move.move_from("neo@host; rm -rf ~", dell, port=8767, run=network, out=lambda _: None)


def test_stop_other_switches_off_the_other_computers_stocks_first(computers: tuple[DeskPaths, DeskPaths]) -> None:
    hp, dell = computers
    network = FakeNetwork(hp)
    move.move_from(HOST, dell, port=8767, stop_other=True, run=network, start=lambda *_: "", out=lambda _: None)
    remote = [c[-1] for c in network.calls if c[0] == "ssh"]
    assert remote[1].startswith("systemctl --user disable --now desk-stocks-dashboard.service")
    assert remote[2] == move.remote_command(False)                                # stopped before the copy


def test_both_kestrel_commands_offer_move_from(tmp_path: Path) -> None:
    for remote in (None, HOST):
        path = launcher.install_command(Path("/home/neo/kestrel"), tmp_path / str(bool(remote)), uv="uv",
                                        remote=remote)
        assert "move-from) " in path.read_text() and "run desk stocks move-from \"$@\"" in path.read_text()
        assert subprocess.run(["sh", "-n", str(path)], check=False).returncode == 0
