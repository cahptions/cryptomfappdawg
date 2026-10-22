"""Independent financial identities, inventory invariants and read-only/fault boundaries. Synthetic fixtures only."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from desk.arbitrage.contracts import InventoryEvent, Observation, Product, Route, Settings, SignalObservation, Source
from desk.arbitrage.economics import allocate, evaluate
from desk.arbitrage.heat import profiles
from desk.arbitrage.inventory import ledger
from desk.arbitrage.repo import Repo, isolated_paths
from desk.arbitrage.runner import Collector, initialize
from desk.arbitrage.sources import fetch, parse_page, read, validate_source
from desk.arbitrage.views import snapshot
from desk.arbitrage.web import create_app
from desk.core.errors import DeskError
from desk.core.paths import DeskPaths
from desk.core.worker_lock import WorkerBusy, WorkerLock

NOW = datetime(2026, 10, 22, 12, tzinfo=UTC)


@pytest.fixture
def paths(tmp_path: Path) -> DeskPaths:
    return DeskPaths(tmp_path / "data", tmp_path / "config", tmp_path / "private.json")


def inputs() -> tuple[Product, Observation, Route, Settings]:
    p = Product(id="synthetic", title="Synthetic fixture", brand="Fixture", mpn="UNIT1", pack_count=1, identity_confirmed=True)
    o = Observation(
        product=p,
        source_id="fixture",
        url="https://www.adafruit.com/product/4300",
        price=50,
        currency="USD",
        at=NOW,
        availability="VERIFIED_AVAILABLE",
        region="US",
        quantity=5,
        shipping=1,
        tax=1,
        verified_until=NOW + timedelta(minutes=5),
        method="synthetic-test",
        evidence="fixture",
    )
    r = Route(
        id="r1",
        product_id=p.id,
        channel="Synthetic channel",
        sale_price=100,
        stress_price=80,
        fee_fraction=Decimal(".10"),
        fixed_fee=1,
        fulfillment_cost=5,
        return_allowance=2,
        overhead=1,
        days_to_cash=14,
        demand_limit=5,
        eligible=True,
        identity_confirmed=True,
        reviewed=True,
        fee_version="synthetic-v1",
        evidence_note="Not a real fee or price",
        valid_until=NOW + timedelta(days=1),
    )
    return p, o, r, Settings(capital=500)


def test_profit_identity_and_cash_basket() -> None:
    p, o, r, s = inputs()
    result = evaluate(p, o, r, s, NOW)
    # $52 acquisition + $10 variable fees + $9 other selling costs + $29 profit = $100 proceeds.
    assert result["qualifies"]
    assert Decimal(result["landed_cost"]) == 52
    assert Decimal(result["net_profit"]) == 29
    assert Decimal(result["stress_profit"]) == 11
    assert Decimal(result["break_even"]) == Decimal("67.78")
    plan = allocate([result], s, Decimal(0))
    assert plan["basket"][0]["units"] == 1  # 20% product cap: two units would cost $104 > $100
    assert Decimal(plan["cash_remaining"]) + Decimal(plan["basket"][0]["commitment"]) == 500
    assert not allocate([result], s, Decimal(52), {p.id: Decimal(52)})["basket"]
    assert not allocate([result], Settings(), Decimal(0))["basket"]


def test_mixed_currency_costs_are_not_computed_or_merged_into_inventory(paths: DeskPaths) -> None:
    _p, o, r, s = inputs()
    result = evaluate(o.product, o.model_copy(update={"currency": "EUR"}), r, s, NOW)
    assert result["net_profit"] is None and result["landed_cost"] is None and not result["qualifies"]
    with Repo.open(paths) as repo:
        repo.observe(o)
        with pytest.raises(DeskError, match="currency"):
            repo.add_event(event("RECEIVE", 1, 20).model_copy(update={"currency": "EUR"}))
        repo.add_event(event("RECEIVE", 1, 20))
        with pytest.raises(DeskError, match="currency"):
            repo.save_settings(s.model_copy(update={"currency": "EUR"}))
        assert repo.settings().currency == "USD"


@pytest.mark.parametrize(
    "change",
    [
        {"shipping": None},
        {"tax": None},
        {"region": "Other region"},
        {"availability": "DECLARED_AVAILABLE"},
        {"availability": "UNAVAILABLE"},
        {"quantity": 0},
        {"at": NOW - timedelta(minutes=6)},
        {"verified_until": NOW - timedelta(seconds=1)},
        {"at": NOW + timedelta(seconds=1)},
    ],
)
def test_uncertain_or_stale_offer_cannot_qualify(change: dict[str, object]) -> None:
    p, o, r, s = inputs()
    assert not evaluate(p, o.model_copy(update=change), r, s, NOW)["qualifies"]


@pytest.mark.parametrize(
    "change",
    [
        {"eligible": None},
        {"eligible": False},
        {"fee_fraction": None},
        {"return_allowance": None},
        {"valid_until": NOW - timedelta(seconds=1)},
        {"monthly_cost": Decimal(1)},
        {"stress_price": Decimal(45)},
        {"reviewed": False},
        {"demand_limit": None},
        {"identity_confirmed": False},
        {"days_to_cash": 365},
        {"currency": "EUR"},
        {"fee_fraction": Decimal(1)},
    ],
)
def test_route_gates_do_not_turn_missing_data_into_profit(change: dict[str, object]) -> None:
    p, o, r, s = inputs()
    assert not evaluate(p, o, r.model_copy(update=change), s, NOW)["qualifies"]


def event(kind: str, units: int, amount: int = 0, n: int = 0) -> InventoryEvent:
    return InventoryEvent(
        id=f"e{n}",
        lot_id="lot",
        product_id="synthetic",
        kind=kind,
        units=units,  # type: ignore[arg-type]
        amount=amount,
        at=NOW + timedelta(seconds=n),
    )


def test_inventory_conservation_returns_and_write_off() -> None:
    events = [event("RECEIVE", 4, 40), event("RESERVE", 2, n=1)]
    assert ledger(events)["lots"][0]["available"] == 2
    with pytest.raises(DeskError):
        ledger([*events, event("SELL", 3, 60, n=2)])
    events += [event("RELEASE", 2, n=2), event("SELL", 3, 60, n=3), event("RETURN", 1, 20, n=4)]
    book = ledger(events)
    assert Decimal(book["committed"]) == 20  # one unsold + one returned/quarantined
    assert Decimal(book["realized_profit"]) == 20  # net $40 proceeds minus $20 cost of net sold units
    assert book["lots"][0]["available"] == 1
    events += [event("DISCARD_RETURN", 1, n=5)]
    assert Decimal(ledger(events)["realized_profit"]) == 10
    assert Decimal(ledger(events)["committed"]) == 10
    with pytest.raises(DeskError):
        ledger([*events, event("ADJUST_COST", 0, 5, n=6)])


def test_inventory_idempotency_transaction_and_no_history_edits(paths: DeskPaths) -> None:
    p, o, _, _ = inputs()
    with Repo.open(paths) as repo:
        repo.observe(o)
        first = event("RECEIVE", 1, 10)
        assert repo.add_event(first)
        assert not repo.add_event(first)
        with pytest.raises(DeskError):
            repo.add_event(first.model_copy(update={"amount": Decimal(12)}))
        with pytest.raises(DeskError):
            repo.add_event(event("SELL", 2, 50, n=1))
        assert len(repo.events()) == 1
        with pytest.raises(sqlite3.IntegrityError):
            repo.conn.execute("DELETE FROM arb_inventory")
        with pytest.raises(sqlite3.IntegrityError):
            repo.conn.execute("UPDATE arb_observations SET body='{}'")
        assert repo.products()[0].id == p.id
    with Repo.reader(paths) as repo, pytest.raises(sqlite3.OperationalError):
        repo.conn.execute("INSERT INTO arb_meta VALUES ('bad','write')")


def test_isolation_does_not_touch_trading_state(paths: DeskPaths) -> None:
    independent = isolated_paths(paths)
    initialize(independent)
    with WorkerLock(paths, "stocks-worker"), WorkerLock(paths), WorkerLock(independent):  # noqa: SIM117
        with pytest.raises(WorkerBusy), WorkerLock(independent):
            pass
    assert not paths.db_dir.exists()
    assert independent.secrets_file != paths.secrets_file


def test_heat_never_calls_attention_sales_or_sparse_data_hot() -> None:
    records = [
        SignalObservation(
            id=f"{p}-{d}",
            product_id=f"p{p}",
            channel="Fixture",
            kind="MENTIONS",
            value=p,
            at=NOW - timedelta(days=d),
            available_at=NOW,
            source="synthetic",
        )
        for p in range(1, 6)
        for d in range(3)
    ]
    products = [Product(id=f"p{p}", title="Fixture", category="Fixture", pack_count=1) for p in range(1, 6)]
    heat = profiles(records, NOW, products)
    assert heat["p5"][0]["rating"] == "HOT"
    assert heat["p5"][0]["kind"] == "MENTIONS"
    assert heat["p1"][0]["rating"] == "COOL"
    assert profiles(records[:3], NOW, products)["p1"][0]["rating"] == "UNKNOWN"
    assert all(r["rating"] == "UNKNOWN" for rows in profiles(records, NOW + timedelta(days=10), products).values() for r in rows)
    assert not profiles([r.model_copy(update={"available_at": NOW + timedelta(hours=1)}) for r in records], NOW, products)
    assert all(r["rating"] == "UNKNOWN" for rows in profiles(records, NOW, []).values() for r in rows)


def test_jsonld_exact_offer_and_variant_ambiguity() -> None:
    source = Source(id="fixture", url="https://www.adafruit.com/product/4300")
    product = {
        "@type": "Product",
        "name": "Synthetic fixture",
        "offers": {"@type": "Offer", "price": "20", "priceCurrency": "USD", "availability": "https://schema.org/InStock"},
    }
    page = '<script type="application/ld+json">' + json.dumps(product) + "</script>"
    result = parse_page(page, source, NOW)
    assert result.availability == "DECLARED_AVAILABLE" and result.shipping is None
    assert not result.product.identity_confirmed
    with pytest.raises(DeskError):
        parse_page(page + page, source, NOW)
    product["offers"] = {"@type": "AggregateOffer", "lowPrice": 10, "highPrice": 20}
    with pytest.raises(DeskError):
        parse_page('<script type="application/ld+json">' + json.dumps(product) + "</script>", source, NOW)


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1/product/4300",
        "https://www.adafruit.com.evil.test/product/4300",
        "https://www.adafruit.com/product/../admin",
        "https://www.adafruit.com/product/4300?key=x",
    ],
)
def test_sources_cannot_fetch_arbitrary_hosts_or_paths(url: str) -> None:
    with pytest.raises(DeskError):
        validate_source(Source(id="fixture", url=url))


@pytest.mark.parametrize("code", [301, 401, 403, 429, 500])
async def test_source_http_failures_do_not_manufacture_observations(code: int) -> None:
    calls = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(code, text="sensitive provider body that must not be returned")

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(DeskError) as exc:
            await fetch(Source(id="fixture", url="https://www.adafruit.com/product/4300"), NOW, client=client)
    assert "sensitive" not in str(exc.value)
    assert len(calls) == 1 and calls[0].method == "GET"


async def test_robots_denial_and_oversized_bodies_are_bounded() -> None:
    calls: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, text="User-agent: *\nDisallow: /product/")

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(DeskError, match="robots policy"):
            await fetch(Source(id="fixture", url="https://www.adafruit.com/product/4300"), NOW, client=client)
    assert calls == ["/robots.txt"]
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b"x" * 2_000_001))
    ) as client:
        with pytest.raises(DeskError, match="2 MB"):
            await read(client, "https://www.adafruit.com/robots.txt")


def test_validators_reject_nonfinite_money_and_naive_time() -> None:
    with pytest.raises(ValidationError):
        Settings(capital="NaN")
    p, o, _, _ = inputs()
    with pytest.raises(ValidationError):
        Observation.model_validate({**o.model_dump(), "at": NOW.replace(tzinfo=None)})
    with pytest.raises(ValidationError):
        Source(id="a", url="https://user:password@example.com/x")
    assert p.id == "synthetic"
    assert Product(id="gtin", title="Fixture", gtin="036000291452").gtin == "00036000291452"
    with pytest.raises(ValidationError):
        Product(id="gtin", title="Fixture", gtin="036000291453")
    for data in ({"value": 0.5, "available_at": NOW}, {"value": 1, "available_at": NOW - timedelta(seconds=1)}):
        with pytest.raises(ValidationError):
            SignalObservation(id="bad", product_id=p.id, channel="Fixture", kind="OWN_SALE", at=NOW, source="Fixture", **data)


def test_identity_change_invalidates_existing_route_and_source_check(paths: DeskPaths) -> None:
    p, o, r, s = inputs()
    with Repo.open(paths) as repo:
        repo.observe(o)
        repo.save_route(r)
        repo.save_product(p.model_copy(update={"variant": "Different pack/variant"}))
        assert not repo.routes()[0].identity_confirmed
        assert not evaluate(repo.products()[0], o, r, s, NOW)["qualifies"]


def test_comparison_fanout_is_bounded_and_can_recover_by_pausing_routes(
    paths: DeskPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    _p, o, r, _s = inputs()
    observations = [
        {**o.model_dump(mode="json"), "id": i, "source_id": f"source-{i}", "input_hash": "fixture"} for i in range(3000)
    ]
    calls: list[int] = []

    def cheap_evaluate(*_: object) -> dict[str, object]:
        calls.append(1)
        return {"qualifies": False, "stress_profit": None, "net_profit": None, "blockers": ["synthetic"]}

    monkeypatch.setattr(Repo, "observations", lambda *_, **__: observations)
    monkeypatch.setattr("desk.arbitrage.views.evaluate", cheap_evaluate)
    with Repo.open(paths) as repo:
        repo.observe(o)
        repo.save_route(r)
        other = r.model_copy(update={"id": "other"})
        repo.save_route(other)
        state = snapshot(repo, NOW)
        assert state["coverage"]["analysis_error"] and state["board"] and not state["basket"]["basket"]
        assert not calls
        repo.save_route(other.model_copy(update={"enabled": False}))
        assert snapshot(repo, NOW)["coverage"]["analysis_error"] is None
        assert len(calls) == 3000


def test_web_session_csrf_guides_and_real_record_workflow(paths: DeskPaths, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("desk.arbitrage.web.utc_now", lambda: NOW + timedelta(days=1))
    with TestClient(create_app(paths, auto_start=False), base_url="http://127.0.0.1") as client:
        assert client.get("/").status_code == 200
        assert client.get("/api/snapshot").json()["board"] == []
        assert client.post("/api/settings", json={}).status_code == 403
        headers = {"X-CSRF-Token": client.get("/api/session").json()["csrf_token"]}
        p, o, r, s = inputs()
        settings = {**s.model_dump(mode="json"), "enabled": False}
        assert client.post("/api/settings", json=settings, headers=headers).status_code == 200
        for kind, value in [("observation", o), ("route", r)]:
            assert client.post("/api/record/" + kind, json=value.model_dump(mode="json"), headers=headers).status_code == 200
        result = client.get("/api/snapshot").json()
        assert result["board"][0]["product"]["id"] == p.id
        assert not result["routes"][0]["qualifies"]  # fixture timestamp is not current live evidence
        assert client.get("/api/product/" + p.id).json()["asking_history"]
        guides = client.get("/api/setup-guides").json()
        assert {g["id"] for g in guides} >= {"included", "ebay", "amazon", "walmart"}
        assert "production access required" in next(g["status"] for g in guides if g["id"] == "ebay")
        unsafe = {"id": "ssrf", "url": "https://127.0.0.1/x"}
        assert client.post("/api/record/source", json=unsafe, headers=headers).status_code == 400
        assert client.post("/api/record/product", json={"id": "x"}, headers=headers).status_code == 422
        assert client.post("/api/settings", content=b"x" * 500001, headers=headers).status_code == 413
        assert client.get("/api/snapshot", headers={"Host": "evil.test"}).status_code == 403
        assert client.get("/api/snapshot", headers={"Origin": "https://evil.test"}).status_code == 403


def test_source_failure_invalidates_prior_availability(paths: DeskPaths) -> None:
    _p, o, r, s = inputs()
    with Repo.open(paths) as repo:
        repo.observe(o)
        repo.save_route(r)
        repo.save_settings(s)
        assert snapshot(repo, NOW)["routes"][0]["qualifies"]
        repo.job(o.source_id, {"status": "unavailable", "attempted_at": NOW.isoformat()})
        assert not snapshot(repo, NOW)["routes"][0]["qualifies"]


def test_prospective_archive_retains_exact_inputs_and_deduplicates_unchanged_decisions(paths: DeskPaths) -> None:
    from desk.arbitrage.evidence import archive

    _p, o, r, s = inputs()
    with Repo.open(paths) as repo:
        repo.observe(o)
        repo.save_route(r)
        repo.save_settings(s)
        archive(repo, NOW)
        archive(repo, NOW + timedelta(seconds=1))
        rows = repo.conn.execute("SELECT body FROM arb_decisions").fetchall()
        assert len(rows) == 1
        data = json.loads(rows[0][0])
        assert data["observation"]["price"] == "50"
        assert data["route"]["fee_fraction"] == "0.10"
        archive(repo, NOW + timedelta(minutes=6))
        assert repo.conn.execute("SELECT COUNT(*) FROM arb_decisions").fetchone()[0] == 2


def test_web_storage_failure_is_explicit_and_preserves_completed_work(paths: DeskPaths, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("desk.arbitrage.web.utc_now", lambda: NOW + timedelta(days=1))
    with TestClient(create_app(paths, auto_start=False), base_url="http://127.0.0.1") as client:
        token = client.get("/api/session").json()["csrf_token"]
        _p, o, _r, _s = inputs()

        def full(*_: object) -> None:
            raise sqlite3.OperationalError("disk full sensitive body")

        with monkeypatch.context() as patch:
            patch.setattr(Repo, "observe", full)
            result = client.post("/api/record/observation", json=o.model_dump(mode="json"), headers={"X-CSRF-Token": token})
        assert result.status_code == 503 and "sensitive" not in result.text
        assert client.get("/api/snapshot").json()["board"] == []


async def test_collector_reports_external_lock_and_releases_its_own(paths: DeskPaths) -> None:
    import asyncio

    initialize(paths)
    collector = Collector(paths)
    with WorkerLock(paths):
        collector.start()
        await asyncio.sleep(0)
        assert collector.external
        await collector.stop()
    assert not WorkerLock(paths).held()
