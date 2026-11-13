"""The Learner page (docs/STOCKS_LEARNER.md → What you see): the Learner PAPER account against its baselines, each
trade with the decision behind it, the do / don't findings, results by market regime, and the model versions with
their held-out and forward results.

``build`` reads the PAPER ledger and the research database; ``refresh`` writes it to ``stocks-learner-account.json``
when anything it shows has changed (the web page reads that file; a page load computes nothing). The account's marks,
P/L and grade come from the same code as every PAPER account (``paper.report``); the comparisons by trade use realised
returns at real fills only (``promotion.realised``).
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from pathlib import Path
from statistics import fmean
from typing import Any

from desk.core.clock import to_iso
from desk.core.paths import DeskPaths
from desk.stocks.connection import _atomic_private
from desk.stocks.learner import actions as act
from desk.stocks.learner import findings as fd
from desk.stocks.learner import model as md
from desk.stocks.learner import policy
from desk.stocks.learner import promotion as pr
from desk.stocks.learner import regimes as rg

SAVED = "stocks-learner-account.json"
TRADES = 60
LABEL = ("PAPER only. The Learner's account trades simulated money by an ordinary statistical model within fixed "
         "risk limits; no order is ever sent, and nothing it does changes Plays, another PAPER account or Learning.")


def saved(home: Path) -> dict[str, Any] | None:
    try:
        path = home / SAVED
        if path.stat().st_size > 8_000_000:
            return None
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None


def _decisions(ledger: sqlite3.Connection) -> dict[int, dict[str, Any]]:
    out = {}
    for r in ledger.execute("SELECT * FROM stk_learner_decisions"):
        out[int(r["id"])] = {"id": r["id"], "session": date.fromordinal(r["session"]).isoformat(),
                             "account": r["account"], "model_id": r["model_id"], "mode": r["mode"],
                             "exit_rule": r["exit_rule"], "exit_title": act.BY_EXIT[r["exit_rule"]].title,
                             "units": r["units"], "risk_usd": r["risk_usd"], "equity_usd": r["equity_usd"],
                             "regime": r["regime"], "scores": json.loads(r["scores_json"]),
                             "alternatives": json.loads(r["alternatives_json"]), "reason": r["reason"],
                             "dials": act.parse_key(r["action_key"]).describe(), "key": r["action_key"]}
    return out


def _trade(p: Any, decision: Mapping[str, Any] | None, evidence: Mapping[str, Any] | None = None) -> dict[str, Any]:
    r = pr.realised(p, evidence)
    return {"id": p.id, "account": p.account, "symbol": p.symbol, "structure": p.structure,
            "title": act.BY_STRUCTURE[p.structure].title if p.structure in act.BY_STRUCTURE else p.structure,
            "legs": [{"occ": leg["occ"], "kind": leg["kind"], "strike": leg["strike"], "expiration": leg["expiration"],
                      "quantity": leg["quantity"], "price": leg.get("entry_price")} for leg in p.legs],
            "entry_day": p.entry_day.isoformat(), "expires": p.expires.isoformat() if p.expires else None,
            "state": p.state, "cost": round(p.entry_cost, 2), "fees": round(p.entry_fees, 2),
            "max_loss": None if p.max_loss is None else round(p.max_loss, 2),
            "value": None if p.last_value is None else round(p.last_value, 2),
            "exit_day": p.exit_day.isoformat() if p.exit_day else None, "exit_reason": p.exit_reason,
            "pnl": None if p.pnl is None else round(p.pnl, 2), "return": None if r is None else round(r, 4),
            "decision": dict(decision) if decision else None}


def by_regime(trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Closed trades by the market regime of their decision session: count, mean realised return, wins, P/L."""
    groups: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for t in trades:
        if t["state"] == "CLOSED" and t["return"] is not None and t["decision"]:
            groups[(t["decision"]["regime"], t["account"])].append(t)
    out = []
    for (regime, account), items in sorted(groups.items()):
        out.append({"regime": regime, "account": account, "trades": len(items),
                    "mean_return": round(fmean(t["return"] for t in items), 4),
                    "win_rate": round(sum(t["return"] > 0 for t in items) / len(items), 4),
                    "pnl": round(sum(t["pnl"] or 0 for t in items), 2)})
    return out


def _curve(book: Any, account: str) -> list[dict[str, Any]]:
    return [{"day": d.isoformat(), "equity": round(e, 2)} for d, _, _, e in book.equity(account)][-400:]


def baselines(book: Any, market: Any, first: date | None) -> dict[str, list[dict[str, Any]]]:
    """SPY's total return and the rule-based option strategies' average, both scaled to the Learner's capital from its
    first day, for the equity chart."""
    if first is None:
        return {"spy": [], "rules": []}
    from desk.stocks.paper.report import spy_total

    spy = {d: v for d, v in spy_total(market, first).items() if d >= first}
    out: dict[str, list[dict[str, Any]]] = {"spy": [], "rules": []}
    if spy:
        base = spy[min(spy)]
        out["spy"] = [{"day": d.isoformat(), "equity": round(policy.CAPITAL * v / base, 2)} for d, v in sorted(spy.items())]
    curves = []
    for sid in pr.rule_strategies():
        rows = [(d, e) for d, _, _, e in book.equity(sid) if d >= first]
        if rows and rows[0][1] > 0:
            curves.append({d: e / rows[0][1] for d, e in rows})
    if curves:
        days = sorted(set.intersection(*(set(c) for c in curves)))
        out["rules"] = [{"day": d.isoformat(), "equity": round(policy.CAPITAL * fmean(c[d] for c in curves), 2)}
                        for d in days]
    return out


def models(research: sqlite3.Connection, decisions: Mapping[int, Mapping[str, Any]],
           trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Every registered model: its held-out headline, whether it passed the gate, and its forward trades."""
    forward: dict[str, list[float]] = defaultdict(list)
    for t in trades:
        d = t["decision"]
        if d and d["account"] == policy.ACCOUNT and d["model_id"] and t["return"] is not None:
            forward[d["model_id"]].append(t["return"])
    used = {d["model_id"] for d in decisions.values() if d["model_id"]}
    out = []
    try:
        rows = research.execute("SELECT * FROM stk_learn_models ORDER BY trained_at DESC LIMIT 20").fetchall()
    except sqlite3.OperationalError:
        return []
    for row in rows:
        metrics = json.loads(row["metrics_json"])
        g = policy.gate(row, load=False)
        f = forward.get(row["id"], [])
        found = metrics.get("findings") or {}
        out.append({"id": row["id"], "trained_at": row["trained_at"], "code_version": row["code_version"],
                    "current_code": row["code_version"] == md.version_hash(), "seconds": row["seconds"],
                    "peak_mb": row["peak_mb"], "rows": sum(int(v.get("rows") or 0)
                                                           for v in json.loads(row["data_json"]).values()),
                    "rank_correlation": metrics.get("rank_correlation"), "test_days": metrics.get("test_days"),
                    "top_minus_random": (metrics.get("deciles") or {}).get("top_minus_random"),
                    "gate": {"ok": g.ok and row["code_version"] == md.version_hash(), "why": g.why},
                    "traded": row["id"] in used, "forward_trades": len(f),
                    "forward_mean": round(fmean(f), 4) if f else None,
                    "findings": {k: found.get(k) for k in ("new", "confirmed", "failed", "retired", "rules_tested")
                                 if k in found}})
    return out


def build(ledger: sqlite3.Connection | None, research: sqlite3.Connection, market: Any, now: datetime) -> dict[str, Any]:
    """The Learner page's data (read-only)."""
    from desk.stocks.paper.book import PaperBook
    from desk.stocks.paper.report import account_report, grade, recovery_sessions, spy_total

    out: dict[str, Any] = {"label": LABEL, "generated_at": to_iso(now), "policy": policy_summary(),
                           "findings": findings_view(research), "regimes": regime_view(research),
                           "promotion": [], "accounts": {}, "trades": [], "days": [], "by_regime": [],
                           "baselines": {"spy": [], "rules": []}, "models": []}
    latest = md.latest(research)
    g = policy.gate(latest, load=False)
    out["gate"] = {"ok": g.ok, "why": g.why, "model_id": g.model_id, "detail": g.detail}
    if ledger is None or not _has(ledger, "stk_learner_days"):
        out["models"] = models(research, {}, [])
        out["promotion"] = pr.overview(None, research)
        return out
    book = PaperBook(ledger)
    decisions = _decisions(ledger)
    trades = []
    for account in policy.ACCOUNTS:
        for p in book.positions(account, state=None):
            evidence = pr._evidence(ledger, p.id) if p.state == "CLOSED" else None
            trades.append(_trade(p, decisions.get(int(p.plan.get("learner_decision") or 0)), evidence))
    trades.sort(key=lambda t: (t["state"] != "OPEN", t["exit_day"] or "", t["entry_day"]), reverse=False)
    open_ = [t for t in trades if t["state"] == "OPEN"]
    closed = sorted((t for t in trades if t["state"] == "CLOSED"), key=lambda t: (t["exit_day"], t["id"]), reverse=True)
    out["trades"] = [*open_, *closed][:TRADES]
    out["by_regime"] = by_regime(trades)
    first = min((rows[0][0] for a in policy.ACCOUNTS if (rows := book.equity(a))), default=None)
    out["baselines"] = baselines(book, market, first)
    spy = spy_total(market, first) if first else {}
    recovery = recovery_sessions(book)
    random_equity = {d: e for d, _, _, e in book.equity(policy.RANDOM)}
    for st in policy.STRATEGIES:
        rep = account_report(book, market, st, None if st.baseline else random_equity, spy, recovery)
        rep["grade"] = grade(rep)
        mine = [t for t in trades if t["account"] == st.id]
        rets = [t["return"] for t in mine if t["return"] is not None]
        risk = [d for d in decisions.values() if d["account"] == st.id]
        explore = sum(d["risk_usd"] for d in risk if d["mode"] == "explore")
        out["accounts"][st.id] = {
            "title": st.title, "hypothesis": st.hypothesis, "baseline": st.baseline, "spec_sha256": st.spec_sha256,
            "equity": rep["equity"], "return": rep["return"], "spy_return": rep["spy_return"], "cash": rep["cash"],
            "sessions": rep["sessions"], "max_drawdown": rep["max_drawdown"], "vs_spy": rep["vs_spy"],
            "vs_baseline": rep["vs_baseline"], "grade": rep["grade"], "open_positions": rep["open_positions"],
            "trades": rep["trades"], "win_rate": rep["win_rate"], "total_pnl": rep["total_pnl"],
            "realised_mean": round(fmean(rets), 4) if rets else None, "realised_trades": len(rets),
            "decisions": len(risk), "explore_decisions": sum(d["mode"] == "explore" for d in risk),
            "new_risk": round(sum(d["risk_usd"] for d in risk), 2), "explore_risk": round(explore, 2),
            "explore_share": round(explore / sum(d["risk_usd"] for d in risk), 4) if risk else None,
            "curve": _curve(book, st.id)}
    vs_rules = rules_comparison(book)
    out["vs_rules"] = vs_rules
    out["days"] = [{"session": date.fromordinal(r["session"]).isoformat(), "account": r["account"],
                    "status": r["status"], "why": r["why"], "model_id": r["model_id"],
                    "detail": {k: v for k, v in json.loads(r["detail_json"]).items()
                               if k in ("candidates", "exploit_eligible", "explore_eligible", "filtered_by_dont",
                                        "new_risk", "explore_risk", "regime", "seconds", "skipped")}}
                   for r in ledger.execute("SELECT * FROM stk_learner_days ORDER BY session DESC, account LIMIT 40")]
    out["models"] = models(research, decisions, trades)
    out["promotion"] = pr.overview(ledger, research)
    return out


def rules_comparison(book: Any) -> dict[str, Any]:
    """Realised returns by entry day: the Learner, its random twin and the rule-based option strategies, each side's
    daily means with a 90% bootstrap range (real fills only)."""
    mine, n, _ = pr.daily(book, policy.ACCOUNT)
    rand, rn, _ = pr.daily(book, policy.RANDOM)
    rules: dict[int, list[float]] = defaultdict(list)
    for sid in pr.rule_strategies():
        got, _, _ = pr.daily(book, sid)
        for d, v in got.items():
            rules[d].append(v)
    rule_days = {d: fmean(v) for d, v in rules.items()}
    paired = [mine[d] - rand[d] for d in sorted(mine) if d in rand]
    return {"learner": pr._boot(list(mine.values())), "learner_trades": n,
            "random": pr._boot(list(rand.values())), "random_trades": rn,
            "rules": pr._boot(list(rule_days.values())), "vs_random": pr._boot(paired),
            "vs_rules": pr._boot_difference(list(mine.values()), list(rule_days.values())),
            "note": "Realised returns on the money at risk at real fills, averaged by entry day; positions still open "
                    "or closed at a modelled price are left out."}


def findings_view(research: sqlite3.Connection) -> dict[str, Any]:
    items = fd.current(research)
    order = {"confirmed": 0, "candidate": 1, "failed": 2, "retired": 3}
    items.sort(key=lambda f: (order[f["status"]], f["kind"], -(abs(f["evidence"].get("mean") or 0))))
    return {"items": [{"id": f["id"], "kind": f["kind"], "status": f["status"], "words": f["words"],
                       "found_at": f["found_at"], "model_id": f["model_id"],
                       "days": f["evidence"].get("days"), "rows": f["evidence"].get("rows"),
                       "regimes": f["evidence"].get("regimes") or [],
                       "stages": [{"status": s["status"], "at": s["at"], "days": s["evidence"].get("days"),
                                   "mean": s["evidence"].get("mean"), "excess": s["evidence"].get("excess")}
                                  for s in f["stages"]],
                       "filter": f["kind"] == "dont" and f["status"] == "confirmed"} for f in items][:60],
            "rules": {"fdr": fd.FDR, "min_days": fd.MIN_DAYS, "confirm_days": fd.CONFIRM_DAYS,
                      "alpha_confirm": fd.ALPHA_CONFIRM, "version": fd.version_hash()}}


def regime_view(research: sqlite3.Connection) -> dict[str, Any]:
    row = md.latest(research)
    metrics = json.loads(row["metrics_json"]) if row is not None else {}
    return {"rule": {"vix_calm": rg.VIX_CALM, "vix_high": rg.VIX_HIGH, "hy_widening": rg.HY_WIDENING,
                     "version": rg.version_hash()},
            "model_id": None if row is None else row["id"], "held_out": metrics.get("by_regime") or []}


def policy_summary() -> dict[str, Any]:
    return {"version": policy.version_hash(), "capital": policy.CAPITAL, "trade_risk": policy.TRADE_RISK,
            "explore_trade_risk": policy.EXPLORE_TRADE_RISK, "session_risk": policy.SESSION_RISK,
            "explore_share": policy.EXPLORE_SHARE, "total_risk": policy.TOTAL_RISK, "stock_risk": policy.STOCK_RISK,
            "sector_risk": policy.SECTOR_RISK, "max_open": policy.MAX_OPEN, "max_new": policy.MAX_NEW,
            "lcb_z": policy.LCB_Z, "ucb_z": policy.UCB_Z, "gate_min_days": policy.GATE_MIN_DAYS,
            "forward_days": pr.FORWARD_DAYS}


def _has(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def refresh(paths: DeskPaths, dbs: Any, now: datetime, *, force: bool = False) -> dict[str, Any]:
    """Rewrite the saved Learner page when what it shows has changed; record promotion stages on the way."""
    from desk.stocks.repo import StocksMarket

    ledger, research = dbs.ledger, dbs.research
    stamp = {"equity": ledger.execute("SELECT MAX(day), COUNT(*) FROM stk_equity WHERE account IN (?, ?)",
                                      policy.ACCOUNTS).fetchone()[:],
             "positions": ledger.execute("SELECT COUNT(*), MAX(closed_at) FROM stk_positions WHERE account IN (?, ?)",
                                         policy.ACCOUNTS).fetchone()[:],
             "days": ledger.execute("SELECT COUNT(*) FROM stk_learner_days").fetchone()[0],
             "models": research.execute("SELECT COUNT(*) FROM stk_learn_models").fetchone()[0],
             "findings": research.execute("SELECT COUNT(*) FROM stk_learn_finding_status").fetchone()[0]}
    previous = saved(paths.home) or {}
    if not force and previous.get("stamp") == json.loads(json.dumps(stamp)):
        return {"refreshed": False}
    promoted = pr.review(ledger, research, now)
    value = {**build(ledger, research, StocksMarket(dbs.market), now), "stamp": stamp, "promotion_review": promoted}
    _atomic_private(paths.home / SAVED, json.dumps(value, allow_nan=False, default=str))
    return {"refreshed": True}


__all__ = ["LABEL", "SAVED", "build", "by_regime", "findings_view", "policy_summary", "refresh", "regime_view",
           "rules_comparison", "saved"]
