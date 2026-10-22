"""Discovery is evidence acquisition, not a claim of purchasable stock or a business edge."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from desk.arbitrage.contracts import Source
from desk.arbitrage.discovery import FEEDS, FeedDelay, fetch_feed, parse_feed
from desk.arbitrage.repo import DDL, Repo
from desk.arbitrage.runner import initialize
from desk.arbitrage.views import snapshot
from desk.arbitrage.web import create_app
from desk.core.errors import DeskError
from desk.core.hashing import sha256_hex
from desk.core.paths import DeskPaths
from tests.unit.test_arbitrage import inputs

NOW = datetime(2026, 10, 22, 12, tzinfo=UTC)


def source() -> Source:
    return Source(id="deals-slickdeals", adapter="deal_feed", url=FEEDS["slickdeals"]["url"])


def feed(*, date: str = "Thu, 22 Oct 2026 11:00:00 +0000") -> str:
    return f"""<rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/"><channel><title>TEST FIXTURE</title>
    <item><title>TEST FIXTURE: $90 kit, not an executable price</title><link>https://slickdeals.net/f/123-fixture</link>
    <pubDate>{date}</pubDate><description>&lt;b&gt;Synthetic mention&lt;/b&gt; &lt;script&gt;bad()&lt;/script&gt;</description>
    <media:thumbnail url="https://static.slickdealscdn.com/fixture.jpg"/></item></channel></rss>"""


@pytest.fixture
def paths(tmp_path: Path) -> DeskPaths:
    return DeskPaths(tmp_path / "data", tmp_path / "config", tmp_path / "private.json")


def test_leads_preserve_event_time_and_never_become_prices_or_stock(paths: DeskPaths) -> None:
    leads = parse_feed(feed(), source(), NOW)
    assert len(leads) == 1 and leads[0].published_at == NOW - timedelta(hours=1)
    assert "<script>" not in leads[0].summary and not hasattr(leads[0], "price")
    with Repo.open(paths) as repo:
        repo.save_leads(leads)
        state = snapshot(repo, NOW)
        assert state["leads"][0]["fresh"]
        assert state["board"] == state["basket"]["basket"] == []
        assert state["qualified_count"] == 0


def test_refetch_cannot_make_old_lead_fresh_or_undo_save_dismiss(paths: DeskPaths) -> None:
    with Repo.open(paths) as repo:
        first = parse_feed(feed(date=""), source(), NOW)
        repo.save_leads(first)
        repo.lead_action(first[0].id, "save")
        repo.lead_action(first[0].id, "dismiss")
        repo.save_leads(parse_feed(feed(date=""), source(), NOW + timedelta(days=2)))
        lead = snapshot(repo, NOW + timedelta(days=2))["leads"][0]
        assert lead["saved"] and lead["dismissed"] and not lead["fresh"]
        assert datetime.fromisoformat(lead["first_seen_at"]) == NOW
        assert len(repo.leads()) == 1
        assert repo.conn.execute("SELECT value FROM arb_meta WHERE key='schema_v1'").fetchone()[0] == sha256_hex(DDL)


@pytest.mark.parametrize(
    "raw", ["<html>maintenance</html>", "<!DOCTYPE rss [<!ENTITY x 'unsafe'>]><rss/>", "<rss>", "x" * 2000001]
)
def test_malformed_html_entity_and_oversized_feeds_fail_explicitly(raw: str) -> None:
    with pytest.raises(DeskError):
        parse_feed(raw, source(), NOW)


def test_future_and_old_mentions_are_not_fresh_leads() -> None:
    assert parse_feed(feed(date="Fri, 23 Oct 2026 11:00:00 +0000"), source(), NOW) == []
    assert parse_feed(feed(date="Thu, 01 Oct 2026 11:00:00 +0000"), source(), NOW) == []
    assert parse_feed(feed().replace("https://slickdeals.net/f/123-fixture", "https://127.0.0.1/private"), source(), NOW) == []
    with pytest.raises(DeskError):
        parse_feed(feed(), source().model_copy(update={"url": "https://127.0.0.1/feed"}), NOW)


async def test_robots_cache_and_rate_limits_are_read_only(monkeypatch: pytest.MonkeyPatch) -> None:
    async def no_wait(*_: object) -> None:
        return None

    monkeypatch.setattr("asyncio.sleep", no_wait)
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        if request.headers.get("if-none-match"):
            return httpx.Response(304)
        return httpx.Response(200, text=feed(), headers={"ETag": "fixture-version"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        leads, cache, unchanged = await fetch_feed(source(), NOW, client=client, previous={})
        assert leads and cache["etag"] == "fixture-version" and not unchanged
        rows, _, unchanged = await fetch_feed(source(), NOW, client=client, previous=cache)
        assert unchanged and not rows
    assert all(r.method == "GET" and r.url.host == "slickdeals.net" for r in requests)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(429, headers={"Retry-After": "7200"}))
    ) as client:
        with pytest.raises(FeedDelay) as caught:
            await fetch_feed(source(), NOW, client=client, previous={})
        assert caught.value.until >= datetime.now(UTC) + timedelta(seconds=7100)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, text="User-agent: *\nDisallow: /"))
    ) as client:
        with pytest.raises(DeskError, match="robots policy"):
            await fetch_feed(source(), NOW, client=client, previous={})


def test_lead_review_is_atomic_and_never_skips_verification(paths: DeskPaths, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("desk.arbitrage.web.utc_now", lambda: NOW)
    with TestClient(create_app(paths, auto_start=False), base_url="http://127.0.0.1") as client:
        with Repo.open(paths) as repo:
            leads = parse_feed(feed(), source(), NOW)
            repo.save_leads(leads)
        lead_id = leads[0].id
        assert client.post(f"/api/leads/{lead_id}/save", json={}).status_code == 403
        headers = {"X-CSRF-Token": client.get("/api/session").json()["csrf_token"]}
        assert client.post(f"/api/leads/{lead_id}/offer", headers=headers, json={"price": "50"}).status_code == 422
        assert client.get("/api/snapshot").json()["board"] == []
        data = {
            "url": "https://www.adafruit.com/product/4300",
            "price": "50",
            "currency": "USD",
            "evidence": "Synthetic reviewed source",
        }
        assert client.post(f"/api/leads/{lead_id}/offer", headers=headers, json=data).status_code == 200
        state = client.get("/api/snapshot").json()
        assert state["leads"][0]["saved"] and state["leads"][0]["product_id"] == lead_id
        assert state["board"][0]["offers"][0]["availability"] == "UNKNOWN"
        assert not state["board"][0]["product"]["identity_confirmed"]
        assert not state["basket"]["basket"]
        policy = client.get("/").headers["Content-Security-Policy"]
        assert "https://i.ebayimg.com" in policy and "connect-src 'self'" in policy and "img-src *" not in policy


def test_absolute_dollars_and_roi_are_distinct_rankings(paths: DeskPaths) -> None:
    product, offer, route, settings = inputs()
    high = product.model_copy(update={"id": "high-dollars", "title": "Higher dollar profit"})
    low = product.model_copy(update={"id": "high-roi", "title": "Small high ROI item"})
    with Repo.open(paths) as repo:
        repo.save_settings(settings)
        repo.observe(offer.model_copy(update={"product": high, "price": Decimal("200")}))
        repo.observe(offer.model_copy(update={"product": low, "price": Decimal("5")}))
        repo.save_route(
            route.model_copy(
                update={"id": "route-high", "product_id": high.id, "sale_price": Decimal("500"), "stress_price": Decimal("400")}
            )
        )
        repo.save_route(
            route.model_copy(
                update={"id": "route-low", "product_id": low.id, "sale_price": Decimal("50"), "stress_price": Decimal("40")}
            )
        )
        state = snapshot(repo, NOW)
        assert state["rankings"]["profit"] == [high.id, low.id]
        assert state["rankings"]["roi"] == [low.id, high.id]
        # A stale or unavailable offer cannot remain in a current-profit ranking.
        assert not snapshot(repo, NOW + timedelta(hours=1))["rankings"]["profit"]
        repo.observe(
            offer.model_copy(
                update={"product": high, "price": Decimal("200"), "availability": "UNAVAILABLE", "at": NOW + timedelta(seconds=1)}
            )
        )
        assert snapshot(repo, NOW + timedelta(seconds=1))["rankings"]["profit"] == [low.id]


def test_later_source_photo_enriches_without_overwriting_reviewed_identity(paths: DeskPaths) -> None:
    p, o, _, _ = inputs()
    with Repo.open(paths) as repo:
        repo.observe(o)
        changed = p.model_copy(update={"image_url": "https://i.ebayimg.com/fixture.jpg", "brand": "WRONG"})
        repo.observe(o.model_copy(update={"product": changed, "at": NOW + timedelta(seconds=1)}))
        assert repo.products()[0].image_url == changed.image_url
        assert repo.products()[0].brand == p.brand
        assert repo.products()[0].identity_confirmed


def test_new_seed_is_additive_and_respects_existing_pause(paths: DeskPaths) -> None:
    initialize(paths)
    with Repo.open(paths) as repo:
        repo.save_settings(repo.settings().model_copy(update={"enabled": False}))
        sources = repo.sources()
        assert {s.adapter for s in sources} >= {"jsonld", "deal_feed"}
    initialize(paths)
    with Repo.open(paths) as repo:
        assert len(repo.sources()) == len(sources) and not repo.settings().enabled


async def test_collector_keeps_feed_failure_local_and_preserves_checkpoints(
    paths: DeskPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from desk.arbitrage.runner import cycle

    async def no_wait(*_: object) -> None:
        return None

    monkeypatch.setattr("asyncio.sleep", no_wait)
    monkeypatch.setattr("desk.arbitrage.runner.utc_now", lambda: NOW)
    initialize(paths)
    with Repo.open(paths) as repo:
        for item in repo.sources():
            repo.save_source(item.model_copy(update={"enabled": item.adapter == "deal_feed"}))
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.host == "www.dealnews.com":
            return httpx.Response(503)
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        return httpx.Response(200, text=feed(), headers={"ETag": "fixture"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await cycle(paths, client=client)
        assert result["attempted"] == 2
        with Repo.reader(paths) as repo:
            assert len(repo.leads()) == 1 and not repo.products()
            jobs = repo.jobs()
            assert jobs["deals-slickdeals"]["status"] == "ok"
            assert jobs["deals-dealnews"]["status"] == "unavailable"
        await cycle(paths, client=client)
        assert len(requests) == 3  # two requests for one successful feed and one failed robots request


def test_exact_daily_history_needs_six_prior_days_and_does_not_use_today(paths: DeskPaths) -> None:
    _p, offer, _, _ = inputs()
    with Repo.open(paths) as repo:
        for day in range(1, 6):
            repo.observe(offer.model_copy(update={"at": NOW - timedelta(days=day), "price": Decimal("100")}))
        repo.observe(offer.model_copy(update={"price": Decimal("50")}))
        assert snapshot(repo, NOW)["rankings"]["price_drops"] == []
        repo.observe(offer.model_copy(update={"at": NOW - timedelta(days=6), "price": Decimal("100")}))
        drop = snapshot(repo, NOW)["rankings"]["price_drops"][0]
        assert Decimal(drop["discount_fraction"]) == Decimal(".5") and drop["baseline_days"] == 6
        repo.observe(offer.model_copy(update={"availability": "UNAVAILABLE", "at": NOW + timedelta(seconds=1)}))
        assert not snapshot(repo, NOW + timedelta(seconds=1))["rankings"]["price_drops"]


def test_restart_will_not_signal_a_different_desks_process(paths: DeskPaths) -> None:
    from desk.core.worker_lock import WorkerLock
    from desk.ops.dashboard_restart import restart_dashboard

    # This pytest process owns the real kernel lock but is NOT an Arbitrage dashboard.
    with WorkerLock(paths, "dashboard"), pytest.raises(DeskError, match="no process was stopped"):
        restart_dashboard(paths, desk="arbitrage", timeout=1)
