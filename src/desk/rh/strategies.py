"""The Robinhood desk's preregistered strategies (v1, frozen 2026-10-20 before any backtest was run).

Pure functions over closed daily bars: the same code drives PAPER decisions and the historical simulation.
A decision for session day D uses only bars that ended by D 00:00 UTC (the last one is day D-1).

Hypotheses (docs/ROBINHOOD.md cites the literature each comes from):
* R1_TREND     time-series momentum: a close above the prior 20-day high, in a coin above its 50-day average.
* R2_XMOM      cross-sectional momentum: weekly, hold the 3 strongest coins over 28 days (skipping the last day)
               that also beat BTC over that window.
* R3_PULLBACK  short-term reversal inside an uptrend: 2-day RSI below 10 while above the 100-day average.
* B1_HOLD_BTC  baseline: buy BTC once and hold (the market).
* B2_RANDOM    baseline: weekly, a seeded random eligible coin held 7 days, with the same stop as the strategies.
Every strategy (not B1) trades only while BTC closes above its 100-day average ("RISK_ON"), and every position
has a hard stop at 2 x ATR(20) below entry, clamped to 5-25%.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from itertools import pairwise
from typing import Any, Literal

from desk.core.hashing import canonical_json, sha256_hex
from desk.rh.contracts import Candle

DAY = timedelta(days=1)
Action = Literal["ENTER", "EXIT"]

COMMON: dict[str, Any] = {
    "bar": "1d UTC, Coinbase Exchange public candles (bucket start)",
    "regime_symbol": "BTC-USD", "regime_sma_days": 100,
    "min_history_days": 120, "max_missing_days_in_history": 5,
    "volume_days": 30, "min_median_dollar_volume_usd": "1000000",
    "atr_days": 20, "stop_atr": "2", "stop_min_fraction": "0.05", "stop_max_fraction": "0.25",
}
SPECS: dict[str, dict[str, Any]] = {
    "R1_TREND": {"strategy_id": "R1_TREND", "version": 1, "kind": "STRATEGY", "breakout_days": 20, "trend_sma_days": 50,
                 "exit_low_days": 10, "trail_atr": "3", "max_hold_days": 60, "max_positions": 3},
    "R2_XMOM": {"strategy_id": "R2_XMOM", "version": 1, "kind": "STRATEGY", "lookback_days": 28, "skip_days": 1,
                "top_k": 3, "rebalance_weekday": 0, "max_positions": 3},
    "R3_PULLBACK": {"strategy_id": "R3_PULLBACK", "version": 1, "kind": "STRATEGY", "rsi_days": 2, "rsi_below": "10",
                    "trend_fast_days": 50, "trend_slow_days": 100, "exit_sma_days": 5, "max_hold_days": 7,
                    "max_positions": 2},
    "B1_HOLD_BTC": {"strategy_id": "B1_HOLD_BTC", "version": 1, "kind": "BASELINE", "symbol": "BTC-USD",
                    "stop": "none", "max_positions": 1},
    "B2_RANDOM": {"strategy_id": "B2_RANDOM", "version": 1, "kind": "BASELINE", "hold_days": 7, "rebalance_weekday": 0,
                  "max_positions": 1},
}
ORDER = ("B1_HOLD_BTC", "B2_RANDOM", "R1_TREND", "R2_XMOM", "R3_PULLBACK")   # entry priority when the book is full


def spec_content(strategy_id: str) -> dict[str, Any]:
    return {"common": COMMON, "spec": SPECS[strategy_id]}


def spec_sha256(strategy_id: str) -> str:
    return sha256_hex(canonical_json(spec_content(strategy_id)))


def _d(value: str) -> Decimal:
    return Decimal(value)


# ── features ────────────────────────────────────────────────────────────────────────────────────
def sma(closes: list[Decimal], n: int) -> Decimal:
    return sum(closes[-n:], Decimal(0)) / n


def atr(bars: list[Candle], n: int) -> Decimal:
    ranges = [max(b.high - b.low, abs(b.high - p.close), abs(b.low - p.close)) for p, b in zip(bars[-n - 1:-1], bars[-n:],
                                                                                               strict=True)]
    return sum(ranges, Decimal(0)) / n


def rsi(closes: list[Decimal], n: int, warmup: int = 60) -> Decimal:
    """Wilder's RSI over the last ``warmup`` closes."""
    window = closes[-warmup - 1:]
    changes = [b - a for a, b in pairwise(window)]
    gains = [max(c, Decimal(0)) for c in changes]
    losses = [max(-c, Decimal(0)) for c in changes]
    avg_gain, avg_loss = sum(gains[:n], Decimal(0)) / n, sum(losses[:n], Decimal(0)) / n
    for g, loss in zip(gains[n:], losses[n:], strict=True):
        avg_gain = (avg_gain * (n - 1) + g) / n
        avg_loss = (avg_loss * (n - 1) + loss) / n
    if avg_loss == 0:
        return Decimal(100)
    return Decimal(100) - Decimal(100) / (1 + avg_gain / avg_loss)


def median(values: list[Decimal]) -> Decimal:
    ordered = sorted(values)
    mid = len(ordered) // 2
    return ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2


@dataclass(frozen=True)
class Snapshot:
    """One coin's features for a session day, from bars that ended by that day's start."""

    symbol: str
    session_day: date
    close: Decimal
    high: Decimal
    low: Decimal
    sma5: Decimal
    sma50: Decimal
    sma100: Decimal
    high_prior20: Decimal
    low_prior10: Decimal
    atr20: Decimal
    rsi2: Decimal
    ret_skip28: Decimal
    median_dollar_volume30: Decimal
    bars_used: int
    last_bar: datetime

    def as_json(self) -> dict[str, str]:
        return {k: str(v) for k, v in self.__dict__.items() if k not in ("symbol",)}

    @property
    def stop_fraction(self) -> Decimal:
        raw = _d(COMMON["stop_atr"]) * self.atr20 / self.close
        return min(max(raw, _d(COMMON["stop_min_fraction"])), _d(COMMON["stop_max_fraction"]))


def snapshot(symbol: str, bars: list[Candle], session_day: date) -> tuple[Snapshot | None, str | None]:
    """Features or the reason the coin is not eligible today. ``bars`` are daily, oldest first."""
    start = datetime(session_day.year, session_day.month, session_day.day, tzinfo=bars[0].start.tzinfo) if bars else None
    usable = [b for b in bars if start is not None and b.start + DAY <= start]
    if not usable:
        return None, "NO_HISTORY"
    if usable[-1].start + DAY != start:
        return None, "STALE_HISTORY"
    need = int(COMMON["min_history_days"])
    window = [b for b in usable if b.start >= start - need * DAY]
    if len(usable) < need or need - len(window) > int(COMMON["max_missing_days_in_history"]):
        return None, "SHORT_HISTORY"
    closes = [b.close for b in usable]
    lookback, skip = int(SPECS["R2_XMOM"]["lookback_days"]), int(SPECS["R2_XMOM"]["skip_days"])
    dollar = median([b.close * b.volume for b in usable[-int(COMMON["volume_days"]):]])
    snap = Snapshot(
        symbol=symbol, session_day=session_day, close=closes[-1], high=usable[-1].high, low=usable[-1].low,
        sma5=sma(closes, 5), sma50=sma(closes, 50), sma100=sma(closes, 100),
        high_prior20=max(b.high for b in usable[-21:-1]), low_prior10=min(b.low for b in usable[-11:-1]),
        atr20=atr(usable, int(COMMON["atr_days"])), rsi2=rsi(closes, int(SPECS["R3_PULLBACK"]["rsi_days"])),
        ret_skip28=closes[-1 - skip] / closes[-1 - skip - lookback] - 1, median_dollar_volume30=dollar,
        bars_used=len(usable), last_bar=usable[-1].start)
    if dollar < _d(COMMON["min_median_dollar_volume_usd"]):
        return snap, "THIN_VOLUME"
    return snap, None


def regime(btc: Snapshot | None) -> str:
    if btc is None:
        return "UNKNOWN"
    return "RISK_ON" if btc.close > btc.sma100 else "RISK_OFF"


# ── signals ─────────────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Held:
    position_id: str
    strategy_id: str
    symbol: str
    session_day: date           # entry session
    peak_close: Decimal


@dataclass(frozen=True)
class Signal:
    strategy_id: str
    symbol: str
    action: Action
    reasons: tuple[str, ...]
    features: dict[str, str] = field(default_factory=dict)
    position_id: str | None = None


def _rebalance_day(day: date, strategy_id: str) -> bool:
    return day.weekday() == int(SPECS[strategy_id]["rebalance_weekday"])


def xmom_targets(day: date, eligible: dict[str, Snapshot], reg: str) -> list[str]:
    if reg != "RISK_ON" or not _rebalance_day(day, "R2_XMOM"):
        return []
    btc = eligible.get(str(COMMON["regime_symbol"]))
    floor = max(Decimal(0), btc.ret_skip28 if btc else Decimal(0))
    ranked = sorted((s for s in eligible.values() if s.ret_skip28 > floor), key=lambda s: (-s.ret_skip28, s.symbol))
    return [s.symbol for s in ranked[:int(SPECS["R2_XMOM"]["top_k"])]]


def random_pick(day: date, eligible: dict[str, Snapshot]) -> str | None:
    names = sorted(eligible)
    if not names:
        return None
    return names[int(hashlib.sha256(f"B2_RANDOM|{day.isoformat()}".encode()).hexdigest(), 16) % len(names)]


def exits(day: date, held: list[Held], snaps: dict[str, Snapshot], eligible: dict[str, Snapshot],
          reg: str) -> list[Signal]:
    out = []
    targets = set(xmom_targets(day, eligible, reg)) if _rebalance_day(day, "R2_XMOM") else None
    for h in held:
        snap = snaps.get(h.symbol)
        days = (day - h.session_day).days
        reasons: list[str] = []
        if h.strategy_id == "R1_TREND" and snap is not None:
            spec = SPECS["R1_TREND"]
            if snap.close < snap.low_prior10:
                reasons.append("DONCHIAN_EXIT")
            if snap.close < max(h.peak_close, snap.close) - _d(spec["trail_atr"]) * snap.atr20:
                reasons.append("TRAILING_ATR")
            if days >= int(spec["max_hold_days"]):
                reasons.append("MAX_HOLD")
        elif h.strategy_id == "R2_XMOM" and targets is not None and h.symbol not in targets:
            reasons.append("REGIME_OFF" if reg != "RISK_ON" else "REBALANCE_OUT")
        elif h.strategy_id == "R3_PULLBACK":
            if snap is not None and snap.close > snap.sma5:
                reasons.append("MEAN_REVERTED")
            if days >= int(SPECS["R3_PULLBACK"]["max_hold_days"]):
                reasons.append("MAX_HOLD")
        elif h.strategy_id == "B2_RANDOM" and days >= int(SPECS["B2_RANDOM"]["hold_days"]):
            reasons.append("HOLD_COMPLETE")
        if reasons:
            out.append(Signal(h.strategy_id, h.symbol, "EXIT", tuple(reasons),
                              snap.as_json() if snap else {}, h.position_id))
    return out


def entries(day: date, eligible: dict[str, Snapshot], reg: str, held: list[Held]) -> list[Signal]:
    """Entry signals in priority order; the caller enforces the book's limits and the cost gate."""
    holding = {(h.strategy_id, h.symbol) for h in held}
    count = {sid: sum(1 for h in held if h.strategy_id == sid) for sid in SPECS}
    out: list[Signal] = []

    def add(sid: str, snap: Snapshot, reasons: tuple[str, ...]) -> None:
        if (sid, snap.symbol) in holding or count[sid] >= int(SPECS[sid]["max_positions"]):
            return
        count[sid] += 1
        holding.add((sid, snap.symbol))
        out.append(Signal(sid, snap.symbol, "ENTER", reasons, snap.as_json()))

    btc = eligible.get(str(SPECS["B1_HOLD_BTC"]["symbol"]))
    if btc is not None:
        add("B1_HOLD_BTC", btc, ("BASELINE_HOLD",))
    if _rebalance_day(day, "B2_RANDOM"):
        pick = random_pick(day, eligible)
        if pick is not None:
            add("B2_RANDOM", eligible[pick], ("BASELINE_RANDOM",))
    if reg != "RISK_ON":
        return out
    ranked = sorted(eligible.values(), key=lambda s: (-s.ret_skip28, s.symbol))
    spec1 = SPECS["R1_TREND"]
    for snap in ranked:
        if snap.close > snap.high_prior20 and snap.close > sma_value(snap, int(spec1["trend_sma_days"])):
            add("R1_TREND", snap, ("BREAKOUT_20D", "ABOVE_SMA50", "BTC_RISK_ON"))
    for symbol in xmom_targets(day, eligible, reg):
        add("R2_XMOM", eligible[symbol], ("TOP3_28D_MOMENTUM", "BEATS_BTC", "BTC_RISK_ON"))
    spec3 = SPECS["R3_PULLBACK"]
    for snap in ranked:
        if snap.close > snap.sma100 and snap.sma50 > snap.sma100 and snap.rsi2 < _d(spec3["rsi_below"]):
            add("R3_PULLBACK", snap, ("RSI2_BELOW_10", "UPTREND_50_OVER_100", "BTC_RISK_ON"))
    return out


def sma_value(snap: Snapshot, days: int) -> Decimal:
    return {5: snap.sma5, 50: snap.sma50, 100: snap.sma100}[days]


__all__ = ["COMMON", "ORDER", "SPECS", "Held", "Signal", "Snapshot", "atr", "entries", "exits", "median", "random_pick",
           "regime", "rsi", "sma", "snapshot", "spec_content", "spec_sha256", "xmom_targets"]
