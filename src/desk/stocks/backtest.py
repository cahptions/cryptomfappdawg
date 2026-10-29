"""Chronological stock-strategy replay in isolated books. No provider calls or forward-ledger writes.

Historical reconstruction is conditional on the stored/current listing universe and available filing history;
it is not a survivor-bias-free archival experiment. Options/social/non-vintage macro inputs are excluded.
"""

from __future__ import annotations

import math
import sqlite3
from collections.abc import Callable, Sequence
from datetime import date, datetime, timedelta
from statistics import mean, stdev
from typing import Any

from desk.core.config import StocksSection
from desk.core.errors import DeskError
from desk.core.hashing import canonical_json, sha256_hex
from desk.core.paths import DeskPaths
from desk.stats.core import max_drawdown
from desk.stocks import calendar as cal
from desk.stocks.analysis import MIN_BARS, Analyzer, Day
from desk.stocks.contracts import Bar, Split
from desk.stocks.desks import DEFAULT_WEIGHTS
from desk.stocks.fundamentals import Fundamentals
from desk.stocks.paper.book import PaperBook, Position
from desk.stocks.paper.engine import PaperEngine, StepReport
from desk.stocks.paper.report import daily_returns, trade_stats
from desk.stocks.paper.strategies import STRATEGIES, Strategy
from desk.stocks.prices import Panel, load_panel, split_factors
from desk.stocks.repo import MARKET_FILE, StocksMarket, StocksResearch
from desk.storage.db import DbKind, migrate

VERSION = 3


class HistoricalMarket(StocksMarket):
    def register_spec(self, name: str, version: int, content: dict[str, Any], at: datetime) -> str:
        # Engine specifications are saved in this run's manifest, never registered in the live source DB.
        return sha256_hex(canonical_json(content))


class ReplayAnalyzer(Analyzer):
    def facts(self, cik: int) -> Fundamentals:
        if cik not in self._facts and len(self._facts) % 25 == 0:
            from desk.stocks.runner import _memory_guard

            _memory_guard()  # stop cooperatively while a broad company's fact cache grows
        return super().facts(cik)


class ReplayEngine(PaperEngine):
    previous: Day | None = None
    previous_bars: dict[str, Bar]
    executing = False

    def __init__(self, market: StocksMarket, book: PaperBook, settings: StocksSection, strategies: tuple[Strategy, ...]) -> None:
        super().__init__(market, book, settings, strategies)
        self.previous_bars = {}

    def _dv(self, symbol: str, day: Day | None, bars: dict[str, Bar]) -> float | None:
        if self.executing:
            price = (self.previous.ctx.price.get(symbol) or self.previous.market_price.get(symbol) if self.previous else {}) or {}
            value = price.get("dollar_volume_20d")
            if value is not None:
                return float(value)
            bar = self.previous_bars.get(symbol)
            return bar.close * bar.volume if bar else None
        return super()._dv(symbol, day, bars)

    def _fill(self, asof: date, now: datetime, bars: dict[str, Bar], day: Day | None, rep: StepReport) -> None:
        self.executing = True
        try:
            super()._fill(asof, now, bars, day, rep)
        finally:
            self.executing = False

    def _stops(self, asof: date, now: datetime, bars: dict[str, Bar], day: Day | None, rep: StepReport) -> None:
        self.executing = True
        try:
            super()._stops(asof, now, bars, day, rep)
        finally:
            self.executing = False


def historical_panel(raw: Panel, asof: date, splits: Sequence[Split]) -> Panel:
    """Do not expose future columns or apply a future split to past decisions/absolute price filters."""
    end = raw.day_index(asof) + 1
    days = raw.days[:end]
    factors = split_factors([s for s in splits if s.execution_date <= asof], raw.symbols, days)
    arrays = [getattr(raw, key)[:, :end] / factors for key in ("open", "high", "low", "close")]
    return Panel(raw.symbols, days, arrays[0], arrays[1], arrays[2], arrays[3], raw.volume[:, :end] * factors)


def position_view(position: Position) -> dict[str, Any]:
    out = position.as_json()
    out["decision_id"] = position.decision_id
    out["unrealized_pnl"] = (
        position.last_value - position.entry_cost - position.entry_fees
        if position.state == "OPEN" and position.last_value is not None
        else None
    )
    return out


def account_views(book: PaperBook, strategies: Sequence[Strategy]) -> list[dict[str, Any]]:
    spy = book.equity("spy_hold")
    spy_return = spy[-1][3] / spy[0][3] - 1 if len(spy) >= 2 else None
    accounts = []
    for strategy in strategies:
        rows = book.equity(strategy.id)
        closed = book.positions(strategy.id, state="CLOSED")
        positions = book.positions(strategy.id)
        deposits = book.deposits(strategy.id)
        returns = daily_returns([r[3] for r in rows])
        volatility = stdev(returns) * math.sqrt(252) if len(returns) >= 20 else None
        net = rows[-1][3] / deposits - 1 if rows and deposits else None
        accounts.append(
            {
                "id": strategy.id,
                "title": strategy.title,
                "asset": strategy.asset,
                "style": strategy.style,
                "hypothesis": strategy.hypothesis,
                "baseline": strategy.baseline,
                "sessions": len(rows),
                "deposits": deposits,
                "equity": rows[-1][3] if rows else None,
                "cash": rows[-1][1] if rows else None,
                "return": net,
                "spy_return": spy_return,
                "excess_return": net - spy_return if net is not None and spy_return is not None else None,
                "max_drawdown": max_drawdown(returns) if len(returns) else None,
                "volatility": volatility,
                "sharpe": mean(returns) / stdev(returns) * math.sqrt(252) if volatility is not None and volatility > 0 else None,
                "open_positions": len(positions),
                **trade_stats(closed),
                "curve": [{"day": day.isoformat(), "equity": round(equity, 2)} for day, _, _, equity in rows],
                "grade": {"grade": "historical", "text": "Historical reconstruction; not forward evidence"},
                "spec_sha256": strategy.spec_sha256,
            }
        )
    return sorted(accounts, key=lambda a: (a["baseline"], -(a["return"] or 0)))


def walk_forward(accounts: list[dict[str, Any]]) -> dict[str, Any]:
    """Choose using an expanding past window, then report the following 21 sessions without retuning."""
    candidates = [a for a in accounts if not a["baseline"] and len(a["curve"]) >= 42]
    if not candidates:
        return {"windows": [], "message": "At least 42 replayed sessions are needed for a train/test comparison."}
    length = min(len(a["curve"]) for a in candidates)
    train = max(21, length // 3)
    spy = next((a for a in accounts if a["id"] == "spy_hold"), None)
    windows = []
    for start in range(train, length, 21):
        end = min(start + 20, length - 1)

        def prior_return(a: dict[str, Any], start: int = start) -> float:
            return float(a["curve"][start - 1]["equity"] / a["curve"][0]["equity"] - 1)

        chosen = max(candidates, key=lambda a: (prior_return(a), a["id"]))
        # At the boundary, hold the chosen existing simulated account; no imaginary free rebalance portfolio.
        beginning = start - 1
        net = chosen["curve"][end]["equity"] / chosen["curve"][beginning]["equity"] - 1
        bench = spy["curve"][end]["equity"] / spy["curve"][beginning]["equity"] - 1 if spy else None
        windows.append(
            {
                "train_through": chosen["curve"][beginning]["day"],
                "test_start": chosen["curve"][start]["day"],
                "test_end": chosen["curve"][end]["day"],
                "strategy": chosen["id"],
                "title": chosen["title"],
                "training_return": prior_return(chosen),
                "test_return": net,
                "spy_return": bench,
                "excess_return": net - bench if bench is not None else None,
            }
        )
    return {
        "windows": windows,
        "message": "Candidates are ranked on prior net return, then the next block is held out. "
        "These are comparisons of independently funded strategy accounts, not a combined switching portfolio. "
        "No weights are promoted and no forward trades are rewritten. Short windows remain exploratory.",
    }


def memory_db(kind: DbKind) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    migrate(conn, kind)
    return conn


def replay(
    paths: DeskPaths, settings: StocksSection, sessions: int, *, progress: Callable[[date, int, int], None] | None = None
) -> dict[str, Any]:
    source = sqlite3.connect((paths.db_dir / MARKET_FILE).as_uri() + "?mode=ro", uri=True, isolation_level=None, timeout=2)
    source.row_factory = sqlite3.Row
    from desk.stocks import replay_archive

    research_db = memory_db(DbKind.STOCKS_RESEARCH)
    run_id, ledger = replay_archive.create(paths)
    try:
        source.execute("BEGIN")  # one consistent source snapshot while collection can continue in WAL
        market = HistoricalMarket(source)
        days = market.bar_days()
        requested = sessions
        if sessions == 0:
            sessions = max(0, len(days) - MIN_BARS)
        if sessions < 2 or len(days) < sessions + MIN_BARS:
            raise DeskError(
                f"Replay needs {sessions} sessions plus {MIN_BARS} warm-up sessions; {len(days)} are saved. "
                "Collection can continue automatically; see Settings for missing price access."
            )
        selected = days[-sessions:]
        # A short evaluation window still needs long indicator lookbacks (for example 12-month momentum).
        since = min(days[-sessions - MIN_BARS], days[-1] - timedelta(days=max(settings.history_days, sessions * 2 + 400)))
        raw = load_panel(market, since, days[-1], adjust=False)
        if "SPY" not in raw.index:
            raise DeskError("SPY closing history is needed for the benchmark before replay can start")
        if raw.day_index(selected[0]) < MIN_BARS:
            raise DeskError("The selected replay window does not have enough earlier warm-up history")
        strategies = tuple(s for s in STRATEGIES if s.asset == "stock")
        # Squeeze rules require a properly archived/publication-timed short-interest history; unavailable here.
        strategies = tuple(s for s in strategies if s.id != "squeeze_runner")
        ledger.execute("INSERT INTO replay_meta VALUES ('manifest',?)", (canonical_json({
            "version": VERSION, "run_id": run_id, "first_day": selected[0].isoformat(), "last_day": selected[-1].isoformat(),
            "settings": settings.model_dump(mode="json"), "strategies": {s.id: s.content() for s in strategies},
            "weights": dict(DEFAULT_WEIGHTS), "stored_symbols": len(raw.symbols),
            "source": "Consistent stored-data snapshot; current listing-universe and filing-revision limitations apply",
        }),))
        book = PaperBook(ledger)
        analyzer = ReplayAnalyzer(market, StocksResearch(research_db), settings)
        engine = ReplayEngine(market, book, settings, strategies)
        splits = market.splits()
        skipped = []
        drawdowns: dict[str, tuple[float, float]] = {}
        for index, asof in enumerate(selected):
            if progress:
                progress(asof, index, len(selected))
            panel = historical_panel(raw, asof, splits)
            day = analyzer.build(panel, asof, now=cal.session_close(asof), crowd_and_options=False, weights=dict(DEFAULT_WEIGHTS))
            # Re-run desks without non-vintage short/macro data. It is not a historical publication archive.
            from desk.stocks.desks import committee, run_desks

            day.ctx.shorts = {}
            day.ctx.macro = {}
            day.results = run_desks(day.ctx, day.ctx.symbols)
            day.committee = {
                s: committee({d: rows.get(s) for d, rows in day.results.items()}, day.weights) for s in day.ctx.symbols
            }
            result = engine.step(asof, cal.session_close(asof) + timedelta(minutes=1), day)
            if result.skipped:
                skipped.append({"day": asof.isoformat(), "reason": result.skipped})
            else:
                replay_archive.capture(ledger, asof, drawdowns)
            engine.previous = day
            engine.previous_bars = engine._bars(asof)
        accounts = account_views(book, strategies)
        from desk.stocks import replay_grades

        grades = replay_grades.compute(book,market,strategies)
        for account in accounts:
            account["historical_grade"] = grades[account["id"]]
        ledger.execute("INSERT INTO replay_meta VALUES ('grades',?)",(canonical_json(grades),))
        positions = book.positions(state=None)[-500:]
        trades = []
        for position in positions:
            reason = ledger.execute("SELECT reasons FROM stk_decisions WHERE id=?", (position.decision_id,)).fetchone()
            trades.append({**position_view(position), "entry_reason": reason[0] if reason else None})
        ledger.execute("UPDATE replay_meta SET value='true' WHERE key='complete'")
        return {
            "version": VERSION,
            "grade_version": replay_grades.VERSION,
            "run_id": run_id,
            "record": "historical",
            "requested_sessions": sessions,
            "window": "all_available" if requested == 0 else "recent_sessions",
            "warmup_sessions": MIN_BARS,
            "days": [d.isoformat() for d in selected if not any(s["day"] == d.isoformat() for s in skipped)],
            "first_day": selected[0].isoformat(),
            "last_day": selected[-1].isoformat(),
            "accounts": accounts,
            "trades": trades,
            "trade_detail_limit": 500,
            "decisions": book.decisions(limit=200),
            "pending": [{"account": o.account, "fill_after": o.fill_after.isoformat(), "order": o.order} for o in book.pending()][
                :200
            ],
            "skipped_sessions": skipped,
            "walk_forward": walk_forward(accounts),
            "manifest": {s.id: {"spec_sha256": s.spec_sha256, "version": s.version} for s in strategies},
            "settings": settings.model_dump(mode="json"),
            "committee_weights": dict(DEFAULT_WEIGHTS),
            "advisory_only": True,
            "source": "Stored bulk closing-price history and dated filings",
            "limitations": [
                "Reconstructed using today's stored listing universe; delisted companies may be missing.",
                "Later restatements/data revisions may be absent from the available filing history.",
                "Options, crowd, short-interest and non-vintage macro inputs are excluded; historical option quotes "
                "cannot be invented from stock candles.",
                "Signals use only data through each decision date. Fills occur at the next available open with the "
                "existing stock spread/slippage/fee model and prior-session liquidity.",
                "Open positions at the end remain marked, not fictitiously closed. Each strategy has its own capital.",
                "Walk-forward comparisons do not establish a profitable edge or automatically change the live model.",
            ],
        }
    finally:
        source.close()
        research_db.close()
        ledger.close()
