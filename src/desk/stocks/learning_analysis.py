"""Why one Learning book reads better than another. Read-only.

The research account and the $500,000 reference receive the same order intents, each with its own estimated
liquidity; only their cash differs (the research account was topped up automatically until it was capped on
2026-10-30). So the gap between them splits cleanly into trades both made and trades only the research account could
afford. The rest of the report says what the research account trades, which experiments made or lost the money, how
concentrated that is, and whether its per-day average is distinguishable from zero.
"""

from __future__ import annotations

import json
import math
import sqlite3
from collections import defaultdict
from datetime import date, datetime, timedelta
from statistics import fmean, median, stdev
from typing import Any

from desk.stocks import calendar as cal
from desk.stocks.learning import ACCOUNTS, BUCKETS, REFERENCE, RESEARCH, STRUCTURES, state
from desk.stocks.paper.book import MICRO, PaperBook


def _positions(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Every Learning position with its P/L: realized when closed, marked (before exit fees) while open."""
    out = []
    for r in conn.execute("SELECT * FROM stk_positions"):
        plan = json.loads(r["plan_json"])
        cost = (r["entry_cost_micros"] + r["entry_fees_micros"]) / MICRO
        if r["state"] == "CLOSED":
            pnl = (r["exit_credit_micros"] - (r["exit_fees_micros"] or 0)) / MICRO - cost
        else:
            pnl = (r["last_value_micros"] or 0) / MICRO - cost
        out.append({"id": r["id"], "account": r["account"], "symbol": r["symbol"], "structure": r["structure"],
                    "experiment": plan.get("experiment_id"), "hold_minutes": plan.get("hold_minutes"),
                    "target": plan.get("target_debit"), "closed": r["state"] == "CLOSED", "cost": cost, "pnl": pnl,
                    "net_return": pnl / cost if cost > 0 else 0.0, "day": r["entry_day"],
                    "opened_at": r["opened_at"], "closed_at": r["closed_at"]})
    return out


def _book(rows: list[dict[str, Any]]) -> dict[str, Any]:
    closed = [r for r in rows if r["closed"]]
    return {"trades": len(closed), "open": len(rows) - len(closed),
            "wins": sum(r["pnl"] > 0 for r in closed),
            "win_rate": sum(r["pnl"] > 0 for r in closed) / len(closed) if closed else None,
            "mean_return": fmean(r["net_return"] for r in closed) if closed else None,
            "realized": sum(r["pnl"] for r in closed), "unrealized": sum(r["pnl"] for r in rows if not r["closed"]),
            "deployed": sum(r["cost"] for r in rows)}


def _groups(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    grouped: dict[Any, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        grouped[r[key]].append(r)
    out = [{"key": k, **_book(v), "pnl": sum(r["pnl"] for r in v)} for k, v in grouped.items()]
    return sorted(out, key=lambda g: -g["pnl"])


def _daily_t(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Closed trades' net returns averaged per entry day (one number a day, so a busy day counts once)."""
    by_day: dict[int, list[float]] = defaultdict(list)
    for r in rows:
        if r["closed"]:
            by_day[r["day"]].append(r["net_return"])
    days = [fmean(v) for v in by_day.values()]
    if len(days) < 3:
        return {"days": len(days), "mean": fmean(days) if days else None, "t": None}
    sd = stdev(days)
    return {"days": len(days), "mean": fmean(days), "t": None if sd == 0 else fmean(days) / (sd / math.sqrt(len(days)))}


def _holding(rows: list[dict[str, Any]], now: datetime) -> dict[str, Any]:
    """Did "held 2 h" mean two hours? Closed trades' actual holds, and open ones already past their planned exit,
    split by whether they were opened too late in the day to finish before the close."""
    held: dict[int, list[float]] = defaultdict(list)
    overdue = late = 0
    for r in rows:
        plan = r["hold_minutes"]
        if not plan:
            continue
        opened = datetime.fromisoformat(r["opened_at"])
        if r["closed"] and r["closed_at"]:
            held[plan].append((datetime.fromisoformat(r["closed_at"]) - opened).total_seconds() / 60)
        elif now - opened > timedelta(minutes=plan):
            overdue += 1
            late += opened + timedelta(minutes=plan) > cal.session_close(cal.new_york(opened).date())
    return {"planned": {plan: {"trades": len(v), "median_minutes": median(v)} for plan, v in sorted(held.items())},
            "open_past_plan": overdue, "opened_too_late": late}


def lifecycle(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Does holding longer pay? For each structure and expiry bucket, every checkpoint's average return and win rate
    over the research account's positions that lived that long, and the final sale's."""
    recipes = []
    for e in conn.execute("SELECT id, config_json FROM stk_learning_experiments "
                          "WHERE json_extract(config_json,'$.kind')='lifecycle' ORDER BY id"):
        cfg = json.loads(e["config_json"])
        steps = []
        for days in cfg["checkpoints"]:
            rows = [r[0] for r in conn.execute(
                "SELECT c.net_return FROM stk_learning_checkpoints c JOIN stk_positions p ON p.id=c.position_id "
                "WHERE p.account=? AND json_extract(p.plan_json,'$.experiment_id')=? AND c.days=?",
                (RESEARCH, e["id"], days))]
            steps.append({"days": days, "n": len(rows), "mean": fmean(rows) if rows else None,
                          "win_rate": sum(r > 0 for r in rows) / len(rows) if rows else None})
        final = [r[0] for r in conn.execute(
            "SELECT o.net_return FROM stk_learning_outcomes o JOIN stk_positions p ON p.id=o.position_id "
            "WHERE p.account=? AND o.experiment_id=?", (RESEARCH, e["id"]))]
        open_ = conn.execute("SELECT COUNT(*) FROM stk_positions WHERE account=? AND state='OPEN' AND "
                             "json_extract(plan_json,'$.experiment_id')=?", (RESEARCH, e["id"])).fetchone()[0]
        ready = [s for s in steps if s["n"] >= 10 and s["mean"] is not None]
        recipes.append({"id": e["id"], "structure": cfg["structure"], "bucket": cfg["bucket"], "label": cfg["label"],
                        "checkpoints": steps, "open": open_,
                        "final": {"n": len(final), "mean": fmean(final) if final else None,
                                  "win_rate": sum(r > 0 for r in final) / len(final) if final else None},
                        "best_so_far": max(ready, key=lambda s: s["mean"])["days"] if ready else None})
    order = list(BUCKETS)
    return sorted(recipes, key=lambda r: (order.index(r["bucket"]), STRUCTURES.index(r["structure"])))


def _pairs(conn: sqlite3.Connection, pnl: dict[str, float]) -> dict[str, Any]:
    """The same intent in both books: research and reference orders share an experiment and a source quote."""
    orders: dict[tuple[str, str], dict[str, dict[str, Any]]] = defaultdict(dict)
    for r in conn.execute(
            "SELECT i.experiment_id,i.source_key,p.account,p.note,COALESCE(SUM(f.units),0) units,"
            "GROUP_CONCAT(f.position_id) positions FROM stk_learning_intents i JOIN stk_pending p ON p.id=i.order_id "
            "LEFT JOIN stk_learning_fills f ON f.order_id=i.order_id WHERE p.account IN (?,?) GROUP BY i.order_id",
            (RESEARCH, REFERENCE)):
        ids = (r["positions"] or "").split(",") if r["positions"] else []
        orders[(r["experiment_id"], r["source_key"])][r["account"]] = {
            "units": r["units"], "pnl": sum(pnl.get(i, 0.0) for i in ids), "note": r["note"] or ""}
    buckets = {k: {"intents": 0, "research_pnl": 0.0, "reference_pnl": 0.0} for k in ("same", "smaller", "skipped")}
    short_of_cash = 0
    for pair in orders.values():
        research, reference = pair.get(RESEARCH), pair.get(REFERENCE)
        if not research or research["units"] <= 0 or not reference:
            continue
        kind = ("same" if reference["units"] >= research["units"] else "smaller" if reference["units"] > 0
                else "skipped")
        buckets[kind]["intents"] += 1
        buckets[kind]["research_pnl"] += research["pnl"]
        buckets[kind]["reference_pnl"] += reference["pnl"]
        short_of_cash += "insufficient cash" in reference["note"] or "buying power" in reference["note"]
    gap = sum(b["research_pnl"] - b["reference_pnl"] for b in buckets.values())
    return {"buckets": buckets, "gap": gap, "reference_short_of_cash": short_of_cash}


def analyse(conn: sqlite3.Connection, now: datetime) -> dict[str, Any]:
    book = PaperBook(conn)
    rows = _positions(conn)
    accounts = []
    for account, title, _ in ACCOUNTS:
        mine = [r for r in rows if r["account"] == account]
        contributed = book.deposits(account)
        equity = book.cash(account) + sum(r["cost"] + r["pnl"] for r in mine if not r["closed"])
        accounts.append({"id": account, "title": title, "contributed": contributed, "equity": equity,
                         "pnl": equity - contributed, "return": (equity - contributed) / contributed if contributed
                         else None, **_book(mine)})
    research = [r for r in rows if r["account"] == RESEARCH]
    experiments = {r["id"]: json.loads(r["config_json"]) for r in conn.execute("SELECT * FROM stk_learning_experiments")}
    for r in research:
        cfg = experiments.get(r["experiment"] or "", {})
        r["recipe"] = (f"{cfg.get('structure', r['structure']).replace('_', ' ')}, " + (
            cfg["label"] if cfg.get("label") else
            f"${cfg.get('target_debit', 0):,.0f} a trade, held {cfg.get('hold_minutes', 0) / 60:g} h")
            if cfg else r["structure"])
    closed = sorted((r for r in research if r["closed"]), key=lambda r: -r["pnl"])
    realized = sum(r["pnl"] for r in closed)
    top = closed[:5]
    deposits = [dict(r) for r in conn.execute(
        "SELECT at, amount_micros FROM stk_cash WHERE account=? AND reason='DEPOSIT' ORDER BY id", (RESEARCH,))]
    cap = state(conn, "research_cap")
    since_cap = None
    if cap:
        before = conn.execute("SELECT pnl FROM stk_learning_nav WHERE account=? AND at<=? ORDER BY at DESC LIMIT 1",
                              (RESEARCH, cap["at"])).fetchone()
        after = next(a for a in accounts if a["id"] == RESEARCH)["pnl"]
        since_cap = None if before is None else after - before["pnl"]
    return {
        "asof": now.isoformat(), "accounts": accounts,
        "recipes": _groups(research, "recipe"), "structures": _groups(research, "structure"),
        "symbols": _groups(research, "symbol"),
        "concentration": {"realized": realized, "top5": sum(r["pnl"] for r in top),
                          "top5_trades": [{"symbol": r["symbol"], "pnl": r["pnl"], "recipe": r["recipe"],
                                           "day": date.fromordinal(r["day"]).isoformat()} for r in top],
                          "without_top5": realized - sum(r["pnl"] for r in top)},
        "daily": _daily_t(research),
        "days": sorted(({"day": date.fromordinal(g["key"]).isoformat(), "trades": g["trades"], "open": g["open"],
                         "pnl": g["pnl"]} for g in _groups(research, "day")), key=lambda d: d["day"]),
        "holding": _holding(research, now),
        "pairs": _pairs(conn, {r["id"]: r["pnl"] for r in rows}),
        "lifecycle": lifecycle(conn),
        "funding": {"deposits": len(deposits), "deposited": sum(d["amount_micros"] for d in deposits) / MICRO,
                    "cap": cap, "pnl_since_cap": since_cap},
    }


def _money(x: float | None) -> str:
    return "—" if x is None else f"{'−' if x < 0 else ''}${abs(x):,.0f}"


def _pct(x: float | None, digits: int = 1) -> str:
    return "—" if x is None else f"{x:+.{digits}%}"


def _share(x: float | None) -> str:
    return "—" if x is None else f"{x:.0%}"


def render(a: dict[str, Any]) -> str:
    out = [f"KESTREL Learning · what the research account is doing · {a['asof'][:16]}", "", "Books (like for like):"]
    for b in a["accounts"]:
        out.append(f"  {b['title']:<20} P/L {_money(b['pnl']):>12} on {_money(b['contributed']):>12} put in "
                   f"({_pct(b['return'])}) · {b['trades']} closed, {b['open']} open · wins "
                   f"{_share(b['win_rate'])} · avg trade {_pct(b['mean_return'])}")
    p = a["pairs"]
    out += ["", "Research against the $500,000 reference, on the same orders:"]
    labels = {"same": "both bought the full size", "smaller": "reference bought fewer (cash)",
              "skipped": "reference could not buy any"}
    for k, b in p["buckets"].items():
        out.append(f"  {labels[k]:<32} {b['intents']:>5} orders · research {_money(b['research_pnl']):>11} · "
                   f"reference {_money(b['reference_pnl']):>11}")
    gap = p["gap"]
    extra = (p["buckets"]["smaller"]["research_pnl"] - p["buckets"]["smaller"]["reference_pnl"]
             + p["buckets"]["skipped"]["research_pnl"])
    out.append(f"  Gap {_money(gap)}; of it {_money(extra)} comes from size the reference could not afford "
               f"({p['reference_short_of_cash']} reference orders cut short of cash).")
    out += ["", "Research account by structure: " + " · ".join(
        f"{g['key'].replace('_', ' ')} {_money(g['pnl'])} ({g['trades']} closed, {g['open']} open, wins "
        f"{_share(g['win_rate'])})" for g in a["structures"])]
    out += ["", "Research account by recipe (structure, size, holding time), best to worst:"]
    for g in a["recipes"]:
        out.append(f"  {g['key']:<42} {g['trades']:>5} closed · wins "
                   f"{_share(g['win_rate'])} · avg {_pct(g['mean_return'])} · "
                   f"P/L {_money(g['pnl'])}")
    c = a["concentration"]
    out += ["", f"Concentration: best 5 trades {_money(c['top5'])} of {_money(c['realized'])} realized; without "
                f"them {_money(c['without_top5'])}."]
    for t in c["top5_trades"]:
        out.append(f"  {t['day']} {t['symbol']:<6} {_money(t['pnl']):>10} · {t['recipe']}")
    sym = a["symbols"]
    if 0 < len(sym) <= 5:
        out.append("  Stocks: " + ", ".join(f"{g['key']} {_money(g['pnl'])}" for g in sym))
    elif sym:
        out.append("  Best stocks: " + ", ".join(f"{g['key']} {_money(g['pnl'])}" for g in sym[:5]))
        out.append("  Worst stocks: " + ", ".join(f"{g['key']} {_money(g['pnl'])}" for g in sym[-5:][::-1]))
    out += ["", "By entry day: " + " · ".join(f"{d['day']} {_money(d['pnl'])} ({d['trades']} closed, {d['open']} open)"
                                               for d in a["days"])]
    h = a["holding"]
    out.append("Holding: " + " · ".join(f"planned {plan / 60:g} h → median {v['median_minutes'] / 60:.1f} h over "
                                         f"{v['trades']} closed" for plan, v in h["planned"].items())
               + f" · {h['open_past_plan']} open past their planned exit, {h['opened_too_late']} of them opened too "
                 "late to finish before that day's close")
    life = a.get("lifecycle") or []
    if life:
        out += ["", "Does holding longer pay? Average return if sold after … (wins; positions that lived that long):"]
        for r in life:
            cells = " · ".join(f"{s['days']}d {_pct(s['mean'], 0)} ({_share(s['win_rate'])}; {s['n']})"
                               for s in r["checkpoints"] if s["n"])
            best = f" · best so far: {r['best_so_far']} days" if r["best_so_far"] else ""
            out.append(f"  {r['structure'].replace('_', ' ')}, {r['label']}: {cells or 'no checkpoint yet'} · "
                       f"{r['final']['n']} sold, {r['open']} open{best}")
    d = a["daily"]
    out += ["", f"Per entry day: {d['days']} days, average trade {_pct(d['mean'])}"
                + ("" if d["t"] is None else f", t = {d['t']:.2f} "
                   f"({'distinguishable from zero' if abs(d['t']) >= 2 else 'not yet distinguishable from luck'})")]
    f = a["funding"]
    out += [f"Funding: {f['deposits']} deposits, {_money(f['deposited'])} put in"
            + (f"; capped {f['cap']['at'][:10]} at {_money(f['cap']['equity'])}" if f["cap"] else "")
            + (f"; P/L since the cap {_money(f['pnl_since_cap'])}" if f["pnl_since_cap"] is not None else "")]
    return "\n".join(out)


__all__ = ["analyse", "render"]
