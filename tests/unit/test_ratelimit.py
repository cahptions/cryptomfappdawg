"""M1-10 (budget never exceeded, shared across processes) and M1-11 (adaptive backoff on 429)."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from desk.providers.ratelimit import RateLimiter


class FakeTime:
    def __init__(self, start: float = 1_800_000_000.0) -> None:
        self.now = start
        self.slept = 0.0

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += seconds
        self.slept += seconds
        await asyncio.sleep(0)


def _windows_ok(times: list[float], windows: list[tuple[int, float]]) -> list[str]:
    """Return every violation: a window position containing more requests than allowed."""
    times = sorted(times)
    problems = []
    for limit, seconds in windows:
        for i, start in enumerate(times):
            in_window = sum(1 for t in times[i:] if t < start + seconds)
            if in_window > limit:
                problems.append(f"{in_window} requests in {seconds}s window starting {start} (limit {limit})")
    return problems


async def test_ten_minute_simulated_load_never_exceeds_any_window(tmp_path: Path) -> None:
    fake = FakeTime()
    limiter = RateLimiter(tmp_path / "runtime.db", clock=fake.clock, sleep=fake.sleep, rng=lambda: 0.5, offload=False)
    windows = [(8, 60.0), (2, 15.0)]                         # GeckoTerminal budget
    granted: list[float] = []
    end = fake.now + 600

    async def worker() -> None:
        while fake.now < end:
            await limiter.acquire("gt", windows)
            if fake.now < end:
                granted.append(fake.now)
            await asyncio.sleep(0)

    await asyncio.gather(worker(), worker(), worker())
    assert _windows_ok(granted, windows) == []
    assert len(granted) >= 70  # roughly 8/min sustained: the limiter is not needlessly strict either
    limiter.close()


async def test_backoff_blocks_all_requests_and_grows_exponentially(tmp_path: Path) -> None:
    fake = FakeTime()
    limiter = RateLimiter(tmp_path / "runtime.db", clock=fake.clock, sleep=fake.sleep, rng=lambda: 0.5, offload=False)
    first = limiter.note_rate_limited("gt", retry_after=0.0, base=2.0, maximum=60.0)   # retry-after: 0 ignored
    second = limiter.note_rate_limited("gt", retry_after=None, base=2.0, maximum=60.0)
    third = limiter.note_rate_limited("gt", retry_after=None, base=2.0, maximum=60.0)
    assert first == pytest.approx(2.0) and second == pytest.approx(4.0) and third == pytest.approx(8.0)
    assert limiter.try_acquire("gt", [(100, 60.0)]) > 0              # still inside the backoff window
    fake.now += 8.1
    assert limiter.try_acquire("gt", [(100, 60.0)]) == 0
    limiter.note_success("gt")
    row = limiter.backoff_state("gt")
    assert row is not None and row["consecutive_429"] == 0
    limiter.close()


async def test_positive_retry_after_is_respected(tmp_path: Path) -> None:
    fake = FakeTime()
    limiter = RateLimiter(tmp_path / "runtime.db", clock=fake.clock, sleep=fake.sleep, rng=lambda: 0.5, offload=False)
    assert limiter.note_rate_limited("j", retry_after=30.0, base=2.0, maximum=60.0) == pytest.approx(30.0)
    limiter.close()


async def test_backoff_capped_at_maximum(tmp_path: Path) -> None:
    fake = FakeTime()
    limiter = RateLimiter(tmp_path / "runtime.db", clock=fake.clock, sleep=fake.sleep, rng=lambda: 1.0, offload=False)
    delays = [limiter.note_rate_limited("x", retry_after=None, base=2.0, maximum=60.0) for _ in range(12)]
    assert max(delays) <= 60.0 * 1.2 + 1e-9
    limiter.close()


async def test_clock_moving_backwards_does_not_deadlock(tmp_path: Path) -> None:
    fake = FakeTime()
    limiter = RateLimiter(tmp_path / "runtime.db", clock=fake.clock, sleep=fake.sleep, rng=lambda: 0.5, offload=False)
    for _ in range(5):
        assert limiter.try_acquire("b", [(5, 60.0)]) == 0
    fake.now -= 3600  # NTP correction / manual clock change
    assert limiter.try_acquire("b", [(5, 60.0)]) == 0  # stale "future" events were purged
    limiter.close()


WORKER = textwrap.dedent(
    """
    import asyncio, sys, time
    from pathlib import Path
    from desk.providers.ratelimit import RateLimiter
    async def main():
        limiter = RateLimiter(Path(sys.argv[1]))
        end = time.time() + float(sys.argv[2])
        while True:
            await limiter.acquire("shared", [(6, 2.0)])
            now = time.time()
            if now >= end:
                break
            print(repr(now), flush=True)
    asyncio.run(main())
    """
)


@pytest.mark.slow
def test_budget_is_shared_across_processes(tmp_path: Path) -> None:
    db = tmp_path / "runtime.db"
    RateLimiter(db).close()  # create schema before the race
    procs = [subprocess.Popen([sys.executable, "-c", WORKER, str(db), "6"], stdout=subprocess.PIPE, text=True,
                              env=os.environ.copy()) for _ in range(3)]
    times: list[float] = []
    for proc in procs:
        out, _ = proc.communicate(timeout=60)
        times.extend(float(line) for line in out.split())
    assert _windows_ok(times, [(6, 2.0)]) == []
    assert len(times) >= 12  # ~3 per second combined over ~6 s, never 3x that
