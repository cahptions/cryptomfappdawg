"""Bounded synthetic resource check, not market or profitability evidence.

Run from the repository: uv run python -m research.reliability.benchmark
"""

from __future__ import annotations

import json
import resource
import tempfile
import time
from pathlib import Path
from typing import Any

from tests.conftest import REPO
from tests.stocks_world import NOW, make_world

from desk.core.config import load_config
from desk.stocks.runner import process


def main() -> None:
    stages: dict[str, Any] = {}
    with tempfile.TemporaryDirectory(prefix='stocks-audit-benchmark-') as directory:
        wall, cpu = time.perf_counter(), time.process_time()
        world = make_world(Path(directory), n=500)
        try:
            stages['synthetic_setup'] = {'wall_seconds': time.perf_counter() - wall,
                                         'cpu_seconds': time.process_time() - cpu}
            wall, cpu = time.perf_counter(), time.process_time()
            result = process(world.paths, load_config(REPO / 'config'), world.dbs, NOW, research_only=True)
            stages['research_preview'] = {'wall_seconds': time.perf_counter() - wall,
                                          'cpu_seconds': time.process_time() - cpu,
                                          'eligible': result['universe']}
            bars = world.dbs.market.execute('SELECT COUNT(*) FROM stk_bars').fetchone()[0]
            stored = sum(p.stat().st_size for p in world.paths.home.rglob('*') if p.is_file())
            output = {'kind': 'synthetic_cloud_resource_check', 'companies': 500, 'sessions': len(world.days),
                      'bar_rows': bars, 'provider_requests': 0, 'paper_positions': 0,
                      'peak_rss_mib': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
                      'database_and_report_mib_including_wal': stored / 2**20, 'stages': stages,
                      'limitations': 'Cloud CPU/filesystem, not a laptop benchmark; no live source or option-chain load'}
            print(json.dumps(output, indent=2))
        finally:
            world.close()


if __name__ == '__main__':
    main()
