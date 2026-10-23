"""Synthetic Stocks dashboard fixture, isolated from operator data and all providers.

uv run python -m tests.browser.stocks_fixture_server --scenario populated --port 8877
"""

from __future__ import annotations

import argparse
import tempfile
from dataclasses import replace
from pathlib import Path

import uvicorn

from desk.core.config import load_config
from desk.core.paths import DeskPaths
from desk.stocks.hub import INDEX_NAMES, SECTOR_NAMES
from desk.stocks.runner import process
from desk.stocks.web import create_app
from tests.conftest import REPO
from tests.stocks_world import NOW, add_chains, make_world


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", choices=["empty", "populated", "stale-prices", "ui-workflows"], default="populated")
    parser.add_argument("--port", type=int, default=8877)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="stocks-browser-") as directory:
        root = Path(directory)
        paths = DeskPaths(root / "home", REPO / "config", root / "secrets.env")
        if args.scenario != "empty":
            world = make_world(root, n=60)
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
            process(paths, load_config(paths.config_dir), world.dbs, NOW)
            process(paths, load_config(paths.config_dir), world.dbs, NOW, research_only=True)
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

                    from desk.stocks import calendar as cal
                    from desk.stocks.contracts import Bar
                    from desk.stocks.stock_views import request, save_history

                    # Synthetic long history exercises actual API storage/ranges, never provider access.
                    bars = [Bar("SPY", day, 100 + i * .002, 101 + i * .002, 99 + i * .002,
                                100.5 + i * .002, 1000000) for i, day in enumerate(
                                    cal.trading_days(date(2006, 10, 23), CAPTURE.date()))]
                    request(world.dbs.market, "SPY", CAPTURE)
                    save_history(world.dbs.market, "SPY", bars, CAPTURE)
            world.close()
        uvicorn.run(create_app(paths, auto_start=False), host="127.0.0.1", port=args.port, access_log=False)


if __name__ == "__main__":
    main()
