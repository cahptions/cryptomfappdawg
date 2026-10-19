"""Where the desk keeps its data, logs, configuration and secrets.

Runtime data lives OUTSIDE the repository (default ``~/desk-data``) so a ``git pull`` can never touch it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


def _expand(value: str) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(value))).resolve()


@dataclass(frozen=True)
class DeskPaths:
    home: Path
    config_dir: Path
    secrets_file: Path

    @property
    def db_dir(self) -> Path:
        return self.home / "db"

    @property
    def raw_dir(self) -> Path:
        return self.home / "raw"

    @property
    def logs_dir(self) -> Path:
        return self.home / "logs"

    @property
    def backups_dir(self) -> Path:
        return self.home / "backups"

    @property
    def signer_socket(self) -> Path:
        return self.home / "run" / "signer.sock"

    @property
    def stop_file(self) -> Path:
        return self.home / "STOP"

    def ensure(self) -> None:
        for d in (self.home, self.db_dir, self.raw_dir, self.logs_dir, self.backups_dir):
            d.mkdir(parents=True, exist_ok=True)

    @classmethod
    def from_env(cls) -> DeskPaths:
        home = _expand(os.environ.get("DESK_HOME", "~/desk-data"))
        config_dir = _expand(os.environ.get("DESK_CONFIG_DIR", str(REPO_ROOT / "config")))
        secrets_file = _expand(os.environ.get("DESK_SECRETS_FILE", "~/.config/desk/secrets.env"))
        return cls(home=home, config_dir=config_dir, secrets_file=secrets_file)
