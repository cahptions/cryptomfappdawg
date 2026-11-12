"""Phone alerts: short plain messages to ntfy and/or Telegram, opt-in and off by default.

Where they go is read from ``~/.config/desk/secrets.env`` only, never from the browser:

- ``NTFY_TOPIC_URL`` (``https://ntfy.sh/<a long random topic>``, or your own ntfy server) and, for a protected topic,
  ``NTFY_TOKEN``;
- ``TELEGRAM_BOT_TOKEN`` and ``TELEGRAM_CHAT_ID``, the same bot and chat as the optional Telegram desk control
  (docs/RUNBOOK.md → Optional Telegram), through its sender (desk.ops.telegram.send_message).

What is sent, each switchable in Settings → Phone alerts (``stocks-alerts.json``, no secrets in it):

- ``schwab``: the 7-day Schwab sign-in ends within 24 hours, or has ended;
- ``tracked_plays``: one of My tracked plays reached its take-profit or its stop;
- ``lotto``: a PAPER lotto is worth 3× what it cost;
- ``filings``: a stock on your watchlist filed an 8-K red flag or a late-filing notice (NT 10-K/Q), first seen since
  alerts were switched on;
- ``worker``: the Stocks worker stopped or stuck, or a job failing for over ``job_hours`` (desk.stocks.watchdog);
- ``research``: the daily research not finished ``research_minutes`` after the close.

Every attempt is written to the append-only ``stk_alerts`` log (research migration 0016). An event is sent once
(its ``key``); a failed one is retried at most every 30 minutes, four times. No more than 6 alerts an hour and 24 a
day go out; the rest wait for the next check. Messages carry no keys, addresses or account values beyond what the
event needs (a play's return in percent, a lotto's multiple). Research and PAPER only: no alert places an order.
"""

from __future__ import annotations

import contextlib
import json
import math
import re
import sqlite3
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

import httpx

from desk.core.clock import to_iso
from desk.core.errors import DeskError
from desk.core.paths import DeskPaths
from desk.core.secrets import Secrets, load_secrets

SETTINGS_FILE = "stocks-alerts.json"
EVENTS = {"schwab": "Schwab sign-in ending or ended", "tracked_plays": "My tracked plays: take-profit or stop reached",
          "lotto": "A PAPER lotto reached 3× its cost", "filings": "Watchlist: 8-K red flag or late filing",
          "worker": "Worker stopped, or a job failing for hours", "research": "Daily research late after the close"}
DEFAULTS: dict[str, Any] = {"enabled": False, "events": dict.fromkeys(EVENTS, True), "job_hours": 6,
                            "research_minutes": 180, "enabled_at": None}
PER_HOUR, PER_DAY = 6, 24
RETRY = timedelta(minutes=30)
MAX_FAILURES = 4
TEST_PER_HOUR = 5
TITLE = "KESTREL Stocks"
_TOPIC = re.compile(r"/[A-Za-z0-9_-]{1,64}")


# ── settings ────────────────────────────────────────────────────────────────────────────────────
def load(paths: DeskPaths) -> dict[str, Any]:
    out = {**DEFAULTS, "events": dict(DEFAULTS["events"])}
    try:
        path = paths.home / SETTINGS_FILE
        value = json.loads(path.read_text(encoding="utf-8")) if path.is_file() and path.stat().st_size < 8192 else {}
    except (OSError, ValueError):
        value = {}
    if not isinstance(value, dict):
        return out
    if isinstance(value.get("enabled"), bool):
        out["enabled"] = value["enabled"]
    if isinstance(value.get("events"), dict):
        out["events"].update({k: v for k, v in value["events"].items() if k in EVENTS and isinstance(v, bool)})
    for key, low, high in (("job_hours", 1, 72), ("research_minutes", 30, 720)):
        if type(value.get(key)) is int and low <= value[key] <= high:
            out[key] = value[key]
    if isinstance(value.get("enabled_at"), str):
        with contextlib.suppress(ValueError):
            out["enabled_at"] = datetime.fromisoformat(value["enabled_at"]).isoformat()
    return out


def save(paths: DeskPaths, update: dict[str, Any], now: datetime) -> dict[str, Any]:
    from desk.stocks.connection import _atomic_private

    current = load(paths)
    if "enabled" in update:
        if not isinstance(update["enabled"], bool):
            raise DeskError("Alerts are on or off")
        if update["enabled"] and not current["enabled"]:
            current["enabled_at"] = now.isoformat()      # older filings and lottos are never sent on switching on
        current["enabled"] = update["enabled"]
    for name, value in (update.get("events") or {}).items():
        if name not in EVENTS or not isinstance(value, bool):
            raise DeskError("Unknown alert")
        current["events"][name] = value
    for key, low, high in (("job_hours", 1, 72), ("research_minutes", 30, 720)):
        if key in update:
            if type(update[key]) is not int or not low <= update[key] <= high:
                raise DeskError(f"{key.replace('_', ' ')} must be a whole number from {low} to {high}")
            current[key] = update[key]
    paths.home.mkdir(parents=True, exist_ok=True)
    _atomic_private(paths.home / SETTINGS_FILE, json.dumps(current))
    return current


# ── channels ────────────────────────────────────────────────────────────────────────────────────
def channels(secrets: Secrets) -> dict[str, Any]:
    """Which channels are set up, and what is wrong with one that is half set up. Never returns a value."""
    from desk.ops.telegram import valid_token

    found: dict[str, Any] = {"ntfy": False, "telegram": False, "problems": []}
    topic = secrets.get("NTFY_TOPIC_URL")
    if topic:
        parts = urlsplit(topic)
        if (parts.scheme == "https" and parts.hostname and not parts.username and not parts.password
                and not parts.query and not parts.fragment and _TOPIC.fullmatch(parts.path)):
            found["ntfy"] = True
        else:
            found["problems"].append("NTFY_TOPIC_URL in secrets.env must be an https address ending in the topic, "
                                     "like https://ntfy.sh/your-long-random-topic")
    token, chat = secrets.get("TELEGRAM_BOT_TOKEN"), secrets.get("TELEGRAM_CHAT_ID")
    if token or chat:
        if valid_token(token) and chat is not None and re.fullmatch(r"-?[0-9]{1,20}", chat):
            found["telegram"] = True
        else:
            found["problems"].append("Telegram needs both TELEGRAM_BOT_TOKEN and a numeric TELEGRAM_CHAT_ID in "
                                     "secrets.env")
    return found


def deliver(text: str, secrets: Secrets, client: httpx.Client | None = None) -> list[str]:
    """Send one message to every channel that is set up; the names of those that took it."""
    from desk.ops.telegram import send_message

    ready = channels(secrets)
    owns = client is None
    http = client or httpx.Client(timeout=15, follow_redirects=False)
    took: list[str] = []
    try:
        if ready["ntfy"]:
            headers = {"Title": TITLE, "Tags": "bird"}
            if token := secrets.get("NTFY_TOKEN"):
                headers["Authorization"] = f"Bearer {token}"
            try:
                answer = http.post(str(secrets.get("NTFY_TOPIC_URL")), content=text.encode("utf-8"), headers=headers)
                if answer.status_code == 200:
                    took.append("ntfy")
            except httpx.HTTPError:
                pass                # never logged: the error names the private topic address
        if ready["telegram"] and send_message(http, str(secrets.get("TELEGRAM_BOT_TOKEN")),
                                              int(str(secrets.get("TELEGRAM_CHAT_ID"))), f"{TITLE} · {text}"):
            took.append("telegram")
    finally:
        if owns:
            http.close()
    return took


# ── the log ─────────────────────────────────────────────────────────────────────────────────────
def _log(research: sqlite3.Connection, at: datetime, event: str, key: str, text: str, took: list[str],
         outcome: str) -> None:
    research.execute("INSERT INTO stk_alerts(at, event, key, text, channels, outcome) VALUES (?,?,?,?,?,?)",
                     (to_iso(at), event, key, text, ",".join(took), outcome))


def recent(research: sqlite3.Connection, limit: int = 12) -> list[dict[str, Any]]:
    return [dict(r) for r in research.execute("SELECT at, event, text, channels, outcome FROM stk_alerts "
                                              "ORDER BY id DESC LIMIT ?", (limit,))]


def _sent_since(research: sqlite3.Connection, since: datetime, outcome: str = "sent") -> int:
    return int(research.execute("SELECT COUNT(*) FROM stk_alerts WHERE outcome=? AND at>=?",
                                (outcome, to_iso(since))).fetchone()[0])


def _due(research: sqlite3.Connection, key: str, now: datetime) -> bool:
    rows = research.execute("SELECT outcome, at FROM stk_alerts WHERE key=? ORDER BY id", (key,)).fetchall()
    if any(r[0] == "sent" for r in rows):
        return False
    failures = [datetime.fromisoformat(r[1]) for r in rows if r[0] == "failed"]
    return len(failures) < MAX_FAILURES and (not failures or now - failures[-1] >= RETRY)


# ── what to say ─────────────────────────────────────────────────────────────────────────────────
Candidate = tuple[str, str, str]          # event, key, text


def _hours(value: float) -> str:
    return f"about {max(1, round(value))} hour{'s' if round(value) != 1 else ''}"


def schwab_events(status: dict[str, Any]) -> list[Candidate]:
    if status.get("relogin_required"):
        mark = status.get("signed_in_at") or status.get("ended_at") or "unknown"
        return [("schwab", f"schwab-ended:{mark}", "Schwab sign-in has ended: quotes and option chains stop until "
                 "you sign in again (kestrel signin, or Settings in the app).")]
    left = status.get("login_days_left")
    if status.get("connected") and isinstance(left, (int, float)) and 0 < left <= 1:
        return [("schwab", f"schwab-ending:{status.get('signed_in_at')}", f"Schwab sign-in ends in {_hours(left * 24)} "
                 "(it lasts 7 days). Sign in again before then: kestrel signin, or Settings in the app.")]
    return []


def tracked_play_events(market: sqlite3.Connection, research: sqlite3.Connection, now: datetime) -> list[Candidate]:
    from desk.stocks.play_history import label
    from desk.stocks.play_selections import view

    out: list[Candidate] = []
    rows = research.execute("SELECT p.id FROM stk_play_selections p WHERE EXISTS (SELECT 1 FROM "
                            "stk_play_selection_events e WHERE e.selection_id=p.id AND e.kind='ENTRY') AND NOT EXISTS "
                            "(SELECT 1 FROM stk_play_selection_events e WHERE e.selection_id=p.id AND e.kind IN "
                            "('CLOSE','CANCEL','EXPIRED_NO_ENTRY')) ORDER BY p.confirmed_at DESC LIMIT 50").fetchall()
    for (ident,) in rows:
        try:
            found = view(research, ident, now, personal=True, market=market)
        except (DeskError, KeyError, ValueError, TypeError):
            continue
        state = ((found.get("chart") or {}).get("exit_status") or {})
        if state.get("state") in {"take_profit", "stop"}:
            what = "take-profit" if state["state"] == "take_profit" else "stop"
            out.append(("tracked_plays", f"play:{ident}:{state['state']}",
                        f"Your tracked play {label(found['idea']['play'])} reached its {what}: {state['text']}. "
                        "PAPER reference; no order was placed."))
    return out


def lotto_events(ledger: sqlite3.Connection, now: datetime, enabled_at: str | None) -> list[Candidate]:
    from desk.stocks.paper.strategies import STRATEGIES

    titles = {s.id: s.title for s in STRATEGIES}
    accounts = [s.id for s in STRATEGIES if "lotto" in s.id]
    if not accounts:
        return []
    since = to_iso((datetime.fromisoformat(enabled_at) if enabled_at else now) - timedelta(days=1))
    rows = ledger.execute(
        "SELECT id, account, symbol, legs_json, entry_cost_micros, peak_value_micros, exit_credit_micros "
        "FROM stk_positions WHERE asset='option' AND entry_cost_micros > 0 AND account IN "
        "(SELECT value FROM json_each(?)) AND MAX(COALESCE(peak_value_micros, 0), COALESCE(exit_credit_micros, 0)) "
        ">= 3 * entry_cost_micros AND (state='OPEN' OR closed_at >= ?)", (json.dumps(accounts), since)).fetchall()
    out: list[Candidate] = []
    for row in rows:
        best = max(row["peak_value_micros"] or 0, row["exit_credit_micros"] or 0) / row["entry_cost_micros"]
        legs = json.loads(row["legs_json"])
        leg = legs[0] if legs else {}
        contract = (f" ${leg['strike']:g} {leg.get('kind', '')} {leg.get('expiration', '')}".rstrip()
                    if isinstance(leg.get("strike"), (int, float)) and math.isfinite(leg["strike"]) else "")
        out.append(("lotto", f"lotto3x:{row['id']}", f"PAPER lotto {row['symbol']}{contract} "
                    f"({titles.get(row['account'], row['account'])}) reached {best:.1f}× its cost."))
    return out


def filing_events(market: sqlite3.Connection, research: sqlite3.Connection, now: datetime,
                  enabled_at: str | None) -> list[Candidate]:
    from desk.stocks.signals import ITEM_WORDS, RED_FLAG_FORMS, RED_FLAG_ITEMS

    watch = [r[0] for r in research.execute("SELECT symbol FROM stk_watchlist")]
    if not watch:
        return []
    since = max(datetime.fromisoformat(enabled_at) if enabled_at else now, now - timedelta(days=3))
    rows = market.execute(
        "SELECT l.symbol, f.accession, f.form, f.filed, f.items FROM stk_filings f JOIN stk_listings l ON l.cik=f.cik "
        "WHERE l.symbol IN (SELECT value FROM json_each(?)) AND f.first_seen >= ? ORDER BY f.filed",
        (json.dumps(watch), to_iso(since))).fetchall()
    out: list[Candidate] = []
    from desk.stocks.repo import date_of

    for row in rows:
        items = [i for i in (row["items"] or "").split(",") if i]
        flags = [ITEM_WORDS[i] for i in items if i in RED_FLAG_ITEMS] if row["form"] in ("8-K", "8-K/A") else []
        if row["form"] in RED_FLAG_FORMS:
            flags.append(RED_FLAG_FORMS[row["form"]])
        if flags:
            filed = date_of(row["filed"])
            out.append(("filings", f"filing:{row['accession']}", f"{row['symbol']} (on your watchlist) "
                        f"{'; '.join(flags)}: SEC {row['form']} filed {filed:%b} {filed.day}."))
    return out


def watchdog_events(issues: list[Any]) -> list[Candidate]:
    return [("research" if i.kind == "research" else "worker", f"watchdog:{i.id}:{i.since}", i.text) for i in issues]


# ── one check ───────────────────────────────────────────────────────────────────────────────────
def cycle(paths: DeskPaths, loaded: Any, now: datetime, *, client: httpx.Client | None = None,
          issues: list[Any] | None = None) -> dict[str, Any]:
    """Find what happened, then send what is new, within the rate limits. Returns what was done."""
    settings = load(paths)
    secrets = load_secrets(paths.secrets_file)
    ready = channels(secrets)
    if not settings["enabled"] or not (ready["ntfy"] or ready["telegram"]):
        return {"enabled": settings["enabled"], "channels": ready, "sent": 0, "held": 0}
    from desk.stocks.connection import connection_status
    from desk.stocks.repo import StocksDatabases

    if issues is None:
        from desk.stocks import watchdog

        issues = watchdog.record(paths, now, job_hours=settings["job_hours"],
                                 research_minutes=settings["research_minutes"])
    on = settings["events"]
    dbs = StocksDatabases.open(paths)
    try:
        candidates: list[Candidate] = []
        finders: list[tuple[str, Callable[[], list[Candidate]]]] = [
            ("schwab", lambda: schwab_events(connection_status(paths, loaded.settings.stocks, secrets)["schwab"])),
            ("worker", lambda: [c for c in watchdog_events(issues or []) if c[0] == "worker"]),
            ("research", lambda: [c for c in watchdog_events(issues or []) if c[0] == "research"]),
            ("tracked_plays", lambda: tracked_play_events(dbs.market, dbs.research, now)),
            ("lotto", lambda: lotto_events(dbs.ledger, now, settings["enabled_at"])),
            ("filings", lambda: filing_events(dbs.market, dbs.research, now, settings["enabled_at"])),
        ]
        problems: list[str] = []
        for name, find in finders:
            if on.get(name):
                try:
                    candidates += find()
                except (sqlite3.Error, DeskError, ValueError, KeyError, TypeError) as exc:
                    problems.append(f"{name}: {type(exc).__name__}")
        sent = held = 0
        for event, key, text in candidates:
            if not _due(dbs.research, key, now):
                continue
            if (_sent_since(dbs.research, now - timedelta(hours=1)) >= PER_HOUR
                    or _sent_since(dbs.research, now - timedelta(days=1)) >= PER_DAY):
                held += 1
                continue
            took = deliver(text, secrets, client)
            _log(dbs.research, now, event, key, text, took, "sent" if took else "failed")
            sent += bool(took)
        return {"enabled": True, "channels": ready, "sent": sent, "held": held, "candidates": len(candidates),
                "problems": problems}
    finally:
        dbs.close()


def send_test(paths: DeskPaths, now: datetime, *, client: httpx.Client | None = None) -> dict[str, Any]:
    """``Send test alert`` and ``desk stocks alerts test``: one fixed message to every channel set up, whether or not
    alerts are switched on, at most 5 an hour."""
    from desk.stocks.repo import StocksDatabases

    secrets = load_secrets(paths.secrets_file)
    ready = channels(secrets)
    if not (ready["ntfy"] or ready["telegram"]):
        raise DeskError("No alert channel is set up. Add NTFY_TOPIC_URL (or TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID) "
                        "to ~/.config/desk/secrets.env, then restart Stocks (kestrel restart). "
                        + " ".join(ready["problems"]))
    dbs = StocksDatabases.open(paths)
    try:
        if _sent_since(dbs.research, now - timedelta(hours=1), "test") >= TEST_PER_HOUR:
            raise DeskError("Five test alerts were sent in the last hour; try again later.")
        text = "Test alert. Phone alerts from KESTREL Stocks reach this device. PAPER research only."
        took = deliver(text, secrets, client)
        _log(dbs.research, now, "test", f"test:{now.isoformat()}", text, took, "test")
    finally:
        dbs.close()
    missing = [name for name in ("ntfy", "telegram") if ready[name] and name not in took]
    return {"sent_to": took, "failed": missing, "ok": bool(took) and not missing,
            "message": ("Sent to " + " and ".join(took) + "." if took else "Not delivered.")
            + (f" {' and '.join(missing)} did not accept it: check the address and token in secrets.env."
               if missing else "")}


def view(paths: DeskPaths) -> dict[str, Any]:
    """Settings → Phone alerts: the switches, which channels are set up (never their addresses) and recent alerts."""
    from desk.stocks.repo import RESEARCH_FILE

    out = {**load(paths), "labels": EVENTS, "channels": channels(load_secrets(paths.secrets_file)), "recent": [],
           "limits": {"per_hour": PER_HOUR, "per_day": PER_DAY}}
    path = paths.db_dir / RESEARCH_FILE
    if path.is_file():
        conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)
        conn.row_factory = sqlite3.Row
        try:
            out["recent"] = recent(conn)
        except sqlite3.Error:
            pass                   # before migration 0016 runs (the app's next start) there is no log yet
        finally:
            conn.close()
    return out


__all__ = ["EVENTS", "channels", "cycle", "deliver", "load", "save", "send_test", "view"]
