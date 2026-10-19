#!/usr/bin/env python3
"""Check the dashboard's actual opaque CSS text tokens against every main surface.

This checks WCAG relative luminance for normal-sized text. Keyboard navigation,
chart labels, browser zoom and focus visibility still require browser review.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

SURFACES = {
    "canvas": ("canvas", "bg", "background"),
    "panel": ("panel", "surface", "surface-1"),
    "raised": ("raised", "surface-raised", "surface-2"),
    "hover": ("hover", "surface-hover"),
}
TEXT = {
    "primary": ("text", "text-primary", "primary"),
    "secondary": ("secondary", "text-secondary", "text-2"),
    "muted": ("muted", "text-muted", "text-3"),
}
SEMANTIC = {
    "positive": ("positive", "success", "pos"),
    "negative": ("negative", "danger", "neg"),
    "warning": ("warning", "warn"),
    "info": ("info",),
}


def luminance(color: str) -> float:
    color = color.removeprefix("#")
    if len(color) == 3:
        color = "".join(char * 2 for char in color)
    if len(color) != 6:
        raise ValueError("contrast tokens must use opaque 3- or 6-digit hex")
    components = [int(color[index:index + 2], 16) / 255 for index in (0, 2, 4)]
    linear = [value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4 for value in components]
    return sum(value * weight for value, weight in zip(linear, (0.2126, 0.7152, 0.0722), strict=True))


def contrast(foreground: str, background: str) -> float:
    values = sorted((luminance(foreground), luminance(background)))
    return (values[1] + 0.05) / (values[0] + 0.05)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("css", type=Path, nargs="?", default=Path("webui/src/styles.css"))
    args = parser.parse_args()
    content = re.sub(r"/\*.*?\*/", "", args.css.read_text(), flags=re.DOTALL)
    colors = dict(re.findall(r"--([\w-]+)\s*:\s*(#[0-9a-fA-F]{3,8})\s*[;}]", content))

    def resolve(groups: dict[str, tuple[str, ...]]) -> dict[str, str]:
        result = {}
        for name, aliases in groups.items():
            found = next((colors[alias] for alias in aliases if alias in colors), None)
            if found is None:
                raise ValueError(f"missing {name} CSS hex token (expected one of {aliases})")
            result[name] = found
        return result

    try:
        surfaces = resolve(SURFACES)
        foregrounds = resolve(TEXT) | resolve(SEMANTIC)
        failed = False
        for name, foreground in foregrounds.items():
            ratio, surface = min((contrast(foreground, background), label) for label, background in surfaces.items())
            passed = ratio >= 4.5
            failed |= not passed
            print(f"{'PASS' if passed else 'FAIL'} {name}: {ratio:.2f}:1 minimum on {surface} (requires 4.5:1)")
        return int(failed)
    except ValueError as exc:
        print(f"FAIL: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
