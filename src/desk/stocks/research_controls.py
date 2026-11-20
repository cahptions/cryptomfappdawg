"""Local per-study switches read by the worker on every pass."""

from __future__ import annotations

from typing import Literal

from desk.core.paths import DeskPaths
from desk.stocks.connection import _atomic_private

Study = Literal["iv_forward_study", "quote_research_trial"]


def enabled(paths: DeskPaths, study: Study, fallback: bool) -> bool:
    try:
        with (paths.home / f"stocks-{study}.txt").open(encoding="ascii") as handle:
            return handle.read(4) == "on\n"
    except FileNotFoundError:
        return fallback
    except (OSError, UnicodeError):
        return False


def save(paths: DeskPaths, study: Study, value: bool) -> None:
    _atomic_private(paths.home / f"stocks-{study}.txt", "on\n" if value else "off\n")
