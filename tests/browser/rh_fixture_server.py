"""Serve the Robinhood dashboard on disposable, offline test data (synthetic prices, no network, no keys).

    uv run python -m tests.browser.rh_fixture_server --scenario populated --port 8878
"""

from __future__ import annotations

import argparse
import random
import tempfile
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import uvicorn

from desk.core.clock import utc_now
from desk.core.config import load_config
from desk.core.paths import REPO_ROOT, DeskPaths
from desk.rh import multiframe as mf
from desk.rh import strategies as st
from desk.rh.contracts import BidAsk, Candle, Estimate, Pair
from desk.rh.history import write_backtest
from desk.rh.repo import RhDatabases, RhLedger, RhMarket
from desk.rh.web import create_rh_app

D = Decimal
COINS = {"BTC-USD": D(82000), "ETH-USD": D(2400), "SOL-USD": D(105), "DOGE-USD": D("0.08"), "LINK-USD": D(12),
         "PEPE-USD": D("0.0000091")}


def walk(symbol: str, last: Decimal, count: int, step: timedelta, end: datetime, seed: int) -> list[Candle]:
    rng = random.Random(seed)
    sigma = 0.03 * (step / timedelta(days=1)) ** 0.5          # ~3% a day, scaled to the bar length
    closes = [last]
    for _ in range(count - 1):
        closes.append(closes[-1] / D(str(round(1 + rng.gauss(0.0002, sigma), 6))))
    closes.reverse()
    out, prev = [], closes[0]
    for i, close in enumerate(closes):
        start = end - step * (count - i)
        out.append(Candle(symbol, int(step.total_seconds()), start, prev, max(prev, close) * D("1.01"),
                          min(prev, close) * D("0.99"), close, D(50_000_000) / close))
        prev = close
    return out


def build(scenario: str, home: Path) -> DeskPaths:
    paths = DeskPaths(home=home, config_dir=REPO_ROOT / "config", secrets_file=home / "no-secrets.env")
    loaded = load_config(paths.config_dir)
    dbs = RhDatabases.open(paths)
    now = utc_now()
    try:
        market, ledger = RhMarket(dbs.market), RhLedger(dbs.ledger)
        market.register_specs(now)
        ledger.ensure_book(loaded.settings.robinhood.learning_cash_usd, now - timedelta(days=31))
        if scenario == "empty":
            return paths
        today = datetime(now.year, now.month, now.day, tzinfo=UTC)
        for i, (symbol, price) in enumerate(COINS.items()):
            market.save_candles("coinbase", walk(symbol, price, 400, timedelta(days=1), today, i), now)
            hour = now.replace(minute=0, second=0, microsecond=0)
            market.save_candles("coinbase", walk(symbol, price, 24 * 14, timedelta(hours=1), hour, i + 100), now)
            market.save_candles("coinbase", walk(symbol, price, 3 * 96, timedelta(minutes=15),
                                                mf.cutoff(now), i + 200), now)
        market.save_pairs([Pair(s, s.split("-")[0], "USD", D("0.00000001"), D("0.01"), D(1_000_000), D(1), "tradable",
                                True) for s in COINS], now)
        market.save_quotes([BidAsk(s, p * D("0.9995"), p * D("1.0005"), now, "fixture") for s, p in COINS.items()])
        for symbol, strategy, days, entry_mult in (("ETH-USD", "R1_TREND", 6, D("0.94")), ("BTC-USD", "B1_HOLD_BTC", 30,
                                                                                            D("1.03"))):
            price = COINS[symbol] * entry_mult
            quantity = (D(10) / price).quantize(D("0.00000001"))
            at = now - timedelta(days=days)
            buy = Estimate(symbol, "MM", "BUY", quantity, price, D(0), price * quantity, None, None, at, "fixture")
            signal = st.Signal(strategy, symbol, "ENTER", ("BREAKOUT_20D",) if strategy == "R1_TREND" else ("BASELINE_HOLD",))
            digest = market.record_decision(at, at.date(), signal, "ENTER", signal.reasons, {"fixture": True})
            ledger.open_position(at=at, day=at.date(), strategy_id=strategy, symbol=symbol, decision_hash=digest, buy=buy,
                                 roundtrip=D("0.008"), stop_price=None if strategy == "B1_HOLD_BTC" else price * D("0.9"),
                                 plan={"fixture": True})
        closed_at = now - timedelta(days=3)
        price = COINS["SOL-USD"] * D("0.9")
        quantity = (D(10) / price).quantize(D("0.00000001"))
        buy = Estimate("SOL-USD", "EX", "BUY", quantity, price, D("0.095"), price * quantity + D("0.095"), D("0.0095"),
                       None, closed_at - timedelta(days=9), "fixture")
        signal = st.Signal("R1_TREND", "SOL-USD", "ENTER", ("BREAKOUT_20D",))
        digest = market.record_decision(closed_at - timedelta(days=9), (closed_at - timedelta(days=9)).date(), signal,
                                        "ENTER", signal.reasons, {"fixture": True})
        pid = ledger.open_position(at=closed_at - timedelta(days=9), day=(closed_at - timedelta(days=9)).date(),
                                   strategy_id="R1_TREND", symbol="SOL-USD", decision_hash=digest, buy=buy,
                                   roundtrip=D("0.012"), stop_price=price * D("0.9"), plan={"fixture": True})
        sell = Estimate("SOL-USD", "MM", "SELL", quantity, COINS["SOL-USD"], D(0), COINS["SOL-USD"] * quantity, None,
                        None, closed_at, "fixture")
        ledger.close_position(pid, at=closed_at, sell=sell, reason="TRAILING_ATR")
        skip = st.Signal("R3_PULLBACK", "DOGE-USD", "ENTER", ("RSI2_BELOW_10", "UPTREND_50_OVER_100", "BTC_RISK_ON"))
        market.record_decision(now - timedelta(hours=4), now.date(), skip, "SKIP",
                               [*skip.reasons, "ROUNDTRIP_COST_ABOVE_CEILING"], {"fixture": True})
        rng = random.Random(7)
        value = loaded.settings.robinhood.learning_cash_usd
        for k in range(30 * 24, 0, -1):
            value += D(str(round(rng.gauss(0.002, 0.05), 4)))
            ledger.conn.execute("INSERT INTO rh_equity(at, at_ms, cash_micros, positions_value_micros, open_positions)"
                                " VALUES (?, ?, ?, 0, 2)", ((now - timedelta(hours=k)).isoformat(),
                                                            int((now - timedelta(hours=k)).timestamp() * 1000),
                                                            int(value * 1_000_000)))
        ledger.event(now - timedelta(hours=1), "SESSION", {"day": now.date().isoformat(), "regime": "RISK_ON",
                                                           "eligible": 6, "ineligible": {}, "open_positions": 2})
    finally:
        dbs.close()
    write_backtest(paths, loaded, now, days=200)
    return paths


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", choices=("empty", "populated"), default="populated")
    parser.add_argument("--port", type=int, default=8878)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="kestrel-rh-fixture-") as tmp:
        paths = build(args.scenario, Path(tmp))
        uvicorn.run(create_rh_app(paths), host="127.0.0.1", port=args.port, access_log=False, server_header=False)


if __name__ == "__main__":
    main()
