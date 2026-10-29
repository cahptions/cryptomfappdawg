"""Stored historical-reconstruction grades. No writes to forward books, forecasts, controls or promotion gates."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from desk.stocks.paper.book import PaperBook
from desk.stocks.paper.report import report
from desk.stocks.paper.strategies import STRATEGIES, Strategy
from desk.stocks.repo import StocksMarket

VERSION = 1


def compute(book: PaperBook, market: StocksMarket, strategies: Sequence[Strategy]) -> dict[str, dict[str, Any]]:
    """Use the same complete equity series, baselines, bootstrap and Holm family as the forward report function."""
    result = report(book,market,strategies)
    family = sorted(a["id"] for a in result["accounts"] if a.get("vs_baseline") and
                    a["vs_baseline"].get("p") is not None)
    out = {}
    for a in result["accounts"]:
        rows = book.equity(a["id"])
        out[a["id"]] = {"method_version":VERSION,"basis":"historical_reconstruction",
            "strategy_id":a["id"],"spec_sha256":a["spec_sha256"],
            "recorded_specifications":a["recorded_specifications"],"mixed_specifications":a["mixed_specifications"],
            "first_day":rows[0][0].isoformat() if rows else None,
            "last_day":rows[-1][0].isoformat() if rows else None,
            "sessions":a["sessions"],"trades":a["trades"],"grade":a["grade"],
            "return":a["return"],"max_drawdown":a["max_drawdown"],"spy_return":a["spy_return"],
            "vs_spy":a["vs_spy"],"vs_baseline":a["vs_baseline"],"holm_family":family,
            "rules":result["rules"],"can_promote":False}
    return out


def current(replay: dict[str,Any] | None, strategies: Sequence[Strategy] = STRATEGIES) -> dict[str,dict[str,Any]]:
    """Match stored results to the current strategy specification, never treating a stale result as current."""
    replay = replay or {}
    accounts = replay.get("accounts")
    rows = {a["id"]:a for a in accounts if isinstance(a,dict) and isinstance(a.get("id"),str)} \
        if isinstance(accounts,list) else {}
    out = {}
    for s in strategies:
        value: dict[str,Any] = {"status":"missing","strategy_id":s.id,"current_spec_sha256":s.spec_sha256,
                               "can_promote":False}
        if s.asset == "option" or s.id == "squeeze_runner" or s.learned:
            value.update(status="unsupported",message="Historical option quotes or publication-timed inputs are not "
                         "available for this strategy; a stock-price replay cannot supply them.")
        elif not replay.get("run_id"):
            value["message"] = "No completed historical replay yet. Run replay to evaluate saved history."
        elif s.id not in rows:
            value["message"] = "This strategy was not included in the completed replay. Run the current replay."
        else:
            row = rows[s.id]
            stored = row.get("historical_grade")
            stored = stored if isinstance(stored,dict) else {}
            value.update(run_id=replay["run_id"],generated_at=replay.get("generated_at"),
                         first_day=row.get("start") or replay.get("first_day"),last_day=replay.get("last_day"))
            if row.get("spec_sha256") != s.spec_sha256 or (stored and stored.get("spec_sha256") != s.spec_sha256):
                value.update(status="stale",message="Rules changed since this replay. Run replay with the current specification.",
                             recorded_spec_sha256=row.get("spec_sha256"))
            elif (stored.get("method_version") != VERSION or stored.get("basis") != "historical_reconstruction"
                  or not isinstance(stored.get("grade"),dict) or stored.get("sessions") != row.get("sessions")):
                value["message"] = "The saved replay predates statistical grading. Run replay to calculate its grade."
            elif stored.get("mixed_specifications"):
                value.update(status="stale",message="Replay contains mixed rule versions. Run a fresh replay.")
            else:
                value.update(stored,status="current",run_id=replay["run_id"],can_promote=False)
        out[s.id] = value
    return out
