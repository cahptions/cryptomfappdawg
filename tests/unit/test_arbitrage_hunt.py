"""The hunt: sold prices read from eBay's sold results (both page layouts, or pasted text), the deal card's profit
math and checks, metals against melt, and the check → refresh → rank flow with a polite, pausable eBay reader.
The eBay pages here are synthetic, written in the structure eBay serves."""

import asyncio
import math
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from desk.arbitrage import comps, hunt, markets, value
from desk.arbitrage.repo import Repo
from desk.core.paths import DeskPaths

NOW = datetime(2026, 11, 2, 18, 0, tzinfo=UTC)
TODAY = NOW.date()


def s_item(title, price, sold, shipping="+$12.00 delivery", condition="Brand New", new=False):
    flag = '<span class="LIGHT_HIGHLIGHT">New Listing</span>' if new else ""
    return (f'<li class="s-item"><div class="s-item__info"><div class="s-item__caption-section">'
            f'<div class="s-item__caption"><span class="POSITIVE">Sold  {sold:%b} {sold.day}, {sold.year}</span></div></div>'
            f'<a class="s-item__link"><div class="s-item__title"><span role="heading">{flag}{title}'
            f'<span class="clipped">Opens in a new window or tab</span></span></div></a>'
            f'<div class="s-item__subtitle"><span class="SECONDARY_INFO">{condition}</span></div>'
            f'<div class="s-item__details"><div class="s-item__detail"><span class="s-item__price">'
            f'<span class="POSITIVE">{price}</span></span></div><div class="s-item__detail">'
            f'<span class="s-item__shipping s-item__logisticsCost">{shipping}</span></div></div></div></li>')


def s_card(title, price, sold, shipping="Free delivery"):
    return (f'<li class="s-card"><div class="s-card__title"><span class="su-styled-text primary">{title}</span></div>'
            f'<div class="s-card__subtitle"><span>Pre-Owned · LEGO</span></div>'
            f'<div class="su-card-container__attributes"><span class="s-card__price">{price}</span>'
            f'<div><span class="su-styled-text secondary large">{shipping}</span></div></div>'
            f'<div class="s-card__caption"><span class="su-styled-text positive">Sold  {sold:%b} {sold.day}, {sold.year}'
            f'</span></div></li>')


def page(results, extra=""):
    return ('<html><head><title>lego 75192 | eBay</title><script>var x = "Sold Jan 1, 2020 $1.00";</script></head>'
            '<body><ul class="srp-results"><li class="s-item"><div class="s-item__title"><span>Shop on eBay</span></div>'
            '<span class="s-item__price">$20.00</span></li>' + "".join(results) + extra + "</ul></body></html>")


FEWER = ('<li class="srp-river-answer"><div><h3>Results matching fewer words</h3></div></li>'
         + s_item("Some other LEGO set 10300", "$90.00", TODAY))


def test_both_layouts_and_pasted_text_read_the_same_sales_and_stop_at_fewer_words():
    d1, d2, d3 = TODAY - timedelta(days=1), TODAY - timedelta(days=5), TODAY - timedelta(days=9)
    html = page([s_item("LEGO Star Wars Millennium Falcon 75192 Sealed", "$719.99", d1, new=True),
                 s_item("LEGO 75192 UCS Falcon New", "$1,050.00", d2, shipping="Free delivery", condition="New (Other)"),
                 s_item("LEGO 75192 lot of parts", "$10.00 to $40.00", d3)], FEWER)
    got = comps.parse_html(html)
    assert [(s.price, s.shipping, s.sold_on, s.condition) for s in got] == [
        (719.99, 12.0, d1, "Brand New"), (1050.0, 0.0, d2, "New (Other)")]
    assert got[0].title == "LEGO Star Wars Millennium Falcon 75192 Sealed" and got[0].total == 731.99
    cards = comps.parse_html(page([s_card("LEGO 75192 Millennium Falcon", "$680.00", d1),
                                   s_card("LEGO Falcon 75192 complete", "$650.50", d2, "+$35.10 delivery")]))
    assert [(s.title, s.price, s.shipping, s.sold_on) for s in cards] == [
        ("LEGO 75192 Millennium Falcon", 680.0, 0.0, d1), ("LEGO Falcon 75192 complete", 650.5, 35.1, d2)]
    pasted = f"""Shop on eBay
$20.00
Sold  {d1:%b} {d1.day}, {d1.year}
New Listing LEGO Star Wars Millennium Falcon 75192 Sealed
Brand New
$719.99
or Best Offer
+$12.00 delivery
Located in United States
Sold  {d2:%b} {d2.day}, {d2.year}
LEGO 75192 UCS Falcon New
New (Other)
$1,050.00
Free delivery
Results matching fewer words
Sold  {d3:%b} {d3.day}, {d3.year}
Some other set
$90.00"""
    assert [(s.title, s.total) for s in comps.parse_text(pasted)] == [
        ("LEGO Star Wars Millennium Falcon 75192 Sealed", 731.99), ("LEGO 75192 UCS Falcon New", 1050.0)]
    assert comps.blocked(403, "") and "robot check" in comps.blocked(200, "<title>Pardon Our Interruption...</title>")
    assert comps.blocked(200, html) is None


def sales(prices, start=TODAY, step=3, shipping=0.0):
    return [comps.Sale(p, shipping, start - timedelta(days=i * step), f"Item {i}") for i, p in enumerate(prices)]


def test_market_value_drops_outliers_and_sees_speed_and_trend():
    m = value.summarize(sales([100, 102, 98, 101, 99, 100, 1000, 5]), TODAY)
    assert m.value == pytest.approx(100.0) and m.count == 6 and m.high < 103                 # $1,000 and $5 dropped
    assert m.per_week == pytest.approx(6 / 90 * 7) and m.trend is None                       # only 15 days of sales
    rising = [comps.Sale(100 * math.exp(0.004 * (60 - d)), 0, TODAY - timedelta(days=d), "x") for d in range(0, 61, 4)]
    r = value.summarize(rising, TODAY)
    assert r.trend == pytest.approx(math.exp(0.004 * 30) - 1, rel=1e-6) and r.yearly > 1
    assert value.summarize(sales([50], start=TODAY - timedelta(days=200)), TODAY) is None   # too old to count


def test_the_deal_card_counts_every_cost_and_needs_both_a_share_and_dollars():
    m = value.summarize(sales([100.0] * 30, step=3), TODAY)                                # 30 sold, ~2.3 a week
    settings = {"purchase_tax": 0.07, "buyer_tax": 0.07, "min_profit_pct": 0.30, "min_profit_usd": 10.0}
    deal = value.evaluate(40.0, m, category="general", settings=settings, comps_at=NOW, price_at=NOW, now=NOW,
                          quantity=50)
    fee = 100 * 1.07 * 0.136 + 0.40
    assert deal["fees"] == pytest.approx(fee, abs=0.01) and deal["cost"] == pytest.approx(42.8)
    assert deal["profit"] == pytest.approx(100 - fee - 10 - 42.8, abs=0.01)                # $10 label for general
    assert deal["verdict"] == "buy" and deal["confidence"]["score"] == 5
    assert deal["buy"] == math.floor(m.per_week * 4)                                       # a month of sales, not 50
    assert value.evaluate(70.0, m, category="general", settings=settings, now=NOW)["verdict"] == "skip"
    cheap = value.summarize(sales([4.0] * 30), TODAY)
    assert value.evaluate(0.5, cheap, category="general", settings=settings, now=NOW)["verdict"] == "skip"  # $1 profit
    capped = value.evaluate(40.0, m, category="general", settings={**settings, "bankroll": 200}, now=NOW, quantity=50)
    assert capped["buy"] == 1                                                              # a quarter of $200
    assert value.ebay_fee(200, "sneakers", 0) == pytest.approx(16.0)                      # 8%, no order fee
    assert value.ebay_fee(8, "cards", 0) == pytest.approx(8 * 0.1325 + 0.30)


def test_gold_is_judged_against_what_a_scrap_buyer_pays_for_its_melt():
    melt = value.melt(10, "10k", 3909.30)
    assert melt == pytest.approx(10 / 31.1034768 * 10 / 24 * 3909.30)
    out = value.evaluate_metal(200.0, 10, "10k", 3909.30, {"purchase_tax": 0.07, "scrap_payout": 0.8})
    assert out["scrap_payout"] == pytest.approx(melt * 0.8, abs=0.01) and out["verdict"] == "buy"
    assert out["break_even_price"] == pytest.approx(melt * 0.8 / 1.07, abs=0.01)
    assert value.evaluate_metal(500.0, 10, "10k", 3909.30, {})["verdict"] == "skip"


def test_typed_and_scanned_things_become_the_right_ebay_searches():
    assert hunt.classify("75192") == ("lego", "LEGO 75192") == hunt.classify("lego 75192-1")
    assert hunt.classify("012345678905") == ("item", "012345678905")
    assert hunt.classify("dz5485-612") == ("sneakers", "DZ5485-612")
    assert hunt.classify("  Nintendo   Switch OLED ") == ("item", "Nintendo Switch OLED")
    kept, ratio = hunt.match("LEGO 75192", "lego", [comps.Sale(1, 0, TODAY, "LEGO 75192 Falcon"),
                                                    comps.Sale(1, 0, TODAY, "LEGO 10300 DeLorean")])
    assert [s.title for s in kept] == ["LEGO 75192 Falcon"] and ratio == 0.5


class Web:
    """eBay, the barcode lookup and the card API, answered locally; counts eBay requests."""

    def __init__(self, ebay_html, status=200):
        self.ebay_html, self.status, self.ebay = ebay_html, status, 0

    def __call__(self, request):
        host = request.url.host
        if host == "www.ebay.com":
            self.ebay += 1
            assert request.headers["user-agent"] == markets.AGENT                     # an honest identity
            assert request.url.params["LH_Sold"] == "1"
            return httpx.Response(self.status, text=self.ebay_html)
        if host == "api.upcitemdb.com":
            return httpx.Response(200, json={"items": [{"title": "Hot Wheels 20-car pack", "images": [],
                                                        "lowest_recorded_price": 21.99}]})
        if host == "api.pokemontcg.io":
            return httpx.Response(200, json={"data": {"id": "base1-4", "name": "Charizard", "number": "4",
                                                      "set": {"name": "Base"}, "images": {"small": None},
                                                      "tcgplayer": {"updatedAt": "2026/10/15", "prices": {
                                                          "holofoil": {"market": 954.65}}}}})
        return httpx.Response(404)


def run(coro):
    return asyncio.run(coro)


def test_check_reads_sold_prices_once_ranks_the_deal_and_pauses_politely_when_refused(tmp_path):
    paths = DeskPaths(tmp_path, tmp_path / "config", tmp_path / "secrets.env")
    sold = [s_item(f"Hot Wheels 20 car pack {i}", f"${30 + (i % 3)}.00", TODAY - timedelta(days=2 * i),
                   shipping="+$8.00 delivery") for i in range(20)]
    web = Web(page(sold))

    async def flow():
        async with httpx.AsyncClient(transport=httpx.MockTransport(web)) as client:
            with Repo.open(paths) as repo:
                h = hunt.Hunt(repo.conn)
                ebay = markets.EbaySold(client, {}, gap=0)
                v = await hunt.check(h, client, ebay, NOW, text="027084686524", price=9.97, quantity=12, store="Walmart")
                assert v["item"]["title"] == "Hot Wheels 20-car pack" and web.ebay == 1
                assert v["market"]["count"] == 20 and v["market"]["value"] == pytest.approx(39.0)
                assert v["deal"]["verdict"] == "buy" and v["deal"]["buy"] >= 1 and v["match"] is None
                again = await hunt.check(h, client, ebay, NOW + timedelta(hours=1), text="027084686524", price=8.50)
                assert web.ebay == 1 and again["check"]["price"] == 8.5                     # sold prices reused
                lego = await hunt.check(h, client, ebay, NOW, text="75192", price=500.0)    # no matching titles
                assert lego["market"] is None and lego["deal"] is None
                card = await hunt.check(h, client, ebay, NOW, text="pokemon:base1-4", price=600.0)
                assert card["market"]["basis"] == "market_price" and card["deal"]["profit"] > 0
                assert card["deal"]["confidence"]["checks"][0]["ok"]                        # market price counts
                ranked = h.deals(NOW)
                assert [r["item"]["kind"] for r in ranked][:2] == ["item", "pokemon"] and ranked[-1]["deal"] is None
                assert [r["item"]["kind"] for r in h.deals(NOW, "cards")] == ["pokemon"]
                web.status = 403                                                            # refused: pause
                refused = await hunt.check(h, client, ebay, NOW + timedelta(days=1), text="nintendo switch", price=100)
                assert refused["status"]["status"] == "blocked" and web.ebay == 3         # cards never ask eBay
                paused = await hunt.check(h, client, ebay, NOW + timedelta(days=1, hours=1), text="xbox", price=100)
                assert paused["status"]["status"] == "paused" and web.ebay == 3             # nothing sent
                assert h.state("ebay")["paused_until"]
                pasted = hunt.paste(h, refused["item"]["id"], "\n".join([
                    f"Sold  {TODAY:%b} {TODAY.day}, {TODAY.year}", "Nintendo Switch OLED console", "$250.00",
                    "Free delivery"]), NOW + timedelta(days=1))
                assert pasted["market"]["value"] == 250.0 and pasted["deal"]["verdict"] == "maybe"   # one sale: not a BUY
                with pytest.raises(Exception, match="No sold results"):
                    hunt.paste(h, refused["item"]["id"], "nothing here", NOW)
    run(flow())


def test_holds_rank_rising_items_and_the_background_refreshes_what_you_watch(tmp_path):
    paths = DeskPaths(tmp_path, tmp_path / "config", tmp_path / "secrets.env")
    rising = [s_item("LEGO 10497 Galaxy Explorer", f"${100 * math.exp(0.003 * (80 - d)):.2f}", TODAY - timedelta(days=d))
              for d in range(0, 81, 5)]
    web = Web(page(rising))

    async def flow():
        async with httpx.AsyncClient(transport=httpx.MockTransport(web)) as client:
            with Repo.open(paths) as repo:
                h = hunt.Hunt(repo.conn)
                ebay = markets.EbaySold(client, {}, gap=0)
                v = await hunt.check(h, client, ebay, NOW, text="10497")
                assert v["check"] is None and v["market"]["yearly"] > 1
                assert [r["item"]["query"] for r in h.holds(NOW)] == ["LEGO 10497"] and h.deals(NOW) == []
                assert h.due(NOW + timedelta(hours=25)) == []                       # never checked a price, not watched
                h.flag(v["item"]["id"], "watch", True)
                assert [i["query"] for i in h.due(NOW + timedelta(hours=25))] == ["LEGO 10497"]
                assert h.due(NOW + timedelta(hours=2)) == []                        # refreshed within a day
                h.flag(v["item"]["id"], "hidden", True)
                assert h.holds(NOW) == []
    run(flow())


def test_opening_arbitrage_restarts_a_server_running_older_code(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from desk.arbitrage.web import create_app
    from desk.ops import dashboard_restart, launcher
    from desk.stocks.runtime import source_revision

    paths = DeskPaths(tmp_path, tmp_path / "config", tmp_path / "secrets.env")
    with TestClient(create_app(paths, auto_start=False), base_url="http://127.0.0.1") as client:
        runtime = client.get("/api/hunt/runtime").json()
    assert runtime == {"application": "kestrel-arbitrage", "running_revision": source_revision()}
    assert not launcher.arbitrage_is_current("http://127.0.0.1:9/")          # nothing there: not current
    restarted = []
    monkeypatch.setattr(launcher, "responding", lambda url, timeout=1.0: True)
    monkeypatch.setattr(dashboard_restart, "restart_dashboard", lambda p, desk: restarted.append(desk))
    for current in (True, False):
        monkeypatch.setattr(launcher, "arbitrage_is_current", lambda url, timeout=2, current=current: current)
        launcher.ensure_running("arbitrage", 8768, paths)
    assert restarted == ["arbitrage"]                                         # only the outdated one
