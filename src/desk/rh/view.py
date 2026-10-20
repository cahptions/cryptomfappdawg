"""Read-only data for the Robinhood dashboard. Every number comes from the desk's own records."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from desk.core.clock import parse_iso8601, utc_now
from desk.core.config import LoadedConfig
from desk.core.paths import DeskPaths
from desk.rh import strategies as st
from desk.rh.history import DAILY, HOURLY, today_start, universe
from desk.rh.repo import RhDatabases, RhLedger, RhMarket

SYMBOL = re.compile(r"^[A-Z0-9]{1,15}-USD$")
QUOTE_FRESH = timedelta(minutes=5)
NAMES = {"BTC": "Bitcoin", "ETH": "Ethereum", "SOL": "Solana", "DOGE": "Dogecoin", "SHIB": "Shiba Inu", "XRP": "XRP",
         "ADA": "Cardano", "AVAX": "Avalanche", "LINK": "Chainlink", "LTC": "Litecoin", "BCH": "Bitcoin Cash",
         "ETC": "Ethereum Classic", "XLM": "Stellar", "UNI": "Uniswap", "AAVE": "Aave", "COMP": "Compound",
         "XTZ": "Tezos", "PEPE": "Pepe", "BONK": "Bonk", "WIF": "dogwifhat", "SUI": "Sui", "ARB": "Arbitrum",
         "OP": "Optimism", "TRUMP": "Official Trump", "PENGU": "Pudgy Penguins", "HYPE": "Hyperliquid",
         "ONDO": "Ondo", "FLOKI": "Floki", "MOODENG": "Moo Deng", "POPCAT": "Popcat", "HBAR": "Hedera",
         "DOT": "Polkadot", "NEAR": "NEAR Protocol", "RENDER": "Render", "VIRTUAL": "Virtuals Protocol"}
ABOUT = {
    "R1_TREND": "Buys a coin closing above its 20-day high while above its 50-day average; exits on a 10-day low, "
                "a 3×ATR trailing stop or after 60 days.",
    "R2_XMOM": "Every Monday holds the 3 coins with the strongest 28-day run that also beat BTC.",
    "R3_PULLBACK": "Buys a sharp 2-day dip (RSI below 10) in a coin that is in an uptrend; exits on the bounce or "
                   "after 7 days.",
    "B1_HOLD_BTC": "Baseline: buy BTC once and hold. Strategies must beat this.",
    "B2_RANDOM": "Baseline: a random coin each Monday held 7 days. Strategies must beat luck.",
}


def _f(value: Decimal | None, places: int = 2) -> str | None:
    return None if value is None else str(value.quantize(Decimal(1).scaleb(-places)))


def _price_places(price: Decimal) -> int:
    return 2 if price >= 1 else 6 if price >= Decimal("0.0001") else 10


def backtest_path(paths: DeskPaths) -> Any:
    return paths.home / "reports" / "rh_backtest.json"


class RhView:
    def __init__(self, paths: DeskPaths, loaded: LoadedConfig) -> None:
        self.paths, self.loaded = paths, loaded
        self.settings = loaded.settings.robinhood

    def _open(self) -> RhDatabases:
        return RhDatabases.open(self.paths)

    # ── pieces ───────────────────────────────────────────────────────────────────────────────────
    def _prices(self, market: RhMarket, symbols: list[str], now: datetime) -> dict[str, dict[str, Any]]:
        quotes = market.latest_quotes()
        out: dict[str, dict[str, Any]] = {}
        for symbol in symbols:
            hourly = market.candles(symbol, HOURLY, since=now - timedelta(days=7, hours=2))
            quote = quotes.get(symbol)
            if quote is not None and now - quote.received_at <= QUOTE_FRESH:
                price, source = quote.mid, "robinhood"
            elif hourly:
                price, source = hourly[-1].close, "history"
            else:
                continue
            day_ago = next((c.close for c in reversed(hourly) if c.start <= now - timedelta(hours=24)), None)
            spark = [c.close for c in hourly][-168:]
            step = max(1, len(spark) // 56)
            places = _price_places(price)
            out[symbol] = {
                "price": _f(price, places), "price_source": source,
                "bid": _f(quote.bid, places) if quote else None, "ask": _f(quote.ask, places) if quote else None,
                "spread": _f(quote.spread_fraction, 6) if quote else None,
                "quote_crossed": quote.crossed if quote else False,
                "quote_at": quote.received_at.isoformat() if quote else None,
                "change_24h": _f(price / day_ago - 1, 6) if day_ago else None,
                "spark": [_f(v, places) for v in spark[::step]],
            }
        return out

    def _account(self, ledger: RhLedger, market: RhMarket, now: datetime) -> dict[str, Any]:
        quotes = market.latest_quotes()
        open_ = ledger.positions("OPEN")
        realized = ledger.realized()
        value = Decimal(0)
        cost = Decimal(0)
        for p in open_:
            quote = quotes.get(p.symbol)
            value += p.quantity * quote.bid if quote else p.entry_cost
            cost += p.entry_cost
        unrealized = value - cost
        base = self.settings.starting_capital_usd
        equity = base + realized + unrealized
        day_start = today_start(now)
        series = ledger.equity_series(day_start - timedelta(days=1))
        offset = self.settings.learning_cash_usd - base
        before = [v for t, v in series if t <= day_start]
        open_today = (before[-1] - offset) if before else base
        return {
            "equity": _f(equity), "starting_capital": _f(base), "realized": _f(realized), "unrealized": _f(unrealized),
            "total_return": _f((equity / base - 1) if base else Decimal(0), 6),
            "today_change": _f(equity - open_today), "today_change_fraction": _f((equity / open_today - 1)
                                                                               if open_today else Decimal(0), 6),
            "invested": _f(cost), "learning_cash": _f(ledger.cash()),
            "ticket": _f((base + realized) * min(self.settings.fixed_fraction, self.settings.max_ticket_fraction)),
            "halted": ledger.halted(), "open_positions": len(open_),
        }

    def _positions(self, ledger: RhLedger, market: RhMarket, now: datetime) -> list[dict[str, Any]]:
        quotes = market.latest_quotes()
        rows = []
        for p in ledger.positions("OPEN"):
            quote = quotes.get(p.symbol)
            value = p.quantity * quote.bid if quote else None
            rows.append({
                "id": p.id, "symbol": p.symbol, "name": NAMES.get(p.symbol.split("-")[0], p.symbol.split("-")[0]),
                "strategy_id": p.strategy_id, "quantity": str(p.quantity), "cost": _f(p.entry_cost),
                "value": _f(value), "pnl": _f(value - p.entry_cost) if value is not None else None,
                "pnl_fraction": _f(value / p.entry_cost - 1, 6) if value is not None else None,
                "entry_price": str(p.entry_unit_price), "stop": None if p.stop_price is None else str(p.stop_price),
                "route": p.entry_route, "roundtrip_cost": _f(p.roundtrip_cost_fraction, 6),
                "opened_at": p.opened_at.isoformat(), "days": (now.date() - p.session_day).days,
            })
        return rows

    def _strategies(self, ledger: RhLedger, backtest: dict[str, Any] | None) -> list[dict[str, Any]]:
        closed = ledger.positions("CLOSED")
        open_ = ledger.positions("OPEN")
        rows = []
        for sid in st.ORDER:
            mine = [p for p in closed if p.strategy_id == sid]
            pnl = [p.realized_pnl or Decimal(0) for p in mine]
            rows.append({
                "id": sid, "kind": st.SPECS[sid]["kind"], "about": ABOUT[sid], "spec_sha256": st.spec_sha256(sid),
                "closed": len(mine), "open": sum(1 for p in open_ if p.strategy_id == sid),
                "wins": sum(1 for v in pnl if v > 0), "realized": _f(sum(pnl, Decimal(0))),
                "mean_return": _f(sum(((p.exit_credit or Decimal(0)) / p.entry_cost - 1 for p in mine), Decimal(0))
                                  / len(mine), 6) if mine else None,
                "backtest": (backtest or {}).get("strategies", {}).get(sid),
            })
        return rows

    def _activity(self, ledger: RhLedger, market: RhMarket, limit: int = 40) -> list[dict[str, Any]]:
        events = [{"at": r["at"], "kind": r["kind"], "position_id": r["position_id"],
                   "detail": json.loads(r["payload_json"])}
                  for r in ledger.conn.execute("SELECT * FROM rh_events WHERE kind IN ('ENTRY', 'EXIT', 'HALT', 'RESUME')"
                                               " ORDER BY id DESC LIMIT ?", (limit,))]
        skips = [{"at": r["decided_at"], "kind": "SKIP", "position_id": None,
                  "detail": {"symbol": r["symbol"], "strategy_id": r["strategy_id"], "reasons": r["reasons"].split(",")}}
                 for r in market.conn.execute("SELECT * FROM rh_decisions WHERE action = 'SKIP' ORDER BY id DESC LIMIT ?",
                                              (limit,))]
        return sorted(events + skips, key=lambda e: e["at"], reverse=True)[:limit]

    def _session(self, ledger: RhLedger, now: datetime) -> dict[str, Any]:
        row = ledger.conn.execute("SELECT at, payload_json FROM rh_events WHERE kind = 'SESSION' ORDER BY id DESC LIMIT 1"
                                  ).fetchone()
        last = json.loads(row["payload_json"]) if row else None
        return {"last": last, "last_at": row["at"] if row else None,
                "next_at": (today_start(now) + timedelta(days=1, minutes=5)).isoformat()}

    def _backtest(self) -> dict[str, Any] | None:
        path = backtest_path(self.paths)
        try:
            return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
        except (OSError, ValueError):
            return None

    # ── endpoints ────────────────────────────────────────────────────────────────────────────────
    def snapshot(self) -> dict[str, Any]:
        now = utc_now()
        dbs = self._open()
        try:
            market, ledger = RhMarket(dbs.market), RhLedger(dbs.ledger)
            symbols, source = universe(market)
            prices = self._prices(market, symbols, now)
            backtest = self._backtest()
            last_quote = dbs.market.execute("SELECT MAX(received_at) FROM rh_quotes").fetchone()[0]
            last_bar = dbs.market.execute("SELECT MAX(start) FROM candles WHERE granularity = ?", (DAILY,)).fetchone()[0]
            key_ready = last_quote is not None and now - parse_iso8601(last_quote) <= QUOTE_FRESH
            coins = [{"symbol": s, "name": NAMES.get(s.split("-")[0], s.split("-")[0]), **prices[s],
                      "roundtrip_cost": _f(market.measured_roundtrip(s), 6)} for s in symbols if s in prices]
            return {
                "generated_at": now.isoformat(), "mode": "PAPER", "venue": "Robinhood Crypto",
                "universe_source": source, "robinhood_prices": key_ready, "last_quote_at": last_quote,
                "last_daily_bar": last_bar, "account": self._account(ledger, market, now),
                "positions": self._positions(ledger, market, now), "coins": coins,
                "strategies": self._strategies(ledger, backtest), "activity": self._activity(ledger, market),
                "session": self._session(ledger, now),
                "backtest": None if backtest is None else {k: backtest.get(k) for k in (
                    "kind", "sessions", "generated_at", "btc_buy_and_hold_return", "cost_model", "universe", "caveats")},
                "limits": {"ticket_fraction": str(self.settings.fixed_fraction),
                           "max_roundtrip_cost": str(self.settings.max_roundtrip_cost_fraction),
                           "max_open_positions": self.settings.max_open_positions},
            }
        finally:
            dbs.close()

    def equity(self, span: str) -> dict[str, Any]:
        now = utc_now()
        spans = {"1D": timedelta(days=1), "1W": timedelta(days=7), "1M": timedelta(days=30), "3M": timedelta(days=90),
                 "1Y": timedelta(days=365), "ALL": timedelta(days=3650)}
        dbs = self._open()
        try:
            offset = self.settings.learning_cash_usd - self.settings.starting_capital_usd
            series = RhLedger(dbs.ledger).equity_series(now - spans[span])
            return {"span": span, "points": [[t.isoformat(), _f(v - offset)] for t, v in series]}
        finally:
            dbs.close()

    def coin(self, symbol: str, span: str) -> dict[str, Any] | None:
        now = utc_now()
        dbs = self._open()
        try:
            market, ledger = RhMarket(dbs.market), RhLedger(dbs.ledger)
            hours = {"1D": 24, "1W": 168}.get(span)
            days = {"1M": 30, "3M": 90, "1Y": 365, "ALL": 3650}.get(span, 30)
            if hours is not None:
                bars = market.candles(symbol, HOURLY, since=now - timedelta(hours=hours + 1))
            else:
                bars = market.candles(symbol, DAILY, since=now - timedelta(days=days + 1))
            if not bars and not market.candles(symbol, DAILY, since=now - timedelta(days=5)):
                return None
            daily = market.candles(symbol, DAILY, since=today_start(now) - timedelta(days=260))
            day = now.date()
            snap, reason = st.snapshot(symbol, daily, day)
            btc_daily = market.candles("BTC-USD", DAILY, since=today_start(now) - timedelta(days=260))
            reg = st.regime(st.snapshot("BTC-USD", btc_daily, day)[0])
            prices = self._prices(market, [symbol], now)
            ranking = _momentum_rank(market, symbol, day, now) if snap is not None and reason is None else None
            decisions = [{"at": r["decided_at"], "strategy_id": r["strategy_id"], "action": r["action"],
                          "reasons": r["reasons"].split(",")}
                         for r in dbs.market.execute("SELECT * FROM rh_decisions WHERE symbol = ? ORDER BY id DESC"
                                                     " LIMIT 30", (symbol,))]
            places = _price_places(bars[-1].close) if bars else 2
            return {
                "symbol": symbol, "name": NAMES.get(symbol.split("-")[0], symbol.split("-")[0]), "span": span,
                **prices.get(symbol, {}), "regime": reg, "eligible": reason is None, "ineligible_reason": reason,
                "points": [[b.start.isoformat(), _f(b.close, places)] for b in bars],
                "features": snap.as_json() if snap else None,
                "signals": _signals(snap, reg, ranking) if snap and reason is None else [],
                "roundtrip_cost": _f(market.measured_roundtrip(symbol), 6),
                "decisions": decisions,
                "positions": [p for p in self._positions(ledger, market, now) if p["symbol"] == symbol],
            }
        finally:
            dbs.close()


def _momentum_rank(market: RhMarket, symbol: str, day: Any, now: datetime) -> dict[str, Any]:
    """Where the coin stands in R2's ranking today: its 28-day run against BTC's, and its rank among eligible coins."""
    symbols, _ = universe(market)
    since = today_start(now) - timedelta(days=260)
    eligible = {}
    for other in symbols:
        snap, reason = st.snapshot(other, market.candles(other, DAILY, since=since), day)
        if snap is not None and reason is None:
            eligible[other] = snap
    btc = eligible.get(str(st.COMMON["regime_symbol"]))
    floor = max(Decimal(0), btc.ret_skip28 if btc else Decimal(0))
    ranked = sorted((s for s in eligible.values() if s.ret_skip28 > floor), key=lambda s: (-s.ret_skip28, s.symbol))
    names = [s.symbol for s in ranked]
    return {"btc_run": btc.ret_skip28 if btc else None, "rank": names.index(symbol) + 1 if symbol in names else None,
            "top_k": int(st.SPECS["R2_XMOM"]["top_k"]), "monday": day.weekday() == 0}


def _pct(value: Decimal | None) -> str:
    return "?" if value is None else f"{(value * 100).quantize(Decimal('0.1'))}%"


def _px(value: Decimal) -> str:
    """A price the way a person reads it: cents above $1, significant digits below."""
    if value >= 1:
        return f"${value.quantize(Decimal('0.01')):,}"
    return f"${value.quantize(Decimal(1).scaleb(-_price_places(value))).normalize():f}"


def _signals(snap: st.Snapshot, reg: str, ranking: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Each strategy's conditions for this coin today, each marked met or not (the 'why' behind a decision)."""
    on = reg == "RISK_ON"
    ranking = ranking or {}
    rank, top_k = ranking.get("rank"), ranking.get("top_k", 3)
    return [
        {"strategy_id": "R1_TREND", "conditions": [
            {"label": "BTC above its 100-day average", "met": on},
            {"label": f"close {_px(snap.close)} above the prior 20-day high {_px(snap.high_prior20)}",
             "met": snap.close > snap.high_prior20},
            {"label": f"close above the 50-day average {_px(snap.sma50)}", "met": snap.close > snap.sma50}]},
        {"strategy_id": "R2_XMOM", "conditions": [
            {"label": "BTC above its 100-day average", "met": on},
            {"label": "BTC is the benchmark this strategy must beat, so it is never picked", "met": False}
            if snap.symbol == st.COMMON["regime_symbol"] else
            {"label": f"28-day run {_pct(snap.ret_skip28)} beats BTC's {_pct(ranking.get('btc_run'))} and is positive",
             "met": rank is not None},
            {"label": f"ranks in the top {top_k}" + (f" (now #{rank})" if rank else ""),
             "met": rank is not None and rank <= top_k},
            {"label": "today is a Monday (weekly rebalance)", "met": bool(ranking.get("monday"))}]},
        {"strategy_id": "R3_PULLBACK", "conditions": [
            {"label": "BTC above its 100-day average", "met": on},
            {"label": f"2-day RSI {snap.rsi2.quantize(Decimal('0.1'))} below 10", "met": snap.rsi2 < 10},
            {"label": "uptrend: close and 50-day average above the 100-day average",
             "met": snap.close > snap.sma100 and snap.sma50 > snap.sma100}]},
    ]


__all__ = ["NAMES", "SYMBOL", "RhView", "backtest_path"]
