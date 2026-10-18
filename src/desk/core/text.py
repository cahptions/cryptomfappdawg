"""Handling of attacker-controlled text (token names, symbols, descriptions, links).

These strings come from anyone who can launch a token. They are stored only after sanitising and are
never interpreted as markup or instructions anywhere in the desk.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

# Unicode categories removed: control (Cc), format (Cf: zero-width, bidi overrides), surrogates, private use,
# unassigned. Line breaks/tabs are collapsed to spaces.
_DROP_CATEGORIES = {"Cc", "Cf", "Cs", "Co", "Cn"}


@dataclass(frozen=True)
class CleanText:
    text: str
    modified: bool


def sanitize_untrusted(value: object, max_len: int = 64) -> CleanText | None:
    """Return a cleaned, length-capped copy of untrusted text (``None`` stays ``None``)."""
    if value is None:
        return None
    raw = value if isinstance(value, str) else str(value)
    normalized = unicodedata.normalize("NFKC", raw)
    kept = []
    for ch in normalized:
        if ch in "\t\r\n":
            kept.append(" ")
        elif unicodedata.category(ch) not in _DROP_CATEGORIES:
            kept.append(ch)
    cleaned = " ".join("".join(kept).split())
    if len(cleaned) > max_len:
        cleaned = cleaned[: max_len - 1] + "…"
    return CleanText(text=cleaned, modified=cleaned != raw)
