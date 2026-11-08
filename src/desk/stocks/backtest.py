"""Chronological strategy replay in isolated books. No provider calls or forward-ledger writes.

Each day's universe is the listings as they stood that day (delisted companies included), but companies delisted before
collection began are missing, and filing history is what is stored: it is not a survivor-bias-free archival
experiment. Crowd counts, company news and world shocks are excluded (collected going forward only); trade actions
count once published. Macro inputs are dated market prints read as of each day. Short interest joins, as FINRA
published it, once the stored settlements cover the replayed window (``short_history``). Option strategies whose
signals come from the stock run on option prices rebuilt from stock history, with Massive's real daily option prices
replacing the model where they exist (``desk.stocks.option_model``). The two whose signal is option data (cheap
implied volatility, earnings straddles) replay on real prices only, inside Massive's window, beside the modeled
accounts (``desk.stocks.real_options``).
"""

from __future__ import annotations

import math
import sqlite3
from collections.abc import Callable, Sequence
from datetime import date, datetime, timedelta
from itertools import pairwise
from statistics import mean, median, stdev
from typing import Any

from desk.core.config import StocksSection
from desk.core.errors import DeskError
from desk.core.hashing import canonical_json, sha256_hex
from desk.core.paths import DeskPaths
from desk.stats.core import max_drawdown
from desk.stocks import calendar as cal
from desk.stocks import option_model
from desk.stocks.analysis import Analyzer, Day
from desk.stocks.contracts import Bar, OptionQuote, Split
from desk.stocks.desks import DEFAULT_WEIGHTS
from desk.stocks.fundamentals import Fundamentals
from desk.stocks.option_model import ModeledOptions
from desk.stocks.paper import lab
from desk.stocks.paper.book import PaperBook, Position
from desk.stocks.paper.engine import PaperEngine, StepReport
from desk.stocks.paper.fills import Dividends, fair_spread, index_chain
from desk.stocks.paper.report import daily_returns, trade_stats
from desk.stocks.paper.strategies import STRATEGIES, Strategy, build_structure
from desk.stocks.prices import Panel, load_panel, split_factors
from desk.stocks.real_options import RealOptions
from desk.stocks.repo import MARKET_FILE, RESEARCH_FILE, StocksMarket, StocksResearch, date_of
from desk.stocks.signals import SHORT_PUBLICATION_LAG
from desk.stocks.validation import evaluation_window
from desk.storage.db import DbKind, migrate

VERSION = 6
# Earlier sessions every replayed day needs: 12-month momentum, the 52-week high/low and the 200-day average read a
# year of prices (desk.stocks.features), so a replay starting after the analysis' 60-bar floor would rank on partial ones.
WARMUP_SESSIONS = 252
SHORT_MIN_SETTLEMENTS = 24     # a year of FINRA's twice-monthly settlements
SHORT_MAX_GAP_DAYS = 35        # at most one missed release between stored settlements
SHORT_MIN_SHARE = 0.5          # a settlement holding under half the typical settlement's symbols is a partial backfill


class HistoricalMarket(StocksMarket):
    options: ModeledOptions | None = None

    def register_spec(self, name: str, version: int, content: dict[str, Any], at: datetime) -> str:
        # Engine specifications are saved in this run's manifest, never registered in the live source DB.
        return sha256_hex(canonical_json(content))

    def option_chain(self, underlying: str, day: date | None = None, *, observed_by: datetime | None = None
                     ) -> tuple[date | None, dict[date, list[OptionQuote]]]:
        """The replay session's rebuilt chain (real prices where Massive has them), never a stored later one."""
        if self.options is None or day is None or day != self.options.asof:
            return None, {}
        return day, self.options.chain(underlying)


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
    options: ModeledOptions | None = None

    def _structure(self, structure: str, symbol: str, asof: date, s: float, p: dict[str, Any],
                   evidence: dict[str, Any]) -> list[dict[str, Any]] | None:
        """Contracts are chosen from the model's prices, so one contract's real price never pulls the pick toward it;
        the decision then sees the day's real price where there is one."""
        if self.options is None:
            return super()._structure(structure, symbol, asof, s, p, evidence)
        chains = self.options.chain(symbol, real=False)
        legs = build_structure(structure, chains, s, asof, self.rate, p, evidence) if chains else None
        if not legs:
            return None
        self.options.remember(symbol, legs)
        quotes = index_chain(self.options.chain(symbol))
        for leg in legs:
            q = quotes.get(leg["occ"])
            if q is not None and fair_spread(q):
                leg.update(decision_price=q.ask if leg["quantity"] > 0 else q.bid, decision_mid=q.mid)
        return legs

    def _no_contract(self, symbol: str, asof: date) -> str:
        if self.options is not None and not self.options.chain(symbol, real=False):
            return "no option prices could be rebuilt for this stock today (too little price history)"
        return "no rebuilt contract fits the rule's expiry, strike and spread"

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


def rate_series(market: StocksMarket) -> Callable[[date], float]:
    """The 3-month Treasury yield known on a day (the engine's default rate before any is stored)."""
    from bisect import bisect_right

    from desk.stocks.paper.engine import DEFAULT_RATE

    points = sorted((p.day, p.value / 100) for p in market.rates("UST3M"))
    days = [d for d, _ in points]

    def on(day: date) -> float:
        i = bisect_right(days, day)
        return points[i - 1][1] if i else DEFAULT_RATE

    return on


def short_history(market: StocksMarket, first: date, last: date) -> dict[str, Any]:
    """Whether stored short interest covers the replayed sessions as FINRA published it: at least 24 settlements,
    the latest one published by the first replayed day (12 days after settlement), and no gap or partial settlement
    from there to the last replayed day. Until it does, the replay leaves short interest and the squeeze rules out."""
    stored = [(date_of(r[0]), int(r[1])) for r in market.conn.execute(
        "SELECT settlement, COUNT(*) FROM stk_short_interest WHERE settlement <= ? GROUP BY settlement ORDER BY settlement",
        (last.toordinal(),))]
    published = [d for d, _ in stored if d <= first - timedelta(days=SHORT_PUBLICATION_LAG)]
    span = [(d, n) for d, n in stored if published and d >= published[-1]]
    typical = median(n for _, n in stored) if stored else 0
    ends = [d for d, _ in span] + [last - timedelta(days=SHORT_PUBLICATION_LAG)]
    replayed = (len(stored) >= SHORT_MIN_SETTLEMENTS and bool(span) and all(n >= typical * SHORT_MIN_SHARE for _, n in span)
                and all((b - a).days <= SHORT_MAX_GAP_DAYS for a, b in pairwise(ends)))
    have = (f"{len(stored)} FINRA settlements are stored" + (f", from {stored[0][0].isoformat()}" if stored else ""))
    return {"replayed": replayed, "settlements": len(stored), "first": stored[0][0].isoformat() if stored else None,
            "message": f"Short interest is read as FINRA published it (each settlement counts {SHORT_PUBLICATION_LAG} "
                       f"days later); {have}." if replayed else
                       f"{have}; the replay starts {first.isoformat()}, so short interest and the squeeze rules wait "
                       f"until the stored history covers it ({SHORT_MIN_SETTLEMENTS} or more settlements, without gaps)."}


def collected_chain(market: StocksMarket) -> Callable[[str, date], dict[date, list[OptionQuote]]]:
    """KESTREL's own stored chain for a stock and session (only quotes stamped that session), as forward PAPER reads."""
    row = market.conn.execute("SELECT MIN(day) FROM stk_option_chains").fetchone()
    first = date_of(row[0]) if row and row[0] is not None else None

    def chain(symbol: str, day: date) -> dict[date, list[OptionQuote]]:
        if first is None or day < first:
            return {}
        found, chains = StocksMarket.option_chain(market, symbol, day)
        if found != day:
            return {}
        same = {e: [q for q in quotes if q.quoted_at is not None and cal.new_york(q.quoted_at).date() == day]
                for e, quotes in chains.items()}
        return {e: quotes for e, quotes in same.items() if quotes}

    return chain


def lab_strategies(paths: DeskPaths) -> list[Strategy]:
    """Every lab strategy ever promoted, read from the research database without writing to it."""
    path = paths.db_dir / RESEARCH_FILE
    if not path.exists():
        return []
    conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, isolation_level=None, timeout=2)
    conn.row_factory = sqlite3.Row
    try:
        return lab.strategies(StocksResearch(conn))[0]
    except sqlite3.OperationalError:           # a research database from before the lab
        return []
    finally:
        conn.close()


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
        start, limited = evaluation_window(market, settings)    # every stored day by default, memory allowing
        days = [d for d in market.bar_days() if d >= start]
        requested = sessions
        if sessions == 0:
            sessions = max(0, len(days) - WARMUP_SESSIONS)
        if sessions < 2 or len(days) < sessions + WARMUP_SESSIONS:
            raise DeskError(
                f"Replay needs {max(sessions, 2)} sessions plus {WARMUP_SESSIONS} warm-up sessions; {len(days)} are saved. "
                "Collection can continue automatically; see Settings for missing price access."
            )
        selected = days[-sessions:]
        # A short window still reads the whole evaluation window: 12-month momentum, 52-week highs and two years of
        # month-end valuations look back far beyond the replayed sessions.
        raw = load_panel(market, days[0], days[-1], adjust=False)
        if "SPY" not in raw.index:
            raise DeskError("SPY closing history is needed for the benchmark before replay can start")
        if raw.day_index(selected[0]) < WARMUP_SESSIONS:
            raise DeskError("The selected replay window does not have enough earlier warm-up history")
        shorts = short_history(market, selected[0], selected[-1])
        replayed = {*option_model.REPLAYED, *(option_model.SHORT_REPLAYED if shorts["replayed"] else ())}
        strategies = tuple(s for s in STRATEGIES if (s.asset == "stock" and s.id not in option_model.SHORT_REPLAYED)
                           or s.id in replayed)
        # Lab strategies blend desks this replay computes, so they replay too (retired ones as well: a grade, not a
        # trading status). They were chosen on rebuilt months, so their replay is in-sample (see the limitations).
        learned = lab_strategies(paths)
        strategies += tuple(learned)
        ledger.execute("INSERT INTO replay_meta VALUES ('manifest',?)", (canonical_json({
            "version": VERSION, "run_id": run_id, "first_day": selected[0].isoformat(), "last_day": selected[-1].isoformat(),
            "settings": settings.model_dump(mode="json"), "strategies": {s.id: s.content() for s in strategies},
            "weights": dict(DEFAULT_WEIGHTS), "stored_symbols": len(raw.symbols),
            "source": "Consistent stored-data snapshot; point-in-time listings since collection began; stored filings",
        }),))
        book = PaperBook(ledger)
        analyzer = ReplayAnalyzer(market, StocksResearch(research_db), settings)
        engine = ReplayEngine(market, book, settings, strategies)
        splits = market.splits()
        real = option_model.RealBars.load(source)
        dividends = Dividends(market.dividends(), splits)
        # An expanding window: each replayed month's option model is fit only on what was observed before it.
        observations = option_model.observe(market, historical_panel(raw, days[-1], splits), real, rate_series(market),
                                            dividends=dividends)
        from desk.stocks.options_history import listings

        modeled = ModeledOptions(observations.fit(selected[0]), real, market.optioned_symbols() & set(raw.symbols),
                                 dividends, listings=listings(source), collected=collected_chain(market))
        refits = 0
        market.options = engine.options = modeled
        real_prices = RealOptions(source, selected[-1], set(modeled.optioned))
        skipped = []
        drawdowns: dict[str, tuple[float, float]] = {}
        for index, asof in enumerate(selected):
            if progress:
                progress(asof, index, len(selected))
            panel = historical_panel(raw, asof, splits)
            day = analyzer.build(panel, asof, now=cal.session_close(asof), historical=True, weights=dict(DEFAULT_WEIGHTS))
            if not shorts["replayed"]:
                # Re-run desks without short interest the stored history can't show as published that day.
                from desk.stocks.desks import committee, run_desks

                day.ctx.shorts = {}
                day.results = run_desks(day.ctx, day.ctx.symbols)
                day.committee = {
                    s: committee({d: rows.get(s) for d, rows in day.results.items()}, day.weights) for s in day.ctx.symbols
                }
            if index and asof.month != selected[index - 1].month:
                modeled.calibration = observations.fit(asof)
                refits += 1
            # Option rules see a modeled implied volatility only after the desks have scored without option data.
            modeled.session(asof, panel, engine._rate(asof))
            day.ctx.options = {s: o for s in sorted(modeled.optioned) if (o := modeled.summary(s))}
            real_prices.observe(day, engine._rate(asof))      # the option-data rules, on real prices only
            result = engine.step(asof, cal.session_close(asof) + timedelta(minutes=1), day)
            if result.skipped:
                skipped.append({"day": asof.isoformat(), "reason": result.skipped})
            else:
                replay_archive.capture(ledger, asof, drawdowns)
            engine.previous = day
            engine.previous_bars = engine._bars(asof)
        accounts = account_views(book, strategies)
        held = book.positions(state=None)
        mix = modeled.price_mix(held, selected[-1])
        for account in accounts:
            if account["asset"] == "option":
                account["option_prices"] = mix.get(account["id"], {"collected": 0, "real": 0, "real_iv": 0, "model": 0})
        contracts = option_model.wanted(held, selected[-1])
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
            "warmup_sessions": WARMUP_SESSIONS,
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
            "short_interest": shorts,
            "walk_forward": walk_forward(accounts),
            "manifest": {s.id: {"spec_sha256": s.spec_sha256, "version": s.version} for s in strategies},
            "settings": settings.model_dump(mode="json"),
            "committee_weights": dict(DEFAULT_WEIGHTS),
            "advisory_only": True,
            "source": "Stored bulk closing-price history and dated filings; option prices rebuilt from stock history, "
                      "with Massive's real daily option prices where they exist",
            "option_pricing": {
                "calibration": modeled.calibration.as_json(),
                "calibration_window": f"Refit at the start of each replayed month ({refits + 1} fits) from observations "
                                      "dated before it",
                "real_from": (selected[-1] - timedelta(days=option_model.MASSIVE_DAYS)).isoformat(),
                "contracts": len(contracts),
                "with_real_prices": sum(1 for c in contracts if c["occ"] in real.series),
                "liquidity_checks": dict(modeled.liquidity),
                "chains": {k: modeled.chain_sources.get(k, 0) for k in ("collected", "listed", "stand_in")},
                "optioned_stocks": len(modeled.optioned),
                "not_replayed": dict(option_model.NOT_REPLAYED),
            },
            "option_contracts": contracts,
            "real_price": real_prices.replay(market),
            "limitations": [
                "Each day's universe is the listings as they stood that day, so companies delisted since collection "
                "began are included; companies delisted before collection began never appeared in a listing file and "
                "are missing. A held stock that stops trading after it is delisted is sold at its last close.",
                "Fundamentals are SEC XBRL company facts: each value counts from the day after the filing that "
                "reported it, and a restatement replaces the earlier value only from its own filing date. Values a "
                "company never filed in XBRL are missing.",
                "Option prices: Massive's real daily closing trades where it has them (the last 2 years), a recent "
                "trade's implied volatility on a quiet day, and otherwise Black-Scholes at the stock's realized "
                "volatility times how options on it have been priced against it (calibrated on collected chains and "
                "Massive's prices), with a put skew and the typical quoted spread. The ratio, skew and spread are "
                "refit at the start of each replayed month from observations dated before it (stated defaults until "
                "there are enough), so no session is priced with what options did later. Dividends enter every "
                "option price as the stock's trailing-12-month cash dividend yield (stored dividends ex by that day).",
                f"Chains: {modeled.chain_sources.get('collected', 0)} stock-days used KESTREL's own collected chain "
                "(real bid/ask, volume and open interest, as forward PAPER fills), "
                f"{modeled.chain_sources.get('listed', 0)} were rebuilt on Massive's real listing for that month, and "
                f"{modeled.chain_sources.get('stand_in', 0)} on the stand-in (monthly expiries and a listing-style "
                "strike grid). Collected chains are kept 45 days; listings download for the stocks and months the "
                "replay trades.",
                "Open interest isn't stored historically. Where Massive was asked about the days before a decision, a "
                f"contract that traded fewer than {option_model.LIQUID_VOLUME} contracts in the "
                f"{option_model.LIQUID_DAYS} days up to it counts as illiquid and is passed over "
                f"({modeled.liquidity['illiquid']} contract-days were, {modeled.liquidity['liquid']} were not); every "
                "other rebuilt contract is assumed to clear the rules' 100-contract open-interest floor.",
                f"Option strategies trade only the {len(modeled.optioned)} stocks whose option chains this app has "
                "collected at some point, a narrower universe than the stock strategies'.",
                "Option strategies whose signal is option data itself are not replayed on modeled prices. Calls when "
                "IV is cheap and Cheap earnings straddles replay on real prices inside Massive's 2-year window instead "
                "(their own accounts, against random calls on real prices, never mixed with the modeled ones); "
                "Follow unusual call buying needs each day's whole-chain volume and is not replayed.",
                shorts["message"],
                "Macro inputs (Treasury yields, credit spreads, VIX) are dated market prints, read only through each "
                "decision date as first stored. Trade actions reach the News desk once published (stored from January "
                "20, 2025). Company news, world shocks and crowd counts are collected going forward only and are "
                "left out.",
                f"Every replayed day has at least {WARMUP_SESSIONS} earlier sessions of prices, so 12-month momentum, "
                "the 52-week high and low and the 200-day average are complete from the first replayed day.",
                "Signals use only data through each decision date. Fills occur at the next available open with the "
                "existing stock spread/slippage/fee model and prior-session liquidity.",
                "Open positions at the end remain marked, not fictitiously closed. Each strategy has its own capital.",
                "Walk-forward comparisons do not establish a profitable edge or automatically change the live model.",
                *(["Lab strategies were chosen on these same rebuilt months, so their replay is in-sample: it shows "
                   "what the chosen rule did, not evidence from history it never saw."] if learned else []),
                *([limited] if limited else []),
            ],
        }
    finally:
        source.close()
        research_db.close()
        ledger.close()
