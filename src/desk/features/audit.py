"""Point-in-time audit (M6-03): a feature computation must not change when future observations are added."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any


def leaking_features(compute: Callable[[datetime], dict[str, Any]], add_future_rows: Callable[[datetime], None],
                     as_of: datetime) -> list[str]:
    """Names whose value at ``as_of`` changes after rows observed later than ``as_of`` are inserted.

    ``compute`` returns ``{name: value}``; ``add_future_rows`` writes observations strictly after ``as_of``.
    An empty list means the computation is point-in-time with respect to those rows.
    """
    before = compute(as_of)
    add_future_rows(as_of)
    after = compute(as_of)
    return sorted(name for name in set(before) | set(after) if before.get(name) != after.get(name))
