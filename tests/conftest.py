from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "tests" / "fixtures"


def load_fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def load_fixture_json(name: str) -> object:
    return json.loads(load_fixture(name))


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    """A private copy of the real config directory that a test may modify."""
    dst = tmp_path / "config"
    shutil.copytree(REPO / "config", dst)
    return dst


@pytest.fixture
def desk_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "desk-data"
    monkeypatch.setenv("DESK_HOME", str(home))
    monkeypatch.setenv("DESK_SECRETS_FILE", str(tmp_path / "secrets.env"))
    return home
