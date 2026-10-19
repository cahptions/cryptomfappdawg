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
from typing import Any

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
        paper = bool(getattr(args, "paper", False))
        if paper:
            _ensure_guardian(dbs, loaded)
        collector = Collector(suite, dbs, loaded, discovery=bool(getattr(args, "discovery", False)), paper=paper)
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


def _ensure_guardian(dbs: Any, loaded: Any) -> None:
    """Start the independent guardian as its own process (own session) unless one is already supervising."""
    import subprocess

    from desk.paper.engine import heartbeat_age

    age = heartbeat_age(dbs.runtime, "guardian", utc_now())
    if age is not None and 0 <= age <= loaded.settings.paper.guardian_heartbeat_max_age_seconds:
        return
    subprocess.Popen([sys.executable, "-m", "desk", "guardian"], start_new_session=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print("  started the independent guardian process (log: ~/desk-data/logs/desk.jsonl)")


def cmd_guardian(args: argparse.Namespace) -> int:
    from desk.paper.guardian import Guardian
    from desk.providers.factory import build_suite
    from desk.storage.databases import Databases

    paths, _ = _bootstrap(args.verbose)
    loaded = load_config(paths.config_dir)
    secrets = load_secrets(paths.secrets_file)

    async def main() -> int:
        dbs = Databases.open(paths)
        suite = build_suite(paths, loaded, secrets, dbs)
        guardian = Guardian(suite, dbs, loaded)
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(NotImplementedError):
                loop.add_signal_handler(sig, guardian.stop)
        log.info("guardian started", extra={"minutes": args.minutes})
        try:
            stats = await guardian.run(args.minutes)
        finally:
            await suite.aclose()
            dbs.close()
        print(json.dumps({"guardian": stats}, indent=1))
        return 1 if stats["unhandled_exceptions"] else 0

    return asyncio.run(main())


def cmd_stop(args: argparse.Namespace) -> int:
    from desk.paper.ledger import PaperLedger
    from desk.storage.databases import Databases

    paths, _ = _bootstrap(args.verbose)
    (paths.home / "STOP").write_text(f"stopped {utc_now().isoformat()}\n")
    dbs = Databases.open(paths)
    try:
        PaperLedger(dbs.paper_ledger).halt("STOP", utc_now())
    finally:
        dbs.close()
    print("  new entries stopped. Open positions stay supervised by the guardian. Clear with: desk resume")
    return 0


def cmd_resume(args: argparse.Namespace) -> int:
    from desk.paper.ledger import PaperLedger
    from desk.storage.databases import Databases

    paths, _ = _bootstrap(args.verbose)
    if not args.yes and input("Type RESUME to allow new PAPER entries again: ").strip() != "RESUME":
        print("  not resumed")
        return 1
    (paths.home / "STOP").unlink(missing_ok=True)
    dbs = Databases.open(paths)
    try:
        PaperLedger(dbs.paper_ledger).resume(utc_now())
    finally:
        dbs.close()
    print("  entries allowed again")
    return 0


def cmd_paper_report(args: argparse.Namespace) -> int:
    from desk.paper.report import paper_report
    from desk.storage.databases import Databases

    paths, _ = _bootstrap(args.verbose)
    loaded = load_config(paths.config_dir)
    dbs = Databases.open(paths)
    try:
        report = paper_report(dbs, loaded, utc_now())
    finally:
        dbs.close()
    print(json.dumps(report, indent=1, sort_keys=True, default=str))
    reconciliation = report.get("reconciliation") or {}
    return 1 if reconciliation.get("difference_lamports") else 0


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


def cmd_shadow(args: argparse.Namespace) -> int:
    from desk.core.config import Mode
    from desk.ops.shadow import ShadowRunner, render_shadow
    from desk.storage.databases import Databases

    paths, _ = _bootstrap(args.verbose)
    if load_config(paths.config_dir).settings.desk.mode is not Mode.SHADOW:
        raise ConfigError("M3 research requires SHADOW mode")
    dbs = Databases.open(paths)
    runner = ShadowRunner(dbs.market, seed=args.seed, interval_seconds=args.interval,
                          max_candidates=args.max_candidates)
    handlers = {sig: signal.signal(sig, lambda *_: runner.stop()) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        print("SHADOW research started. Reading existing collection data; no provider calls or positions.", flush=True)
        print(render_shadow(runner.run(args.minutes)))
        return 0
    finally:
        for sig, handler in handlers.items():
            signal.signal(sig, handler)
        dbs.close()


def cmd_shadow_report(args: argparse.Namespace) -> int:
    from desk.storage.databases import Databases
    from desk.storage.shadow_repo import ShadowRepository

    paths, _ = _bootstrap(args.verbose)
    dbs = Databases.open(paths)
    try:
        print(json.dumps(ShadowRepository(dbs.market).report(args.run_id), indent=2))
        return 0
    finally:
        dbs.close()


def cmd_replay(args: argparse.Namespace) -> int:
    from pathlib import Path

    from desk.contracts.strategy import StrategySpec
    from desk.ops.replay import replay_run
    from desk.storage.databases import Databases

    paths, _ = _bootstrap(args.verbose)
    dbs = Databases.open(paths)
    try:
        specs = None
        if args.specs_file:
            raw = json.loads(Path(args.specs_file).read_text(encoding="utf-8"))
            if not isinstance(raw, list):
                raise ConfigError("replay specs file must contain an array of complete strategy specifications")
            specs = tuple(StrategySpec.model_validate(spec) for spec in raw)
        report = replay_run(dbs.market, dbs.research, args.run_id, specs=specs, require_hours=args.require_hours)
        print(json.dumps(report, indent=2))
        return 0 if report["passed"] else 1
    except (ValueError, OSError) as exc:
        raise ConfigError(str(exc)) from exc
    finally:
        dbs.close()


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

    p = sub.add_parser("paper", help="PAPER learning book: discovery + quote-filled paper entries (M5); no wallet")
    p.add_argument("--minutes", type=_positive_minutes, default=1440.0)
    p.set_defaults(func=cmd_collect, discovery=True, paper=True)

    p = sub.add_parser("guardian", help="independent PAPER guardian: marks and exits open positions")
    p.add_argument("--minutes", type=_positive_minutes, default=None, help="run time; default until idle")
    p.set_defaults(func=cmd_guardian)

    sub.add_parser("stop", help="kill switch: stop new PAPER entries (positions stay supervised)").set_defaults(func=cmd_stop)
    p = sub.add_parser("resume", help="clear the kill switch / halt (typed confirmation)")
    p.add_argument("--yes", action="store_true", help="skip the typed confirmation")
    p.set_defaults(func=cmd_resume)

    sub.add_parser("paper-report", help="PAPER results, reconciliation and the $100-account replica").set_defaults(
        func=cmd_paper_report)

    p = sub.add_parser("discovery-report", help="candidate outcomes, rejection reasons and audit completeness")
    p.add_argument("--run-id", help="run to check; defaults to the latest run")
    p.add_argument("--require-hours", type=_positive_minutes, help="fail unless a completed M2 run meets this duration")
    p.set_defaults(func=cmd_discovery_report)

    p = sub.add_parser("console", help="local read-only M2 status console")
    p.add_argument("--port", type=int, default=8765)
    p.set_defaults(func=cmd_console)

    p = sub.add_parser("shadow", help="record deterministic research decisions from existing data; no positions")
    p.add_argument("--minutes", type=_positive_minutes, default=360.0)
    p.add_argument("--interval", type=_positive_minutes, default=30.0, help="seconds between bounded evaluation cycles")
    p.add_argument("--max-candidates", type=int, choices=range(1, 1001), default=100, metavar="1..1000")
    p.add_argument("--seed", type=int, default=0)
    p.set_defaults(func=cmd_shadow)

    p = sub.add_parser("shadow-report", help="recorded SHADOW sessions and signal counts")
    p.add_argument("--run-id")
    p.set_defaults(func=cmd_shadow_report)

    p = sub.add_parser("replay", help="reproduce recorded SHADOW decisions or compare copied strategy specifications")
    p.add_argument("--run-id")
    p.add_argument("--require-hours", type=_positive_minutes, help="check actual completed forward-session duration")
    p.add_argument("--specs-file", help="JSON array of alternative specs; recorded source inputs remain unchanged")
    p.set_defaults(func=cmd_replay)

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
