"""Read-only diagnostic for the two KESTREL Learning PAPER accounts. Standard library only."""

import json
import os
import platform
import shutil
import sqlite3
import subprocess
from contextlib import closing
from datetime import datetime
from http.client import HTTPConnection, HTTPException
from pathlib import Path


def preference(home, filename):
    path = home / filename
    if not path.exists():
        return True
    try:
        enabled = json.loads(path.read_text()).get("enabled")
        return enabled if isinstance(enabled, bool) else "unreadable"
    except (OSError, ValueError, AttributeError):
        return "unreadable"


def diagnostics(home):
    git = shutil.which("git")
    revision = None
    if git:
        revision = subprocess.run(  # noqa: S603 - the executable is resolved above and all arguments are fixed
            [git, "rev-parse", "--short", "HEAD"], capture_output=True, text=True
        ).stdout.strip()
    machine = platform.freedesktop_os_release()
    mem = Path("/proc/meminfo")
    total = next((ln.split()[1] for ln in mem.read_text().splitlines() if ln.startswith("MemTotal:")), None)
    out = {
        "local_time": datetime.now().astimezone().isoformat(timespec="seconds"),
        "os": machine.get("PRETTY_NAME"),
        "kernel": platform.release(),
        "architecture": platform.machine(),
        "cpu_threads": os.cpu_count(),
        "ram_gib": round(int(total) / 1024**2, 1) if total else None,
        "installed_git_head": revision or None,
        "paper_enabled": preference(home, "stocks-automation.json"),
        "learning_enabled": preference(home, "stocks-learning-preferences.json"),
        "stop_active": (home / "STOP").exists(),
    }
    with closing(HTTPConnection("127.0.0.1", 8767, timeout=2)) as client:
        try:
            client.request("GET", "/api/stocks/runtime")
            response = client.getresponse()
            runtime = json.loads(response.read(8192)) if response.status == 200 else {}
            out["dashboard"] = {k: runtime[k] for k in ("running_revision", "installed_revision", "restart_required")
                                if k in runtime}
        except (OSError, HTTPException, ValueError):
            out["dashboard"] = "Local Stocks dashboard unavailable at port 8767"
    db = home / "db" / "stocks-learning-paper.db"
    if not db.is_file():
        return {**out, "learning_database": "Missing under DESK_HOME (default ~/desk-data)"}
    out["data_disk_free_gib"] = round(shutil.disk_usage(home).free / 1024**3, 1)
    with closing(sqlite3.connect(db.as_uri() + "?mode=ro", uri=True, timeout=3)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN")
        keys = ("refilled_2026_11_16", "progress", "collection", "collection_at", "retail:reference_1k",
                "retail:reference_5k")
        saved = {key: json.loads(value) for key, value in conn.execute(
            "SELECT key,value FROM stk_learning_state WHERE key IN (?,?,?,?,?,?)", keys
        )}
        out.update(
            refill_check=saved.get(keys[0]),
            last_tick=saved.get("progress"),
            collection={k: v for k, v in saved.get("collection", {}).items()
                        if k in ("phase", "contracts_received", "held_legs", "held_legs_quoted", "errors")},
            last_collection_attempt=saved.get("collection_at"),
            accounts={},
        )
        refill_at = (out["refill_check"] or {}).get("at")
        for account in ("reference_1k", "reference_5k"):
            cash = conn.execute("SELECT COALESCE(SUM(amount_micros),0)/1000000.0 FROM stk_cash WHERE account=?",
                                (account,)).fetchone()[0]
            deposits = conn.execute("SELECT at,amount_micros/1000000.0 amount FROM stk_cash "
                                    "WHERE account=? AND reason='DEPOSIT' ORDER BY id", (account,)).fetchall()
            fills = conn.execute(
                "SELECT COUNT(*) count, MAX(opened_at) last_fill, "
                "COALESCE(SUM(entry_cost_micros+entry_fees_micros),0)/1000000.0 entry_debit "
                "FROM stk_positions WHERE account=? AND (? IS NULL OR opened_at>=?)",
                (account, refill_at, refill_at),
            ).fetchone()
            orders = conn.execute("SELECT created_at,resolved_at,state,note FROM stk_pending WHERE account=? "
                                  "ORDER BY id DESC LIMIT 5", (account,)).fetchall()
            open_count, marked = conn.execute(
                "SELECT COUNT(*),COALESCE(SUM(last_value_micros),0)/1000000.0 FROM stk_positions "
                "WHERE account=? AND state='OPEN'", (account,)
            ).fetchone()
            out["accounts"][account] = {
                "cash": cash, "equity": round(cash + marked, 6), "open_positions": open_count,
                "deposits": [dict(row) for row in deposits],
                "entries_since_refill_check" if refill_at else "all_entries": dict(fills),
                "latest_orders": [dict(row) for row in orders],
                "policy": {k: v for k, v in saved.get(f"retail:{account}", {}).items()
                           if k in ("phase", "mode", "asof", "budget", "cash")},
            }
    return out


if __name__ == "__main__":
    data_home = Path(os.environ.get("DESK_HOME", "~/desk-data")).expanduser().resolve()
    print(json.dumps(diagnostics(data_home), indent=2))
