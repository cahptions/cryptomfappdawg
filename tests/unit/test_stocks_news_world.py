"""The News desk's inputs: GDELT's world event files turned into supply shocks for exposed companies, presidential
trade actions tagged and studied against what prices did next, and company news tone and attention, all point in
time. The desk starts with no vote. Every web answer here is synthetic, in the shape each source serves."""

import asyncio
import io
import json
import zipfile
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest

from desk.stocks import desks, news_flow, policy, world
from tests.stocks_world import make_world

NOW = datetime(2026, 11, 3, 15, 0, tzinfo=UTC)
TODAY = NOW.date()


def event_row(day: date, root: str, base: str, country: str, articles: int, url: str, place: str = "Kinshasa") -> list[str]:
    row = [""] * 61
    row[26], row[27], row[28] = base + "0", base, root
    row[30], row[33], row[52], row[53] = "-8.0", str(articles), place, country
    row[59], row[60] = day.strftime("%Y%m%d") + "120000", url
    return row


def export_zip(rows: list[list[str]]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        z.writestr("x.export.CSV", "\n".join("\t".join(r) for r in rows))
    return buffer.getvalue()


def test_event_files_become_daily_totals_by_country_and_kind_with_their_top_sources(tmp_path):
    rows = [event_row(TODAY, "19", "190", "CG", 12, "https://www.radiookapi.net/a"),
            event_row(TODAY, "19", "193", "CG", 30, "https://actualite.cd/b"),
            event_row(TODAY, "16", "163", "RS", 8, "https://tass.ru/c", "Moscow"),       # sanctions
            event_row(TODAY, "04", "040", "CG", 99, "https://x.test/consult")]            # a meeting: not a disruption
    totals = world.parse_events(export_zip(rows))
    assert totals.events == 3
    assert totals.days[(TODAY.toordinal(), "CG", "conflict")] == [2, 42, -16.0]
    assert totals.days[(TODAY.toordinal(), "RS", "sanctions")][1] == 8
    w = make_world(tmp_path, n=12)
    try:
        conn = w.market.conn
        world.save_file(conn, "20261103143000", totals, NOW)
        world.save_file(conn, "20261103143000", totals, NOW)                             # never counted twice
        assert conn.execute("SELECT articles FROM stk_world_daily WHERE country='CG'").fetchone()[0] == 42
        urls = [r[0] for r in conn.execute("SELECT url FROM stk_world_sources WHERE country='CG' ORDER BY articles DESC")]
        assert urls == ["https://actualite.cd/b", "https://www.radiookapi.net/a"]
    finally:
        w.close()
    assert world.kind_of("19", "190") == "conflict" and world.kind_of("17", "172") == "sanctions"
    assert world.kind_of("04", "040") is None
    names = world.stamps(NOW, days=1)
    assert names[0] == "20261103143000" and len(names) == 96 and all(n[-4:] in ("0000", "1500", "3000", "4500")
                                                                     for n in names)


def seed_world(conn, *, base: int, recent: int, country: str = "CG", days: int = 20) -> None:
    for back in range(days):
        day = TODAY - timedelta(days=back)
        for q in range(96):                                                              # every 15-minute file arrived
            stamp = datetime.combine(day, datetime.min.time()) + timedelta(minutes=15 * q)
            conn.execute("INSERT OR IGNORE INTO stk_world_files VALUES (?,?,0)", (stamp.strftime("%Y%m%d%H%M%S"),
                                                                                   NOW.isoformat()))
        conn.execute("INSERT INTO stk_world_daily VALUES (?,?,?,?,?,?)",
                     (day.toordinal(), country, "conflict", 10, recent if back < 2 else base, -50.0))
    conn.execute("INSERT INTO stk_world_sources VALUES (?,?,?,?,?,?)",
                 (TODAY.toordinal(), country, "conflict", "https://actualite.cd/mines", 40, "Kolwezi"))


def test_a_supply_country_whose_coverage_jumps_flags_the_companies_that_depend_on_it(tmp_path):
    w = make_world(tmp_path, n=12)
    try:
        conn = w.market.conn
        seed_world(conn, base=40, recent=160)                                            # four times its usual level
        nodes = {n["id"]: n for n in world.status(conn, TODAY)}
        cobalt = nodes["cobalt"]
        assert cobalt["spike"] and cobalt["ratio"] == pytest.approx(4.0) and cobalt["strength"] == 1.0
        assert cobalt["sources"][0]["url"] == "https://actualite.cd/mines"
        assert not nodes["taiwan_chips"]["spike"] and nodes["global_conflict"]["ratio"] == pytest.approx(4.0)
        node = world.BY_ID["cobalt"]
        assert world.exposure(node, "XYZ", "3711") == -1 and world.exposure(node, "XYZ", "7372") == 0
        assert world.exposure(world.BY_ID["hormuz"], "XOM", None) == 1
        got = news_flow.inputs(conn, TODAY, NOW, ["CARS", "SOFT", "LMT"], {"CARS": "3711", "SOFT": "7372"})
        assert [s["node"] for s in got["CARS"]["world"]] == ["cobalt"] and got["CARS"]["world"][0]["sign"] == -1
        assert "SOFT" not in got and got["LMT"]["world"][0]["node"] == "global_conflict"
    finally:
        w.close()
    w = make_world(tmp_path / "quiet", n=12)
    try:
        seed_world(w.market.conn, base=40, recent=44, days=3)                            # too little history yet
        cobalt = next(n for n in world.status(w.market.conn, TODAY) if n["id"] == "cobalt")
        assert not cobalt["ready"] and not cobalt["spike"]
    finally:
        w.close()


def doc(number, title, signed, abstract=""):
    return {"document_number": number, "title": title, "publication_date": signed, "signing_date": signed,
            "html_url": f"https://www.federalregister.gov/d/{number}", "abstract": abstract, "subtype": "proclamation"}


def test_trade_actions_are_tagged_and_studied_against_what_prices_did_next(tmp_path):
    t = policy.tags("Adjusting Imports of Steel and Aluminum Into the United States", "Section 232 duties on China")
    assert t["trade"] and t["topics"] == ["steel", "aluminum"] and t["countries"] == ["China"]
    assert not policy.tags("Delegation of Authority Under Section 8335", None)["trade"]
    w = make_world(tmp_path, n=12)
    try:
        conn = w.market.conn
        days = sorted({b.day for b in w.market.bars("SPY", date(2000, 1, 1))})
        first = days[-40]
        assert policy.save(conn, [doc("2026-1", "Adjusting Imports of Steel Into the United States", first.isoformat()),
                                  doc("2026-2", "Further Modifying Steel Duties", (first + timedelta(days=1)).isoformat()),
                                  doc("2026-3", "Honoring a Holiday", (first + timedelta(days=9)).isoformat())]) == 3
        assert policy.save(conn, [doc("2026-1", "Adjusting Imports of Steel Into the United States",
                                      first.isoformat())]) == 0
        docs = policy.documents(conn, first, days[-1])
        assert [d["trade"] for d in docs] == [False, True, True] and docs[-1]["topic_labels"] == ["Steel"]
        study = policy.event_study(conn, w.market)
        assert study["events"] == 1                                                      # two days apart: one event
        assert set(study["horizons"]) == {"1", "5", "10", "20"} and study["horizons"]["5"]["n"] == 1
        got = news_flow.inputs(conn, first + timedelta(days=3), NOW, ["NUE", "CARS"], {"CARS": "3711"})
        assert got["NUE"]["policy"][0]["sign"] == 1 and got["CARS"]["policy"][0]["sign"] == -1
        assert news_flow.inputs(conn, first + timedelta(days=30), NOW, ["NUE"], {}) == {}    # older than 15 days
    finally:
        w.close()


def article(aid, published, tickers, sentiment="negative"):
    return {"id": aid, "published": published.isoformat(), "title": f"Headline {aid}", "url": f"https://news.test/{aid}",
            "source": "Wire", "summary": None, "tickers": tickers,
            "insights": [{"ticker": t, "sentiment": sentiment, "reasoning": "fixture"} for t in tickers]}


def test_company_news_is_point_in_time_and_the_news_desk_scores_it_without_a_vote(tmp_path):
    w = make_world(tmp_path, n=12)
    try:
        conn = w.market.conn
        end = datetime(2026, 11, 3, 20, 0, tzinfo=UTC)
        arts = [article(f"n{i}", end - timedelta(days=i * 0.5), ["ACME"]) for i in range(6)]
        arts += [article("p1", end - timedelta(days=1), ["ACME"], "positive"),
                 article("old", end - timedelta(days=30), ["ACME"], "positive")]
        assert news_flow.save(conn, arts, NOW) == 8 and news_flow.save(conn, arts, NOW) == 0
        late = article("late", end - timedelta(hours=2), ["ACME"], "positive")
        news_flow.save(conn, [late], end + timedelta(days=1))                            # received after the analysis
        c = news_flow.company(conn, TODAY, end)["ACME"]
        assert (c["negative"], c["positive"]) == (6, 1) and c["recent3"] == 7
        # the store begins 30 days back, so 23.3 of the 60 comparison days are covered: one article there is 2.57 in 60
        assert c["earlier_days"] == 23.3 and c["earlier60"] == round(60 / (23 + 479 / 1440), 2) == 2.57
        ctx = desks.Context(TODAY, ["ACME", "QUIET"], {}, {}, {}, {}, {}, news=news_flow.inputs(conn, TODAY, end, ["ACME"],
                                                                                              {}))
        result = desks.news(ctx, ["ACME", "QUIET"])
        assert result["ACME"].score < 40 and "6 negative" in result["ACME"].reasons[0]
        assert result["QUIET"].score is None
        assert desks.DEFAULT_WEIGHTS["news"] == 0.0 and "news" in desks.DESKS             # shown, but no vote yet
        vote = desks.committee({"news": result["ACME"], "momentum": desks.DeskResult(80.0, 1.0, "", [])},
                               desks.DEFAULT_WEIGHTS)
        assert vote["score"] == 80.0
    finally:
        w.close()


class FakeMassive:
    configured = True

    def __init__(self, pages, older=None):
        self.pages, self.calls, self.older, self.before = pages, [], older or [], []

    async def market_news(self, since, limit=1000):
        self.calls.append(since)
        return self.pages.pop(0) if self.pages else []

    async def market_news_before(self, until, limit=1000):
        self.before.append(until)
        return self.older.pop(0) if self.older else []


def test_collection_pages_forward_and_reads_the_world_and_the_federal_register(tmp_path):
    w = make_world(tmp_path, n=12)
    try:
        conn = w.market.conn
        start = NOW - timedelta(days=7)                                                  # the first time: a week
        page = [article(f"a{i}", start + timedelta(minutes=i), ["ACME"]) for i in range(1000)]
        massive = FakeMassive([page, [article("b1", NOW - timedelta(hours=1), ["ACME"])]])
        out = asyncio.run(news_flow.collect(conn, massive, NOW))
        assert out["new"] == 1001 and out["calls"] == 2 and massive.calls[0] == start
        assert massive.before == [start] and out["history"]["calls"] == 1                 # then back, in the same pass
        assert massive.calls[1] == datetime.fromisoformat(page[-1]["published"])          # forward from the last one

        def answer(request):
            if request.url.host == "data.gdeltproject.org":
                stamp = request.url.path.rsplit("/", 1)[-1][:14]
                if stamp.endswith("0000"):
                    return httpx.Response(404)
                return httpx.Response(200, content=export_zip([event_row(TODAY, "19", "190", "TW", 5,
                                                                         "https://udn.com/x", "Taipei")]))
            if request.url.host == "www.federalregister.gov":
                return httpx.Response(200, json={"results": [doc("2026-9", "Adjusting Imports of Robotics", "2026-11-02")]})
            return httpx.Response(404)

        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(answer)) as client:
                got = await world.collect(conn, client, NOW, limit=8)
                again = await world.collect(conn, client, NOW, limit=8)
                fr = await policy.collect(conn, client, TODAY)
                return got, again, fr
        got, again, fr = asyncio.run(run())
        assert got["files"] + got["failed"] <= 8 and got["missing"] > 0
        assert again["missing"] == got["missing"] - 8                                     # the next pass continues
        assert conn.execute("SELECT COUNT(*) FROM stk_world_files").fetchone()[0] == 16   # 404s recorded as empty
        assert fr == {"documents": 1, "new": 1}
        assert json.loads(conn.execute("SELECT body FROM stk_policy_docs").fetchone()[0])["topics"] == ["machinery"]
    finally:
        w.close()
