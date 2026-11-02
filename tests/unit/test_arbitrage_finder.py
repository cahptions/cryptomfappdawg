"""The finders fill Deals and Buy & Hold on their own: underpriced eBay listings against the same item's sold prices,
gold and silver listings against melt, deal posts naming an exact item, and rising Pokémon cards. All web answers here
are synthetic, in the structure each source serves."""

import asyncio
from datetime import timedelta

import httpx
import pytest

from desk.arbitrage import comps, finder, hunt, markets
from desk.arbitrage.repo import Repo
from desk.core.paths import DeskPaths
from tests.unit.test_arbitrage_hunt import NOW, TODAY, page, s_item


def listing(title, price, number, shipping="Free delivery"):
    return (f'<li class="s-item"><div class="s-item__image"><img src="https://i.ebayimg.com/images/g/{number}/s-l140.jpg">'
            f'</div><a class="s-item__link" href="https://www.ebay.com/itm/{number}?hash=x"><div class="s-item__title">'
            f'<span role="heading">{title}</span></div></a><div class="s-item__subtitle"><span>Brand New</span></div>'
            f'<span class="s-item__price">{price}</span><span class="s-item__shipping">{shipping}</span></li>')


SOLD = page([s_item(f"LEGO Star Wars 75192 Millennium Falcon sealed #{i}", f"${700 + i * 5}.00", TODAY - timedelta(days=3 * i),
                    shipping="Free delivery") for i in range(15)])
FOR_SALE = page([listing("LEGO 75192 Millennium Falcon NEW sealed", "$400.00", 111),
                 listing("LEGO 75192 Millennium Falcon BOX ONLY", "$40.00", 112),
                 listing("LEGO 10300 DeLorean", "$120.00", 113),
                 listing("LEGO 75192 Millennium Falcon UCS new", "$690.00", 114, "+$25.00 delivery")])
GOLD = page([listing("10K Yellow Gold Scrap Lot 5.2 Grams Not Scrap Ring", "$120.00", 201),
             listing("14k Gold Plated Chain 10g", "$15.00", 202),
             listing("Sterling Silver 925 scrap 50 grams", "$60.00", 203)])
RSS = """<?xml version="1.0"?><rss version="2.0"><channel><title>Feed</title>
<item><title>LEGO Star Wars Millennium Falcon (75192) $499.99 + Free Shipping</title>
<link>https://slickdeals.net/f/1-lego-falcon</link></item>
<item><title>The Lego Movie 2 Videogame (PC Digital Download) $0.89</title><link>https://slickdeals.net/f/2</link></item>
<item><title>Kobalt Multi Tool $8 + Free Pickup</title><link>https://slickdeals.net/f/3</link></item>
</channel></rss>"""


def card(cid, name, market, avg7, avg30):
    return {"id": cid, "name": name, "number": "4", "set": {"name": "Fixture Set"}, "images": {"small": None},
            "tcgplayer": {"updatedAt": "2026/10/30", "prices": {"holofoil": {"market": market}}},
            "cardmarket": {"prices": {"avg1": avg7, "avg7": avg7, "avg30": avg30}}}


class Web:
    def __init__(self):
        self.ebay = []
        self.status = 200

    def __call__(self, request):
        host, params = request.url.host, request.url.params
        if host == "www.ebay.com":
            assert request.headers["user-agent"] == markets.AGENT
            self.ebay.append(params["_nkw"])
            if self.status != 200:
                return httpx.Response(self.status, text="")
            if "gold" in params["_nkw"] or "silver" in params["_nkw"]:
                return httpx.Response(200, text=GOLD)
            return httpx.Response(200, text=SOLD if params.get("LH_Sold") == "1" else FOR_SALE)
        if host in ("slickdeals.net", "www.dealnews.com"):
            return httpx.Response(200, text=RSS if "lego" in str(request.url) else "<rss><channel></channel></rss>")
        if host == "api.pokemontcg.io" and request.url.path == "/v2/sets":
            return httpx.Response(200, json={"data": [{"id": "fx1", "name": "Fixture Set"}]})
        if host == "api.pokemontcg.io":
            return httpx.Response(200, json={"data": [card("fx1-4", "Riser", 40.0, 30.0, 20.0),
                                                      card("fx1-5", "Flat", 40.0, 20.0, 20.0),
                                                      card("fx1-6", "Cheap riser", 2.0, 3.0, 1.0)]})
        if host == "api.gold-api.com":
            return httpx.Response(200, json={"price": 4000.0 if "XAU" in request.url.path else 50.0})
        return httpx.Response(404)


def test_listing_pages_read_each_listing_with_its_link_and_photo():
    got = comps.parse_listings(FOR_SALE)
    assert [(x.price, x.shipping, x.url) for x in got][:2] == [(400.0, 0.0, "https://www.ebay.com/itm/111"),
                                                              (40.0, 0.0, "https://www.ebay.com/itm/112")]
    assert got[0].image == "https://i.ebayimg.com/images/g/111/s-l140.jpg" and got[3].total == 715.0
    assert comps.parse_listings(SOLD) == []                                   # sold results aren't offers


def test_deal_posts_and_metal_titles_name_exact_items_only():
    assert finder.identify("LEGO Icons Millennium Falcon (75192) $499.99") == ("lego", "LEGO 75192")
    assert finder.identify("80-Piece LEGO Bluey Grannies Set (11216) $11.99") == ("lego", "LEGO 11216")
    assert finder.identify("LEGO The Hobbit (PC Digital Download) $2.09") is None
    assert finder.identify("Air Jordan 1 Lost and Found DZ5485-612 $120") == ("sneakers", "DZ5485-612")
    assert finder.identify("Kobalt 15-in-1 Multi Tool $8") is None
    assert finder.metal_of("10K Yellow Gold Scrap Lot 5.2 Grams") == ("10k", 5.2)
    assert finder.metal_of("14k Gold Plated Chain 10g") is None
    assert finder.metal_of("Sterling Silver 925 scrap 50 grams") == ("925", 50.0)
    assert finder.metal_of("14K gold ring size 7") is None                   # no weight, no deal


def test_the_finders_fill_deals_and_buy_and_hold_by_themselves(tmp_path):
    paths = DeskPaths(tmp_path, tmp_path / "config", tmp_path / "secrets.env")
    web = Web()

    async def flow():
        async with httpx.AsyncClient(transport=httpx.MockTransport(web)) as client:
            with Repo.open(paths) as repo:
                h = hunt.Hunt(repo.conn)
                finder.save_hunts(h, "LEGO 75192\nmetal: 10k gold scrap")
                await hunt.spot(h, client, NOW)
                ebay = markets.EbaySold(client, {}, gap=0)
                await finder.run(h, client, ebay, NOW)
                deals = h.deals(NOW)
                assert deals[0]["offer"]["url"] == "https://www.ebay.com/itm/201"      # 10k gold: +74%, ranked first
                first = next(d for d in deals if d.get("offer", {}).get("url") == "https://www.ebay.com/itm/111")
                assert first["deal"]["verdict"] == "buy"
                assert first["deal"]["price"] == 400.0 and first["market"]["count"] == 15
                urls = {d["offer"]["url"] for d in deals if d.get("offer")}
                assert "https://www.ebay.com/itm/112" not in urls and "https://www.ebay.com/itm/113" not in urls
                assert "https://slickdeals.net/f/1-lego-falcon" in urls                 # the post, priced against sales
                metals = [d for d in deals if d.get("metal")]
                assert {(d["offer"]["karat"], d["offer"]["grams"]) for d in metals} == {("10k", 5.2), ("925", 50.0)}
                gold = next(d for d in metals if d["offer"]["karat"] == "10k")
                assert gold["metal"]["melt"] == pytest.approx(5.2 / 31.1034768 * 10 / 24 * 4000, abs=0.01)
                assert [d["item"]["kind"] for d in h.deals(NOW, "metal")] == ["item", "item"]
                holds = h.holds(NOW)
                assert [v["item"]["title"] for v in holds] == ["Riser · Fixture Set #4"]
                assert "+50%" in holds[0]["rise"]["text"]
                state = h.state("finders")
                assert state["listings"]["ok"] and state["feeds"]["found"] == 1 and state["cards"]["found"] == 1
                reads = len(web.ebay)
                await finder.run(h, client, ebay, NOW + timedelta(hours=1))           # nothing is due yet
                assert len(web.ebay) == reads
                finder.hide_offer(h, first["offer"]["id"])
                assert all(d.get("offer", {}).get("url") != "https://www.ebay.com/itm/111" for d in h.deals(NOW))
                web.status = 403                                                       # eBay refuses: finders wait
                await finder.run(h, client, ebay, NOW + timedelta(hours=13))
                assert "Waiting for eBay" not in (h.state("finders")["listings"]["note"] or "")
                await finder.run(h, client, ebay, NOW + timedelta(hours=14))
                assert "Waiting for eBay" in h.state("finders")["listings"]["note"]
                h.flag(first["item"]["id"], "hidden", True)                          # hiding an item hides its offers
                assert all(d["item"]["id"] != first["item"]["id"] for d in h.deals(NOW + timedelta(hours=14)))
    asyncio.run(flow())
