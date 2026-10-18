"""Loading API keys from ``~/.config/desk/secrets.env`` without ever printing or logging them.

File format (one per line, ``#`` comments allowed)::

    JUPITER_API_KEY=...
    SOLANA_RPC_URL=https://mainnet.helius-rpc.com/?api-key=...

The file must be readable only by its owner (``chmod 600``); a looser file is reported as a problem.
Environment variables with the same names override the file.
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass, field
from pathlib import Path

KNOWN_SECRET_NAMES = ("JUPITER_API_KEY", "SOLANA_RPC_URL", "SOLANA_WS_URL", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")
SECRET_NAME_MARKERS = ("KEY", "SECRET", "TOKEN", "PASSPHRASE", "PASSWORD", "PRIVATE", "RPC_URL", "WS_URL")


def looks_secret(name: str) -> bool:
    upper = name.upper()
    return any(marker in upper for marker in SECRET_NAME_MARKERS)


@dataclass
class Secrets:
    values: dict[str, str] = field(default_factory=dict)
    file_path: Path | None = None
    file_exists: bool = False
    file_mode_ok: bool | None = None
    problems: list[str] = field(default_factory=list)

    def get(self, name: str) -> str | None:
        value = self.values.get(name)
        return value if value else None

    def configured(self, name: str) -> bool:
        return self.get(name) is not None

    def all_secret_values(self) -> list[str]:
        """Every value that must be redacted from logs (including secret-looking environment variables)."""
        found = {v for k, v in self.values.items() if v and looks_secret(k)}
        found |= {v for k, v in os.environ.items() if v and len(v) >= 6 and looks_secret(k)}
        return sorted(found, key=len, reverse=True)


def _parse_env_file(text: str) -> tuple[dict[str, str], list[str]]:
    values: dict[str, str] = {}
    problems: list[str] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export "):
            stripped = stripped[len("export ") :].strip()
        if "=" not in stripped:
            problems.append(f"line {lineno}: expected NAME=value")
            continue
        name, _, value = stripped.partition("=")
        name = name.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        if not name.isidentifier():
            problems.append(f"line {lineno}: invalid variable name")
            continue
        values[name] = value
    return values, problems


def load_secrets(path: Path) -> Secrets:
    secrets = Secrets(file_path=path)
    if path.exists():
        secrets.file_exists = True
        mode = stat.S_IMODE(path.stat().st_mode)
        secrets.file_mode_ok = (mode & 0o077) == 0
        if not secrets.file_mode_ok:
            secrets.problems.append(f"{path} is readable by other users (mode {oct(mode)}); run: chmod 600 {path}")
        parsed, problems = _parse_env_file(path.read_text(encoding="utf-8"))
        secrets.values.update(parsed)
        secrets.problems.extend(problems)
    for name in KNOWN_SECRET_NAMES:
        env_value = os.environ.get(name)
        if env_value:
            secrets.values[name] = env_value
    return secrets
