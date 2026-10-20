"""``desk rh ...``: the Robinhood desk's commands (keygen, doctor, backtest, paper, report, dashboard)."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from typing import Any

from desk.core.clock import utc_now
from desk.core.config import load_config
from desk.core.errors import DeskError
from desk.core.paths import DeskPaths
from desk.core.secrets import load_secrets

log = logging.getLogger(__name__)
CREDENTIAL_STEPS = """\
Create the Robinhood credential (read-only is enough for PAPER):
  1. Open https://robinhood.com/account/crypto on a computer (web classic) → API trading → add a key.
  2. Paste the PUBLIC key above. Enable only the read actions (accounts, holdings, orders, products, quotes);
     leave order placing OFF. Robinhood then shows your API key (starts with rh-api-).
  3. Add this line to {secrets}:
       ROBINHOOD_API_KEY=<the rh-api-... key Robinhood showed you>
  4. Run: uv run desk rh doctor
The private key stays in {key} on this laptop; never paste it anywhere."""


def _paths() -> DeskPaths:
    paths = DeskPaths.from_env()
    paths.ensure()
    return paths


def keygen() -> int:
    from desk.rh.signing import generate_key, key_path, load_private_key, public_key_b64

    paths = _paths()
    path = key_path(paths.secrets_file)
    if path.exists():
        print("A Robinhood key already exists; its public key is:\n")
        print("  " + public_key_b64(load_private_key(path)))
    else:
        print("Created a new Robinhood key. Public key:\n")
        print("  " + generate_key(path))
    print("\n" + CREDENTIAL_STEPS.format(secrets=paths.secrets_file, key=path))
    return 0


async def _doctor(paths: DeskPaths) -> list[tuple[str, bool, str]]:
    from desk.rh.repo import RhDatabases
    from desk.rh.suite import build_rh_suite

    loaded = load_config(paths.config_dir)
    secrets = load_secrets(paths.secrets_file)
    dbs = RhDatabases.open(paths)
    suite = build_rh_suite(paths, loaded, secrets, dbs.runtime)
    rows: list[tuple[str, bool, str]] = []
    try:
        rows.append(("Databases", all(v["integrity"] == "ok" for v in dbs.health().values()),
                     ", ".join(f"{k} {v['integrity']}" for k, v in dbs.health().items())))
        rows.append(("Robinhood key", suite.key_problem is None, suite.key_problem or "API key and private key found"))
        if suite.key_problem is None:
            try:
                accounts = await suite.robinhood.accounts()
                tiers = [a.get("fee_tier_status") or {} for a in accounts]
                rows.append(("Robinhood account", True, f"{len(accounts)} account(s); fee ratio "
                             f"{', '.join(str(t.get('fee_ratio', '?')) for t in tiers) or '?'}"))
                pairs = await suite.robinhood.trading_pairs()
                tradable = [p for p in pairs if p.tradable]
                rows.append(("Robinhood pairs", bool(tradable), f"{len(tradable)} tradable USD pairs of {len(pairs)}"))
                quotes = await suite.robinhood.best_bid_ask(["BTC-USD"])
                rows.append(("Robinhood quote", bool(quotes),
                             (f"BTC bid {quotes[0].bid} ask {quotes[0].ask}"
                              + (" (crossed aggregate; size and fees checked with estimates)" if quotes[0].crossed else ""))
                             if quotes else "no quote"))
            except DeskError as exc:
                rows.append(("Robinhood API", False, str(exc)[:200]))
        try:
            products = await suite.coinbase.usd_products()
            rows.append(("Coinbase history", "BTC-USD" in products, f"{len(products)} USD products (keyless)"))
        except DeskError as exc:
            rows.append(("Coinbase history", False, str(exc)[:200]))
    finally:
        await suite.aclose()
        dbs.close()
    return rows


def doctor() -> int:
    rows = asyncio.run(_doctor(_paths()))
    print("\nKESTREL Robinhood desk doctor\n" + "=" * 40)
    for name, ok, detail in rows:
        print(f"  {'✓' if ok else '✗'} {name:<20} {detail}")
    return 0 if all(ok for _, ok, _ in rows) else 1


async def _backtest(paths: DeskPaths, days: int, fetch: bool) -> dict[str, Any]:
    from desk.rh.history import DAILY, backtest, sync_candles, universe
    from desk.rh.repo import RhDatabases, RhMarket
    from desk.rh.suite import build_rh_suite

    loaded = load_config(paths.config_dir)
    dbs = RhDatabases.open(paths)
    market = RhMarket(dbs.market)
    now = utc_now()
    try:
        market.register_specs(now)
        symbols, source = universe(market)
        sync: dict[str, Any] | None = None
        if fetch:
            suite = build_rh_suite(paths, loaded, load_secrets(paths.secrets_file), dbs.runtime)
            try:
                sync = await sync_candles(suite.coinbase, market, symbols, granularity=DAILY, days=days + 200, now=now)
            finally:
                await suite.aclose()
        _, report = backtest(market, loaded, symbols, days=days, now=now, universe_source=source)
        report["sync"] = sync
    finally:
        dbs.close()
    if days == 1095:
        from desk.rh.history import write_backtest

        write_backtest(paths, loaded, now)              # what the dashboard shows
    return report


def print_backtest(report: dict[str, Any]) -> None:
    print(f"\n{report['kind']}\n  sessions {report['sessions']}; universe {report['universe']['with_history']} coins "
          f"({report['universe']['source']})")
    print(f"  cost per round trip: {report['cost_model']['assumed_roundtrip_fraction']} assumed"
          f" ({len(report['cost_model']['measured_symbols'])} coins measured on Robinhood)")
    print(f"  BTC buy-and-hold over the window: {report['btc_buy_and_hold_return']}; regime days {report['regime_days']}")
    print(f"\n  {'strategy':<13}{'trades':>7}{'win%':>7}{'mean':>9}{'median':>9}{'P&L $':>10}{'max DD $':>10}{'hold d':>8}"
          f"{'per $·yr':>10}")
    for sid, row in report["strategies"].items():
        win = "-" if row["win_rate"] is None else f"{float(row['win_rate']) * 100:.0f}"
        mean = "-" if row["mean_net_return"] is None else f"{float(row['mean_net_return']) * 100:+.1f}%"
        med = "-" if row["median_net_return"] is None else f"{float(row['median_net_return']) * 100:+.1f}%"
        per_year = "-" if row["pnl_per_dollar_year"] is None else f"{float(row['pnl_per_dollar_year']) * 100:+.0f}%"
        print(f"  {sid:<13}{row['trades']:>7}{win:>7}{mean:>9}{med:>9}{row['total_pnl_usd']:>10}"
              f"{row['max_drawdown_usd']:>10}{row['mean_hold_days'] or '-':>8}{per_year:>10}")
    print("\n  caveats: " + "; ".join(report["caveats"]))


async def _paper(paths: DeskPaths, minutes: float | None) -> dict[str, Any]:
    from desk.core.worker_lock import WorkerLock
    from desk.rh.engine import RhDesk
    from desk.rh.repo import RhDatabases
    from desk.rh.suite import build_rh_suite

    loaded = load_config(paths.config_dir)
    with WorkerLock(paths, "rh-worker"):
        dbs = RhDatabases.open(paths)
        suite = build_rh_suite(paths, loaded, load_secrets(paths.secrets_file), dbs.runtime)
        try:
            if suite.key_problem:
                print(f"Robinhood prices unavailable ({suite.key_problem}).\n"
                      "Running on history only: signals are recorded, no paper trades until the key works.", flush=True)
            stats = await RhDesk(paths, loaded, suite, dbs).run(minutes)
        finally:
            await suite.aclose()
            dbs.close()
    return stats.__dict__


def report(paths: DeskPaths) -> dict[str, Any]:
    from desk.rh.view import RhView

    snap = RhView(paths, load_config(paths.config_dir)).snapshot()
    return {k: snap[k] for k in ("generated_at", "mode", "universe_source", "robinhood_prices", "account", "positions",
                                 "strategies", "session", "backtest")}


def print_report(data: dict[str, Any]) -> None:
    account = data["account"]
    print(f"\nKESTREL Robinhood desk — PAPER ({'Robinhood prices' if data['robinhood_prices'] else 'no live prices'})")
    print(f"  $100 account: ${account['equity']} (realized {account['realized']}, unrealized {account['unrealized']});"
          f" ticket ${account['ticket']}; open {account['open_positions']}"
          + (f"; ENTRIES HALTED: {account['halted']}" if account["halted"] else ""))
    last = (data["session"] or {}).get("last") or {}
    print(f"  last session {last.get('day', 'none yet')} regime {last.get('regime', '-')}; next {data['session']['next_at']}")
    print(f"\n  {'strategy':<13}{'open':>5}{'closed':>7}{'wins':>5}{'P&L $':>9}   history (3y sim, per $·yr)")
    for row in data["strategies"]:
        bt = row["backtest"] or {}
        sim = "-" if bt.get("pnl_per_dollar_year") is None else f"{float(bt['pnl_per_dollar_year']) * 100:+.0f}%"
        print(f"  {row['id']:<13}{row['open']:>5}{row['closed']:>7}{row['wins']:>5}{row['realized']:>9}   {sim}")


def cmd_rh(args: argparse.Namespace) -> int:
    if args.action == "keygen":
        return keygen()
    if args.action == "doctor":
        return doctor()
    if args.action == "backtest":
        result = asyncio.run(_backtest(_paths(), args.days, args.fetch))
        if args.json:
            print(json.dumps(result, indent=1, default=str))
        else:
            print_backtest(result)
        return 0
    if args.action == "paper":
        stats = asyncio.run(_paper(_paths(), args.minutes))
        print(json.dumps(stats, indent=1, default=str))
        return 0
    if args.action == "report":
        data = report(_paths())
        if args.json:
            print(json.dumps(data, indent=1, default=str))
        else:
            print_report(data)
        return 0
    if args.action == "dashboard":
        from desk.rh.web import serve_rh

        paths = _paths()
        port = args.port or load_config(paths.config_dir).settings.robinhood.dashboard_port
        print(f"KESTREL Robinhood dashboard: http://127.0.0.1:{port} (this laptop only)", flush=True)
        serve_rh(paths, port=port)
        return 0
    raise SystemExit(f"unknown action {args.action}")


__all__ = ["cmd_rh", "print_backtest"]
