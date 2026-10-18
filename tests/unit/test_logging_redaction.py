"""M1-14: secret values never reach log output."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from desk.core.logging import REDACTED, setup_logging
from desk.core.secrets import load_secrets

SECRET_KEY = "jup_live_ABCDEF1234567890"
SECRET_RPC = "https://mainnet.helius-rpc.com/?api-key=11111111-2222-3333-4444-555555555555"


@pytest.fixture
def configured_logging(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    secrets_file = tmp_path / "secrets.env"
    secrets_file.write_text(f"JUPITER_API_KEY={SECRET_KEY}\nSOLANA_RPC_URL={SECRET_RPC}\n")
    secrets_file.chmod(0o600)
    monkeypatch.setenv("MY_TELEGRAM_TOKEN", "tg-token-value-xyz")
    secrets = load_secrets(secrets_file)
    logs = tmp_path / "logs"
    setup_logging(logs, secrets.all_secret_values())
    return logs


def _read_all(logs: Path, capsys: pytest.CaptureFixture[str]) -> str:
    for handler in logging.getLogger().handlers:
        handler.flush()
    captured = capsys.readouterr()
    return (logs / "desk.jsonl").read_text() + captured.err + captured.out


def test_secrets_redacted_from_message_args_extra_and_exceptions(
    configured_logging: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    log = logging.getLogger("desk.test")
    log.info("calling %s with key %s", SECRET_RPC, SECRET_KEY)
    log.warning("extra fields", extra={"url": SECRET_RPC, "nested": {"k": [SECRET_KEY]}})
    log.info("env token tg-token-value-xyz leaked?")
    try:
        raise RuntimeError(f"boom {SECRET_KEY}")
    except RuntimeError:
        log.exception("failure")
    output = _read_all(configured_logging, capsys)
    assert SECRET_KEY not in output
    assert "11111111-2222-3333-4444-555555555555" not in output
    assert "tg-token-value-xyz" not in output
    assert REDACTED in output


def test_unknown_key_in_url_query_is_redacted(configured_logging: Path, capsys: pytest.CaptureFixture[str]) -> None:
    logging.getLogger("desk.test").info("GET https://example.org/x?api-key=zzzzzzzzzzzz&page=2")
    output = _read_all(configured_logging, capsys)
    assert "zzzzzzzzzzzz" not in output and "page=2" in output


def test_file_output_is_json_lines(configured_logging: Path, capsys: pytest.CaptureFixture[str]) -> None:
    logging.getLogger("desk.test").info("hello", extra={"provider": "dexscreener"})
    lines = _read_all(configured_logging, capsys).splitlines()
    record = json.loads(next(line for line in lines if line.startswith("{") and "hello" in line))
    assert record["provider"] == "dexscreener" and record["level"] == "INFO"


def test_loose_secrets_file_permissions_reported(tmp_path: Path) -> None:
    secrets_file = tmp_path / "secrets.env"
    secrets_file.write_text("JUPITER_API_KEY=abc123456\n")
    secrets_file.chmod(0o644)
    secrets = load_secrets(secrets_file)
    assert secrets.file_mode_ok is False
    assert any("chmod 600" in p for p in secrets.problems)
    assert secrets.configured("JUPITER_API_KEY")
