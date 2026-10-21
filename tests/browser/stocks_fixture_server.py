"""Synthetic Stocks dashboard fixture, isolated from operator data and all providers.

uv run python -m tests.browser.stocks_fixture_server --scenario populated --port 8877
"""

from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

import uvicorn

from desk.core.config import load_config
from desk.core.paths import DeskPaths
from desk.stocks.runner import process
from desk.stocks.web import create_app
from tests.conftest import REPO
from tests.stocks_world import NOW, make_world


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", choices=["empty", "populated"], default="populated")
    parser.add_argument("--port", type=int, default=8877)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="stocks-browser-") as directory:
        root = Path(directory)
        paths = DeskPaths(root / "home", REPO / "config", root / "secrets.env")
        if args.scenario == "populated":
            world = make_world(root, n=12)
            process(paths, load_config(paths.config_dir), world.dbs, NOW)
            world.close()
        uvicorn.run(create_app(paths), host="127.0.0.1", port=args.port, access_log=False)


if __name__ == "__main__":
    main()
