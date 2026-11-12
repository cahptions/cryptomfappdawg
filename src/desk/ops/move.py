"""``kestrel move-from USER@HOST``: bring another computer's Stocks data to this one, which keeps Stocks from now on.

Run on the computer that should keep Stocks. In order:

1. refuse when ``USER@HOST`` is this computer; check SSH answers without a password prompt (BatchMode);
2. with ``--stop-other``, stop and disable Stocks on the other computer first, so the copy is its final state;
3. ask the other computer for consistent copies of its four Stocks databases with SQLite's online backup API
   (``desk.ops.backups.create_backup``, the same path as ``desk backups create``): a live main file is never copied
   without its write-ahead log; the copies land in the other computer's ``backups/manual`` folder and stay there;
4. copy them here over SSH (scp) into a staging folder, then check every checksum, SQLite's integrity and foreign-key
   checks, and that the trading ledger says PAPER (``desk.ops.backups.verify_backup``);
5. stop this computer's Stocks (the always-on service, or a plain app process) and wait for every Stocks worker lock;
6. move this computer's own Stocks databases (each with its ``-wal``/``-shm``) and derived reports into
   ``backups/before-move/<time>``, never deleting them; put the copies in place; run the migrations;
7. start Stocks again here and print what moved: price sessions, Plays sessions, tracked plays, PAPER trades and
   Learning positions.

Secrets are never copied (``secrets.env``, the Schwab sign-in): sign in to Schwab again here (``kestrel signin``).
``--dry-run`` does steps 1 and a listing of the other computer's files, and changes nothing anywhere.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
import time
from collections.abc import Callable
from contextlib import closing, suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from desk.core.errors import DeskError
from desk.core.paths import DeskPaths
from desk.core.worker_lock import WorkerLock
from desk.ops.backups import DATABASES, verify_backup
from desk.ops.launcher import SSH, check_host, this_computer

KINDS = ("stocks-market", "stocks-research", "stocks-ledger-paper", "stocks-learning-paper")
LOCKS = ("stocks-dashboard", "stocks-worker", "stocks-evidence", "stocks-backtest", "stocks-learning")
# Reports the app rebuilds from the databases: moved aside with the old databases so none shows this computer's
# old data beside the moved-in data.
DERIVED = ("stocks-worker.json", "stocks-paper.json", "stocks-research.json", "stocks-progress.json",
           "stocks-options.json", "stocks-plays.json", "stocks-plays-performance.json", "stocks-watchdog.json")
MARK = "KESTREL-MOVE "
_REMOTE_PATH = re.compile(r"/[A-Za-z0-9._/-]+")

# Runs on the other computer with its own KESTREL code: no single quotes inside (it is passed in single quotes).
_SCRIPT = """
import json, os, sys
from datetime import UTC, datetime
from desk.core.paths import DeskPaths
from desk.ops.backups import DATABASES, create_backup
p = DeskPaths.from_env()
wanted = ("stocks-market", "stocks-research", "stocks-ledger-paper", "stocks-learning-paper")
missing = [k for k in wanted if k not in DATABASES]
if missing:
    print("KESTREL-MOVE " + json.dumps({"error": "old"}))
    sys.exit(3)
found = {k: os.path.getsize(p.db_dir / DATABASES[k]) for k in wanted if (p.db_dir / DATABASES[k]).is_file()}
if not found:
    print("KESTREL-MOVE " + json.dumps({"error": "none", "folder": str(p.db_dir)}))
    sys.exit(4)
if os.environ.get("KESTREL_MOVE_DRY") == "1":
    print("KESTREL-MOVE " + json.dumps({"files": found}))
    sys.exit(0)
manifest = create_backup(p, tuple(found), datetime.now(UTC), cadence="manual")
print("KESTREL-MOVE " + json.dumps({"manifest": str(manifest), "files": found}))
"""
_STOP_REMOTE = "systemctl --user disable --now desk-stocks-dashboard.service desk-stocks-lid.service 2>/dev/null; true"

Run = Callable[..., Any]


def remote_command(dry_run: bool) -> str:
    """The shell command the other computer runs: its own uv and KESTREL checkout make the copies."""
    flag = "KESTREL_MOVE_DRY=1 " if dry_run else ""
    return ("U=$HOME/.local/bin/uv; [ -x \"$U\" ] || U=uv; cd ~/kestrel && "
            f"{flag}\"$U\" run --quiet python -c '{_SCRIPT.strip()}'")


def _size(n: float) -> str:
    for unit in ("bytes", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:,.0f} {unit}" if unit == "bytes" else f"{n:,.1f} {unit}"
        n /= 1024
    return f"{n:,.1f} GB"


def counts(db_dir: Path) -> dict[str, int | None]:
    """What a set of Stocks databases holds, read-only; None where a database or table is missing."""
    asks = {"price sessions": ("stocks-market.db", "SELECT COUNT(*) FROM stk_bar_days WHERE rows > 0"),
            "Plays sessions": ("stocks-research.db", "SELECT COUNT(DISTINCT session) FROM stk_play_days"),
            "tracked plays": ("stocks-research.db", "SELECT COUNT(*) FROM stk_play_selections"),
            "PAPER trades": ("stocks-ledger-paper.db", "SELECT COUNT(*) FROM stk_positions"),
            "Learning positions": ("stocks-learning-paper.db", "SELECT COUNT(*) FROM stk_positions")}
    out: dict[str, int | None] = {}
    for label, (name, sql) in asks.items():
        path = db_dir / name
        out[label] = None
        if path.is_file():
            with suppress(sqlite3.Error), closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True,
                                                                  timeout=5)) as conn:
                out[label] = int(conn.execute(sql).fetchone()[0])
    return out


def _parse(stdout: str) -> dict[str, Any]:
    for line in reversed((stdout or "").splitlines()):
        if line.startswith(MARK):
            try:
                value = json.loads(line[len(MARK):])
            except ValueError:
                break
            if isinstance(value, dict):
                return value
    return {}


def _ask(run: Run, host: str, dry_run: bool) -> dict[str, Any]:
    done = run([*SSH, host, remote_command(dry_run)], capture_output=True, text=True, check=False, timeout=7200)
    found = _parse(done.stdout)
    if found.get("error") == "old":
        raise DeskError(f"KESTREL on {host} is too old to make the copies. Update it there first: "
                        f"ssh {host} '~/.local/bin/kestrel update', then run this again.")
    if found.get("error") == "none":
        raise DeskError(f"{host} has no Stocks databases in {found.get('folder', 'its desk-data/db')}.")
    if done.returncode or not found.get("files"):
        raise DeskError(f"{host} could not make consistent copies of its Stocks databases (exit {done.returncode}). "
                        f"Check there: ssh {host}, then cd ~/kestrel && uv run desk backups create")
    return found


def stop_local(paths: DeskPaths, run: Run, *, timeout: float = 90) -> None:
    """Stop this computer's Stocks: the always-on service, else the app process that holds the dashboard lock; then
    wait until no Stocks worker (collection, evaluation, replay, Learning) holds its lock."""
    if shutil.which("systemctl"):
        run(["systemctl", "--user", "stop", "desk-stocks-dashboard.service"], check=False, capture_output=True,
            text=True, timeout=120)
    if WorkerLock(paths, "stocks-dashboard").held():
        from desk.ops.dashboard_restart import restart_dashboard

        restart_dashboard(paths, "stocks", timeout=timeout)      # stops it; nothing starts it again here
    until = time.monotonic() + timeout
    while busy := [name for name in LOCKS if WorkerLock(paths, name).held()]:
        if time.monotonic() > until:
            raise DeskError(f"Stocks is still running here ({', '.join(busy)}). Nothing was changed. Stop it with "
                            "systemctl --user stop desk-stocks-dashboard.service, then run this again.")
        time.sleep(0.5)


def _set_aside(paths: DeskPaths, folder: Path, filenames: list[str]) -> list[str]:
    """Move this computer's databases (each with its -wal and -shm, after a checkpoint) and derived reports into
    ``folder``. Renames on the same disk: nothing is deleted or rewritten."""
    moved: list[str] = []
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    for name in filenames:
        main = paths.db_dir / name
        if main.is_file():
            with suppress(sqlite3.Error), closing(sqlite3.connect(main, timeout=10)) as conn:
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        for suffix in ("", "-wal", "-shm"):
            path = paths.db_dir / (name + suffix)
            if path.exists():
                os.replace(path, folder / path.name)
                moved.append(path.name)
    for name in DERIVED:
        path = paths.home / name
        if path.exists():
            os.replace(path, folder / name)
            moved.append(name)
    return moved


def _migrate(paths: DeskPaths) -> None:
    from desk.stocks import learning
    from desk.stocks.repo import StocksDatabases

    StocksDatabases.open(paths).close()
    learning.open_book(paths).close()


def _start(paths: DeskPaths, port: int) -> str:
    import sys

    from desk.core.paths import REPO_ROOT
    from desk.ops.launcher import ensure_running, install_command, remote_host, set_remote_host
    from desk.ops.services import keep_stocks_running

    notes = []
    if remote_host(paths) is not None:            # this computer showed the other's Stocks: it runs its own now
        set_remote_host(paths, None)
        install_command(REPO_ROOT, Path.home() / ".local" / "bin", uv=os.environ.get("UV"))
        notes.append("This computer showed the other computer's Stocks; it now runs its own (kestrel runs here).")
    notes += keep_stocks_running(paths, sys.executable, port)
    url = ensure_running("stocks", port, paths)
    return "\n".join([*notes, f"KESTREL Stocks is running here: {url}"])


def move_from(host: str, paths: DeskPaths, *, port: int, dry_run: bool = False, stop_other: bool = False,
              run: Run = subprocess.run, start: Callable[[DeskPaths, int], str] = _start,
              out: Callable[[str], None] = print) -> dict[str, Any]:
    check_host(host)
    if this_computer(host):
        raise DeskError(f"{host} is this computer. Run kestrel move-from on the computer that should keep Stocks, "
                        "naming the other one.")
    probe = run([*SSH, host, "true"], capture_output=True, text=True, check=False, timeout=60)
    if probe.returncode:
        raise DeskError(f"Could not reach {host} over SSH without a password. Check it is on and on this network, "
                        f"then once: ssh-copy-id {host}   (and test with: ssh {host} true)")
    out(f"✓ SSH to {host} works.")
    if dry_run:
        found = _ask(run, host, True)
        out(f"{host} has: " + ", ".join(f"{DATABASES[k]} ({_size(n)})" for k, n in found["files"].items()
                                        if k in KINDS))
        out("Dry run: nothing was copied, stopped or changed here or there. Without --dry-run this would make "
            f"consistent copies on {host}, copy them here, check them, stop Stocks here, keep this computer's "
            f"databases in {paths.backups_dir / 'before-move'}, install the copies and start Stocks again.")
        return {"dry_run": True, "files": found["files"]}
    if stop_other:
        run([*SSH, host, _STOP_REMOTE], capture_output=True, text=True, check=False, timeout=120)
        out(f"✓ Stopped Stocks on {host} (and switched off its always-on service), so the copy is its final state.")
    out(f"Asking {host} for consistent copies (SQLite online backup; the market database takes a few minutes)…")
    found = _ask(run, host, False)
    manifest = str(found.get("manifest", ""))
    if not _REMOTE_PATH.fullmatch(manifest) or not manifest.endswith("/manifest.json"):
        raise DeskError(f"{host} answered with an unexpected backup location; nothing was copied.")
    remote_folder = manifest.rsplit("/", 1)[0]
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    staging = paths.home / "moving-in" / stamp
    staging.mkdir(parents=True, mode=0o700)
    try:
        scp = ["scp", "-q", "-p", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8"]
        if run([*scp, f"{host}:{manifest}", str(staging / "manifest.json")], check=False, timeout=600).returncode:
            raise DeskError(f"Could not copy the backup list from {host}; nothing was changed here.")
        listed = json.loads((staging / "manifest.json").read_text(encoding="utf-8"))
        databases = listed.get("databases") if isinstance(listed, dict) else None
        if not isinstance(databases, dict) or not databases or any(k not in KINDS for k in databases):
            raise DeskError(f"{host}'s copy holds something other than the Stocks databases; nothing was changed.")
        need = sum(int(info.get("bytes", 0)) for info in databases.values())
        if shutil.disk_usage(staging).free < need + 512 * 1024 * 1024:
            raise DeskError(f"Not enough free disk here for {_size(need)} of copies plus 512 MB; nothing was changed.")
        for kind in databases:
            out(f"Copying {DATABASES[kind]} ({_size(int(databases[kind].get('bytes', 0)))})…")
            if run([*scp, f"{host}:{remote_folder}/{DATABASES[kind]}", str(staging / DATABASES[kind])], check=False,
                   timeout=7200).returncode:
                raise DeskError(f"Copying {DATABASES[kind]} from {host} failed; nothing was changed here.")
        try:
            verify_backup(staging / "manifest.json")
        except ValueError as exc:
            raise DeskError(f"The copies from {host} did not pass their checks ({exc}); nothing was changed.") from exc
        if "stocks-ledger-paper" in databases:
            with closing(sqlite3.connect((staging / DATABASES["stocks-ledger-paper"]).as_uri() + "?mode=ro",
                                         uri=True)) as conn:
                row = conn.execute("SELECT value FROM ledger_meta WHERE key='mode'").fetchone()
            if row is None or row[0] != "PAPER":
                raise DeskError(f"{host}'s Stocks ledger is not marked PAPER; nothing was changed.")
        out("✓ Copies checked: checksums, SQLite integrity and foreign keys, PAPER ledger.")
        moving = counts(staging)
        before = counts(paths.db_dir)
        out("Stopping Stocks here…")
        stop_local(paths, run)
        kept = paths.backups_dir / "before-move" / stamp
        set_aside = _set_aside(paths, kept, [DATABASES[k] for k in databases])
        for kind in databases:
            os.replace(staging / DATABASES[kind], paths.db_dir / DATABASES[kind])
        _migrate(paths)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        with suppress(OSError):
            staging.parent.rmdir()
    out(f"✓ Installed. This computer's previous Stocks data is kept in {kept}" if set_aside else
        "✓ Installed (this computer had no Stocks data before).")
    out("Moved here:")
    for label, value in moving.items():
        was = before.get(label)
        out(f"  {label:<20} {'—' if value is None else f'{value:,}'}"
            + ("" if was is None else f"   (this computer had {was:,})"))
    out(start(paths, port))
    out(f"Not copied: secrets.env and the Schwab sign-in. Sign in to Schwab on this computer: kestrel signin (a new "
        f"sign-in ends {host}'s within hours). If keys are missing here, copy the lines you need into "
        "~/.config/desk/secrets.env by hand (chmod 600).")
    out(f"{host} keeps its copy in {remote_folder}." + ("" if stop_other else
        f" Its own Stocks was not stopped: if it is still running, stop it there so the two do not both collect: "
        f"ssh {host} 'systemctl --user disable --now desk-stocks-dashboard.service'"))
    return {"moved": moving, "before": before, "kept": str(kept) if set_aside else None, "remote_copy": remote_folder}


__all__ = ["counts", "move_from", "remote_command", "stop_local"]
