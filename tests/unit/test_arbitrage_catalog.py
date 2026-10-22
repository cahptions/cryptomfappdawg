"""Synthetic merchant/provider payloads. Capacity and parsing are not proof of live-source access."""
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from desk.arbitrage.catalog import fetch_catalog, shopify_page, sitemap
from desk.arbitrage.comparables import Comparable, save, summary
from desk.arbitrage.contracts import Observation, Product, Source
from desk.arbitrage.ebay import Browse
from desk.arbitrage.integrations import Credentials
from desk.arbitrage.matching import exact_comparable
from desk.arbitrage.repo import Repo
from desk.arbitrage.views import snapshot
from desk.core.errors import DeskError
from desk.core.paths import DeskPaths

NOW = datetime(2026, 10, 22, tzinfo=UTC)
GTIN = "00012345678905"


def source() -> Source:
    return Source(id="fixture", url="https://store.arduino.cc/products.json", adapter="shopify_catalog")


def test_catalog_prices_need_declared_currency_variants_and_real_availability() -> None:
    import json
    raw = json.dumps({"products": [{"id": 1, "title": "SYNTHETIC product", "handle": "fixture", "variants": [
        {"id": 11, "title": "Blue", "price": "29.95", "barcode": GTIN, "available": True},
        {"id": 12, "title": "Red", "price": "39.95"}]}]})
    rows = shopify_page(raw, source(), NOW, currency="EUR")
    assert len(rows) == 2 and rows[0].product.id != rows[1].product.id
    assert rows[0].currency == "EUR" and rows[0].price == Decimal("29.95")
    assert rows[0].availability == "DECLARED_AVAILABLE" and rows[1].availability == "UNKNOWN"
    assert rows[0].shipping is None and rows[0].product.pack_count is None
    with pytest.raises(DeskError):
        shopify_page(raw, source(), NOW, currency="")
    urls, children = sitemap('<urlset><url><loc>https://www.adafruit.com/product/1</loc></url>'
        '<url><loc>https://127.0.0.1/product/2</loc></url></urlset>')
    assert urls == ["https://www.adafruit.com/product/1"] and children == []
    with pytest.raises(DeskError):
        sitemap('<!DOCTYPE x [<!ENTITY x "evil">]><urlset/>')


async def test_catalog_checkpoint_advances_and_detects_ignored_pagination() -> None:
    import json
    calls = []
    raw = json.dumps({"products": [{"id": 1, "title": "SYNTHETIC", "handle": "fixture", "variants": [
        {"id": 11, "price": "20"}]}]})
    def respond(request):
        calls.append(str(request.url))
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /")
        if request.url.path == "/":
            return httpx.Response(200, text='Shopify.currency = {"active":"USD"};')
        return httpx.Response(200, text=raw)
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        rows, _, state = await fetch_catalog(source(), NOW, client, {})
        assert rows and state["next_page"] == 2
        with pytest.raises(DeskError, match="repeated a page"):
            await fetch_catalog(source(), NOW, client, state)
    assert any("page=2" in u for u in calls)


async def test_ebay_pagination_restarts_from_saved_offset_without_following_next_url() -> None:
    offsets = []
    def respond(request):
        if request.method == "POST":
            return httpx.Response(200, json={"access_token": "SYNTHETIC", "expires_in": 3600})
        offsets.append(int(request.url.params["offset"]))
        return httpx.Response(200, json={"total": 450, "itemSummaries": [], "next": "http://127.0.0.1/private"})
    credentials = Credentials(client_id="SYNTHETIC", client_secret="SYNTHETIC", enabled=True, free_access_confirmed=True)
    s = Source(id="ebay-test", url="https://www.ebay.com/", adapter="ebay_browse", query="test")
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        a = Browse(credentials, lambda: None)
        await a.search(client, s, NOW)
        saved = a.next_offset
        b = Browse(credentials, lambda: None)  # New process/client state; offset comes from durable job.
        await b.search(client, s, NOW, offset=saved)
        await b.search(client, s, NOW, offset=b.next_offset)
        assert b.next_offset == 0
    assert offsets == [0, 200, 400]


def test_exact_matching_abstains_on_wrong_pack_or_missing_identifier() -> None:
    p = Product(id="fixture", title="SYNTHETIC", gtin=GTIN, pack_count=1, identity_confirmed=True)
    item = {"itemId": "fixture", "title": "SYNTHETIC", "gtin": GTIN, "condition": "New",
            "localizedAspects": [{"name": "Number in Pack", "value": "1"}],
            "price": {"value": "50", "currency": "USD"}, "itemWebUrl": "https://www.ebay.com/itm/fixture"}
    assert exact_comparable(p, item, NOW)
    item["localizedAspects"][0]["value"] = "2"
    assert exact_comparable(p, item, NOW) is None
    item.pop("gtin")
    assert exact_comparable(p, item, NOW) is None


def test_comparable_statistics_do_not_mix_sales_asking_wrong_packs_or_repeat_polls(tmp_path: Path) -> None:
    paths = DeskPaths(tmp_path / "data", tmp_path / "config", tmp_path / "private")
    with Repo.open(paths) as repo:
        p = Product(id="fixture", title="SYNTHETIC", gtin=GTIN, pack_count=1)
        repo.save_product(p)
        for i, price in enumerate([10, 20, 30]):
            save(repo, Comparable(id=f"c{i}", product_id=p.id, title="SYNTHETIC", channel="eBay",
                url=f"https://www.ebay.com/itm/{i}", price=Decimal(price), currency="USD", region="US", condition="new",
                pack_count=1, gtin=GTIN, at=NOW-timedelta(hours=3-i), evidence_note="Synthetic asking-price fixture"))
        save(repo, Comparable(id="wrong", product_id=p.id, title="SYNTHETIC", channel="eBay",
                url="https://www.ebay.com/itm/wrong", price=Decimal(999), currency="USD", region="US", condition="new",
                pack_count=2, gtin=GTIN, at=NOW, evidence_note="Wrong pack fixture"))
        result = summary(repo, p, NOW, currency="USD", region="US")
        c = result["cohorts"][0]
        assert c["count"] == 3 and [c[k] for k in ["low", "average", "high", "median"]] == ["10", "20", "30", "20"]
        assert result["excluded"] == 1 and c["evidence_type"] == "ASKING"
        repo.observe(Observation(product=p, source_id="fixture", url="https://www.adafruit.com/product/1",
            price=Decimal(5), currency="USD", at=NOW, availability="UNKNOWN", method="synthetic"))
        state = snapshot(repo, NOW)
        assert state["board"][0]["resale_evidence"]["cohorts"][0]["median"] == "20"
        assert state["qualified_count"] == 0 and not state["rankings"]["profit"]


def test_profit_index_is_global_and_invalidated_when_source_price_changes(tmp_path: Path) -> None:
    from desk.arbitrage.evidence import archive
    from tests.unit.test_arbitrage import NOW as economics_now
    from tests.unit.test_arbitrage import inputs
    paths = DeskPaths(tmp_path / "data", tmp_path / "config", tmp_path / "private")
    p, o, r, settings = inputs()
    with Repo.open(paths) as repo:
        repo.save_settings(settings)
        for i in range(100):
            repo.save_product(Product(id=f"a{i}", title=f"A uncosted fixture {i:03d}"))
        repo.observe(o)
        repo.save_route(r)
        archive(repo, economics_now)
        ids, total = repo.catalog_ids(current_after=economics_now.isoformat())
        assert ids[0] == p.id and total == 101
        repo.observe(o.model_copy(update={"price": Decimal(200), "at": economics_now+timedelta(seconds=1)}))
        ids, _ = repo.catalog_ids(current_after=economics_now.isoformat())
        assert ids[0] != p.id  # Previous positive profit cannot survive a changed source observation.
        assert repo.conn.execute("SELECT COUNT(*) FROM arb_opportunity_index").fetchone()[0] == 0


def test_catalog_metadata_search_keeps_count_pagination_and_category_consistent(tmp_path: Path) -> None:
    paths = DeskPaths(tmp_path / "data", tmp_path / "config", tmp_path / "private")
    with Repo.open(paths) as repo:
        for i in range(55):
            repo.save_product(Product(id=f"p{i:02d}", title=f"Distinct item {i:02d}",
                brand="OpticsTest", mpn="MODEL-needle", gtin=GTIN,
                category="Tools" if i == 54 else "Electronics"))
        repo.save_product(Product(id="other", title="Unrelated", category="Tools"))
        for query in ["opticstest", "MODEL-needle", GTIN]:
            ids, total = repo.catalog_ids(query=query, offset=50)
            assert total == 55 and len(ids) == 5 and "other" not in ids
            ids, total = repo.catalog_ids(query=query, category="Tools")
            assert total == 1 and ids == ["p54"]
        assert repo.catalog_ids(query="%'")[1] == 0  # Literal text, never a SQL pattern/expression.


async def test_in_memory_api_pagination_csrf_and_atomic_comparable_import(tmp_path: Path) -> None:
    from desk.arbitrage.runner import initialize
    from desk.arbitrage.web import create_app
    paths = DeskPaths(tmp_path / "data", tmp_path / "config", tmp_path / "private")
    initialize(paths)
    with Repo.open(paths) as repo:
        for i in range(51):
            repo.save_product(Product(id=f"p{i:02d}", title=f"SYNTHETIC {i:02d}", gtin=GTIN, pack_count=1))
    app = create_app(paths, auto_start=False)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as client:
        page = (await client.get("/api/snapshot?offset=50")).json()
        assert page["catalog"]["total"] == 51 and len(page["board"]) == 1
        assert page["board"][0]["product"]["id"] == "p50"
        assert (await client.get("/api/snapshot?offset=-1")).status_code == 400
        assert (await client.post("/api/comparables/import", json={})).status_code == 403
        token = (await client.get("/api/session")).json()["csrf_token"]
        headers = {"X-CSRF-Token": token}
        raw = ('title,channel,url,price,currency,region,condition,pack_count,gtin,evidence_type,at,evidence_note\n'
               f'SYNTHETIC,eBay,https://www.ebay.com/itm/1,20,USD,US,new,1,{GTIN},ASKING,2026-10-22T00:00:00Z,Fixture\n')
        invalid = raw+f'SYNTHETIC,eBay,https://www.ebay.com/itm/2,NaN,USD,US,new,1,{GTIN},ASKING,2026-10-22T00:00:00Z,Fixture\n'
        assert (await client.post("/api/comparables/import", json={"product_id": "p50", "csv": invalid},
                                  headers=headers)).status_code == 400
        with Repo.reader(paths) as repo:
            assert repo.conn.execute("SELECT COUNT(*) FROM arb_comparables").fetchone()[0] == 0
        assert (await client.post("/api/comparables/import", json={"product_id": "p50", "csv": raw},
                                  headers=headers)).status_code == 200


def test_sources_group_products_by_actual_merchant_and_filters_cover_all_pages(tmp_path: Path) -> None:
    paths = DeskPaths(tmp_path / "data", tmp_path / "config", tmp_path / "private")
    with Repo.open(paths) as repo:
        for i in range(61):
            repo.observe(Observation(product=Product(id=f"p{i:02d}", title=f"SYNTHETIC {i:02d}"), source_id="deal-feed",
                url=f"https://www.adafruit.com/product/{i}", price=10, currency="USD", at=NOW,
                availability="UNKNOWN", method="synthetic"))
        repo.observe(Observation(product=Product(id="ebay", title="SYNTHETIC eBay"), source_id="different-search",
            url="https://www.ebay.com/itm/fixture", price=20, currency="USD", at=NOW,
            availability="UNKNOWN", method="synthetic"))
        state = snapshot(repo, NOW, source_host="www.adafruit.com", offset=50)
        assert len(state["board"]) == 11 and state["catalog"]["total"] == 61
        assert state["source_groups"][0]["host"] == "www.adafruit.com"
        assert not any(g["host"] == "deal-feed" for g in state["source_groups"])
        assert snapshot(repo, NOW, source_host="www.ebay.com")["catalog"]["total"] == 1


def test_expired_profit_cache_and_unknown_quantity_never_qualify(tmp_path: Path) -> None:
    from desk.arbitrage.economics import evaluate
    from desk.arbitrage.evidence import archive
    from tests.unit.test_arbitrage import NOW as economics_now
    from tests.unit.test_arbitrage import inputs
    p, o, r, settings = inputs()
    assert not evaluate(p, o.model_copy(update={"quantity": None}), r, settings, economics_now)["qualifies"]
    paths = DeskPaths(tmp_path / "data", tmp_path / "config", tmp_path / "private")
    with Repo.open(paths) as repo:
        repo.save_settings(settings)
        repo.observe(o)
        repo.save_route(r.model_copy(update={"valid_until": economics_now+timedelta(seconds=30)}))
        repo.save_product(Product(id="unknown", title="A unranked fixture"))
        archive(repo, economics_now)
        after = economics_now+timedelta(minutes=1)
        ids, _ = repo.catalog_ids(current_after=economics_now.isoformat(), current_at=after.isoformat())
        assert ids[0] == "unknown"  # The expired positive case cannot displace unranked products.


async def test_paused_source_does_not_drain_its_merchant_queue(tmp_path: Path) -> None:
    from desk.arbitrage.runner import cycle
    paths = DeskPaths(tmp_path / "data", tmp_path / "config", tmp_path / "private")
    with Repo.open(paths) as repo:
        repo.save_source(Source(id="paused", url="https://www.adafruit.com/product/1", enabled=False))
        repo.queue_offers(["https://www.adafruit.com/product/1"], "paused", NOW.isoformat())
    def no_request(request):
        raise AssertionError("Paused source attempted a network request")
    async with httpx.AsyncClient(transport=httpx.MockTransport(no_request)) as client:
        result = await cycle(paths, client=client)
    assert result["attempted"] == 0
    with Repo.reader(paths) as repo:
        assert repo.conn.execute("SELECT attempted_at FROM arb_offer_queue").fetchone()[0] is None


def test_url_failure_vetoes_a_previously_verified_offer(tmp_path: Path) -> None:
    from tests.unit.test_arbitrage import NOW as economics_now
    from tests.unit.test_arbitrage import inputs
    _p, o, r, settings = inputs()
    paths = DeskPaths(tmp_path / "data", tmp_path / "config", tmp_path / "private")
    with Repo.open(paths) as repo:
        repo.save_settings(settings)
        repo.observe(o)
        repo.save_route(r)
        assert snapshot(repo, economics_now)["routes"][0]["qualifies"]
        repo.queue_offers([o.url], "fixture", economics_now.isoformat())
        repo.conn.execute("UPDATE arb_offer_queue SET attempted_at=?,error='Synthetic source failure'",
                          ((economics_now+timedelta(seconds=1)).isoformat(),))
        state = snapshot(repo, economics_now+timedelta(seconds=2))
        assert not state["routes"][0]["qualifies"]
        assert any("Source failed" in s for s in state["routes"][0]["blockers"])


def test_comparable_batch_rolls_back_on_later_invalid_record(tmp_path: Path) -> None:
    from desk.storage.db import transaction
    paths = DeskPaths(tmp_path / "data", tmp_path / "config", tmp_path / "private")
    c = Comparable(id="fixture", product_id="fixture", title="SYNTHETIC", channel="eBay",
        url="https://www.ebay.com/itm/fixture", price=20, currency="USD", region="US", condition="new",
        gtin=GTIN, pack_count=1, at=NOW, evidence_note="Synthetic import")
    with Repo.open(paths) as repo:
        repo.save_product(Product(id="fixture", title="SYNTHETIC", gtin=GTIN, pack_count=1))
        with pytest.raises(ValueError, match="timezone"), transaction(repo.conn):
            save(repo,c)
            save(repo,c.model_copy(update={"id":"bad", "at":NOW.replace(tzinfo=None)}))
        assert repo.conn.execute("SELECT COUNT(*) FROM arb_comparables").fetchone()[0] == 0


def test_actual_snapshot_endpoint_pages_without_running_a_server(tmp_path: Path) -> None:
    """Handler-level check; deliberately not a substitute for HTTP/middleware/browser validation."""
    from desk.arbitrage.runner import initialize
    from desk.arbitrage.web import create_app
    paths = DeskPaths(tmp_path / "data", tmp_path / "config", tmp_path / "private")
    initialize(paths)
    with Repo.open(paths) as repo:
        for i in range(51):
            repo.save_product(Product(id=f"p{i:02d}", title=f"SYNTHETIC {i:02d}"))
    app = create_app(paths, auto_start=False)
    endpoint = next(r.endpoint for r in app.routes if getattr(r, "path", "") == "/api/snapshot")
    result = endpoint(offset=50)
    assert result["catalog"]["total"] == 51 and len(result["board"]) == 1
    assert result["board"][0]["product"]["id"] == "p50"
    assert result["integrations"]["ebay"]["enabled"] is False


async def test_actual_csv_handler_is_atomic_without_threaded_http(tmp_path: Path) -> None:
    import json

    from fastapi import HTTPException, Request

    from desk.arbitrage.runner import initialize
    from desk.arbitrage.web import create_app
    paths = DeskPaths(tmp_path / "data", tmp_path / "config", tmp_path / "private")
    initialize(paths)
    with Repo.open(paths) as repo:
        repo.save_product(Product(id="p", title="SYNTHETIC", gtin=GTIN, pack_count=1))
    app = create_app(paths, auto_start=False)
    endpoint = next(r.endpoint for r in app.routes if getattr(r, "path", "") == "/api/comparables/import")
    raw = ('title,channel,url,price,currency,region,condition,pack_count,gtin,evidence_type,at,evidence_note\n'
           f'SYNTHETIC,eBay,https://www.ebay.com/itm/1,20,USD,US,new,1,{GTIN},ASKING,2026-10-21T00:00:00Z,Fixture\n')
    async def request(csv):
        body = json.dumps({"csv": csv, "product_id": "p"}).encode()
        async def receive():
            return {"type": "http.request", "body": body, "more_body": False}
        return Request({"type": "http", "method": "POST", "path": "/api/comparables/import"}, receive)
    with pytest.raises(HTTPException) as exc:
        await endpoint(await request(raw+'invalid,row\n'))
    assert exc.value.status_code == 400
    with Repo.reader(paths) as repo:
        assert repo.conn.execute("SELECT COUNT(*) FROM arb_comparables").fetchone()[0] == 0
    assert await endpoint(await request(raw)) == {"saved": 1}
