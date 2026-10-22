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
    paths, _ = _bootstrap(args.verbose)
    return _run_collector(paths, load_config(paths.config_dir), load_secrets(paths.secrets_file), args)


def _run_collector(paths: Any, loaded: Any, secrets: Any, args: argparse.Namespace, *, live: bool = False) -> int:
    from desk.core.worker_lock import WorkerLock
    from desk.ops.collector import Collector, render_stats
    from desk.providers.factory import build_suite
    from desk.storage.databases import Databases

    async def main() -> int:
        dbs = Databases.open(paths)
        suite = build_suite(paths, loaded, secrets, dbs)
        paper = bool(getattr(args, "paper", False))
        if paper:
            _ensure_guardian(dbs, loaded)
        live_book = live_conn = engine = None
        if live:
            from desk.live.runtime import live_engine, open_book

            _ensure_guardian(dbs, loaded, live=True)
            live_book, live_conn = open_book(paths)
            engine = live_engine(paths, loaded, suite, dbs, live_book)
        collector = Collector(suite, dbs, loaded, discovery=bool(getattr(args, "discovery", False)), paper=paper,
                              live_engine=engine)
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(NotImplementedError):
                loop.add_signal_handler(sig, collector.stop)
        log.info("collection started", extra={"minutes": args.minutes, "run_id": collector.stats.run_id, "live": live})
        try:
            stats = await collector.run(args.minutes)
        finally:
            await suite.aclose()
            if live_conn is not None:
                live_conn.close()
            dbs.close()
        print(render_stats(stats))
        return 1 if stats.unhandled_exceptions else 0

    with WorkerLock(paths):
        return asyncio.run(main())


def _ensure_guardian(dbs: Any, loaded: Any, *, live: bool = False) -> None:
    """Start an independent guardian as its own process (own session) unless one is already supervising."""
    import subprocess

    from desk.live.gates import LIVE_GUARDIAN
    from desk.paper.engine import heartbeat_age

    age = heartbeat_age(dbs.runtime, LIVE_GUARDIAN if live else "guardian", utc_now())
    if age is not None and 0 <= age <= loaded.settings.paper.guardian_heartbeat_max_age_seconds:
        return
    if live:
        subprocess.Popen([sys.executable, "-m", "desk", "live", "guardian"], start_new_session=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        subprocess.Popen([sys.executable, "-m", "desk", "guardian"], start_new_session=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(f"  started the independent {'LIVE ' if live else ''}guardian process (log: ~/desk-data/logs/desk.jsonl)")


def cmd_guardian(args: argparse.Namespace) -> int:
    from desk.core.worker_lock import WorkerLock
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

    with WorkerLock(paths, "guardian"):
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


def cmd_confidence(args: argparse.Namespace) -> int:
    from desk.stats.evidence import confidence_report, render
    from desk.storage.databases import Databases

    paths, _ = _bootstrap(args.verbose)
    dbs = Databases.open(paths)
    try:
        report = confidence_report(dbs.paper_ledger, dbs.research, utc_now(), seed=args.seed)
    finally:
        dbs.close()
    print(json.dumps(report, indent=1, default=str) if args.json else render(report))
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    """`desk report readiness`: the M9 report, written as Markdown from recorded data only (PLAN §14.3)."""
    from pathlib import Path

    from desk.ops.readiness import default_path, readiness_report, record
    from desk.storage.databases import Databases

    paths, _ = _bootstrap(args.verbose)
    loaded = load_config(paths.config_dir)
    output = Path(args.output).expanduser() if args.output else default_path(paths)
    dbs = Databases.open(paths)
    try:
        markdown, summary = readiness_report(dbs, loaded)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(markdown, encoding="utf-8")
        record(dbs, summary, str(output), utc_now())
    finally:
        dbs.close()
    print(f"  verdict: {summary['verdict']}")
    print(f"  as of {summary['as_of'] or '—'} (latest recorded PAPER event); sha256 {summary['markdown_sha256'][:16]}…")
    print(f"  written to {output}. The mode and every LIVE gate are unchanged.")
    return 0


def cmd_champion(args: argparse.Namespace) -> int:
    from desk.stats import champion
    from desk.storage.databases import Databases

    paths, _ = _bootstrap(args.verbose)
    dbs = Databases.open(paths)
    try:
        if args.action == "status":
            print(json.dumps({"champion": champion.current_champion(dbs.research),
                              "history": champion.history(dbs.research)}, indent=1))
            return 0
        if args.action == "evaluate":
            print(json.dumps(champion.record_evaluation(dbs.paper_ledger, dbs.research, utc_now()), indent=1, default=str))
            return 0
        if not args.version:
            print("  usage: desk champion approve VERSION")
            return 2
        typed = input(f"Type PROMOTE {args.version} to make it the champion: ").strip()
        approval = champion.approve(dbs.research, args.version, typed, utc_now(), note=args.note or "")
        print(f"  champion is now {args.version} (approval {approval})")
        return 0
    except ValueError as exc:
        print(f"  refused: {exc}")
        return 1
    finally:
        dbs.close()


def cmd_model(args: argparse.Namespace) -> int:
    from desk.models.research import benchmark, score_decisions
    from desk.storage.databases import Databases

    paths, _ = _bootstrap(args.verbose)
    dbs = Databases.open(paths)
    try:
        if args.action == "score":
            print(json.dumps(score_decisions(dbs.paper_ledger, dbs.research, utc_now()), indent=1))
        else:
            print(json.dumps(benchmark(dbs.paper_ledger, dbs.research), indent=1, default=str))
        return 0
    finally:
        dbs.close()


def cmd_council(args: argparse.Namespace) -> int:
    """Evidence council (research/council): two record-only advisors with zero influence."""
    from desk.ops.council import council_report, rebuild_benchmark, run_council
    from desk.storage.databases import Databases

    paths, _ = _bootstrap(args.verbose)
    loaded = load_config(paths.config_dir)
    if args.action == "run":
        stats = run_council(paths, loaded, limit=args.limit)
        print(json.dumps(stats, indent=1, default=str))
        return 0
    dbs = Databases.open(paths)
    try:
        if args.action == "rebuild-benchmark":
            print(json.dumps(rebuild_benchmark(dbs, loaded, utc_now()), indent=1))
            return 0
        report = council_report(dbs)
    finally:
        dbs.close()
    if args.json:
        print(json.dumps(report, indent=1, default=str))
        return 0
    coverage, diagnostic = report["coverage"], report["filter_diagnostic"]
    print("\nEvidence council — research records only; nothing here changes a trade (influence NONE)\n")
    print(f"  decisions advised {coverage['decisions_advised']}, in the universe {coverage['decisions_in_universe']},"
          f" C1 with full 76 h history {coverage['c1_with_full_history']}")
    print(f"  SOL/USD benchmark minutes {coverage['benchmark_minutes']}"
          f" ({coverage['benchmark_first'] or '-'} → {coverage['benchmark_last'] or '-'})")
    for advisor, detail in report["stances"].items():
        print(f"  {advisor}: {detail['all_time'] or 'no records yet'}; top reasons {detail['top_reasons'] or '-'}")
    print(f"  filter diagnostic: {diagnostic['verdict']}")
    return 0


def _cmd_rh(args: argparse.Namespace) -> int:
    from desk.rh.cli import cmd_rh

    _bootstrap(args.verbose)
    return cmd_rh(args)


def cmd_counterfactuals(args: argparse.Namespace) -> int:
    from desk.discovery.counterfactual import summary
    from desk.storage.databases import Databases

    paths, _ = _bootstrap(args.verbose)
    dbs = Databases.open(paths)
    try:
        print(json.dumps(summary(dbs.market), indent=1))
        return 0
    finally:
        dbs.close()


def cmd_opportunities(args: argparse.Namespace) -> int:
    from desk.ops.opportunities import pass_once, run

    paths = DeskPaths.from_env()
    venues = ["robinhood", "solana"] if args.venue == "both" else [args.venue]
    if args.action == "run":
        records = asyncio.run(run(paths, venues, minutes=args.minutes, limit=args.limit, outcomes=args.outcomes))
    else:
        records = [pass_once(paths, venue, ingest=False) for venue in venues]
    if args.json:
        print(json.dumps(records, indent=1))
        return 0
    for item in records:
        print(f"\n{item['venue']} opportunity research — SHADOW, trading influence NONE")
        if not item["available"]:
            print("  " + item["reason"])
            continue
        report = item["report"]
        print(f"  {report['cohorts']} observed instruments/pools; "
              f"{report['cohorts_with_comparable_points']} with comparable research points")
        print(f"  source backlog (approx rows): {report['source_rows_pending_approx']}; "
              f"matured outcomes: {report['matured_outcomes']}")
        print("  " + report["verdict"])
        for name, horizons in report["groups"].items():
            row = horizons["3600"]
            print(f"  {name} at 1h: {row['matured']} matured, {row['charted']} chart marks; "
                  f"quote coverage {row['quote_status']}")
    return 0


def cmd_live(args: argparse.Namespace) -> int:
    paths, _ = _bootstrap(args.verbose)
    action = args.action
    if action == "keygen":
        from desk.signer.main import keygen

        return keygen(paths)
    loaded = load_config(paths.config_dir)
    secrets = load_secrets(paths.secrets_file)
    if action == "arm":
        from desk.signer.main import arm

        return arm(paths, loaded, secrets)
    if action in ("run", "guardian"):
        from desk.live.runtime import start_problems

        problems = start_problems(paths, loaded)
        if problems and action == "run":
            print("  LIVE run not started:\n" + "\n".join(f"    - {p}" for p in problems))
            return 2
        if action == "run":
            args.discovery, args.paper = True, True       # LIVE always runs beside PAPER, which keeps learning
            args.minutes = args.minutes or 1440.0
            return _run_collector(paths, loaded, secrets, args, live=True)
        return _live_guardian(paths, loaded, secrets, args)
    if action == "resume":
        return _live_resume(paths, args)
    if action in ("disarm", "withdraw"):
        return asyncio.run(_live_socket_action(paths, loaded, args))
    if action == "validate":
        return asyncio.run(_live_validate(loaded, secrets, args))
    from desk.storage.databases import Databases

    dbs = Databases.open(paths)
    try:
        if action == "approve":
            if not args.version:
                print("  usage: desk live approve STRATEGY_VERSION")
                return 2
            typed = input(f"Type APPROVE LIVE {args.version} to approve this exact version for LIVE review: ").strip()
            if typed != f"APPROVE LIVE {args.version}":
                print("  not approved")
                return 1
            from desk.core.hashing import canonical_json
            from desk.core.ids import new_id

            dbs.research.execute("INSERT INTO approvals(id, created_at, kind, subject, confirmation, payload_json)"
                                 " VALUES (?, ?, 'LIVE_STRATEGY', ?, ?, ?)",
                                 (new_id("appr"), utc_now().isoformat(), args.version, typed, canonical_json({})))
            print("  approval recorded. It is one of seven gates; it does not arm anything.")
            return 0
        return asyncio.run(_live_status(paths, loaded, dbs, args.version))
    finally:
        dbs.close()


def _live_guardian(paths: Any, loaded: Any, secrets: Any, args: argparse.Namespace) -> int:
    """Supervise and exit LIVE positions through the signer. It runs even when entries are not allowed."""
    from desk.core.worker_lock import WorkerLock
    from desk.live.guardian import LiveGuardian
    from desk.live.runtime import open_book, signer_link
    from desk.providers.factory import build_suite
    from desk.storage.databases import Databases

    async def main() -> int:
        dbs = Databases.open(paths)
        book, conn = open_book(paths)
        suite = build_suite(paths, loaded, secrets, dbs)
        guardian = LiveGuardian(suite, dbs, loaded, book, signer_link(paths))
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(NotImplementedError):
                loop.add_signal_handler(sig, guardian.stop)
        log.info("LIVE guardian started", extra={"minutes": args.minutes})
        try:
            stats = await guardian.run(args.minutes)
        finally:
            await suite.aclose()
            conn.close()
            dbs.close()
        print(json.dumps({"live_guardian": stats}, indent=1))
        return 1 if stats["unhandled_exceptions"] else 0

    with WorkerLock(paths, "guardian-live"):
        return asyncio.run(main())


def _live_resume(paths: Any, args: argparse.Namespace) -> int:
    from desk.live.runtime import open_book

    book, conn = open_book(paths)
    try:
        halted = book.halted()
        if halted is None:
            print("  LIVE entries are not halted")
            return 0
        if not args.yes and input(f"LIVE entries are halted ({halted}). Type RESUME LIVE to allow them again: "
                                  ).strip() != "RESUME LIVE":
            print("  not resumed")
            return 1
        book.resume(utc_now())
    finally:
        conn.close()
    print("  LIVE entries allowed again (the STOP file, if any, is cleared separately with: desk resume)")
    return 0


async def _live_status(paths: Any, loaded: Any, dbs: Any, version: str | None) -> int:
    from desk.live.gates import LIVE_GUARDIAN, capital_ceiling, evaluate_gates, live_allowed, live_halt, status_context
    from desk.paper.engine import heartbeat_age
    from desk.signer.main import public_address, socket_path
    from desk.signer.server import request
    from desk.stats.champion import current_champion

    unlocked = False
    if socket_path(paths).exists():
        with contextlib.suppress(OSError, TimeoutError, ValueError):
            unlocked = bool((await request(socket_path(paths), {"op": "status"}, timeout=5)).get("unlocked"))
    ceiling = capital_ceiling(paths)
    target = version or current_champion(dbs.research) or "none"
    age = heartbeat_age(dbs.runtime, LIVE_GUARDIAN, utc_now())
    ctx = status_context(dbs.research, loaded, target, signer_unlocked=unlocked, ceiling=ceiling,
                         halted=live_halt(paths), guardian_fresh=age is not None and 0 <= age <= 20)
    gates = evaluate_gates(ctx)
    print(f"\nLIVE gates for strategy version {target} (hot wallet: {public_address(paths) or 'not created'})\n")
    for gate in gates:
        print(f"  {'✓' if gate.ok else '✗'} {gate.number}. {gate.name:<20} {gate.detail}")
    print(f"\n  LIVE {'would be allowed' if live_allowed(gates) else 'is NOT allowed'}. This screen can only show"
          " gates; nothing here arms LIVE.")
    return 0


async def _live_socket_action(paths: Any, loaded: Any, args: argparse.Namespace) -> int:
    from datetime import timedelta
    from decimal import Decimal

    from desk.core.ids import new_id
    from desk.core.units import sol_to_lamports
    from desk.signer.main import socket_path
    from desk.signer.server import request

    if args.action == "disarm":
        answer = await request(socket_path(paths), {"op": "disarm"}, timeout=10)
        print(f"  {answer}")
        return 0
    destination = loaded.settings.live.withdraw_address
    if not destination or args.sol is None:
        print("  set live.withdraw_address in config first, then: desk live withdraw --sol AMOUNT")
        return 2
    lamports = sol_to_lamports(Decimal(args.sol))
    phrase = f"WITHDRAW {lamports} LAMPORTS TO {destination}"
    typed = input(f"Type exactly: {phrase}\n> ").strip()
    if typed != phrase:
        print("  not withdrawn")
        return 1
    answer = await request(socket_path(paths), {"op": "withdraw", "withdrawal": {
        "intent_id": new_id("wd"), "destination": destination, "lamports": lamports, "confirmation": typed,
        "expires_at": (utc_now() + timedelta(seconds=25)).isoformat()}}, timeout=180)
    print(json.dumps(answer, indent=1))
    return 0 if answer.get("ok") else 1


async def _live_validate(loaded: Any, secrets: Any, args: argparse.Namespace) -> int:
    from decimal import Decimal

    import httpx

    from desk.core.units import sol_to_lamports
    from desk.live.validate import validate_path
    from desk.signer.net import JupiterSwap, SolanaRpc

    async with httpx.AsyncClient(timeout=20.0) as client:
        result = await validate_path(JupiterSwap(client, secrets.get("JUPITER_API_KEY")),
                                     SolanaRpc(client, secrets.get("SOLANA_RPC_URL") or "https://api.mainnet-beta.solana.com"),
                                     taker=args.taker, output_mint=args.mint,
                                     amount_lamports=sol_to_lamports(Decimal(args.sol or "0.01")),
                                     max_priority_fee_lamports=loaded.settings.live.max_priority_fee_lamports)
    print(json.dumps(result, indent=1, default=str))
    return 0 if result["ok"] else 1


def cmd_liquidate(args: argparse.Namespace) -> int:
    from desk.paper.ledger import PaperLedger
    from desk.storage.databases import Databases

    paths, _ = _bootstrap(args.verbose)
    if not args.all:
        print("  usage: desk liquidate --all [--live]")
        return 2
    if input("Type LIQUIDATE ALL to stop entries and attempt to exit every position: ").strip() != "LIQUIDATE ALL":
        print("  nothing done")
        return 1
    (paths.home / "STOP").write_text(f"liquidation {utc_now().isoformat()}\n")
    dbs = Databases.open(paths)
    try:
        if not args.live:
            ledger = PaperLedger(dbs.paper_ledger)
            ledger.halt("STOP", utc_now())
            rows = ledger.open_positions()
            for row in rows:
                if row["state"] == "OPEN":
                    ledger.set_state(row["id"], "EXITING", utc_now(), {"reason": "MANUAL_LIQUIDATION",
                                                                       "attempts": row["exit_attempts"]})
            print(f"  entries stopped; {len(rows)} PAPER positions handed to the guardian for exit (see desk paper-report)")
            return 0
    finally:
        dbs.close()
    return asyncio.run(_liquidate_live(paths))


async def _liquidate_live(paths: Any) -> int:
    import fcntl

    from desk.live.guardian import LiveGuardian
    from desk.live.liquidate import liquidate_all
    from desk.live.runtime import open_book, signer_link
    from desk.providers.factory import build_suite
    from desk.storage.databases import Databases

    loaded = load_config(paths.config_dir)
    dbs = Databases.open(paths)
    book, conn = open_book(paths)
    try:
        with (paths.home / LiveGuardian.lock_name).open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                # the running LIVE guardian owns exits: hand every position to it rather than racing it
                book.halt("STOP", utc_now())
                rows = book.open_positions()
                for row in rows:
                    if row["state"] == "OPEN":
                        book.set_state(row["id"], "EXITING", utc_now(), {"reason": "EMERGENCY_LIQUIDATION",
                                                                         "attempts": row["exit_attempts"]})
                print(f"  entries stopped; {len(rows)} LIVE positions handed to the running LIVE guardian for exit")
                return 0
            suite = build_suite(paths, loaded, load_secrets(paths.secrets_file), dbs)
            try:
                guardian = LiveGuardian(suite, dbs, loaded, book, signer_link(paths))
                report = await liquidate_all(guardian, stop_entries=lambda: book.halt("STOP", utc_now()))
            finally:
                await suite.aclose()
    finally:
        conn.close()
        dbs.close()
    for item in report:
        print(f"  {item['result']:<10} {item['position_id']}  {item.get('reason') or ''}")
    print("  positions not CLOSED remain open and supervised by the LIVE guardian.")
    return 0 if all(r["result"] == "CLOSED" for r in report) else 1


def cmd_discovery_report(args: argparse.Namespace) -> int:
    from desk.ops.acceptance import discovery_run_failures, discovery_test
    from desk.storage.databases import Databases
    from desk.storage.discovery_repo import DiscoveryRepository

    paths, _ = _bootstrap(args.verbose)
    loaded = load_config(paths.config_dir)
    dbs = Databases.open(paths)
    try:
        report = DiscoveryRepository(dbs.market, loaded.settings.discovery).report()
        if args.require_hours is not None and not args.continuous and args.run_id is None:
            # the 24 hours may be collected in pieces (operator decision 2026-10-20): see desk.ops.acceptance
            test = discovery_test(dbs.market, dbs.runtime, utc_now())
            test = {**test, "required_hours": args.require_hours,
                    "passed": test["counted_hours"] >= args.require_hours
                    and test["longest_segment_hours"] >= test["segment_hours"]}
            report["acceptance"] = test
            print(json.dumps(report, indent=2))
            print(f"\n24-hour test: {test['counted_hours']:.2f} of {args.require_hours:g} h counted, longest piece "
                  f"{test['longest_segment_hours']:.2f} h -> {'PASSED' if test['passed'] else 'not yet'}", flush=True)
            return 1 if not test["passed"] or report["orphans"] else 0
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
    from desk.core.worker_lock import WorkerLock

    paths, _ = _bootstrap(args.verbose)
    with WorkerLock(paths):
        return _run_shadow(paths, args)


def _run_shadow(paths: DeskPaths, args: argparse.Namespace) -> int:
    from desk.core.config import Mode
    from desk.ops.shadow import ShadowRunner, render_shadow
    from desk.storage.databases import Databases

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


def cmd_dashboard(args: argparse.Namespace) -> int:
    from desk.web.app import serve

    paths, _ = _bootstrap(args.verbose)
    print(f"KESTREL dashboard: http://127.0.0.1:{args.port} (this laptop only)", flush=True)
    serve(paths, port=args.port)
    return 0


def cmd_services(args: argparse.Namespace) -> int:
    from desk.ops.launcher import install_launchers
    from desk.ops.services import control_services, install_services

    paths, _ = _bootstrap(args.verbose)
    action = getattr(args, "action", "start")
    settings = load_config(paths.config_dir).settings
    rh_port = settings.robinhood.dashboard_port
    if action == "install":
        for path in install_services(paths, sys.executable, port=args.port, start=False):
            print(path)
        print("User services installed. Turn everything on (now and at every login): desk up")
    elif action == "start":
        paper = not getattr(args, "dashboard_only", False)
        install_services(paths, sys.executable, port=args.port, start=True, paper=paper)
        install_launchers(paths, sys.executable, port=args.port, rh_port=rh_port,
                          stocks_port=settings.stocks.dashboard_port)
        print("KESTREL is on, and starts again by itself whenever you log in:")
        print(f"  dashboards  http://127.0.0.1:{args.port} (Solana)   http://127.0.0.1:{rh_port} (Robinhood)")
        if paper:
            print("  PAPER       Solana desk in 4-hour pieces (they add up to the 24-hour test), Robinhood desk")
        print("  app icons   'KESTREL', 'KESTREL Crypto' and 'KESTREL Stocks' in your menu")
        print("  Stocks      its icon opens the research dashboard on demand; Start research controls collection")
        print("  turn off    desk down   (open paper positions stay supervised by the guardian)")
    else:
        control_services(action, paper=bool(getattr(args, "paper", False)))
        if action == "stop":
            print("Engines and dashboards stopped and will not start at login. The exit guardian keeps running.")
    return 0


def cmd_open(args: argparse.Namespace) -> int:
    from desk.ops.launcher import install_launchers, open_desk

    restart = {"restart": True} if getattr(args, "restart", False) else {}
    if args.desk == 'arbitrage':
        paths = DeskPaths.from_env()
        port = args.port or 8768
        if getattr(args, 'install', False):
            for path in install_launchers(paths, sys.executable, arbitrage_port=port, desks=('arbitrage',)):
                print(path)
        print(open_desk(args.desk, port, paths, **restart))
        return 0
    paths, _ = _bootstrap(args.verbose)
    settings = load_config(paths.config_dir).settings
    ports = {"robinhood": settings.robinhood.dashboard_port, "stocks": settings.stocks.dashboard_port, "solana": 8765}
    port = args.port or ports[args.desk]
    if getattr(args, "install", False):
        ports[args.desk] = port
        for path in install_launchers(paths, sys.executable, port=ports["solana"], rh_port=ports["robinhood"],
                                      stocks_port=ports["stocks"], desks=(args.desk,)):
            print(path)
        print("App installed in your application menu.")
    print(open_desk(args.desk, port, paths, **restart))
    return 0


def cmd_backups(args: argparse.Namespace) -> int:
    from pathlib import Path

    from desk.ops.backups import DATABASES, backup_schedule_once, create_backup, restore_backup, verify_backup

    paths, _ = _bootstrap(args.verbose)
    try:
        if args.action == "run":
            result: Any = backup_schedule_once(paths, utc_now())
        elif args.action == "create":
            kinds = tuple(kind for kind, filename in DATABASES.items() if (paths.db_dir / filename).exists())
            result = {"manifest": str(create_backup(paths, kinds, utc_now()))}
        elif not args.manifest:
            raise ValueError("provide the backup manifest path")
        elif args.action == "verify":
            result = verify_backup(Path(args.manifest).expanduser().resolve())
        else:
            from desk.core.worker_lock import WorkerLock

            if not args.kind:
                raise ValueError("restore requires --kind (for example ledger-paper)")
            if input(f"Stop dashboard, guardian and engine first. Type RESTORE {args.kind}: ").strip() != f"RESTORE {args.kind}":
                print("Not restored")
                return 1
            if _restore_workers_active(paths):
                raise ValueError("stop dashboard, guardian and engine before restore")
            # Hold the same locks as every supervised startup through verification and replacement.
            with WorkerLock(paths), WorkerLock(paths, "dashboard"), WorkerLock(paths, "guardian"), WorkerLock(paths, "telegram"):
                previous = restore_backup(
                    Path(args.manifest).expanduser().resolve(), paths, kind=args.kind, expected_mode=args.mode,
                    workers_active=lambda: _restore_processes_alive(paths),
                )
            result = {"previous_backup": None if previous is None else str(previous)}
        print(json.dumps(result, indent=1, default=str))
        return 0
    except (ValueError, RuntimeError) as exc:
        raise DeskError(str(exc)) from exc


def _restore_workers_active(paths: DeskPaths) -> bool:
    from desk.core.worker_lock import WorkerLock

    if any(WorkerLock(paths, name).held() for name in ("entry-worker", "dashboard", "guardian", "telegram")):
        return True
    return _restore_processes_alive(paths)


def _restore_processes_alive(paths: DeskPaths) -> bool:
    import os
    import sqlite3

    database = paths.db_dir / "runtime.db"
    if not database.exists():
        return False
    with contextlib.closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=2)) as conn:
        rows = conn.execute("SELECT pid FROM heartbeats").fetchall()
    for row in rows:
        try:
            os.kill(int(row[0]), 0)
        except ProcessLookupError:
            continue
        except PermissionError:
            return True
        else:
            return True
    return False


def cmd_telegram(args: argparse.Namespace) -> int:
    from desk.core.worker_lock import WorkerLock
    from desk.ops.telegram import build_daily_report

    paths, _ = _bootstrap(args.verbose)
    # Report generation is entirely local; outbound Telegram is an explicit separate opt-in.
    if args.action == "report":
        print(build_daily_report(paths))
        return 0
    from desk.ops.telegram import poll_telegram

    secrets = load_secrets(paths.secrets_file)
    token, chat = secrets.get("TELEGRAM_BOT_TOKEN"), secrets.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        raise ConfigError("set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in the existing private secrets file")
    import re

    if not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]{20,}", token) or not re.fullmatch(r"-?[0-9]+", chat):
        raise ConfigError("Telegram requires a valid bot token and exact integer chat ID in the private secrets file")

    async def run() -> None:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop.set)
        await poll_telegram(paths, token, int(chat), stop)

    with WorkerLock(paths, "telegram"):
        asyncio.run(run())
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="desk", description="KESTREL Solana quant desk")
    parser.add_argument("--version", action="version", version=f"desk {__version__}")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    sub = parser.add_subparsers(dest="command", required=True)
    from desk.arbitrage.cli import add_parser as add_arbitrage_parser
    from desk.stocks.cli import add_parser as add_stocks_parser

    add_stocks_parser(sub)
    add_arbitrage_parser(sub)

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

    sub.add_parser("paper-report", help="PAPER results, reconciliation and the reference-account replica").set_defaults(
        func=cmd_paper_report)

    p = sub.add_parser("confidence", help="confidence matrix and readiness per strategy from PAPER evidence (M6)")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_confidence)

    p = sub.add_parser("report", help="M9 readiness report: one verdict from recorded data (READINESS_REPORT.md)")
    p.add_argument("kind", choices=["readiness"])
    p.add_argument("--output", help="Markdown path (default: DESK_HOME/reports/READINESS_REPORT.md)")
    p.set_defaults(func=cmd_report)
    p = sub.add_parser("champion", help="champion/challenger status, evaluation and typed approval (M6)")
    p.add_argument("action", choices=["status", "evaluate", "approve"])
    p.add_argument("version", nargs="?")
    p.add_argument("--note")
    p.set_defaults(func=cmd_champion)

    p = sub.add_parser("model", help="local model: point-in-time scores and out-of-sample benchmark (no influence)")
    p.add_argument("action", choices=["score", "report"])
    p.set_defaults(func=cmd_model)

    sub.add_parser("counterfactuals", help="what happened to rejected candidates (M6-05)").set_defaults(
        func=cmd_counterfactuals)

    p = sub.add_parser("opportunities", help="early-opportunity research; separate Robinhood/Solana SHADOW records")
    p.add_argument("action", choices=["run", "report"])
    p.add_argument("--venue", choices=["robinhood", "solana", "both"], default="robinhood")
    p.add_argument("--minutes", type=_positive_minutes, help="run: repeat every minute for this duration; default one pass")
    p.add_argument("--limit", type=int, default=2000, help="source rows per pass, at most 10000")
    p.add_argument("--outcomes", type=int, default=200, help="matured outcomes per pass, at most 2000")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_opportunities)

    p = sub.add_parser("council", help="evidence council: two record-only research advisors (zero influence)")
    p.add_argument("action", choices=["run", "report", "rebuild-benchmark"])
    p.add_argument("--limit", type=int, default=300, help="run: decisions to advise in this pass")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_council)

    p = sub.add_parser("rh", help="the Robinhood desk: keygen, doctor, backtest, paper, report, dashboard (docs/ROBINHOOD.md)")
    p.add_argument("action", choices=["keygen", "doctor", "backtest", "learn", "paper", "report", "dashboard"])
    p.add_argument("--no-fetch", action="store_true", help="learn: use stored history only")
    p.add_argument("--days", type=int, default=730, help="backtest: sessions to simulate (default 730)")
    p.add_argument("--fetch", action="store_true", help="backtest: download missing Coinbase daily history first")
    p.add_argument("--minutes", type=_positive_minutes, default=None, help="paper: run time (default until stopped)")
    p.add_argument("--port", type=int, default=None, help="dashboard: port (default robinhood.dashboard_port)")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_cmd_rh)

    p = sub.add_parser("live", help="LIVE (M8): keygen, arm, disarm, status, approve, validate, withdraw, run, guardian,"
                                    " resume")
    p.add_argument("action", choices=["keygen", "arm", "disarm", "status", "approve", "validate", "withdraw", "run",
                                      "guardian", "resume"])
    p.add_argument("--minutes", type=_positive_minutes, default=None,
                   help="run/guardian: run time (run default 1440; guardian default until idle)")
    p.add_argument("--yes", action="store_true", help="resume: skip the typed confirmation")
    p.add_argument("version", nargs="?", help="strategy version id (status/approve)")
    p.add_argument("--taker", default="5tzFkiKscXHK5ZXCGbXZxdw7gTjjD1mBwuoFbhUvuAi9",
                   help="validate: a funded PUBLIC address used read-only for simulation")
    p.add_argument("--mint", default="EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", help="validate: output mint")
    p.add_argument("--sol", default=None, help="validate/withdraw: amount in SOL")
    p.set_defaults(func=cmd_live)

    p = sub.add_parser("liquidate", help="stop entries and attempt to exit every position (typed confirmation)")
    p.add_argument("--all", action="store_true")
    p.add_argument("--live", action="store_true", help="LIVE ledger via the signer (default: PAPER via the guardian)")
    p.set_defaults(func=cmd_liquidate)

    p = sub.add_parser("discovery-report", help="candidate outcomes, rejection reasons and audit completeness")
    p.add_argument("--run-id", help="run to check; defaults to the latest run")
    p.add_argument("--require-hours", type=_positive_minutes,
                   help="fail unless this many hours of cleanly finished discovery runs are recorded (pieces count)")
    p.add_argument("--continuous", action="store_true", help="require one unbroken run instead of pieces")
    p.set_defaults(func=cmd_discovery_report)

    p = sub.add_parser("console", help="local read-only M2 status console")
    p.add_argument("--port", type=int, default=8765)
    p.set_defaults(func=cmd_console)

    p = sub.add_parser("dashboard", help="KESTREL local React dashboard and bounded research controls")
    p.add_argument("--port", type=int, choices=range(1024, 65536), default=8765, metavar="1024..65535")
    p.set_defaults(func=cmd_dashboard)

    p = sub.add_parser("services", help="install or inspect opt-in systemd user services")
    p.add_argument("action", choices=["install", "status"])
    p.add_argument("--port", type=int, default=8765)
    p.set_defaults(func=cmd_services)

    p = sub.add_parser("up", help="turn everything on now and at every login (dashboards, PAPER, backups, app icons)")
    p.add_argument("--dashboard-only", action="store_true", help="dashboards and backups only, no PAPER engines")
    p.add_argument("--paper", action="store_true", help=argparse.SUPPRESS)      # the default since 2026-10-20
    p.add_argument("--port", type=int, choices=range(1024, 65536), default=8765, metavar="1024..65535")
    p.set_defaults(func=cmd_services, action="start")
    sub.add_parser("down", help="stop engines and dashboards and keep them off at login; the exit guardian stays").set_defaults(
        func=cmd_services, action="stop")

    p = sub.add_parser("open", help="open a dashboard in its own window, starting it if needed")
    p.add_argument("--desk", choices=["solana", "robinhood", "stocks", "arbitrage"], default="solana")
    p.add_argument("--install", action="store_true", help="also install this desk's application-menu icon")
    p.add_argument("--restart", action="store_true", help="load updated code by safely restarting Stocks or Arbitrage")
    p.add_argument("--port", type=int, choices=range(1024, 65536), default=None, metavar="1024..65535")
    p.set_defaults(func=cmd_open)

    p = sub.add_parser("backups", help="online backups, verification and typed-confirmation restore")
    p.add_argument("action", choices=["run", "create", "verify", "restore"])
    p.add_argument("manifest", nargs="?")
    p.add_argument("--kind", choices=["market", "research", "ledger-paper", "ledger-live"])
    p.add_argument("--mode", choices=["PAPER", "LIVE"], default="PAPER")
    p.set_defaults(func=cmd_backups)

    p = sub.add_parser("telegram", help="local report, or explicitly opt in to allowlisted Telegram status/stop")
    p.add_argument("action", choices=["report", "poll"])
    p.set_defaults(func=cmd_telegram)

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
