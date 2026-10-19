"""Optional allowlisted PAPER status/stop channel. No resume, arming, signing or shell commands."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

from desk.core.logging import redaction_filter
from desk.core.paths import DeskPaths
from desk.ops.backups import _atomic_json
from desk.paper.ledger import PaperLedger
from desk.storage.db import transaction

log = logging.getLogger("desk.telegram")


@dataclass(frozen=True)
class TelegramAction:
    command: str
    text: str


def build_daily_report(paths: DeskPaths) -> str:
    """A local report builder; calling this never sends a message or creates a database."""
    ledger = paths.db_dir / "ledger-paper.db"
    if not ledger.is_file():
        return "KESTREL | PAPER\nNo PAPER ledger exists yet.\nNew entries: " + (
            "STOPPED" if paths.stop_file.exists() else "not started")
    try:
        conn = sqlite3.connect(ledger.resolve().as_uri() + "?mode=ro", timeout=2, uri=True)
        conn.row_factory = sqlite3.Row
        try:
            mode = conn.execute("SELECT value FROM ledger_meta WHERE key='mode'").fetchone()
            if mode is None or mode[0] != "PAPER":
                return "KESTREL | PAPER\nLedger unavailable: mode check failed."
            cash = PaperLedger(conn).cash_lamports()
            open_count = conn.execute("SELECT COUNT(*) FROM positions WHERE state != 'CLOSED'").fetchone()[0]
            today = datetime.now(UTC).date().isoformat()
            closed, pnl = conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(realized_pnl_lamports), 0) FROM positions "
                "WHERE state='CLOSED' AND substr(closed_at,1,10)=?", (today,)).fetchone()
            halted = paths.stop_file.exists() or PaperLedger(conn).halted() is not None
        finally:
            conn.close()
    except sqlite3.Error:
        return "KESTREL | PAPER\nLedger unavailable. Check desk doctor locally."
    text = (f"KESTREL | PAPER\nUTC day: {today}\nCash: {cash / 1_000_000_000:.6f} SOL\n"
            f"Open positions: {open_count}\nClosed today: {closed}\n"
            f"Realized net today: {pnl / 1_000_000_000:+.6f} SOL\n"
            f"New entries: {'STOPPED' if halted else 'permitted by local risk checks'}")
    return redaction_filter().redact(text)


def _stop_paper(paths: DeskPaths) -> bool:
    paths.home.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW
    fd = os.open(paths.stop_file, flags, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(f"Telegram PAPER stop {datetime.now(UTC).isoformat()}\n")
        stream.flush()
        os.fsync(stream.fileno())
    ledger = paths.db_dir / "ledger-paper.db"
    if not ledger.is_file():
        return True  # STOP remains effective before first startup.
    try:
        conn = sqlite3.connect(ledger.resolve().as_uri() + "?mode=rw", timeout=2, isolation_level=None, uri=True)
        conn.row_factory = sqlite3.Row
        try:
            mode = conn.execute("SELECT value FROM ledger_meta WHERE key='mode'").fetchone()
            if mode is None or mode[0] != "PAPER":
                return False
            with transaction(conn):
                PaperLedger(conn).halt("Telegram /stop", datetime.now(UTC))
        finally:
            conn.close()
        return True
    except sqlite3.Error:
        return False


def handle_update(update: Any, allowed_chat_id: int, paths: DeskPaths) -> TelegramAction | None:
    """Ignore unauthorized/malformed updates before doing any filesystem work."""
    if type(allowed_chat_id) is not int or not isinstance(update, dict):
        return None
    message = update.get("message")
    if not isinstance(message, dict) or not isinstance(message.get("chat"), dict):
        return None
    chat_id = message["chat"].get("id")
    if type(chat_id) is not int or chat_id != allowed_chat_id:
        return None
    text = message.get("text")
    if not isinstance(text, str) or len(text) > 100:
        return None
    command = text.strip()
    if command == "/status":
        return TelegramAction(command, build_daily_report(paths))
    if command == "/stop":
        try:
            complete = _stop_paper(paths)
        except OSError:
            return TelegramAction(command, "KESTREL | PAPER\nSTOP could not be written. Run desk stop locally now.")
        message = ("New entries STOPPED. The independent guardian keeps supervising open PAPER positions."
                   if complete else "STOP file set; ledger halt could not be confirmed. Run desk doctor locally.")
        return TelegramAction(command, redaction_filter().redact("KESTREL | PAPER\n" + message))
    return None


async def poll_telegram(paths: DeskPaths, token: str, allowed_chat_id: int, stop_event: asyncio.Event,
                        *, client: httpx.AsyncClient | None = None) -> None:
    """Explicit opt-in worker. Tests inject a transport; no polling happens at import/startup."""
    if not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]{20,}", token) or type(allowed_chat_id) is not int:
        raise ValueError("valid Telegram token and exact integer chat ID are required")
    redaction_filter().update([token])
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    paths.home.mkdir(parents=True, exist_ok=True)
    offset_file = paths.home / "telegram-offset.json"
    offset = int(json.loads(offset_file.read_text())["offset"]) if offset_file.exists() else 0
    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=35, follow_redirects=False)
    base = f"https://api.telegram.org/bot{token}"
    try:
        while not stop_event.is_set():
            try:
                response = await http.post(base + "/getUpdates", json={"offset": offset, "timeout": 20,
                                                                      "allowed_updates": ["message"]})
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict) or payload.get("ok") is not True or not isinstance(payload.get("result"), list):
                    raise ValueError("invalid Telegram response")
                for update in payload["result"]:
                    if not isinstance(update, dict) or type(update.get("update_id")) is not int:
                        continue
                    if update["update_id"] < offset:
                        continue
                    action = handle_update(update, allowed_chat_id, paths)
                    if action is not None:
                        sent = await http.post(base + "/sendMessage", json={"chat_id": allowed_chat_id,
                                                                           "text": action.text[:4000]})
                        sent.raise_for_status()
                        receipt = sent.json()
                        if not isinstance(receipt, dict) or receipt.get("ok") is not True:
                            raise ValueError("Telegram refused response")
                    offset = update["update_id"] + 1
                    _atomic_json(offset_file, {"offset": offset})
            except (httpx.HTTPError, ValueError, TypeError, KeyError):
                # Exceptions often contain a token-bearing URL; never log the exception text.
                log.warning("Telegram connection failed; local PAPER controls remain available")
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop_event.wait(), timeout=5)
    finally:
        if owns_client:
            await http.aclose()
