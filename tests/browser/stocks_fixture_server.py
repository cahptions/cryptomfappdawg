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
    parser.add_argument("--scenario", choices=["empty", "populated"], default="populated")
    parser.add_argument("--port", type=int, default=8877)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="stocks-browser-") as directory:
        root = Path(directory)
        paths = DeskPaths(root / "home", REPO / "config", root / "secrets.env")
        if args.scenario == "populated":
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
            add_chains(world, ["CHEAP", "SQZ", "S10", "S11"], world.days[-3:])
            world.dbs.market.execute("UPDATE stk_option_chains SET source='synthetic_browser_fixture'")
            process(paths, load_config(paths.config_dir), world.dbs, NOW)
            world.close()
        uvicorn.run(create_app(paths), host="127.0.0.1", port=args.port, access_log=False)


if __name__ == "__main__":
    main()
