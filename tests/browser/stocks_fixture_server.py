"""Synthetic Stocks dashboard fixture, isolated from operator data and all providers.

uv run python -m tests.browser.stocks_fixture_server --scenario populated --port 8877
"""

from __future__ import annotations

import argparse
import tempfile
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import uvicorn

from desk.core.config import load_config
from desk.core.paths import DeskPaths
from desk.stocks import calendar as cal
from desk.stocks.contracts import Bar
from desk.stocks.hub import INDEX_NAMES, SECTOR_NAMES
from desk.stocks.runner import process
from desk.stocks.web import create_app
from tests.conftest import REPO
from tests.stocks_world import NOW, add_chains, make_world


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", choices=["empty", "populated", "stale-prices", "ui-workflows",
                                               "paper-workflows", "intraday-workflows", "learning-workflows",
                                               "plays-history"],
                        default="populated")
    parser.add_argument("--port", type=int, default=8877)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="stocks-browser-") as directory:
        root = Path(directory)
        paths = DeskPaths(root / "home", REPO / "config", root / "secrets.env")
        if args.scenario == "plays-history":
            from desk.stocks import play_performance
            from tests.integration.test_stocks_play_history import simulate

            # Six synthetic sessions of picks, tracked, marked, one laptop-off session repaired (no provider).
            world = make_world(root, n=60)
            simulate(world)
            play_performance.refresh(world.paths, world.market.conn, world.research.conn,
                                     cal.session_close(world.days[-1]) + timedelta(hours=2), changed=True)
            world.close()
        elif args.scenario != "empty":
            through = NOW.date() - timedelta(days=7) if args.scenario == "paper-workflows" else NOW.date()
            world = make_world(root, n=60, through=through)
            # Synthetic chart coverage for visual checks; never market observations or forward evidence.
            benchmarks = list(dict.fromkeys([*INDEX_NAMES, *SECTOR_NAMES]))
            for bar in world.market.bars("SPY"):
                extras = []
                for index, symbol in enumerate(benchmarks):
                    if symbol == "SPY":
                        continue
                    scale = 0.18 + (index + 1) * 0.095
                    extras.append(replace(bar, symbol=symbol, open=bar.open * scale, high=bar.high * scale,
                                          low=bar.low * scale, close=bar.close * scale))
                world.market.save_bars(bar.day, extras, "synthetic_browser_fixture", NOW)
            add_chains(world, ["SPY", "QQQ", "CHEAP", "SQZ", "S10", "S11"], world.days[-3:])
            world.dbs.market.execute("UPDATE stk_option_chains SET source='synthetic_browser_fixture'")
            initial_now = cal.session_close(world.days[-1])+timedelta(minutes=76) if args.scenario == "paper-workflows" else NOW
            process(paths, load_config(paths.config_dir), world.dbs, initial_now)
            process(paths, load_config(paths.config_dir), world.dbs, initial_now, research_only=True)
            if args.scenario == "paper-workflows":
                # Three further genuinely processed synthetic sessions give filled positions and stop exits.
                for index in range(3):
                    previous = world.market.bar_days()[-1]
                    day = cal.next_trading_day(previous)
                    prior_bars = [Bar(r[0],previous,r[2],r[3],r[4],r[5],r[6]) for r in world.market.bar_rows(previous,previous)]
                    bars = [replace(b, day=day, open=b.close, high=b.close*1.03,
                                    low=b.close*(.70 if index == 1 and b.symbol != "SPY" else .98),
                                    close=b.close*1.01) for b in prior_bars]
                    world.market.save_bars(day, bars, "synthetic_browser_fixture", NOW)
                    process(paths, load_config(paths.config_dir), world.dbs,
                            cal.session_close(day)+timedelta(minutes=76))
            if args.scenario in {"stale-prices", "ui-workflows"}:
                import asyncio
                from types import SimpleNamespace

                from desk.stocks.collect import option_chains
                from tests.unit.test_stocks_options_population import CAPTURE, StandardSchwab

                # Exact user failure scenario: newest options are independent of the denied newer close.
                asyncio.run(option_chains(SimpleNamespace(tradier=StandardSchwab(), option_source="synthetic_schwab_shape"),
                                           world.market, CAPTURE, ["SPY", "QQQ", "CHEAP"], rate=.04,
                                           last_prices={}, clock=lambda: CAPTURE))
                world.market.job("prices", CAPTURE, error="Synthetic Massive grouped_daily HTTP 403")
                process(paths, load_config(paths.config_dir), world.dbs, CAPTURE, research_only=True)
                if args.scenario == "ui-workflows":
                    from datetime import date

                    from desk.stocks.stock_views import request, save_history

                    # Synthetic long history exercises actual API storage/ranges, never provider access.
                    bars = [Bar("SPY", day, 100 + i * .002, 101 + i * .002, 99 + i * .002,
                                100.5 + i * .002, 1000000) for i, day in enumerate(
                                    cal.trading_days(date(2006, 10, 23), CAPTURE.date()))]
                    request(world.dbs.market, "SPY", CAPTURE)
                    save_history(world.dbs.market, "SPY", bars, CAPTURE)
            if args.scenario == "intraday-workflows":
                import math

                from desk.stocks.contracts import IntradayBar
                from desk.stocks.stock_views import request, save_history, save_intraday

                for symbol in ("SPY", "QQQ"):
                    session = world.days[-1]
                    opening = cal.session_close(session) - timedelta(hours=6, minutes=30)
                    minutes = []
                    for minute in range(390):
                        op = 100 + minute * .005
                        close = op + .02 * math.cos(minute)
                        minutes.append(IntradayBar(symbol, opening + timedelta(minutes=minute), op,
                                       max(op, close) + .04, min(op, close) - .04, close, 1000 + minute))
                    request(world.dbs.market, symbol, NOW)
                    save_intraday(world.dbs.market, symbol, minutes, NOW)
                    save_history(world.dbs.market, symbol, [
                        Bar(symbol, world.days[-2], 99, 100, 98, 99.5, 100000),
                        Bar(symbol, session, 100, 103, 99, minutes[-1].close, 100000),
                    ], NOW)
            if args.scenario == "learning-workflows":
                from desk.stocks import learning
                from tests.unit.test_stocks_learning import AT, chain

                lab = learning.open_book(paths)
                try:
                    for at, bid, ask in [(AT,4.8,5.0),(AT+timedelta(minutes=1),4.8,5.0),
                                         (AT+timedelta(minutes=122),5.8,6.0)]:
                        chain(world,at,bid,ask,source="synthetic_browser_fixture")
                        learning.tick(lab,world.market,at)
                finally:
                    lab.close()
            world.close()
        uvicorn.run(create_app(paths, auto_start=False), host="127.0.0.1", port=args.port, access_log=False)


if __name__ == "__main__":
    main()
