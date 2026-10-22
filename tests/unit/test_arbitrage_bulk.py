"""Bulk imports are published asking-price facts, never inferred quantity, delivery or profitable purchases."""
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from desk.arbitrage.bulk import category_page
from desk.arbitrage.catalog import fetch_catalog, shopify_page, sitemap
from desk.arbitrage.contracts import Source
from desk.arbitrage.repo import Repo
from desk.arbitrage.runner import initialize
from desk.core.errors import DeskError
from desk.core.paths import DeskPaths

NOW = datetime(2026, 10, 22, tzinfo=UTC)


def page(price: str = "29.5000", stock: str = "InStock", url: str = "https://www.adafruit.com/product/4754") -> str:
    # Reduced published microdata shape, without page scripts, session tokens or cart forms.
    return f'''<h1>Sensors</h1><ul><li itemscope itemtype="https://schema.org/Product">
    <a itemprop="name" content="Orientation IMU breakout" href="/product/4754">Name</a>
    <img src="https://cdn-shop.adafruit.com//310x233/4754-00.jpg">
    <div itemscope itemprop="offers" itemtype="https://schema.org/Offer">
    <meta itemprop="price" content="{price}"/><meta itemprop="priceCurrency" content="USD"/>
    <meta itemprop="availability" content="https://schema.org/{stock}"/></div>
    <meta itemprop="url" content="{url}"/></li></ul>'''


def source() -> Source:
    return Source(id="adafruit-category-35", url="https://www.adafruit.com/category/35", adapter="adafruit_category")


def test_category_imports_declared_price_photo_stock_without_shipping_or_identity_assumptions() -> None:
    rows = category_page(page(), source(), NOW)
    assert len(rows) == 1
    offer = rows[0]
    assert str(offer.price) == "29.5000" and offer.currency == "USD"
    assert offer.product.category == "Sensors" and offer.product.image_url.endswith("/310x233/4754-00.jpg")
    assert offer.availability == "DECLARED_AVAILABLE"
    assert offer.quantity is offer.shipping is offer.tax is offer.verified_until is None
    assert offer.product.pack_count is None and not offer.product.identity_confirmed
    assert category_page(page(stock="OutOfStock"), source(), NOW)[0].availability == "UNAVAILABLE"
    assert category_page(page(stock="Unknown"), source(), NOW)[0].availability == "UNKNOWN"


@pytest.mark.parametrize("raw", ["<html>maintenance</html>", page(price="n/a"),
    page(url="https://127.0.0.1/product/4754"), page().replace('</li>', '')])
def test_category_fails_without_manufacturing_a_successful_empty_catalog(raw: str) -> None:
    with pytest.raises(DeskError):
        category_page(raw, source(), NOW)


def test_seed_upgrade_keeps_pause_reviewed_data_and_still_adds_bulk_monitors(tmp_path: Path) -> None:
    paths = DeskPaths(tmp_path/'data', tmp_path/'config', tmp_path/'private')
    initialize(paths)
    with Repo.open(paths) as repo:
        repo.save_settings(repo.settings().model_copy(update={"enabled": False}))
        repo.save_source(source().model_copy(update={"enabled": False}))
    initialize(paths)
    with Repo.reader(paths) as repo:
        assert not repo.settings().enabled
        assert not next(s for s in repo.sources() if s.id == source().id).enabled
        assert any(s.id == 'arduino-us-catalog' for s in repo.sources())


async def test_bulk_network_check_uses_fixed_urls_and_respects_robots(monkeypatch) -> None:
    calls = []
    async def no_wait(*_):
        pass
    monkeypatch.setattr('asyncio.sleep', no_wait)
    def serve(request):
        calls.append(str(request.url))
        return httpx.Response(200, text='User-agent: *\nDisallow: /category/' if request.url.path=='/robots.txt' else page())
    async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as client:
        with pytest.raises(DeskError, match='robots'):
            await fetch_catalog(source(), NOW, client, {})
    assert calls == ['https://www.adafruit.com/robots.txt']


def test_us_catalog_keeps_its_actual_origin_currency_and_identity_namespace() -> None:
    import json
    raw = json.dumps({'products':[{'id':1,'title':'Fixture','handle':'fixture','variants':[{'id':2,'price':'42'}]}]})
    us = Source(id='arduino-us-catalog',url='https://store-usa.arduino.cc/products.json',adapter='shopify_catalog')
    row = shopify_page(raw,us,NOW,currency='USD')[0]
    assert row.url.startswith('https://store-usa.arduino.cc/') and row.currency=='USD'
    assert row.product.id.startswith('arduino-us-')
    assert row.product.pack_count is None


def test_sitemap_does_not_silently_drop_products_after_5000() -> None:
    raw='<urlset>'+''.join(f'<url><loc>https://www.adafruit.com/product/{i}</loc></url>' for i in range(5665))+'</urlset>'
    assert len(sitemap(raw)[0]) == 5665


async def test_automatic_bulk_refresh_deduplicates_products_and_preserves_reviewed_identity(tmp_path, monkeypatch) -> None:
    from datetime import timedelta

    from desk.arbitrage.runner import cycle
    from desk.arbitrage.views import snapshot
    paths = DeskPaths(tmp_path/'data', tmp_path/'config', tmp_path/'private')
    initialize(paths)
    clock = [NOW]
    async def no_wait(*_):
        pass
    monkeypatch.setattr('asyncio.sleep', no_wait)
    monkeypatch.setattr('desk.arbitrage.runner.utc_now', lambda: clock[0])
    with Repo.open(paths) as repo:
        for s in repo.sources():
            repo.save_source(s.model_copy(update={'enabled':s.id == source().id}))
    def serve(request):
        return httpx.Response(200,text='User-agent: *\nAllow: /' if request.url.path=='/robots.txt' else page())
    async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as client:
        await cycle(paths,client=client)
        with Repo.open(paths) as repo:
            p=repo.products()[0]
            repo.save_product(p.model_copy(update={'brand':'Reviewed Brand','mpn':'Reviewed model',
                'pack_count':1,'identity_confirmed':True}),manual=True)
        clock[0] += timedelta(minutes=16)
        await cycle(paths,client=client)
    with Repo.reader(paths) as repo:
        state=snapshot(repo,clock[0])
        assert state['pipeline']['priced_products']==1 and len(repo.products())==1
        assert len(repo.observations(p.id))==2
        assert repo.products()[0].brand=='Reviewed Brand' and repo.products()[0].identity_confirmed
        assert state['qualified_count']==0
        assert state['jobs'][source().id]['status']=='ok'


def test_unicode_catalog_handle_is_encoded_and_cannot_escape_the_product_path() -> None:
    import json
    s=Source(id='arduino-us-catalog',url='https://store-usa.arduino.cc/products.json',adapter='shopify_catalog')
    product={'id':1,'title':'Speaker','handle':'speaker-8ω','variants':[{'id':2,'price':'4.95'}]}
    row=shopify_page(json.dumps({'products':[product]}),s,NOW,currency='USD')[0]
    assert '/products/speaker-8%CF%89?variant=2' in row.url
    for invalid in ['../account','https://another.example','a?variant=3','a/b']:
        product['handle']=invalid
        with pytest.raises(DeskError):
            shopify_page(json.dumps({'products':[product]}),s,NOW,currency='USD')


def test_catalog_prioritizes_last_observed_available_offers_without_hiding_archive(tmp_path) -> None:
    from desk.arbitrage.contracts import Observation, Product
    paths=DeskPaths(tmp_path/'data',tmp_path/'config',tmp_path/'private')
    with Repo.open(paths) as repo:
        for pid,title,status in [('gone','A unavailable','UNAVAILABLE'),('buy','Z available','DECLARED_AVAILABLE')]:
            repo.observe(Observation(product=Product(id=pid,title=title),source_id='fixture',
                url='https://www.adafruit.com/product/1',price=10,currency='USD',at=NOW,
                availability=status,method='synthetic-test'))
        ids,total=repo.catalog_ids(current_at=NOW.isoformat())
        assert total==2 and ids==['buy','gone']
