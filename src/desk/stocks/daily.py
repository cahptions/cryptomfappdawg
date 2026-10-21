"""The daily refresh. Once a session's prices are in, analyse the day, keep every stock's page, and append the day's
lists and desk scores to the track record once (stocks-research.db; never rewritten). Later refreshes the same day
(new crowd counts, a late filing) update the pages but not the record."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from desk.core.clock import to_iso
from desk.stocks import desks as dk
from desk.stocks import validation as val
from desk.stocks.analysis import Analyzer, Day, compact, stock_detail
from desk.stocks.lists import LISTS, build_lists
from desk.stocks.prices import Panel
from desk.stocks.repo import StocksMarket


@dataclass
class Refresh:
    asof: date
    universe: int = 0
    recorded: list[str] = field(default_factory=list)     # lists appended to the track record by this refresh
    skipped: str | None = None
    day: Day | None = None

    def as_json(self) -> dict[str, Any]:
        return {"asof": self.asof.isoformat(), "universe": self.universe, "recorded": self.recorded,
                "skipped": self.skipped}


def register_specs(market: StocksMarket, at: datetime) -> dict[str, str]:
    """Freeze the methodology: each rule set is stored by version, and a changed rule without a new version is
    refused (StorageError) rather than silently mixing records."""
    out = {"stocks_desks": market.register_spec("stocks_desks", dk.METHOD_VERSION, dk.method_content(), at),
           "stocks_validation": market.register_spec("stocks_validation", val.VERSION, val.content(), at)}
    for spec in LISTS:
        out[f"stocks_list_{spec.id}"] = market.register_spec(f"stocks_list_{spec.id}", spec.version, spec.content(), at)
    return out


def refresh(analyzer: Analyzer, panel: Panel, asof: date, now: datetime) -> Refresh:
    """Analyse ``asof`` (a closed session) and record it. Waits, recording nothing, until that session's prices are
    in the panel: a list labelled with today's date must never be built from yesterday's prices."""
    if not panel.days or panel.days[-1] != asof:
        have = panel.days[-1].isoformat() if panel.days else "none"
        return Refresh(asof, skipped=f"waiting for {asof.isoformat()} prices (latest stored: {have})")
    register_specs(analyzer.market, now)
    day = analyzer.build(panel, asof, now=now, cache_today=True)
    analyzer.research.save_scores(asof, {s: stock_detail(day, s) for s in day.ctx.symbols},
                                  {s: compact(day, s) for s in day.ctx.symbols}, now)
    lists = build_lists(day)
    recorded = [spec.id for spec in LISTS
                if analyzer.research.save_list(asof, spec.id, lists[spec.id], spec.spec_sha256, now)]
    out = Refresh(asof, day.universe_size, recorded, None, day)
    analyzer.research.record_run("refresh", now, now, asof, True, {**out.as_json(), "computed_at": to_iso(now)})
    return out


__all__ = ["Refresh", "refresh", "register_specs"]
