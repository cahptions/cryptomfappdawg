"""Phone alerts: off by default, sent once per event, rate-limited, to ntfy and Telegram read from secrets.env only;
messages carry no secrets. The watchdog behind the worker and research alerts. All network is a fake transport."""

from __future__ import annotations

import json
import os
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from desk.core.config import load_config
from desk.core.errors import DeskError
from desk.core.paths import DeskPaths
from desk.core.worker_lock import WorkerLock
from desk.stocks import alerts, watchdog
from desk.stocks.paper.book import PaperBook
from desk.stocks.repo import StocksDatabases, day_of
from tests.conftest import REPO

TOPIC = "https://ntfy.sh/kestrel-a8f3c2d9e7b1f0a6c4d2"
NTFY_TOKEN = "tk_secretntfytoken0123456789"
BOT = "123456789:AAH-secret_bot_token_value_0123456789"
NOW = datetime(2026, 11, 12, 18, 0, tzinfo=UTC)          # Thursday 1 PM New York


@pytest.fixture(autouse=True)
def no_environment_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("NTFY_TOPIC_URL", "NTFY_TOKEN", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
        monkeypatch.delenv(name, raising=False)


def setup(tmp_path: Path, secrets: dict[str, str] | None = None) -> DeskPaths:
    paths = DeskPaths(tmp_path / "home", REPO / "config", tmp_path / "secrets.env")
    lines = "".join(f"{k}={v}\n" for k, v in (secrets if secrets is not None else
                                              {"NTFY_TOPIC_URL": TOPIC, "NTFY_TOKEN": NTFY_TOKEN}).items())
    paths.secrets_file.write_text(lines)
    os.chmod(paths.secrets_file, 0o600)
    StocksDatabases.open(paths).close()
    return paths


class Phone:
    """A fake ntfy server and Telegram API."""

    def __init__(self, status: int = 200) -> None:
        self.requests: list[httpx.Request] = []
        self.status = status

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.host == "api.telegram.org":
            return httpx.Response(self.status, json={"ok": self.status == 200, "result": {}})
        return httpx.Response(self.status, json={"id": "x"})

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self))

    def bodies(self) -> list[str]:
        return [r.content.decode() for r in self.requests]


def log(paths: DeskPaths) -> list[dict[str, Any]]:
    dbs = StocksDatabases.open(paths)
    try:
        return [dict(r) for r in dbs.research.execute("SELECT * FROM stk_alerts ORDER BY id")]
    finally:
        dbs.close()


def red_flags(paths: DeskPaths, count: int, first_seen: datetime) -> None:
    dbs = StocksDatabases.open(paths)
    try:
        dbs.market.execute("INSERT INTO stk_listings VALUES ('XYZ','XYZ Corp','NYSE','common',0,0,77,1,?,?)",
                           (first_seen.isoformat(), first_seen.isoformat()))
        dbs.research.execute("INSERT INTO stk_watchlist(symbol, added_at) VALUES ('XYZ', ?)", (first_seen.isoformat(),))
        for k in range(count):
            form, items = ("NT 10-Q", "") if k == 0 else ("8-K", "4.02,9.01")
            dbs.market.execute("INSERT INTO stk_filings VALUES (?,77,?,?,NULL,NULL,?,'x.htm',?)",
                               (f"acc-{k}", form, day_of(date(2026, 11, 11)), items, first_seen.isoformat()))
        dbs.market.execute("INSERT INTO stk_filings VALUES ('old',77,'8-K',?,NULL,NULL,'1.03','x.htm',?)",
                           (day_of(date(2026, 1, 5)), (first_seen - timedelta(days=200)).isoformat()))
    finally:
        dbs.close()


def test_alerts_are_off_by_default_and_nothing_is_sent(tmp_path: Path) -> None:
    paths = setup(tmp_path)
    red_flags(paths, 2, NOW)
    phone = Phone()
    assert alerts.load(paths)["enabled"] is False
    done = alerts.cycle(paths, load_config(REPO / "config"), NOW, client=phone.client(), issues=[])
    assert done["sent"] == 0 and phone.requests == [] and log(paths) == []


def test_channels_come_from_secrets_only_and_problems_never_echo_a_value(tmp_path: Path) -> None:
    from desk.core.secrets import load_secrets

    paths = setup(tmp_path, {"NTFY_TOPIC_URL": "http://ntfy.sh/plain-http-topic", "TELEGRAM_BOT_TOKEN": BOT})
    found = alerts.channels(load_secrets(paths.secrets_file))
    assert found["ntfy"] is False and found["telegram"] is False and len(found["problems"]) == 2
    assert not any("plain-http-topic" in p or BOT in p for p in found["problems"])
    paths = setup(tmp_path, {"NTFY_TOPIC_URL": TOPIC, "TELEGRAM_BOT_TOKEN": BOT, "TELEGRAM_CHAT_ID": "-100123"})
    assert alerts.channels(load_secrets(paths.secrets_file)) == {"ntfy": True, "telegram": True, "problems": []}
    assert TOPIC in load_secrets(paths.secrets_file).all_secret_values()          # redacted from logs like a key


def test_a_test_alert_reaches_both_channels_and_holds_no_secret(tmp_path: Path) -> None:
    paths = setup(tmp_path, {"NTFY_TOPIC_URL": TOPIC, "NTFY_TOKEN": NTFY_TOKEN, "TELEGRAM_BOT_TOKEN": BOT,
                             "TELEGRAM_CHAT_ID": "4242"})
    phone = Phone()
    sent = alerts.send_test(paths, NOW, client=phone.client())
    assert sent["ok"] and sent["sent_to"] == ["ntfy", "telegram"] and "Sent to ntfy and telegram" in sent["message"]
    ntfy, telegram = phone.requests
    assert str(ntfy.url) == TOPIC and ntfy.headers["authorization"] == f"Bearer {NTFY_TOKEN}"
    assert ntfy.headers["title"] == "KESTREL Stocks" and "Test alert" in ntfy.content.decode()
    assert telegram.url.path == f"/bot{BOT}/sendMessage" and json.loads(telegram.content)["chat_id"] == 4242
    row = log(paths)[0]
    assert row["outcome"] == "test" and row["channels"] == "ntfy,telegram"
    for secret in (TOPIC, NTFY_TOKEN, BOT, "4242"):
        assert secret not in json.dumps(row) and secret not in json.dumps(sent)
    for _ in range(4):
        alerts.send_test(paths, NOW, client=phone.client())
    with pytest.raises(DeskError, match="Five test alerts"):
        alerts.send_test(paths, NOW, client=phone.client())


def test_a_test_alert_without_a_channel_says_what_to_add(tmp_path: Path) -> None:
    paths = setup(tmp_path, {})
    with pytest.raises(DeskError, match="NTFY_TOPIC_URL"):
        alerts.send_test(paths, NOW, client=Phone().client())


def test_each_event_is_sent_once_within_the_rate_limit(tmp_path: Path) -> None:
    paths = setup(tmp_path)
    alerts.save(paths, {"enabled": True, "events": {"worker": False}}, NOW - timedelta(minutes=5))
    red_flags(paths, 8, NOW)
    phone, loaded = Phone(), load_config(REPO / "config")
    first = alerts.cycle(paths, loaded, NOW, client=phone.client(), issues=[])
    assert first["sent"] == 6 and first["held"] == 2                              # 6 an hour at most
    assert "XYZ (on your watchlist) filed its quarterly report late (NT 10-Q): SEC NT 10-Q filed Nov 11." in phone.bodies()
    assert any("said past financial statements can no longer be relied on" in b for b in phone.bodies())
    assert not any("bankruptcy" in b for b in phone.bodies())                      # first seen before alerts were on
    again = alerts.cycle(paths, loaded, NOW + timedelta(minutes=5), client=phone.client(), issues=[])
    assert again["sent"] == 0 and len(phone.requests) == 6
    later = alerts.cycle(paths, loaded, NOW + timedelta(hours=1, minutes=1), client=phone.client(), issues=[])
    assert later["sent"] == 2 and len({r["key"] for r in log(paths)}) == 8 == len(log(paths))


def test_a_failed_delivery_is_logged_and_retried_after_half_an_hour(tmp_path: Path) -> None:
    paths = setup(tmp_path)
    alerts.save(paths, {"enabled": True}, NOW - timedelta(minutes=5))
    red_flags(paths, 1, NOW)
    loaded = load_config(REPO / "config")
    alerts.cycle(paths, loaded, NOW, client=Phone(500).client(), issues=[])
    assert [r["outcome"] for r in log(paths)] == ["failed"]
    phone = Phone()
    alerts.cycle(paths, loaded, NOW + timedelta(minutes=10), client=phone.client(), issues=[])
    assert phone.requests == []
    alerts.cycle(paths, loaded, NOW + timedelta(minutes=31), client=phone.client(), issues=[])
    assert [r["outcome"] for r in log(paths)] == ["failed", "sent"] and len(phone.requests) == 1


def test_schwab_sign_in_ending_and_ended() -> None:
    ending = alerts.schwab_events({"connected": True, "login_days_left": 0.4, "signed_in_at": "2026-11-05T14:00:00"})
    assert ending[0][1] == "schwab-ending:2026-11-05T14:00:00" and "ends in about 10 hours" in ending[0][2]
    assert alerts.schwab_events({"connected": True, "login_days_left": 3.0, "signed_in_at": "x"}) == []
    ended = alerts.schwab_events({"relogin_required": True, "signed_in_at": "2026-11-05T14:00:00"})
    assert ended[0][1] == "schwab-ended:2026-11-05T14:00:00" and "kestrel signin" in ended[0][2]
    assert alerts.schwab_events({"connected": False, "reason": "Run desk stocks connect"}) == []


def test_a_paper_lotto_at_three_times_its_cost(tmp_path: Path) -> None:
    paths = setup(tmp_path)
    dbs = StocksDatabases.open(paths)
    try:
        book = PaperBook(dbs.ledger)
        legs = [{"occ": "TSLA261120C00500000", "strike": 500.0, "kind": "call", "expiration": "2026-11-20",
                 "side": "buy", "quantity": 1}]
        for account, value in (("lotto_runners", 0.75), ("lotto_picks", 0.5), ("momentum", 9.0)):
            decision = book.decide(NOW, date(2026, 11, 11), account, "sha", "TSLA", "ENTER", ["x"], {})
            pid = book.open_position(account=account, spec_sha256="sha", asset="option", symbol="TSLA",
                                     structure="long_call", legs=legs, decision_id=decision, at=NOW,
                                     day=date(2026, 11, 11), cost=0.25, fees=0.65, max_loss=0.9, stop_price=None,
                                     expires=date(2026, 11, 20), plan={})
            book.mark(pid, value)
        found = alerts.lotto_events(dbs.ledger, NOW, None)
    finally:
        dbs.close()
    assert len(found) == 1 and found[0][0] == "lotto"
    assert found[0][2] == "PAPER lotto TSLA $500 call 2026-11-20 (Lotto picks · let them run) reached 3.0× its cost."


def test_a_tracked_play_at_its_take_profit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from desk.stocks import play_selections

    paths = setup(tmp_path)
    play = {"symbol": "AAPL", "structure": "long_call", "action": "BUY", "expiration": "2026-12-18",
            "legs": [{"occ": "AAPL261218C00200000", "strike": 200.0, "side": "buy", "kind": "call",
                      "expiration": "2026-12-18"}]}
    dbs = StocksDatabases.open(paths)
    try:
        dbs.research.execute("INSERT INTO stk_play_ideas VALUES ('i1','2026-11-10T21:00:00+00:00',1,'c','k',?,'m')",
                             (json.dumps(play),))
        for ident in ("s1", "s2"):
            dbs.research.execute("INSERT INTO stk_play_selections VALUES (?,?,'i1','2026-11-10T21:00:00+00:00',"
                                 "'2026-11-11T14:30:00+00:00',1)", (ident, ident + "-request"))
            dbs.research.execute("INSERT INTO stk_play_selection_events(selection_id,kind,at,detail_json) "
                                 "VALUES (?,'ENTRY','2026-11-11T15:00:00+00:00','{}')", (ident,))
        dbs.research.execute("INSERT INTO stk_play_selection_events(selection_id,kind,at,detail_json) "
                             "VALUES ('s2','CLOSE','2026-11-12T15:00:00+00:00','{}')")

        def view(research: Any, ident: str, now: datetime, **_: Any) -> dict[str, Any]:
            return {"idea": {"play": play}, "chart": {"exit_status": {
                "state": "take_profit", "text": "Take-profit reached: +52% against a +50% target"}}}

        monkeypatch.setattr(play_selections, "view", view)
        found = alerts.tracked_play_events(dbs.market, dbs.research, NOW)
    finally:
        dbs.close()
    assert [c[1] for c in found] == ["play:s1:take_profit"]                        # the closed one is not followed
    assert found[0][2] == ("Your tracked play AAPL $200 call · Dec 18 reached its take-profit: Take-profit reached: "
                           "+52% against a +50% target. PAPER reference; no order was placed.")


def test_watchdog_finds_a_stopped_worker_a_failing_job_and_late_research(tmp_path: Path) -> None:
    paths = setup(tmp_path)
    dbs = StocksDatabases.open(paths)
    try:
        dbs.market.execute("INSERT INTO stk_jobs(name,last_run_at,last_ok_at,last_error) VALUES "
                           "('prices',?,?,'HTTPStatusError: 403 key=abc123secret')",
                           (NOW.isoformat(), (NOW - timedelta(days=2)).isoformat()))
        dbs.research.execute("INSERT INTO stk_scores_latest(symbol, asof, computed_at, detail_json) "
                             "VALUES ('AAA', ?, ?, '{}')", (day_of(date(2026, 11, 10)), NOW.isoformat()))
    finally:
        dbs.close()
    issues, seen = watchdog.observe(paths, NOW, {}, worker_running=False)
    assert [i.id for i in issues] == ["research:2026-11-11"]                        # due Wed 7 PM ET, not done
    assert issues[0].text == ("Research for Wed Nov 11's close is not finished (due Wed Nov 11, 7:00 PM ET; the last "
                              "analysed close is Tue Nov 10).")
    later = NOW + timedelta(hours=7)
    issues, _ = watchdog.observe(paths, later, seen, worker_running=False)
    by = {i.id: i for i in issues}
    assert "stopped since Thu Nov 12, 1:00 PM ET" in by["worker_stopped"].text
    assert by["job:prices"].text.startswith("The prices job has been failing for 7 hours (authorization; last worked")
    assert "abc123secret" not in json.dumps([i.text for i in issues])
    (paths.home / "stocks-automation.json").write_text('{"enabled": false}')        # paused on purpose: no alarm
    assert "worker_stopped" not in {i.id for i in watchdog.observe(paths, later, seen, worker_running=False)[0]}
    events = alerts.watchdog_events(issues)
    assert {e[0] for e in events} == {"worker", "research"} and all(e[1].startswith("watchdog:") for e in events)
    recorded = watchdog.record(paths, NOW, worker_running=True)
    assert watchdog.saved(paths) == {"checked_at": NOW.isoformat(),
                                     "issues": [{"id": i.id, "kind": i.kind, "text": i.text, "since": i.since}
                                                for i in recorded]}


def test_a_stuck_worker_is_found(tmp_path: Path) -> None:
    paths = setup(tmp_path)
    (paths.home / "stocks-progress.json").write_text(json.dumps({"phase": "massive_options",
                                                                 "updated_at": (NOW - timedelta(hours=2)).isoformat()}))
    with WorkerLock(paths, "stocks-worker"):
        issues, _ = watchdog.observe(paths, NOW, {})
    assert [i.id for i in issues] == ["worker_stalled"] and "(step: massive options)" in issues[0].text


def test_settings_page_saves_switches_with_csrf_and_never_shows_where_alerts_go(tmp_path: Path) -> None:
    from desk.stocks.web import create_app

    paths = setup(tmp_path, {"NTFY_TOPIC_URL": TOPIC, "NTFY_TOKEN": NTFY_TOKEN})
    with TestClient(create_app(paths, auto_start=False), base_url="http://127.0.0.1:8767") as client:
        shown = client.get("/api/stocks/alerts").json()
        assert shown["enabled"] is False and shown["channels"]["ntfy"] is True
        assert TOPIC not in json.dumps(shown) and NTFY_TOKEN not in json.dumps(shown)
        body = {"enabled": True, "events": {"lotto": False}, "job_hours": 4}
        assert client.post("/api/stocks/alerts", json=body).status_code == 403          # no session token
        headers = {"X-CSRF-Token": client.get("/api/stocks/session").json()["csrf_token"]}
        saved = client.post("/api/stocks/alerts", json=body, headers=headers).json()
        assert saved["enabled"] and saved["events"]["lotto"] is False and saved["job_hours"] == 4
        assert saved["enabled_at"] is not None
        assert client.post("/api/stocks/alerts", json={"job_hours": 99}, headers=headers).status_code == 422
        assert client.post("/api/stocks/alerts", json={"events": {"bogus": True}}, headers=headers).status_code == 422
        assert client.post("/api/stocks/alerts/test", json={}).status_code == 403
