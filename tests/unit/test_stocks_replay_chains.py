"""The replay's chain on a past day: KESTREL's own collected chain where one is stored, else rebuilt on Massive's real
listing for the month where it was downloaded, else on the monthly-expiry stand-in; the coverage line counts each."""

import asyncio
import json
import math
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np

from desk.core.config import load_config
from desk.stocks import calendar as cal
from desk.stocks import option_model as om
from desk.stocks import options_history as oh
from desk.stocks.backtest import replay
from desk.stocks.contracts import OptionQuote
from desk.stocks.paper.fills import index_chain
from desk.stocks.prices import Panel
from tests.conftest import REPO
from tests.stocks_world import ASOF, add_chains, make_world

DAY = date(2026, 6, 1)
NOW = datetime(2026, 10, 24, 15, 0, tzinfo=UTC)


def _model(**kw: Any) -> tuple[om.ModeledOptions, float]:
    days = cal.trading_days(DAY - timedelta(days=200), DAY)[-80:]
    closes = np.array([[100 * math.exp(0.015 * (1 if i % 2 else -1)) for i in range(80)]])
    panel = Panel(["AAA"], days, closes, closes, closes, closes, np.ones_like(closes) * 1e6)
    model = om.ModeledOptions(om.Calibration({}, 1.1, 0.1, {}, 0.1), om.RealBars(), {"AAA"}, **kw)
    model.session(DAY, panel, 0.04)
    return model, float(closes[0, -1])


def test_a_downloaded_listing_gives_the_real_expiries_and_strikes() -> None:
    weekly, monthly = date(2026, 7, 10), date(2026, 7, 17)
    listing = {("AAA", 2026, 6): {"as_of": DAY, "expiries": {weekly: [97.0, 103.0], monthly: [95.0, 100.0, 105.0]}}}
    model, _ = _model(listings=listing)
    chain = model.chain("AAA", real=False)
    assert sorted(chain) == [weekly, monthly]
    assert sorted({q.strike for q in chain[weekly]}) == [97.0, 103.0]
    assert model.chain_sources == {"listed": 1}
    stand_in, _ = _model()
    assert set(stand_in.chain("AAA", real=False)) == set(om.monthly_expiries(DAY))
    assert stand_in.chain_sources == {"stand_in": 1}


def test_a_collected_chain_replaces_the_model_and_a_held_contract_keeps_its_quote() -> None:
    e = om.monthly_expiries(DAY)[1]
    quoted = datetime(2026, 6, 1, 19, 0, tzinfo=UTC)
    real = [OptionQuote(om.occ("AAA", e, "call", 100.0), "AAA", "call", 100.0, e, 3.1, 3.3, 3.2, 900, 4000, None, quoted)]
    model, _ = _model(collected=lambda symbol, day: {e: real} if (symbol, day) == ("AAA", DAY) else {})
    held = om.occ("AAA", e, "call", 110.0)
    model.remember("AAA", [{"occ": held, "expiration": e.isoformat(), "strike": 110.0}])
    quotes = index_chain(model.chain("AAA"))
    assert quotes[real[0].occ].bid == 3.1 and quotes[real[0].occ].open_interest == 4000   # KESTREL's own quote
    assert held in quotes and quotes[held].open_interest == om.OPEN_INTEREST            # still priced by the model
    assert model.chain_sources == {"collected": 1}


def test_listings_download_once_per_stock_month_the_replay_traded(tmp_path: Path) -> None:
    w = make_world(tmp_path, n=12)
    try:
        items = [{"occ": "CHEAP260918C00050000", "first": "2026-08-10", "last": "2026-09-10"},
                 {"occ": "CHEAP260918C00055000", "first": "2026-08-12", "last": "2026-09-10"},
                 {"occ": "S10260821P00020000", "first": "2026-07-20", "last": "2026-08-05"}]
        (w.paths.home / oh.REPORT).write_text(json.dumps({"option_contracts": items}))
        assert oh.listing_months(w.paths.home) == [("CHEAP", date(2026, 8, 3)), ("S10", date(2026, 7, 1))]

        class Fake:
            configured = True

            def __init__(self) -> None:
                self.asked: list[tuple[str, date]] = []

            async def option_contracts(self, underlying: str, as_of: date, first: date, last: date, *,
                                       kind: Any = None, strikes: Any = None) -> list[dict[str, Any]]:
                self.asked.append((underlying, as_of))
                return [{"ticker": "X", "kind": k, "strike": 50.0, "expiration": first + timedelta(days=4),
                         "underlying": underlying} for k in ("call", "put")]

        fake = Fake()
        got = asyncio.run(oh.listings_task(fake, w.market, w.paths.home, NOW, 1))
        assert got["requests"] == 1 and got["months_left"] == 1 and fake.asked == [("CHEAP", date(2026, 8, 3))]
        asyncio.run(oh.listings_task(fake, w.market, w.paths.home, NOW, 5))
        asyncio.run(oh.listings_task(fake, w.market, w.paths.home, NOW, 5))                # nothing asked twice
        assert len(fake.asked) == 2
        stored = oh.listings(w.market.conn)[("CHEAP", 2026, 8)]
        assert stored["as_of"] == date(2026, 8, 3) and list(stored["expiries"].values()) == [[50.0]]
    finally:
        w.close()


def test_the_replay_uses_collected_chains_on_their_days_and_says_which_days_used_which(tmp_path: Path) -> None:
    w = make_world(tmp_path, n=12)
    try:
        days = [d for d in sorted({b.day for b in w.market.bars("CHEAP")}) if d <= ASOF][-3:]
        add_chains(w, ["CHEAP", "SQZ", "S10", "S11"], days)              # the optioned names' last three sessions
        result = replay(w.paths, load_config(REPO / "config").settings.stocks, 8)
        chains = result["option_pricing"]["chains"]
        assert chains["collected"] > 0 and chains["stand_in"] > 0 and chains["listed"] == 0
        mixes = [a["option_prices"] for a in result["accounts"] if a["asset"] == "option"]
        assert any(m["collected"] for m in mixes)                     # held days priced by KESTREL's own quotes
        notes = " ".join(result["limitations"])
        assert "used KESTREL's own collected chain" in notes and "stand-in" in notes
    finally:
        w.close()
