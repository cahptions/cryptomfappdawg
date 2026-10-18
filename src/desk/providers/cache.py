"""In-process TTL cache with single-flight: concurrent identical requests share one upstream call."""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

T = TypeVar("T")


class SingleFlightCache:
    def __init__(self, *, clock: Callable[[], float] = time.monotonic, max_entries: int = 2048) -> None:
        self._clock = clock
        self._max_entries = max_entries
        self._values: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._inflight: dict[str, asyncio.Future[Any]] = {}
        self.upstream_calls = 0

    async def get_or_fetch(self, key: str, ttl: float, fetch: Callable[[], Awaitable[T]]) -> T:
        cached = self._values.get(key)
        if cached is not None and cached[0] > self._clock():
            self._values.move_to_end(key)
            value: T = cached[1]
            return value
        pending = self._inflight.get(key)
        if pending is not None:
            shared: T = await asyncio.shield(pending)
            return shared

        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._inflight[key] = future
        try:
            self.upstream_calls += 1
            result = await fetch()
        except BaseException as exc:
            future.set_exception(exc)
            future.exception()  # mark retrieved: waiters re-raise it, nobody else needs to
            raise
        finally:
            self._inflight.pop(key, None)
        future.set_result(result)
        if ttl > 0:
            self._values[key] = (self._clock() + ttl, result)
            while len(self._values) > self._max_entries:
                self._values.popitem(last=False)
        return result
