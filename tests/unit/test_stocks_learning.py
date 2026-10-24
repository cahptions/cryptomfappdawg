"""Uncapped virtual funding, later-quote execution, mature labels and isolation from existing PAPER."""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from desk.stocks import learning
from desk.stocks.contracts import OptionQuote
from desk.stocks.learning_service import allowed, control
from desk.stocks.paper.book import PaperBook
from desk.stocks.web import create_app
from tests.stocks_world import make_world

AT = datetime(2026, 10, 21, 14, 0, tzinfo=UTC)
EXP = date(2026, 11, 20)


def chain(world, at, bid=4.8, ask=5.0, source="synthetic_learning"):
    quotes = [
        OptionQuote(
            f"SPY261120{kind[0].upper()}00100000", "SPY", kind, 100, EXP, bid, ask, (bid + ask) / 2, 50000, 2_000_000, None, at
        )
        for kind in ("call", "put")
    ]
    world.market.save_option_chain("SPY", at.date(), EXP, at, 100, quotes, source=source)
    return quotes


def test_virtual_funding_is_uncapped_separate_from_profit_and_preserves_losses(tmp_path):
    world = make_world(tmp_path, n=12)
    conn = learning.open_book(world.paths)
    try:
        learning.initialize(conn, AT)
        book = PaperBook(conn)
        assert learning.fund(book, learning.RESEARCH, 2_000_000, AT)
        assert book.deposits(learning.RESEARCH) == 2_000_000 and book.cash(learning.RESEARCH) == 2_000_000
        main = learning.report(conn)["accounts"][0]
        assert main["additional_funding"] == 1_500_000 and main["trading_pnl"] == 0
        assert not learning.fund(book, learning.REFERENCE, 2_000_000, AT)
        book.add_cash(learning.RESEARCH, AT, AT.date(), -2_000_000 * 1_000_000, "FEE")
        assert learning.fund(book, learning.RESEARCH, 3_000_000, AT + timedelta(minutes=1))
        main = learning.report(conn)["accounts"][0]
        assert main["trading_pnl"] == -2_000_000 and main["contributed"] == 5_000_000
        learning.initialize(conn, AT + timedelta(minutes=2))
        assert book.deposits(learning.RESEARCH) == 5_000_000  # restart cannot reset losses or seed twice
    finally:
        conn.close()
        world.close()


def test_real_later_quotes_generate_partial_fills_funding_mature_outcomes_and_preserve_forward_books(tmp_path):
    world = make_world(tmp_path, n=12)
    conn = learning.open_book(world.paths)
    try:
        forward = list(world.dbs.ledger.iterdump())
        chain(world, AT)
        first = learning.tick(conn, world.market, AT)
        assert first["orders"] > 0 and first["filled"] == 0
        pending = conn.execute("SELECT COUNT(*) FROM stk_pending").fetchone()[0]
        learning.tick(conn, world.market, AT + timedelta(seconds=30))
        assert not PaperBook(conn).positions()
        assert conn.execute("SELECT COUNT(*) FROM stk_pending").fetchone()[0] == pending
        later = AT + timedelta(minutes=1)
        chain(world, later)
        second = learning.tick(conn, world.market, later)
        assert second["filled"] > 0
        report = learning.report(conn)
        assert report["accounts"][0]["additional_funding"] > 0
        assert report["accounts"][1]["additional_funding"] == 0
        assert all(a["trading_pnl"] < 0 and a["cash"] >= 0 for a in report["accounts"])
        assert all(value is None for value in report["limits"].values())
        fills = conn.execute("SELECT COUNT(*) FROM stk_learning_fills").fetchone()[0]
        learning.tick(conn, world.market, later)
        assert conn.execute("SELECT COUNT(*) FROM stk_learning_fills").fetchone()[0] == fills
        end = later + timedelta(minutes=121)
        chain(world, end, bid=5.8, ask=6.0)
        result = learning.tick(conn, world.market, end)
        assert result["closed"] > 0
        assert conn.execute("SELECT COUNT(*) FROM stk_learning_outcomes").fetchone()[0] > 0
        experiment = conn.execute("SELECT experiment_id FROM stk_learning_outcomes LIMIT 1").fetchone()[0]
        assert learning.forecast(conn, experiment, AT)["expected_net_return"] is None
        assert learning.forecast(conn, experiment, end + timedelta(seconds=1))["blocks"] >= 1
        order = learning.report(conn)["order_progress"][0]
        assert 0 <= order["filled_units"] <= order["desired_units"]
        assert list(world.dbs.ledger.iterdump()) == forward
    finally:
        conn.close()
        world.close()


def test_new_sizing_hypotheses_have_no_old_position_or_premium_policy_ceiling(tmp_path):
    world = make_world(tmp_path, n=12)
    conn = learning.open_book(world.paths)
    try:
        learning.initialize(conn, AT)
        for n in range(40):
            learning.add_experiment(conn, AT, "long_call", 10_000_000 + n, 60 + n)
        assert conn.execute("SELECT COUNT(*) FROM stk_learning_experiments").fetchone()[0] > 30
        chain(world, AT)
        learning.tick(conn, world.market, AT)
        assert conn.execute("SELECT MAX(desired_units) FROM stk_learning_intents").fetchone()[0] > 10_000
        assert conn.execute("SELECT COUNT(*) FROM stk_pending").fetchone()[0] > 30
    finally:
        conn.close()
        world.close()


def test_capacity_is_per_quote_timestamp_and_cumulative_volume_is_not_recounted(tmp_path):
    world = make_world(tmp_path, n=12)
    conn = learning.open_book(world.paths)
    try:
        q = chain(world, AT)[0]
        assert learning.capacity(conn, learning.RESEARCH, [q]) == 2000
        learning.consume(conn, learning.RESEARCH, [q], 2000)
        assert learning.capacity(conn, learning.RESEARCH, [replace(q, volume=q.volume * 2)]) == 0
        assert learning.capacity(conn, learning.REFERENCE, [q]) == 2000
        assert learning.capacity(conn, learning.RESEARCH, [replace(q, quoted_at=AT + timedelta(minutes=1))]) == 2000
    finally:
        conn.close()
        world.close()


def test_decisions_cannot_see_future_quotes_or_future_underlying_prices(tmp_path):
    import json
    import zlib

    world = make_world(tmp_path, n=12)
    conn = learning.open_book(world.paths)
    try:
        quotes = chain(world, AT)
        assert not learning.fresh(replace(quotes[0], quoted_at=AT + timedelta(seconds=1)), AT)
        future = AT + timedelta(minutes=1)
        next_expiry = EXP + timedelta(days=7)
        world.market.save_option_chain("SPY", AT.date(), next_expiry, future, 999,
            [replace(q, expiration=next_expiry, quoted_at=future) for q in quotes], source="synthetic_learning")
        learning.tick(conn, world.market, AT)
        source = conn.execute("SELECT payload_zlib FROM stk_learning_sources").fetchone()[0]
        observation = json.loads(zlib.decompress(source))
        assert observation["underlying_price"] == 100
        assert all(q["expiration"] == EXP.isoformat() for q in observation["quotes"])
    finally:
        conn.close()
        world.close()


def test_learning_controls_and_gets_are_private_read_only_and_preserve_global_pause(tmp_path):
    world = make_world(tmp_path, n=12)
    try:
        control(world.paths, True)
        assert allowed(world.paths)
        world.paths.stop_file.write_text("STOP")
        assert not allowed(world.paths)
        world.paths.stop_file.unlink()
        (world.paths.home / "stocks-automation.json").write_text('{"enabled":false}')
        assert not allowed(world.paths)
        with TestClient(create_app(world.paths, auto_start=False), base_url="http://127.0.0.1:8767") as client:
            conn = learning.open_book(world.paths)
            before = list(conn.iterdump())
            assert client.get("/api/stocks/learning").json()["accounts"][0]["starting_capital"] == 500000
            assert client.get("/api/stocks/learning/trades").json()["positions"] == []
            assert list(conn.iterdump()) == before
            conn.close()
            for path in ("control", "experiments"):
                assert client.post("/api/stocks/learning/" + path, json={}).status_code == 403
            headers = {"X-CSRF-Token": client.get("/api/stocks/session").json()["csrf_token"]}
            assert client.post("/api/stocks/learning/control", json={"enabled": False}, headers=headers).json() == {
                "enabled": False
            }
            assert not client.get("/api/stocks/learning").json()["control"]["enabled"]
            assert client.post(
                "/api/stocks/learning/experiments",
                headers=headers,
                json={"structure": "long_put", "budget": 2000000, "hold_minutes": 15},
            ).json()["registered"]
            assert (world.paths.home / "stocks-automation.json").read_text() == '{"enabled":false}'
    finally:
        world.close()


@pytest.mark.parametrize("held_call_return", [0.1, -0.1])
def test_chronological_selection_uses_prior_labels_and_preserves_losing_held_out_results(tmp_path, held_call_return):
    from desk.core.clock import to_iso
    from desk.stocks.calendar import trading_days

    world = make_world(tmp_path, n=12)
    conn = learning.open_book(world.paths)
    try:
        learning.initialize(conn, AT)
        book = PaperBook(conn)
        configs = {
            structure: conn.execute(
                "SELECT id,spec_sha256 FROM stk_learning_experiments WHERE json_extract(config_json,'$.structure')=? LIMIT 1",
                (structure,),
            ).fetchone()
            for structure in learning.STRUCTURES
        }
        days = trading_days(date(2026, 7, 1), date(2026, 10, 20))[:50]
        with book.atomic():
            for i, day in enumerate(days):
                entered = datetime.combine(day, AT.time(), UTC)
                for structure, exp in configs.items():
                    net = (0.1 if i < 25 else held_call_return) if structure == "long_call" else -0.2
                    did = book.decide(
                        entered,
                        day,
                        learning.RESEARCH,
                        exp["spec_sha256"],
                        "SPY",
                        "ENTER",
                        ["Synthetic isolated trainer fixture"],
                        {},
                    )
                    pid = book.open_position(
                        account=learning.RESEARCH,
                        spec_sha256=exp["spec_sha256"],
                        asset="option",
                        symbol="SPY",
                        structure=structure,
                        legs=[],
                        decision_id=did,
                        at=entered,
                        day=day,
                        cost=100,
                        fees=0,
                        max_loss=100,
                        stop_price=None,
                        expires=EXP,
                        plan={},
                    )
                    position = next(p for p in book.positions() if p.id == pid)
                    mature = entered + timedelta(minutes=60)
                    book.close_position(
                        position,
                        at=mature,
                        day=day,
                        credit=100 * (1 + net),
                        fees=0,
                        reason="Synthetic trainer fixture",
                        evidence={},
                    )
                    conn.execute(
                        "INSERT INTO stk_learning_outcomes VALUES (?,?,?,?,?,?,?,?,?)",
                        (pid, exp["id"], to_iso(entered), to_iso(mature), day.isoformat() + ":SPY", net, 100 * net, 1, 1),
                    )
        learning.check_in(conn, datetime(2026, 1, 1, tzinfo=UTC))
        assert learning.state(conn, "model") is None  # future outcomes cannot train a past model
        before = conn.execute("SELECT COUNT(*) FROM stk_learning_experiments").fetchone()[0]
        learning.check_in(conn, AT)
        model = learning.state(conn, "model")
        assert model["candidate"] == "long_call"
        assert model["test_net_return"] == pytest.approx(held_call_return)
        assert model["promoted"] is (held_call_return > 0)
        after = conn.execute("SELECT COUNT(*) FROM stk_learning_experiments").fetchone()[0]
        assert (after > before) is (held_call_return > 0)
        if model["promoted"]:
            import json

            followups = [json.loads(r[0]) for r in conn.execute("SELECT config_json FROM stk_learning_experiments")]
            assert any(config.get("parent_experiment") == configs["long_call"]["id"] for config in followups)
        assert conn.execute("SELECT COUNT(*) FROM stk_learning_outcomes").fetchone()[0] == 150
    finally:
        conn.close()
        world.close()


def test_history_cannot_be_erased_and_new_funding_does_not_hide_drawdown(tmp_path):
    import sqlite3

    from desk.core.clock import to_iso

    world = make_world(tmp_path, n=12)
    conn = learning.open_book(world.paths)
    try:
        learning.initialize(conn, AT)
        conn.execute("INSERT INTO stk_learning_nav VALUES (?,?,?,?,?)",
                     (to_iso(AT), learning.RESEARCH, 500000, 500000, 0))
        book = PaperBook(conn)
        book.add_cash(learning.RESEARCH, AT, AT.date(), -100_000 * 1_000_000, "FEE")
        learning.fund(book, learning.RESEARCH, 1_000_000, AT)
        conn.execute("INSERT INTO stk_learning_nav VALUES (?,?,?,?,?)",
                     (to_iso(AT + timedelta(minutes=1)), learning.RESEARCH, 1000000, 1100000, -100000))
        account = learning.report(conn)["accounts"][0]
        assert account["trading_pnl"] == -100000
        assert account["trading_pnl_drawdown"] == 100000
        with pytest.raises(sqlite3.IntegrityError, match="retained"):
            conn.execute("DELETE FROM stk_learning_experiments")
    finally:
        conn.close()
        world.close()


async def test_independent_collection_rotates_requests_and_never_overrides_global_pause(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from desk.core.config import load_config
    from desk.stocks import learning_service

    world = make_world(tmp_path, n=12)
    conn = learning.open_book(world.paths)
    calls = []

    class Provider:
        configured = True

        async def quotes(self, symbols):
            return [SimpleNamespace(symbol=s, last=100, quoted_at=AT) for s in symbols]

        async def expirations(self, symbol):
            return [EXP]

        async def chain(self, symbol, expiry):
            calls.append((symbol, expiry))
            return [replace(q, underlying=symbol, occ=symbol + q.occ[3:]) for q in chain(world, AT)]

    class Suite:
        options = Provider()
        option_source = "synthetic_learning"

        async def aclose(self):
            calls.append("closed")

    monkeypatch.setattr(learning_service, "build_stocks_suite", lambda *args: Suite())
    monkeypatch.setattr(learning_service, "utc_now", lambda: AT)
    try:
        learning.initialize(conn, AT)
        loaded = load_config(world.paths.config_dir)
        result = await learning_service.collect(world.paths, loaded, conn, world.market, AT)
        assert result["checked"] == ["SPY", "QQQ"]
        assert len([x for x in calls if isinstance(x, tuple)]) == 2
        assert "closed" in calls
        assert world.market.jobs()["learning_quotes"]["last_ok_at"]
        assert "options" not in world.market.jobs()  # the lab must not postpone ordinary options collection
        before = len(calls)
        await learning_service.collect(world.paths, loaded, conn, world.market, AT + timedelta(seconds=15))
        assert len(calls) == before
        control(world.paths, False)
        await learning_service.cycle(world.paths, loaded)
        assert len(calls) == before
    finally:
        conn.close()
        world.close()
