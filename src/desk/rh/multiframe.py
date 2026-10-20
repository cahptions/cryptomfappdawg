"""Experimental v2 PAPER rules. Closed UTC bars only; no orders, HTTP or daily-v1 backtest reuse.

The 4h series is assembled from four complete Coinbase 1h buckets. A missing bucket is a gap,
never a flat candle. Inputs are bounded, point-in-time, and shared by the engine and dashboard.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from desk.core.hashing import canonical_json, sha256_hex
from desk.rh import strategies as st
from desk.rh.contracts import Candle

if TYPE_CHECKING:
    from desk.rh.repo import Position, RhMarket

D = Decimal
QUARTER = 900
HOUR = 3600
FOUR_HOURS = 14400
DAY = 86400
GRACE = timedelta(seconds=30)
IDS = ("R1_TREND", "R2_XMOM", "R3_PULLBACK")
COMMON: dict[str, Any] = {
    "version": 2, "mode": "PAPER experimental", "source": "Coinbase Exchange USD candles",
    "frames_seconds": [QUARTER, HOUR, FOUR_HOURS, DAY], "close_grace_seconds": 30,
    "warmup_bars": {"15m": 61, "1h": 61, "4h": 60, "1d": 100},
    "ema_seed": "SMA(first n of bounded window), then alpha=2/(n+1)",
    "gaps": "exact contiguous UTC buckets required; 4h needs all four 1h components",
    "regime": "latest closed BTC daily close > SMA100", "trend": "4h close > EMA20 > EMA50",
    "min_median_daily_usd_volume30": "1000000", "atr_hourly_bars": 20,
    "stop": "clamp(2*hourly ATR20 / hourly close, 0.05, 0.25)",
    "shared_exits": ["BTC daily regime off", "4h close below EMA50", "3*hourly ATR20 trailing close"],
    "entry_dedup": "one filled entry per strategy/symbol/hourly setup close, including closed positions",
    "publication": "latest expected closed bucket at decision time minus grace; no catch-up entries",
    "priority": list(IDS), "unchanged_limits": "exact-size Robinhood costs, STOP, halt, book and strategy caps",
}
SPECS: dict[str, dict[str, Any]] = {
    "R1_TREND": {"version": 2, "entry": "1h close > prior 20 highs AND 15m close > prior high AND EMA20",
                 "exit": "1h close < prior 10 lows OR 72h age", "max_positions": 3},
    "R2_XMOM": {"version": 2, "entry": "top 3 positive 24h returns beating BTC, 1h close > EMA20, "
                 "15m crosses above EMA20", "minimum_ranked_assets": 3,
                "rank_universe": "currently complete/liquid/uptrend assets; missing assets explicitly reported",
                "exit": "24h return <= max(0, BTC return) OR 48h age", "max_positions": 3},
    "R3_PULLBACK": {"version": 2, "entry": "1h Wilder RSI2 < 10 AND 15m close > prior high AND open",
                    "exit": "1h close > SMA5 OR 24h age", "max_positions": 2},
}
ABOUT = {
    "R1_TREND": "v2 PAPER: daily BTC regime, 4h uptrend, 1h breakout and 15m confirmation; maximum hold 72h.",
    "R2_XMOM": "v2 PAPER: top-three 24h momentum beating BTC, 4h uptrend and 15m EMA reclaim; maximum hold 48h.",
    "R3_PULLBACK": "v2 PAPER: hourly RSI2 dip in a 4h uptrend, confirmed by a 15m recovery; maximum hold 24h.",
}


def spec_content(sid: str) -> dict[str, Any]:
    return {"common": COMMON, "spec": SPECS[sid]}


def spec_sha256(sid: str) -> str:
    return sha256_hex(canonical_json(spec_content(sid)))


def signal_spec(signal: st.Signal) -> str:
    if signal.features.get("learned_spec"):              # the learning lab's selection versions its own positions
        return str(signal.features["learned_spec"])
    return spec_sha256(signal.strategy_id) if signal.features.get("spec_version") == "2" else st.spec_sha256(
        signal.strategy_id)


def floor_time(at: datetime, seconds: int) -> datetime:
    return datetime.fromtimestamp(int(at.timestamp()) // seconds * seconds, UTC)


def cutoff(now: datetime) -> datetime:
    return floor_time(now - GRACE, QUARTER)


def aggregate(bars: list[Candle], seconds: int = FOUR_HOURS) -> list[Candle]:
    """No partial buckets, duplicates or manufactured observations."""
    groups: dict[datetime, list[Candle]] = {}
    for b in bars:
        groups.setdefault(floor_time(b.start, seconds), []).append(b)
    out = []
    for start, group in sorted(groups.items()):
        group.sort(key=lambda b: b.start)
        step = group[0].granularity
        if seconds % step or len(group) != seconds // step or any(
                b.granularity != step or b.start != start + timedelta(seconds=i * step)
                or b.product != group[0].product for i, b in enumerate(group)):
            continue
        out.append(Candle(group[0].product, seconds, start, group[0].open, max(b.high for b in group),
                          min(b.low for b in group), group[-1].close, sum((b.volume for b in group), D(0))))
    return out


def window(bars: list[Candle], seconds: int, n: int, end: datetime) -> list[Candle] | None:
    expected = floor_time(end, seconds)
    usable = [b for b in bars if b.start + timedelta(seconds=seconds) <= expected][-n:]
    if len(usable) != n or any(b.granularity != seconds or b.start != expected - timedelta(seconds=(n - i) * seconds)
                               for i, b in enumerate(usable)):
        return None
    return usable


def ema(values: list[Decimal], n: int) -> Decimal:
    value = sum(values[:n], D(0)) / n
    alpha = D(2) / (n + 1)
    for close in values[n:]:
        value += alpha * (close - value)
    return value


@dataclass(frozen=True)
class FrameState:
    symbol: str
    bar_end: datetime
    frames: dict[str, dict[str, Any]]
    features: dict[str, str]
    input_hash: str
    missing: tuple[str, ...]

    @property
    def ready(self) -> bool:
        return not self.missing

    @property
    def stop_fraction(self) -> Decimal:
        return min(max(2 * D(self.features["atr20"]) / D(self.features["hour_close"]), D("0.05")), D("0.25"))


def load(market: RhMarket, symbol: str, now: datetime) -> FrameState:
    end = cutoff(now)
    raw = {"15m": market.candles(symbol, QUARTER, since=end - timedelta(days=2), as_of=now),
           "1h": market.candles(symbol, HOUR, since=end - timedelta(days=14), as_of=now),
           "1d": market.candles(symbol, DAY, since=end - timedelta(days=130), as_of=now)}
    raw["4h"] = aggregate(raw["1h"])
    frames, samples, missing = {}, {}, []
    for label, seconds, n in (("15m", QUARTER, 61), ("1h", HOUR, 61), ("4h", FOUR_HOURS, 60), ("1d", DAY, 100)):
        bars = raw[label]
        chosen = window(bars, seconds, n, end)
        samples[label] = chosen
        closed = [b for b in bars if b.start + timedelta(seconds=seconds) <= floor_time(end, seconds)]
        lower = floor_time(end, seconds) - timedelta(seconds=seconds * n)
        available = sum(b.start >= lower for b in closed)
        problem = None if chosen is not None else _problem(bars, closed, seconds, n, end)
        frames[label] = {"required": n, "available": available, "ready": chosen is not None, "problem": problem,
                         "last_close": (closed[-1].start + timedelta(seconds=seconds)).isoformat() if closed else None,
                         "expected_close": floor_time(end, seconds).isoformat()}
        if problem:
            missing.append(f"{label}:{problem}")
    # Hash the exact bounded observations, not a price-only proxy; persist inputs for replay.
    evidence = {label: [[b.start.isoformat(), str(b.open), str(b.high), str(b.low), str(b.close), str(b.volume)]
                        for b in (samples[label] or [])] for label in frames}
    digest = sha256_hex(canonical_json({"symbol": symbol, "bar_end": end.isoformat(), "bars": evidence}))
    f: dict[str, str] = {"spec_version": "2", "bar_end": end.isoformat(), "input_hash": digest}
    if not missing:
        q, h, four, daily = (samples[k] for k in ("15m", "1h", "4h", "1d"))
        assert q and h and four and daily
        qc, hc, fc = ([b.close for b in series] for series in (q, h, four))
        f.update({k: str(v) for k, v in {
            "close": qc[-1], "previous_high": q[-2].high, "quarter_open": q[-1].open,
            "quarter_ema20": ema(qc, 20), "previous_close": qc[-2], "previous_ema20": ema(qc[:-1], 20),
            "hour_close": hc[-1], "hour_ema20": ema(hc, 20), "sma5": st.sma(hc, 5),
            "high_prior20": max(b.high for b in h[-21:-1]), "low_prior10": min(b.low for b in h[-11:-1]),
            "atr20": st.atr(h, 20), "rsi2": st.rsi(hc, 2), "return24h": hc[-1] / hc[-25] - 1,
            "four_close": fc[-1], "four_ema20": ema(fc, 20), "four_ema50": ema(fc, 50),
            "daily_close": daily[-1].close, "daily_sma100": st.sma([b.close for b in daily], 100),
            "median_dollar_volume30": st.median([b.close * b.volume for b in daily[-30:]]),
        }.items()})
        if D(f["median_dollar_volume30"]) < D(COMMON["min_median_daily_usd_volume30"]):
            missing.append("THIN_VOLUME")
    return FrameState(symbol, end, frames, f, digest, tuple(missing))


def _problem(bars: list[Candle], closed: list[Candle], seconds: int, n: int, end: datetime) -> str:
    """Why a frame is not ready: nothing stored at all (NONE), its newest closed bar has not arrived (STALE), the
    stored history starts too recently (WARMUP), or buckets inside the window are absent (GAP; Coinbase omits a
    bucket with no trades)."""
    expected = floor_time(end, seconds)
    if not bars:
        return "NONE"
    if not closed or closed[-1].start + timedelta(seconds=seconds) < expected:
        return "STALE"
    if bars[0].start > expected - timedelta(seconds=seconds * n):
        return "WARMUP"
    return "GAP"


FRAME_WORDS = {"15m": "15-minute", "1h": "hourly", "4h": "4-hour", "1d": "daily"}


def describe(s: FrameState) -> list[str]:
    """Each reason a coin is not ready, in plain words (the System list and the coin page)."""
    out = []
    for code in s.missing:
        if code == "THIN_VOLUME":
            out.append(f"too little trading volume: ${D(s.features['median_dollar_volume30']):,.0f} a day on "
                       f"Coinbase (median of 30 days; needs ${D(COMMON['min_median_daily_usd_volume30']):,.0f})")
            continue
        label, _, problem = code.partition(":")
        frame, words = s.frames[label], FRAME_WORDS[label]
        if problem == "GAP":
            empty = int(frame["required"]) - int(frame["available"])
            what = "lack an hour with any trade" if label == "4h" else "had no trades"
            out.append(f"{words}: {empty} of the last {frame['required']} periods {what} on Coinbase "
                       f"(the rules need every one)")
        elif problem == "NONE":
            out.append(f"{words}: no price history from Coinbase yet (Coinbase may not list this coin)")
        elif problem == "STALE":
            out.append(f"{words}: the newest closed candle has not been downloaded yet")
        else:
            out.append(f"{words}: {frame['available']} of the {frame['required']} candles needed so far")
    return out


def regime(btc: FrameState | None) -> str:
    if btc is None or not btc.frames["1d"]["ready"]:
        return "UNKNOWN"
    # Other BTC frames must be complete too: a partial feature bundle cannot authorize entries.
    if "daily_close" not in btc.features:
        return "UNKNOWN"
    return "RISK_ON" if D(btc.features["daily_close"]) > D(btc.features["daily_sma100"]) else "RISK_OFF"


def conditions(s: FrameState, states: dict[str, FrameState], reg: str) -> list[dict[str, Any]]:
    f = s.features
    common = [{"label": "Complete, current 1d / 4h / 1h / 15m data and sufficient volume", "met": s.ready},
              {"label": "BTC daily close above SMA100", "met": reg == "RISK_ON"}]
    if not s.ready:
        return [{"strategy_id": sid, "conditions": common} for sid in IDS]
    trend = D(f["four_close"]) > D(f["four_ema20"]) > D(f["four_ema50"])
    common += [{"label": "4h close > EMA20 > EMA50", "met": trend}]
    btc = states.get("BTC-USD")
    floor = max(D(0), D(btc.features["return24h"])) if btc and btc.ready else D("Infinity")
    candidates = sorted((o for o in states.values() if o.ready and o.symbol != "BTC-USD"
                         and D(o.features["four_close"]) > D(o.features["four_ema20"]) > D(o.features["four_ema50"])
                         and D(o.features["return24h"]) > floor),
                        key=lambda o: (-D(o.features["return24h"]), o.symbol))
    enough = sum(o.ready for o in states.values()) >= 3
    def row(sid: str, extra: list[tuple[str, bool]]) -> dict[str, Any]:
        return {"strategy_id": sid, "conditions": common + [{"label": label, "met": met} for label, met in extra]}
    return [row("R1_TREND", [("1h close above prior 20 hourly highs", D(f["hour_close"]) > D(f["high_prior20"])),
                            ("15m close above previous high and EMA20", D(f["close"]) > max(
                                D(f["previous_high"]), D(f["quarter_ema20"])))]),
            row("R2_XMOM", [("At least 3 complete assets in the comparison universe", enough),
                           ("Top 3 positive 24h returns beating BTC among ready uptrends", s in candidates[:3]),
                           ("1h close above EMA20", D(f["hour_close"]) > D(f["hour_ema20"])),
                           ("15m crosses above EMA20", D(f["previous_close"]) <= D(f["previous_ema20"])
                            and D(f["close"]) > D(f["quarter_ema20"]))]),
            row("R3_PULLBACK", [("1h RSI2 below 10", D(f["rsi2"]) < 10),
                               ("15m recovery above previous high and open", D(f["close"]) > max(
                                   D(f["previous_high"]), D(f["quarter_open"])))] )]


def entries(states: dict[str, FrameState], reg: str, held: list[Position], *,
            allowed: set[tuple[str, str]] | None = None) -> list[st.Signal]:
    out = []
    for sid in IDS:
        remaining = int(SPECS[sid]["max_positions"]) - sum(p.strategy_id == sid for p in held)
        for symbol, s in sorted(states.items(), key=lambda item: (
                -D(item[1].features.get("return24h", "-Infinity")), item[0])):
            if remaining <= 0:
                break
            if allowed is not None and (sid, symbol) not in allowed:
                continue
            if any(p.strategy_id == sid and p.symbol == symbol for p in held):
                continue
            check = next(row for row in conditions(s, states, reg) if row["strategy_id"] == sid)
            if all(c["met"] for c in check["conditions"]):
                out.append(st.Signal(sid, symbol, "ENTER", ("MULTIFRAME_V2", *(
                    c["label"] for c in check["conditions"])), s.features))
                remaining -= 1
    return out


def exit_reason(pos: Position, s: FrameState | None, reg: str, btc: FrameState | None, now: datetime) -> str | None:
    limit = {"R1_TREND": 72, "R2_XMOM": 48, "R3_PULLBACK": 24}[pos.strategy_id]
    if now - pos.opened_at >= timedelta(hours=limit):
        return "MAX_HOLD_V2"
    if reg == "RISK_OFF":
        return "REGIME_OFF_V2"
    if s is None or not s.ready:
        return None  # missing data cannot be treated as an exit signal; live quotes still enforce hard stops
    f = s.features
    if D(f["four_close"]) < D(f["four_ema50"]):
        return "TREND_INVALIDATED_V2"
    if D(f["close"]) < pos.peak_close - 3 * D(f["atr20"]):
        return "TRAILING_ATR_V2"
    if pos.strategy_id == "R1_TREND" and D(f["hour_close"]) < D(f["low_prior10"]):
        return "DONCHIAN_EXIT_V2"
    if pos.strategy_id == "R2_XMOM" and btc and btc.ready and D(f["return24h"]) <= max(
            D(0), D(btc.features["return24h"])):
        return "MOMENTUM_LOST_V2"
    if pos.strategy_id == "R3_PULLBACK" and D(f["hour_close"]) > D(f["sma5"]):
        return "MEAN_REVERTED_V2"
    return None
