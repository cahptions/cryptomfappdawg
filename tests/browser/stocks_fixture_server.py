"""Synthetic Stocks dashboard fixture, isolated from operator data and all providers.

uv run python -m tests.browser.stocks_fixture_server --scenario populated --port 8877
"""

from __future__ import annotations

import argparse
import json
import tempfile
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import uvicorn

from desk.core.config import load_config
from desk.core.paths import DeskPaths
from desk.stocks import calendar as cal
from desk.stocks.contracts import Bar
from desk.stocks.hub import INDEX_NAMES, SECTOR_NAMES
from desk.stocks.runner import process
from desk.stocks.web import create_app
from tests.conftest import REPO
from tests.stocks_world import NOW, StocksWorld, add_chains, make_world


def stock_page_fixture(world: StocksWorld) -> None:
    """Synthetic company context for S10: every value is invented and labelled as a fixture, never issuer data."""
    import json
    import math

    from desk.stocks.contracts import Dividend, IntradayBar, StockQuote
    from desk.stocks.stock_views import request, save_history, save_intraday, save_quote
    from desk.stocks.symbol_feeds import request as request_feeds

    market = world.dbs.market
    request(market, "S10", NOW)
    request_feeds(market, "S10", NOW)
    session = world.days[-1]
    opening = cal.session_close(session) - timedelta(hours=6, minutes=30)
    minutes = []
    for day_index, day in enumerate(world.days[-5:]):
        start = cal.session_close(day) - timedelta(hours=6, minutes=30)
        for minute in range(390):
            op = 49 + day_index * .3 + minute * .002 + .15 * math.sin(minute / 37)
            close = op + .03 * math.cos(minute)
            minutes.append(IntradayBar("S10", start + timedelta(minutes=minute), op, max(op, close) + .03,
                                       min(op, close) - .03, close, 2000 + minute))
    save_intraday(market, "S10", minutes, NOW)
    save_history(market, "S10", [Bar("S10", b.day, b.open, b.high, b.low, b.close, b.volume)
                                 for b in world.market.bars("S10")], NOW)
    save_quote(market, StockQuote("S10", 50.18, 50.17, 50.19, 49.07, 2.1e6, opening + timedelta(hours=6, minutes=29)), NOW,
               {"sections": ["quote", "fundamental", "reference", "extended"], "open": 49.2, "high": 50.4, "low": 49.1,
                "bid_size": 300, "ask_size": 200, "avg_volume_10d": 2400000, "high_52w": 50.38, "low_52w": 34.51,
                "hard_to_borrow": True, "shortable": True, "borrow_rate": 7.5,
                "ext_last": 50.31, "regular_last": 50.18})
    world.market.save_dividends([Dividend("S10", session - timedelta(days=d), .21, session - timedelta(days=d - 14), 4,
                                          "CD") for d in (35, 126, 217, 308)]
                                + [Dividend("S10", session + timedelta(days=56), .22, None, 4, "CD")], NOW)
    news = [{"id": f"fixture-{i}", "title": title, "url": f"https://example.test/news/{i}", "summary": summary,
             "published": (NOW - timedelta(hours=hours)).isoformat(), "source": source, "author": None,
             "tickers": ["S10"], "insights": [{"ticker": "S10", "sentiment": mood, "reasoning": "Fixture sentiment."}]}
            for i, (title, summary, source, hours, mood) in enumerate([
                ("S10 Corp raises its full-year outlook after a strong quarter",
                 "Synthetic browser fixture headline used to check layout; not a real article.", "Fixture Wire", 3,
                 "positive"),
                ("Analysts debate S10's margins as costs rise",
                 "Synthetic browser fixture headline used to check layout; not a real article.", "Example Markets", 27,
                 "neutral"),
                ("S10 insider files a planned share sale",
                 "Synthetic browser fixture headline used to check layout; not a real article.", "Fixture Wire", 75,
                 "negative")])]
    feeds = {
        "details": {"name": "S10 Corp", "description": "S10 Corp is a synthetic company used by KESTREL's browser tests. "
                    "It designs, builds and services equipment for regional banks and payment networks, and licenses "
                    "the software that runs on it. Every figure on this page comes from fixture data and none of it "
                    "describes a real issuer. The paragraph is long enough to exercise the show-more control on the "
                    "page, which keeps the about section short until a reader asks for the rest of the text.",
                    "employees": 12400, "headquarters": "Pittsburgh, PA", "list_date": "1998-05-14",
                    "homepage_url": "https://example.test", "industry": "SERVICES-COMPUTER PROGRAMMING"},
        "wikidata": {"founded": "1987", "ceo": "Fixture Person"},
        "news": news,
        "ratings": [{"period": session.replace(day=1).isoformat(), "strong_buy": 6, "buy": 11, "hold": 7, "sell": 2,
                     "strong_sell": 0}],
        "earnings": [{"period": q, "year": None, "quarter": None, "actual": a, "estimate": e,
                      "surprise": a - e, "surprise_pct": 100 * (a - e) / e}
                     for q, a, e in [("2025-12-31", .61, .58), ("2026-03-31", .55, .57), ("2026-06-30", .66, .63),
                                     ("2026-09-30", .71, .69)]],
        # nine days from the real today: the page compares against today's date, so a fixed date expires
        "calendar": [{"date": (datetime.now(UTC).date() + timedelta(days=9)).isoformat(), "when": "after the close",
                      "year": None,
                      "quarter": None, "eps_estimate": .74, "eps_actual": None, "revenue_estimate": None,
                      "revenue_actual": None}],
    }
    for feed, value in feeds.items():
        market.execute("UPDATE stk_symbol_feeds SET payload_json=?,updated_at=?,attempted_at=?,error=NULL "
                       "WHERE symbol='S10' AND feed=?", (json.dumps(value), NOW.isoformat(), NOW.isoformat(), feed))


def events_fixture(world: StocksWorld) -> None:
    """Notable events: the real scan over the synthetic history, plus one planted sell-off day (fixture only)."""
    from desk.stocks import market_events as me
    from desk.storage.db import transaction

    world.market.job("corporate_actions", NOW, detail={"through": world.days[-1].isoformat()})
    me.scan(world.market, world.research.conn, NOW)
    day = world.days[-2]
    members = [{"symbol": s, "move": m} for s, m in (("S10", -0.131), ("S11", -0.052), ("CHEAP", -0.031),
                                                       ("SQZ", -0.024), ("S12", 0.004))]
    planted = [
        me.Event(day, "move", "stock", "S10", 3, "down", "negative", "S10 Corp (S10) fell 13.1%",
                 "#1 most traded US stock; 6.2× its usual daily move of 2.1%. Its largest one-day fall in stored "
                 "prices (since Oct 21, 2024). The day it reported results (SEC 8-K filed Oct 19).",
                 {"symbol": "S10", "move": -0.131, "z": -6.2, "usual": 0.021, "most_traded_rank": 1}),
        me.Event(day, "move", "theme", "ai", 2, "down", "negative", "AI stocks fell 3.4%",
                 "4 of 5 members fell. 2.9× its usual daily move of 1.2% (prior 63 sessions).",
                 {"move": -0.034, "z": -2.9, "usual": 0.012, "members": members, "counted": 5, "moved": 4}),
        me.Event(day, "breadth", "market", "market", 1, "down", "negative",
                 "Broad sell-off: 88% of 1,912 actively traded stocks fell",
                 "Stocks above $5 trading over $20M a day; the median stock fell 1.9%.",
                 {"share": 0.88, "counted": 1912, "fell": 1683, "rose": 229, "median_move": -0.019}),
    ]
    with transaction(world.research.conn):
        me.save(world.research.conn, planted, NOW, "close")
    me.save_headlines(world.research.conn, day, "S10", [
        {"title": "Synthetic headline: S10 slides after results (browser fixture)", "url": "https://example.test/s10",
         "source": "Fixture Wire", "published": NOW.isoformat()}], NOW)


def replay_fixture(world: StocksWorld) -> None:
    """A few synthetic section-replay picks and outcomes (source ``synthetic_replay_fixture``), so Performance shows
    the replay section and its own calibration; never real prices."""
    from desk.core.hashing import canonical_json
    from desk.stocks import play_replay

    m, conn = play_replay.method(), world.market.conn
    at = NOW.isoformat()
    for k, day in enumerate(world.days[-140:-20:20]):
        expiry = day + timedelta(days=30)
        for category, structure, action in (("edge", "long_call", "BUY"), ("iron_condors", "iron_condor", "SELL")):
            legs = [{"occ": f"SPY{k}{category}", "kind": "call", "side": "buy", "strike": 100.0,
                     "expiration": expiry.isoformat(), "bid": 1.9, "ask": 2.1, "delta": 0.4, "iv": 0.3}]
            chances = {"chances": {"itm_kestrel_raw": 0.35 + 0.05 * k, "itm_market": 0.33, "profit": 0.3},
                       "max_gain_return": None}
            twin = {"symbol": "QQQ", "action": action, "structure": structure, "expiration": expiry.isoformat(),
                    "legs": legs, "entry": 2.1, "max_loss": 210.65}
            conn.execute("INSERT INTO stk_replay_plays VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         (m, day.toordinal(), category, 1, "SPY", structure, action, expiry.toordinal(), "itm", "call",
                          100.0, 0.0, 2.1, 210.65, 30, canonical_json(legs), canonical_json(chances), "chain",
                          canonical_json(twin), "synthetic_replay_fixture", at))
            conn.execute("INSERT INTO stk_replay_days VALUES (?,?,?,?,?,?)", (m, day.toordinal(), category, 1, 3, at))
            net = 0.6 if k % 2 else -0.8
            conn.execute("INSERT INTO stk_replay_outcomes VALUES (?,?,?,?,?,?,?,?,?,?)",
                         (m, day.toordinal(), category, 1, 104.0 if k % 2 else 97.0, k % 2, 3.4 if k % 2 else 0.0, net,
                          -0.2, at))


def learner_fixture(root: Path) -> None:
    """Forty recorded closing sessions of synthetic chains, labelled, a small shadow model trained on them (short
    trees, so the fixture starts in seconds) and the shadow views saved: synthetic data, never real prices."""
    from desk.stocks import plays
    from desk.stocks.learner import experience, labels, model, view
    from desk.stocks.runner import publish_plays

    world = make_world(root, n=14)
    add_chains(world, ["SPY", "QQQ", "CHEAP", "SQZ", "S10", "S11", "S12"], world.days[-70:], max_days=120)
    for d in world.days[-70:-28]:
        close = cal.session_close(d) + timedelta(minutes=30)
        plays.build(world.market, world.research, close)
        experience.record_forward(world.market, world.research, d, close)
    evening = cal.session_close(world.days[-1]) + timedelta(hours=4)
    labels.run(world.research.conn, world.market.conn, labels.ForwardPrices(world.market.conn, world.research.conn),
               evening, limit=10_000)
    model.PARAMS = {**model.PARAMS, "max_iter": 12}
    model.EXIT_PARAMS = {**model.EXIT_PARAMS, "max_iter": 6}
    model.train(world.research.conn, world.paths.home, evening, note="synthetic browser fixture")
    publish_plays(world.paths, world.dbs, evening)
    view.refresh(world.paths, world.dbs, evening, force=True)
    world.close()


def prepare(root: Path, scenario: str) -> DeskPaths:
    """Build one scenario's synthetic data directory under ``root`` (no server): the browser fixture's data, also
    used by the accuracy checks (desk.stocks.accuracy) to test every scenario's numbers."""
    paths = DeskPaths(root / "home", REPO / "config", root / "secrets.env")
    args = argparse.Namespace(scenario=scenario)
    if args.scenario == "ops":
        # Portfolio risk, phone alerts and the watchdog. Alert delivery is a recorder: nothing leaves this computer.
        import os

        from desk.stocks import alerts, watchdog, web
        from tests.unit.test_stocks_risk import NOW as RISK_NOW
        from tests.unit.test_stocks_risk import world as risk_world

        risk_world(root)
        web.utc_now = lambda: RISK_NOW
        paths.secrets_file.write_text("NTFY_TOPIC_URL=https://ntfy.example.test/kestrel-browser-fixture-topic\n")
        os.chmod(paths.secrets_file, 0o600)
        alerts.deliver = lambda text, secrets, client=None: ["ntfy"]
        watchdog.record(paths, RISK_NOW, worker_running=True)
    elif args.scenario == "learner":
        learner_fixture(root)
    elif args.scenario == "archive-history":
        from desk.stocks import play_selections, play_tracking, web
        from desk.stocks.repo import StocksDatabases
        from tests.integration.test_stocks_play_archive import IDENTIFIED, LATER, board, initial_two, pick

        web.utc_now = lambda: LATER
        dbs = StocksDatabases.open(paths)
        initial_two(dbs)
        for category,names in [("likely_itm",["AAPL","MSFT"]),("unusual",["NVDA"])]:
            play_selections.register(dbs.research,board(IDENTIFIED,category,[pick(s) for s in names]),IDENTIFIED)
        # Original older installation saved this sighting without putting it in the closing cohort.
        play_tracking.first_seen(dbs.research,board(IDENTIFIED,"bear_puts",[pick("AMZN","put")]),IDENTIFIED)
        for symbol in ["SPY","QQQ","AAPL","MSFT","NVDA","AMZN"]:
            p=pick(symbol,"put" if symbol == "AMZN" else "call")
            dbs.research.execute("INSERT INTO stk_track_ticks(occ,at,session,bid,ask,underlying,quoted_at,source) "
                "VALUES (?,?,?,?,?,?,?,?)",(p["legs"][0]["occ"],LATER.isoformat(),LATER.date().toordinal(),
                                           4.,4.1,102.,LATER.isoformat(),"synthetic_archive_fixture"))
        dbs.close()
    elif args.scenario in {"plays-history", "learning-report"}:
        from desk.stocks import play_learning, play_performance
        from tests.integration.test_stocks_play_history import simulate

        # Six synthetic sessions of picks, tracked, marked, one laptop-off session repaired (no provider).
        world = make_world(root, n=60)
        simulate(world)
        replay_fixture(world)
        evening = cal.session_close(world.days[-1]) + timedelta(hours=2)
        if args.scenario == "learning-report":
            # The Learning report's richest fixture: last week's snapshot is taken before the Plays learning
            # report and the Learning book exist, so "Changed since last week" has something to say.
            from desk.stocks import learning_report

            earlier = learning_report.build(paths, evening)
            world.research.conn.execute(
                "INSERT INTO stk_learning_reports(week, created_at, report_json) VALUES (?, ?, ?)",
                (learning_report.week_of(evening), evening.isoformat(), json.dumps(earlier)))
        play_learning.learn(world.market.conn, world.research.conn, evening)
        play_performance.refresh(world.paths, world.market.conn, world.research.conn, evening, changed=True)
        if args.scenario == "learning-report":
            from desk.stocks import learning
            from tests.unit.test_stocks_learning import AT
            from tests.unit.test_stocks_learning_lifecycle import ladder

            lab = learning.open_book(paths)
            try:     # lifecycle recipes on every bucket: fills, a 1-day checkpoint and the weeklies' sale
                for at, bid, ask in [(AT, 4.8, 5.0), (AT + timedelta(minutes=1), 4.8, 5.0),
                                     (AT + timedelta(days=1), 5.4, 5.6),
                                     (datetime(2026, 10, 28, 16, tzinfo=UTC), 5.8, 6.0)]:
                    ladder(world, at, bid, ask, source="synthetic_browser_fixture")
                    learning.tick(lab, world.market, at)
            finally:
                lab.close()
        world.close()
    elif args.scenario != "empty":
        through = NOW.date() - timedelta(days=7) if args.scenario == "paper-workflows" else NOW.date()
        world = make_world(root, n=60, through=through)
        # Synthetic chart coverage for visual checks; never market observations or forward evidence.
        benchmarks = list(dict.fromkeys([*INDEX_NAMES, *SECTOR_NAMES]))
        for bar in world.market.bars("SPY"):
            extras = []
            for index, symbol in enumerate(benchmarks):
                if symbol == "SPY":
                    continue
                scale = 0.18 + (index + 1) * 0.095
                extras.append(replace(bar, symbol=symbol, open=bar.open * scale, high=bar.high * scale,
                                      low=bar.low * scale, close=bar.close * scale))
            world.market.save_bars(bar.day, extras, "synthetic_browser_fixture", NOW)
        add_chains(world, ["SPY", "QQQ", "CHEAP", "SQZ", "S10", "S11"], world.days[-3:])
        world.dbs.market.execute("UPDATE stk_option_chains SET source='synthetic_browser_fixture'")
        initial_now = cal.session_close(world.days[-1])+timedelta(minutes=76) if args.scenario == "paper-workflows" else NOW
        process(paths, load_config(paths.config_dir), world.dbs, initial_now)
        process(paths, load_config(paths.config_dir), world.dbs, initial_now, research_only=True)
        if args.scenario == "paper-workflows":
            # Three further genuinely processed synthetic sessions give filled positions and stop exits.
            for index in range(3):
                previous = world.market.bar_days()[-1]
                day = cal.next_trading_day(previous)
                prior_bars = [Bar(r[0],previous,r[2],r[3],r[4],r[5],r[6]) for r in world.market.bar_rows(previous,previous)]
                bars = [replace(b, day=day, open=b.close, high=b.close*1.03,
                                low=b.close*(.70 if index == 1 and b.symbol != "SPY" else .98),
                                close=b.close*1.01) for b in prior_bars]
                world.market.save_bars(day, bars, "synthetic_browser_fixture", NOW)
                process(paths, load_config(paths.config_dir), world.dbs,
                        cal.session_close(day)+timedelta(minutes=76))
        if args.scenario in {"stale-prices", "ui-workflows"}:
            import asyncio
            from types import SimpleNamespace

            from desk.stocks.collect import option_chains
            from tests.unit.test_stocks_options_population import CAPTURE, StandardSchwab

            # Exact user failure scenario: newest options are independent of the denied newer close.
            asyncio.run(option_chains(SimpleNamespace(tradier=StandardSchwab(), option_source="synthetic_schwab_shape"),
                                       world.market, CAPTURE, ["SPY", "QQQ", "CHEAP"], rate=.04,
                                       last_prices={}, clock=lambda: CAPTURE))
            world.market.job("prices", CAPTURE, error="Synthetic Massive grouped_daily HTTP 403")
            process(paths, load_config(paths.config_dir), world.dbs, CAPTURE, research_only=True)
            if args.scenario == "ui-workflows":
                from datetime import date

                from desk.stocks.stock_views import request, save_history

                # Synthetic long history exercises actual API storage/ranges, never provider access.
                bars = [Bar("SPY", day, 100 + i * .002, 101 + i * .002, 99 + i * .002,
                            100.5 + i * .002, 1000000) for i, day in enumerate(
                                cal.trading_days(date(2006, 10, 23), CAPTURE.date()))]
                request(world.dbs.market, "SPY", CAPTURE)
                save_history(world.dbs.market, "SPY", bars, CAPTURE)
        if args.scenario == "intraday-workflows":
            import math

            from desk.stocks.contracts import IntradayBar
            from desk.stocks.stock_views import request, save_history, save_intraday

            for symbol in ("SPY", "QQQ"):
                session = world.days[-1]
                opening = cal.session_close(session) - timedelta(hours=6, minutes=30)
                minutes = []
                for minute in range(390):
                    op = 100 + minute * .005
                    close = op + .02 * math.cos(minute)
                    minutes.append(IntradayBar(symbol, opening + timedelta(minutes=minute), op,
                                   max(op, close) + .04, min(op, close) - .04, close, 1000 + minute))
                request(world.dbs.market, symbol, NOW)
                save_intraday(world.dbs.market, symbol, minutes, NOW)
                save_history(world.dbs.market, symbol, [
                    Bar(symbol, world.days[-2], 99, 100, 98, 99.5, 100000),
                    Bar(symbol, session, 100, 103, 99, minutes[-1].close, 100000),
                ], NOW)
        if args.scenario in {"learning-workflows","learning-retail"}:
            from desk.stocks import learning
            from tests.unit.test_stocks_learning import AT

            lab = learning.open_book(paths)
            try:
                if args.scenario == "learning-workflows":
                    # A book from before the cap (2026-10-30): the research account had a virtual deposit.
                    from desk.stocks.paper.book import PaperBook

                    legacy, book = AT - timedelta(days=1), PaperBook(lab)
                    with book.atomic():
                        book.open_account(learning.RESEARCH, learning.CAPITAL, legacy, legacy.date())
                        book.add_cash(learning.RESEARCH, legacy, legacy.date(), 1_500_000 * 1_000_000, "DEPOSIT")
                        book.event(legacy, learning.RESEARCH, "VIRTUAL_FUNDING", None,
                                   {"amount": 1_500_000.0,
                                    "reason": "Additional experiment capital; excluded from trading P/L"})
                if args.scenario == "learning-retail":
                    from tests.unit.test_stocks_learning_retail import outcome

                    learning.initialize(lab,AT-timedelta(days=30))
                    for structure in learning.STRUCTURES:     # prior minute-hold research, registered after
                        learning.add_experiment(lab,AT-timedelta(days=30),structure,50_000,30,30)
                    outcome(lab,"long_call",-.1,AT-timedelta(days=2))
                    outcome(lab,"long_put",.2,AT-timedelta(days=2))
                # Lifecycle recipes on every bucket's expiry: fills, a 1-day checkpoint, then the weekly recipes
                # sell at their 7-day horizon (Oct 28), so finished results exist before today.
                from tests.unit.test_stocks_learning_lifecycle import ladder
                for at, bid, ask in [(AT,4.8,5.0),(AT+timedelta(minutes=1),4.8,5.0),
                                     (AT+timedelta(days=1),5.4,5.6),
                                     (datetime(2026,10,28,16,tzinfo=UTC),5.8,6.0)]:
                    ladder(world,at,bid,ask,source="synthetic_browser_fixture")
                    learning.tick(lab,world.market,at)
            finally:
                lab.close()
        if args.scenario == "personal-workflows":
            from copy import deepcopy

            from desk.stocks import play_selections
            from desk.stocks.company_profiles import request
            from desk.stocks.runner import saved, saved_path

            # Explicit synthetic put counterexample for exact-side navigation (this world's natural picks are calls).
            board = saved(paths,"plays")
            sample = deepcopy(board["categories"][0]["plays"][0])
            _, chains = world.market.option_chain(sample["symbol"],world.days[-1])
            expiration = next(e for e in chains if e.isoformat() == sample["expiration"])
            quote = min((q for q in chains[expiration] if q.kind == "put"),key=lambda q:abs(q.strike-sample["strike"]))
            sample.update(type="PUT",structure="long_put",strike=quote.strike,entry=quote.ask,
                          rank=3,source="synthetic_browser_fixture",delta=-.5,
                          cost=quote.ask*100+.65,max_loss=quote.ask*100+.65,
                          max_gain=quote.strike*100-quote.ask*100-.65,
                          breakeven=round(quote.strike-quote.ask,2),breakevens=None,
                          reasons=["Synthetic exact-put navigation counterexample"],
                          legs=[{"occ":quote.occ,"kind":"put","side":"buy","strike":quote.strike,
                                 "expiration":expiration.isoformat(),"bid":quote.bid,"ask":quote.ask}])
            sample.pop("probability_targets",None)
            board["categories"][0]["plays"].append(sample)
            from desk.stocks.plays import BUDGET_BANDS
            band = next([lo,hi] for lo,hi in BUDGET_BANDS if lo < sample["cost"] <= hi)   # consistent with its cost
            board["categories"][0]["budget_plays"].append({**deepcopy(sample),
                "discovery":"budget","budget_band":band})
            play_selections.register(world.dbs.research,board,NOW)
            saved_path(paths,"plays").write_text(json.dumps(board))

            for symbol in ("CHEAP", "SQZ", "S10", "S11"):
                request(world.dbs.market, symbol, NOW)
                world.dbs.market.execute("UPDATE stk_company_profiles SET about=?,metadata_json=?,source_url=?,"
                    "filing_date='2026-02-20',updated_at=? WHERE cik=(SELECT cik FROM stk_listings WHERE symbol=?)",
                    ("Synthetic company: designs and sells consumer products. This is a browser fixture, not issuer data.",
                     json.dumps({"headquarters":"Pittsburgh, PA", "website":"https://example.test", "phone":"555-0100"}),
                     "https://www.sec.gov/", NOW.isoformat(),symbol))
        if args.scenario == "stock-page":
            stock_page_fixture(world)
        if args.scenario in {"populated", "stock-page"}:
            events_fixture(world)
        if args.scenario == "account":
            from tests.browser.account_fixture import populate

            populate(world, NOW)
        world.close()
    paths.ensure()
    return paths


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", choices=["empty", "populated", "stale-prices", "ui-workflows",
                                               "paper-workflows", "intraday-workflows", "learning-workflows",
                                               "plays-history", "personal-workflows", "archive-history", "learning-retail",
                                               "stock-page", "learning-report", "ops", "account", "learner"],
                        default="populated")
    parser.add_argument("--port", type=int, default=8877)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="stocks-browser-") as directory:
        root = Path(directory)
        paths = prepare(root, args.scenario)
        # Workflows assert explanatory text, so fixtures show it; the desk's own default is numbers first.
        (paths.home / "stocks-ui.json").write_text('{"explain": true}')
        app = create_app(paths, auto_start=False)
        if args.scenario == "personal-workflows":

            from desk.stocks import play_selections, web
            from desk.stocks.repo import StocksDatabases

            clock = [datetime(2026,10,22,8,tzinfo=cal.NEW_YORK)]
            web.utc_now = lambda: clock[0]

            @app.post("/test/quote-pass")
            def fixture_quote_pass() -> dict:
                clock[0] = clock[0].replace(hour=9,minute=30) if clock[0].hour < 9 else clock[0]+timedelta(minutes=1)
                dbs = StocksDatabases.open(paths)
                try:
                    active = play_selections.active_legs(dbs.research, clock[0].date())
                    factor = 1.1 + .025*(clock[0].minute-30)
                    for leg in {item["occ"]:item for item in active}.values():
                        dbs.research.execute("INSERT INTO stk_track_ticks(occ,at,session,bid,ask,underlying,quoted_at,source)"
                            " VALUES (?,?,?,?,?,?,?,?)",(leg["occ"],clock[0].isoformat(),clock[0].date().toordinal(),
                                leg["bid"]*factor,leg["ask"]*factor,100.,clock[0].isoformat(),"synthetic_browser_fixture"))
                    return {"changed":play_selections.advance(dbs.market,dbs.research,clock[0])}
                finally:
                    dbs.close()
        uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False)


if __name__ == "__main__":
    main()
