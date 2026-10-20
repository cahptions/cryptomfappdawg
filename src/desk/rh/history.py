"""Daily history for the Robinhood desk: sync Coinbase candles, choose the universe, run the simulation."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from desk.core.config import LoadedConfig
from desk.core.errors import DeskError
from desk.rh import strategies as st
from desk.rh.coinbase import CoinbaseClient
from desk.rh.contracts import Candle
from desk.rh.repo import RhMarket
from desk.rh.simulate import SimResult, simulate

log = logging.getLogger(__name__)
DAILY = 86400
HOURLY = 3600
# Used only until the operator's key fetches Robinhood's own list: large coins Robinhood is known to list in
# the US. Labelled "starter (unverified)" wherever it is used.
STARTER_SYMBOLS = ("BTC-USD", "ETH-USD", "SOL-USD", "DOGE-USD", "SHIB-USD", "XRP-USD", "ADA-USD", "AVAX-USD",
                   "LINK-USD", "LTC-USD", "BCH-USD", "ETC-USD", "XLM-USD", "UNI-USD", "AAVE-USD", "COMP-USD",
                   "XTZ-USD", "PEPE-USD", "BONK-USD", "WIF-USD")


def universe(market: RhMarket) -> tuple[list[str], str]:
    """Tradable Robinhood pairs when known, else the starter list; BTC is always included (the regime)."""
    pairs = [p.symbol for p in market.pairs() if p.tradable]
    if pairs:
        return sorted(set(pairs) | {str(st.COMMON["regime_symbol"])}), "robinhood trading_pairs"
    return list(STARTER_SYMBOLS), "starter list (unverified until the Robinhood key fetches the real pairs)"


def today_start(now: datetime) -> datetime:
    return datetime(now.year, now.month, now.day, tzinfo=UTC)


async def sync_candles(coinbase: CoinbaseClient, market: RhMarket, symbols: list[str], *, granularity: int,
                       days: int, now: datetime) -> dict[str, Any]:
    """Fetch closed bars after the last stored one (or ``days`` back); a symbol Coinbase lacks is reported."""
    report: dict[str, Any] = {"added": 0, "missing_products": [], "errors": {}}
    end = today_start(now) if granularity == DAILY else now.replace(minute=0, second=0, microsecond=0)
    for symbol in symbols:
        last = market.last_candle_start(symbol, granularity)
        begin = (last + timedelta(seconds=granularity)) if last else end - timedelta(days=days)
        if begin >= end:
            continue
        try:
            bars = await coinbase.candles(symbol, granularity, begin, end)
        except DeskError as exc:
            if "no product" in str(exc):
                report["missing_products"].append(symbol)
            else:
                report["errors"][symbol] = str(exc)[:160]
            continue
        report["added"] += market.save_candles("coinbase", bars, now)
    return report


def load_daily(market: RhMarket, symbols: list[str]) -> dict[str, list[Candle]]:
    return {s: bars for s in symbols if (bars := market.candles(s, DAILY))}


def backtest(market: RhMarket, loaded: LoadedConfig, symbols: list[str], *, days: int, now: datetime,
             universe_source: str) -> tuple[SimResult, dict[str, Any]]:
    settings = loaded.settings.robinhood
    history = load_daily(market, symbols)
    if str(st.COMMON["regime_symbol"]) not in history:
        raise DeskError("no BTC-USD history: run with --fetch first")
    end = today_start(now).date() - timedelta(days=1)
    start = end - timedelta(days=days - 1)
    measured = {s: market.measured_roundtrip(s) for s in history}
    assumed = settings.backtest_roundtrip_cost_fraction

    def cost(symbol: str) -> Decimal:
        value = measured.get(symbol)
        return max(value, Decimal(0)) if value is not None else assumed

    def source(symbol: str) -> str:
        return "measured on Robinhood" if measured.get(symbol) is not None else "assumed"

    result = simulate(history, start, end, cost=cost, cost_source=source, starting_capital=settings.starting_capital_usd,
                      fixed_fraction=settings.fixed_fraction, max_fraction=settings.max_ticket_fraction,
                      max_open=settings.max_open_positions, max_roundtrip_cost=settings.max_roundtrip_cost_fraction)
    btc = history[str(st.COMMON["regime_symbol"])]
    first = next((b for b in btc if b.start.date() >= start), None)
    last = next((b for b in reversed(btc) if b.start.date() <= end), None)
    report = {
        "kind": "HISTORICAL SIMULATION (not evidence of future profit)",
        "generated_at": now.isoformat(), "sessions": f"{start.isoformat()}..{end.isoformat()}",
        "universe": {"source": universe_source, "requested": len(symbols), "with_history": len(history),
                     "missing_history": sorted(set(symbols) - set(history))},
        "specs": {sid: st.spec_sha256(sid) for sid in st.ORDER},
        "cost_model": {"assumed_roundtrip_fraction": str(assumed),
                       "measured_symbols": sorted(s for s, v in measured.items() if v is not None)},
        "btc_buy_and_hold_return": None if not (first and last) else str((last.close / first.open - 1).quantize(
            Decimal("0.0001"))),
        "regime_days": dict(result.regime_days), "skipped_entries": dict(result.skipped),
        "strategies": result.summary(),
        "caveats": ["universe is today's list: coins that were delisted or died are missing (survivorship bias)",
                    "fills at the daily open with a fixed cost; real fills move with size and time of day",
                    "parameters were frozen before this run, but the hypotheses come from published research that "
                    "already saw much of this history"],
    }
    return result, report


def write_backtest(paths: Any, loaded: LoadedConfig, now: datetime, *, days: int = 1095) -> dict[str, Any]:
    """Run the simulation on stored history with its own connection (safe in a worker thread) and save it."""
    import json
    import os

    from desk.rh.repo import RhDatabases

    dbs = RhDatabases.open(paths)
    try:
        market = RhMarket(dbs.market)
        symbols, source = universe(market)
        _, report = backtest(market, loaded, symbols, days=days, now=now, universe_source=source)
    finally:
        dbs.close()
    target = paths.home / "reports" / "rh_backtest.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(report, indent=1, default=str) + "\n", encoding="utf-8")
    os.replace(temporary, target)
    return report


__all__ = ["DAILY", "HOURLY", "STARTER_SYMBOLS", "backtest", "load_daily", "sync_candles", "today_start", "universe",
           "write_backtest"]
