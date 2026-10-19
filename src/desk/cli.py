"""The ``desk`` command.

    desk doctor [--offline] [--json]    health report for this machine and every provider
    desk collect --minutes 60           read-only data collection (M1 acceptance run)
    desk status                         provider health, last collection run, database sizes
    desk config                         effective configuration and its snapshot hash
    desk db check | backup              database integrity / consistent backup copies
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import math
import signal
import sys

from desk import __version__
from desk.core.clock import utc_now
from desk.core.config import load_config
from desk.core.errors import ConfigError, DeskError
from desk.core.logging import setup_logging
from desk.core.paths import DeskPaths
from desk.core.secrets import load_secrets

log = logging.getLogger("desk")


def _positive_minutes(text: str) -> float:
    value = float(text)
    if not math.isfinite(value) or value <= 0:
        raise argparse.ArgumentTypeError("duration must be finite and greater than zero")
    return value


def _bootstrap(verbose: bool = False) -> tuple[DeskPaths, list[str]]:
    paths = DeskPaths.from_env()
    paths.ensure()
    secrets = load_secrets(paths.secrets_file)
    setup_logging(paths.logs_dir, secrets.all_secret_values(), logging.DEBUG if verbose else logging.INFO)
    return paths, secrets.all_secret_values()


def cmd_doctor(args: argparse.Namespace) -> int:
    from desk.ops.doctor import render, render_json, run_doctor

    paths, _ = _bootstrap(args.verbose)
    logging.getLogger().setLevel(logging.ERROR)  # keep the report readable
    report = asyncio.run(run_doctor(paths, offline=args.offline))
    print(render_json(report) if args.json else render(report))
    return 1 if report.failed else 0


def cmd_collect(args: argparse.Namespace) -> int:
    from desk.ops.collector import Collector, render_stats
    from desk.providers.factory import build_suite
    from desk.storage.databases import Databases

    paths, _ = _bootstrap(args.verbose)
    loaded = load_config(paths.config_dir)
    secrets = load_secrets(paths.secrets_file)

    async def main() -> int:
        dbs = Databases.open(paths)
        suite = build_suite(paths, loaded, secrets, dbs)
        collector = Collector(suite, dbs, loaded, discovery=bool(getattr(args, "discovery", False)))
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(NotImplementedError):
                loop.add_signal_handler(sig, collector.stop)
        log.info("collection started", extra={"minutes": args.minutes, "run_id": collector.stats.run_id})
        try:
            stats = await collector.run(args.minutes)
        finally:
            await suite.aclose()
            dbs.close()
        print(render_stats(stats))
        return 1 if stats.unhandled_exceptions else 0

    return asyncio.run(main())


def cmd_discovery_report(args: argparse.Namespace) -> int:
    from desk.ops.acceptance import discovery_run_failures
    from desk.storage.databases import Databases
    from desk.storage.discovery_repo import DiscoveryRepository

    paths, _ = _bootstrap(args.verbose)
    loaded = load_config(paths.config_dir)
    dbs = Databases.open(paths)
    try:
        report = DiscoveryRepository(dbs.market, loaded.settings.discovery).report()
        if args.require_hours is not None:
            row = dbs.market.execute("SELECT * FROM collector_runs WHERE id = ?", (args.run_id,)).fetchone() if args.run_id else (
                dbs.market.execute("SELECT * FROM collector_runs ORDER BY started_at DESC LIMIT 1").fetchone())
            run = None if row is None else {**dict(row), "stats": json.loads(row["stats_json"] or "{}")}
            failures = discovery_run_failures(run, args.require_hours)
            report["acceptance"] = {"required_hours": args.require_hours, "run_id": None if run is None else run["id"],
                                    "passed": not failures, "failures": failures}
            print(json.dumps(report, indent=2))
            return 1 if failures or report["orphans"] else 0
        print(json.dumps(report, indent=2))
        return 1 if report["orphans"] else 0
    finally:
        dbs.close()


def cmd_console(args: argparse.Namespace) -> int:
    from desk.ops.console import serve

    paths, _ = _bootstrap(args.verbose)
    serve(paths, args.port)
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    from desk.providers.health import HealthTracker
    from desk.storage.databases import Databases
    from desk.storage.market_repo import MarketRepository

    paths, _ = _bootstrap(args.verbose)
    loaded = load_config(paths.config_dir)
    dbs = Databases.open(paths)
    try:
        print(f"Mode: {loaded.settings.desk.mode.value}   config snapshot: {loaded.snapshot_hash[:12]}")
        print("\nProvider health:")
        records = HealthTracker(dbs.runtime, loaded.settings.health).all()
        if not records:
            print("  (no requests made yet)")
        for rec in records:
            print(f"  {rec.provider:<15} {rec.state.value:<9} last ok {rec.last_success_at or '-'}"
                  f"{'   last error: ' + rec.last_error if rec.last_error else ''}")
        last = dbs.market.execute("SELECT id, started_at, ended_at FROM collector_runs ORDER BY started_at DESC"
                                  " LIMIT 1").fetchone()
        print("\nLast collection run:", dict(last) if last else "none")
        print("\nDatabase rows:", MarketRepository(dbs.market).counts())
    finally:
        dbs.close()
    return 0


def cmd_config(args: argparse.Namespace) -> int:
    paths, _ = _bootstrap(args.verbose)
    loaded = load_config(paths.config_dir)
    print(json.dumps(loaded.settings.model_dump(mode="json"), indent=2))
    print(f"\nsnapshot hash: {loaded.snapshot_hash}\nceilings hash: {loaded.ceilings_hash}\nsource: {loaded.source}")
    return 0


def cmd_db(args: argparse.Namespace) -> int:
    from desk.storage.databases import Databases
    from desk.storage.db import backup

    paths, _ = _bootstrap(args.verbose)
    dbs = Databases.open(paths)
    try:
        if args.action == "check":
            bad = 0
            for name, info in dbs.health().items():
                ok = info["integrity"] == "ok" and info["journal_mode"] == "wal"
                bad += not ok
                print(f"  {'✓' if ok else '✗'} {name:<13} integrity={info['integrity']} journal={info['journal_mode']}")
            return 1 if bad else 0
        stamp = utc_now().strftime("%Y%m%dT%H%M%SZ")
        for name, conn in dbs.all().items():
            if name == "runtime":
                continue  # rebuildable coordination state
            target = backup(conn, paths.backups_dir / stamp / f"{name}.db")
            print(f"  backed up {name} -> {target}")
        return 0
    finally:
        dbs.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="desk", description="KESTREL Solana quant desk")
    parser.add_argument("--version", action="version", version=f"desk {__version__}")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("doctor", help="check this machine and every data provider")
    p.add_argument("--offline", action="store_true", help="skip network checks")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("collect", help="read-only data collection")
    p.add_argument("--minutes", type=_positive_minutes, default=60.0)
    p.set_defaults(func=cmd_collect)

    p = sub.add_parser("discover", help="read-only discovery and fail-closed safety evaluation (M2)")
    p.add_argument("--minutes", type=_positive_minutes, default=1440.0)
    p.set_defaults(func=cmd_collect, discovery=True)

    p = sub.add_parser("discovery-report", help="candidate outcomes, rejection reasons and audit completeness")
    p.add_argument("--run-id", help="run to check; defaults to the latest run")
    p.add_argument("--require-hours", type=_positive_minutes, help="fail unless a completed M2 run meets this duration")
    p.set_defaults(func=cmd_discovery_report)

    p = sub.add_parser("console", help="local read-only M2 status console")
    p.add_argument("--port", type=int, default=8765)
    p.set_defaults(func=cmd_console)

    sub.add_parser("status", help="provider health and database summary").set_defaults(func=cmd_status)
    sub.add_parser("config", help="show effective configuration").set_defaults(func=cmd_config)

    p = sub.add_parser("db", help="database maintenance")
    p.add_argument("action", choices=["check", "backup"])
    p.set_defaults(func=cmd_db)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except ConfigError as exc:
        print(f"\nConfiguration problem — the desk will not start:\n{exc}", file=sys.stderr)
        return 2
    except DeskError as exc:
        print(f"\nError: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
