"""The four preregistered option strategies replay on the IV history's real contracts: their rules are fixed text
with a version hash, and each result says what it was compared with."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from desk.stocks import calendar as cal
from desk.stocks import option_research as orx
from desk.stocks import options as opt
from desk.stocks.iv_history import SOURCE, save_list
from desk.stocks.repo import day_of
from tests.stocks_world import ASOF, make_world

NOW = datetime(2026, 10, 24, 15, 0, tzinfo=UTC)


def _seed(w: Any, symbol: str, months: int = 6, iv: float = 0.20) -> None:
    """Monthly at-the-money pairs priced at ``iv`` on the stored closes, as the IV history downloads them."""
    closes = {b.day: b.close for b in w.market.bars(symbol)}
    expiry = cal.next_trading_day(ASOF - timedelta(days=5))
    for k in range(months):
        e = expiry - timedelta(days=28 * k)
        e = e if cal.is_trading_day(e) else cal.previous_trading_day(e)
        as_of = cal.previous_trading_day(e - timedelta(days=62))
        strike = round(closes[max(d for d in closes if d <= as_of)], 1)
        legs = []
        for kind in ("call", "put"):
            occ = f"{symbol}{e:%y%m%d}{kind[0].upper()}{round(strike * 1000):08d}"
            legs.append({"occ": occ, "kind": kind, "strike": strike})
            rows = []
            for d, s in sorted(closes.items()):
                if as_of <= d < e:
                    price = max(opt.bs_price(kind, s, strike, opt.year_fraction(d, e), iv, 0.04), 0.05)
                    rows.append({"day": d, "open": price, "high": price, "low": price, "close": price, "volume": 30})
            w.market.save_option_bars(occ, as_of, e, rows, NOW)
        save_list(w.market.conn, "iv", symbol, e, as_of, 20, legs, None, NOW)
    w.market.conn.executemany("INSERT OR REPLACE INTO stk_iv_daily VALUES (?,?,?,?,?,?)",
                              [(symbol, day_of(d), iv, SOURCE, 2, NOW.isoformat()) for d in closes
                               if d >= ASOF - timedelta(days=400)])


def test_the_rules_are_fixed_text_with_a_hash_and_every_strategy_reports(tmp_path: Path) -> None:
    assert set(orx.RULES) == {"vol_gap_straddles", "spy_put_write", "iv_spread", "regime_chooser"}
    assert all(r["rule"] and r["source"] for r in orx.RULES.values()) and len(orx.RULES_SHA256) == 64
    w = make_world(tmp_path, n=12)
    try:
        for symbol in ("SPY", "CHEAP", "SQZ"):
            _seed(w, symbol, iv=0.10 if symbol == "SQZ" else 0.20)           # SQZ's options price far less movement
        report = orx.run(w.market, NOW)
        assert report["rules_sha256"] == orx.RULES_SHA256 and report["stocks"] == 3
        writes = report["strategies"]["spy_put_write"]
        assert writes["trades"] >= 4 and "spy_same_days" in writes
        gap = report["strategies"]["vol_gap_straddles"]
        assert gap["trades"] >= 1 and gap["baseline"]["trades"] >= 1
        assert report["strategies"]["iv_spread"]["weeks"] == 0                # fewer than 10 stocks: no ranking
        chooser = report["strategies"]["regime_chooser"]
        assert chooser["always_put_write"]["trades"] == writes["trades"] and "rule" in chooser
        orx.save(w.paths.home, w.market, NOW)
        assert json.loads((w.paths.home / orx.REPORT).read_text())["version"] == 1 and orx.load(w.paths.home)
    finally:
        w.close()
