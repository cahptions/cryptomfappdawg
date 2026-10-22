"""Provider setup guide data; no unused credentials are collected."""

import json
from pathlib import Path
from typing import Any

GUIDES: list[dict[str, Any]] = json.loads((Path(__file__).with_name("static") / "setup-guides.json").read_text())
