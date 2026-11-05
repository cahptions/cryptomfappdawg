"""Finite $1k/$5k forward PAPER tests of findings from uncapped research.

Own decisions, quantities, execution and cash. Only research outcomes matured BEFORE a decision can select its
policy. No top-ups, scaled research P/L, fractional option contracts, retroactive trades or new broker methods.
"""

from __future__ import annotations

import json
import math
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta
from statistics import mean, stdev
from typing import Any

from desk.core.clock import to_iso
from desk.core.hashing import canonical_json, sha256_hex
from desk.stocks import calendar as cal
from desk.stocks import learning as lab
from desk.stocks.contracts import OptionQuote
from desk.stocks.paper.book import MICRO, PaperBook, to_micros
from desk.stocks.paper.strategies import build_structure

VERSION = "small-account-transfer-v1"
TARGET_FRACTION = .25
LOOKBACK_DAYS = 90
MIN_CHECKPOINT = 10     # positions valued at a checkpoint before it can name a holding time (as the Learning page does)


Checkpoints = dict[tuple[str, int], tuple[float, int, str]]   # (experiment, days) -> (sum of returns, count, latest)


def checkpoint_stats(conn: sqlite3.Connection, now: datetime) -> Checkpoints:
    """Research checkpoints valued in the previous 90 days and strictly before ``now``, per experiment and day, in one
    pass: only checkpoints that matured before a decision can choose its holding time."""
    return {(r[0], r[1]): (r[2], r[3], r[4]) for r in conn.execute(
        "SELECT json_extract(p.plan_json,'$.experiment_id') e,c.days,SUM(c.net_return),COUNT(*),MAX(c.at) "
        "FROM stk_learning_checkpoints c JOIN stk_positions p ON p.id=c.position_id "
        "WHERE p.account=? AND c.at<? AND c.at>=? GROUP BY e,c.days",
        (lab.RESEARCH, to_iso(now), to_iso(now - timedelta(days=LOOKBACK_DAYS))))}


def best_holding(stats: Checkpoints, experiment_ids: list[str]) -> dict[str, Any] | None:
    """The research checkpoint with the highest average return, among those with at least ``MIN_CHECKPOINT`` positions
    of these experiments: the holding time the small accounts sell at. Checkpoints compare the positions that lived that
    long, so a longer one can rest on fewer, surviving positions; ties go to the shorter hold."""
    days: dict[int, list[Any]] = {}
    for (experiment, day), (total, n, latest) in stats.items():
        if experiment in experiment_ids:
            seen = days.setdefault(day, [0.0, 0, ""])
            seen[0], seen[1], seen[2] = seen[0] + total, seen[1] + n, max(seen[2], latest)
    ready = [(total / n, -day, day, n, latest) for day, (total, n, latest) in days.items() if n >= MIN_CHECKPOINT]
    if not ready:
        return None
    mean_return, _, day, n, latest = max(ready)
    return {"days": day, "mean_net_return": mean_return, "positions": n, "valued_through": latest}


def holding(stats: Checkpoints, cfg: dict[str, Any], experiment_ids: list[str]) -> dict[str, Any]:
    """When a small account sells a lifecycle recipe's position: at its best checkpoint so far, else the full horizon."""
    if cfg.get("kind") != "lifecycle":
        return {"exit_days": None, "best_checkpoint": None, "exit_basis": "The recipe's registered holding time"}
    horizon = cfg["hold_minutes"] // 1440
    best = best_holding(stats, experiment_ids)
    if best is None:
        return {"exit_days": horizon, "best_checkpoint": None,
                "exit_basis": f"Recipe horizon of {horizon} days: no checkpoint has {MIN_CHECKPOINT} research positions "
                              "valued before this decision yet"}
    return {"exit_days": best["days"], "best_checkpoint": best,
            "exit_basis": f"Best research checkpoint so far: {best['days']} days, average {best['mean_net_return']:+.1%} "
                          f"over {best['positions']} positions valued before this decision"}


def policies(conn: sqlite3.Connection, now: datetime) -> list[dict[str, Any]]:
    """Group sizing variants and repeated fills into one family/underlying-day block, then shrink the mean. A family is
    structure, expiry bucket, holding time and entry spacing (``learning.family``), so a lifecycle family's config, and
    the expiry window (``dte``) a small account buys from, belong to every experiment in it."""
    rows = conn.execute("SELECT o.experiment_id,o.block,AVG(o.net_return) r,MAX(o.matured_at) mature,e.config_json "
        "FROM stk_learning_outcomes o JOIN stk_learning_experiments e ON e.id=o.experiment_id "
        "JOIN stk_positions p ON p.id=o.position_id AND p.account='uncapped_research' "
        "WHERE o.matured_at<? AND o.matured_at>=? GROUP BY o.experiment_id,o.block",
        (to_iso(now),to_iso(now-timedelta(days=LOOKBACK_DAYS)))).fetchall()
    groups: dict[tuple[str,str | None,int,int],dict[str,Any]] = {}
    gone = lab.retired(conn)                    # retired minute-hold recipes are history, not something to copy
    for row in rows:
        if row["experiment_id"] in gone:
            continue
        cfg = json.loads(row["config_json"])
        key = lab.family(cfg)
        if key not in groups:
            groups[key] = {"config":cfg,"blocks":defaultdict(list),"experiments":set(),"matured_through":""}
        group = groups[key]
        group["blocks"][row["block"]].append(row["r"])
        group["experiments"].add(row["experiment_id"])
        group["matured_through"] = max(group["matured_through"],row["mature"])
    previous = conn.execute("SELECT payload_json FROM stk_learning_checks WHERE at<? ORDER BY at DESC,id DESC LIMIT 1",
                            (to_iso(now),)).fetchone()
    model = json.loads(previous[0]).get("model",{}) if previous else {}
    stats = checkpoint_stats(conn,now)
    out = []
    for key, group in groups.items():
        values = [mean(v) for v in group["blocks"].values()]
        expected = mean(values)*len(values)/(len(values)+10)
        evidence = {"asof":to_iso(now),"matured_through":group["matured_through"],"blocks":len(values),
            "days":len({b.split(":")[0] for b in group["blocks"]}),"expected_net_return":expected,
            "lower_bound":mean(values)-2*stdev(values)/math.sqrt(len(values)) if len(values)>1 else None,
            "experiment_ids":sorted(group["experiments"]),"model_version":model.get("version",0),
            "mode":"research_champion" if model.get("champion")==key[0] else "provisional",
            **holding(stats,group["config"],sorted(group["experiments"]))}
        evidence["policy_hash"] = sha256_hex(canonical_json({"rule":VERSION,"family":key,"evidence":evidence}))[:20]
        out.append({"config":group["config"],"evidence":evidence})
    if not out:
        # A transparent fixed baseline while research matures, rather than pretending an untrained model learned it.
        seed = conn.execute("SELECT id,config_json FROM stk_learning_experiments "
            "WHERE created_at<=? AND json_extract(config_json,'$.structure')='long_call' "
            "AND json_extract(config_json,'$.bucket')='1m' ORDER BY created_at,id LIMIT 1",(to_iso(now),)).fetchone()
        if seed:
            cfg = json.loads(seed["config_json"])
            exit_ = holding(stats,cfg,[seed["id"]])
            out.append({"config":cfg,"evidence":{"asof":to_iso(now),"mode":"starter",
                "blocks":0,"days":0,"expected_net_return":None,"experiment_ids":[seed["id"]],"model_version":0,
                "policy_hash":VERSION+":starter"+(f":sell-after-{exit_['exit_days']}d" if exit_["best_checkpoint"]
                                                  else ""),
                "matured_through":None,"lower_bound":None,**exit_}})
    else:
        out = [p for p in out if p["evidence"]["expected_net_return"] > 0]
    return sorted(out,key=lambda p:(p["evidence"]["mode"]!="research_champion",
                  -(p["evidence"]["expected_net_return"] or 0),p["config"]["structure"],p["config"]["hold_minutes"]))


def spacing_key(account: str, cfg: dict[str, Any], symbol: str) -> str:
    bucket = f":{cfg['bucket']}" if cfg.get("bucket") else ""
    return f"retail_entry:{account}:{cfg['structure']}:{cfg['hold_minutes']}{bucket}:{symbol}"


def amount(fill: Any) -> float:
    return (to_micros(fill.debit,cost=True)+to_micros(fill.fees,cost=True))/MICRO


def spread(q: OptionQuote) -> float:
    return (q.ask-q.bid)/q.mid if q.ask is not None and q.bid is not None and q.mid else math.inf


def propose(conn: sqlite3.Connection, now: datetime, quotes: dict[str,OptionQuote],
            snapshots: dict[str,tuple[str,float,dict[Any,list[OptionQuote]]]]) -> int:
    book = PaperBook(conn)
    choices = policies(conn,now)
    count = 0
    cache: dict[tuple[Any,...],list[dict[str,Any]] | None] = {}
    for account in lab.SMALL_ACCOUNTS:
        prefix = "retail:"+account
        if conn.execute("SELECT 1 FROM stk_pending WHERE account=? AND state='PENDING' LIMIT 1",(account,)).fetchone():
            lab.set_state(conn,prefix,{**lab.state(conn,prefix,{}),"phase":"Waiting for a later fresh entry quote"})
            continue
        cash = book.cash(account)
        marked = conn.execute("SELECT COALESCE(SUM(last_value_micros),0) FROM stk_positions WHERE account=? AND state='OPEN'",
                              (account,)).fetchone()[0]/MICRO
        budget = min(cash,max(0,cash+marked)*TARGET_FRACTION)
        if not snapshots:
            lab.set_state(conn,prefix,{**lab.state(conn,prefix,{}),"phase":"Waiting for fresh option quotes",
                                      "budget":budget,"cash":cash})
            continue
        if not choices:
            lab.set_state(conn,prefix,{"phase":"Research results are not positive; holding cash","mode":"waiting",
                                      "asof":to_iso(now),"note":"No positive prior research family is available."})
            continue
        held = {leg["occ"] for position in book.positions(account) for leg in position.legs}
        candidates = []
        for policy_index, policy in enumerate(choices):
            cfg,evidence = policy["config"],policy["evidence"]
            for symbol,(source,spot,chains) in sorted(snapshots.items()):
                # Spacing per family; a lifecycle family's key names its bucket (the older key is still honoured).
                last = max(filter(None,(lab.state(conn,spacing_key(account,cfg,symbol)),
                                        lab.state(conn,f"retail_entry:{account}:{cfg['structure']}:{cfg['hold_minutes']}:{symbol}"))),
                           default=None)
                if last and now-datetime.fromisoformat(last)<timedelta(minutes=cfg.get("repeat_minutes",0)):
                    continue
                for expiry in sorted(chains):
                    if not cfg["dte"][0] <= (expiry-cal.new_york(now).date()).days <= cfg["dte"][1]:
                        continue
                    for target_delta in ((.5,) if cfg["structure"]=="long_straddle" else (.5,.4,.3)):
                        key = (symbol,cfg["structure"],expiry,target_delta)
                        if key not in cache:
                            cache[key] = build_structure(cfg["structure"],{expiry:chains[expiry]},spot,
                                cal.new_york(now).date(),.04,{**cfg,"delta":target_delta},{})
                        legs = cache[key]
                        if (not legs or any(leg["occ"] in held or not lab.fresh(quotes[leg["occ"]],now)
                                            or not .2 <= abs(leg["entry_delta"]) <= .75 for leg in legs)):
                            continue
                        unit = amount(lab.impact(legs,quotes,1,True))
                        if unit > cash or unit <= 0:
                            continue
                        # Prefer contracts within the target; a whole-contract floor may exceed it, never cash.
                        concentrated = unit > budget
                        score = (concentrated,policy_index,abs(target_delta-.5),
                                 max(spread(quotes[leg["occ"]]) for leg in legs),
                                 unit,symbol,expiry)
                        candidates.append((score,policy,symbol,source,spot,legs,unit))
        if not candidates:
            lab.set_state(conn,prefix,{"phase":"Waiting for an affordable, liquid contract or available cash",
                "mode":choices[0]["evidence"]["mode"],"budget":budget,"cash":cash,"asof":to_iso(now)})
            continue
        _score,policy,symbol,source,spot,legs,unit = min(candidates,key=lambda candidate:candidate[0])
        cfg,evidence = policy["config"],policy["evidence"]
        desired = max(1,int(budget//unit))
        if unit <= budget:
            low,high = 1,desired
            while low<high:
                middle=(low+high+1)//2
                if amount(lab.impact(legs,quotes,middle,True)) <= budget:
                    low=middle
                else:
                    high=middle-1
            desired=low
        forecast = {**evidence,"version":VERSION,"target_fraction":TARGET_FRACTION,"decision_equity":cash+marked,
                    "decision_cash":cash,"target_budget":budget,"single_contract_exceeds_target":unit>budget}
        digest = sha256_hex(canonical_json({"rule":VERSION,"config":cfg,"forecast":forecast}))
        exit_days = evidence.get("exit_days")
        plan = {**cfg,"experiment_id":evidence["experiment_ids"][0],"spec_sha256":digest,"symbol":symbol,
            "asset":"option","underlying_price":spot,"legs":legs,"desired_units":desired,"forecast":forecast,
            "source_key":source,"transfer_policy":forecast,"comparison":"Independent finite-capital forward test",
            "target_debit":budget,**({"hold_minutes":exit_days*1440,"recipe_hold_minutes":cfg["hold_minutes"]}
                                     if exit_days else {})}
        reason = ("Untrained starter long-call policy; no learned edge claimed" if evidence["mode"]=="starter" else
                  f"Prior research: {cfg['structure']} / {cfg.get('label') or str(cfg['hold_minutes']) + ' minutes'}; "
                  f"{evidence['blocks']} underlying/day blocks, expected net return {evidence['expected_net_return']:.2%}")
        if exit_days:
            reason += f"; sells after {exit_days} days ({evidence['exit_basis']})"
        if unit>budget:
            reason += "; one whole contract exceeds the 25% sizing target but fits available cash"
        did = book.decide(now,cal.new_york(now).date(),account,digest,symbol,"ENTER",[reason],
                          {"prediction":forecast,"plan":plan})
        oid = book.place(did,account,now,cal.new_york(now).date(),plan)
        conn.execute("INSERT INTO stk_learning_intents VALUES (?,?,?,?,?)",
                     (oid,plan["experiment_id"],source,desired,canonical_json(forecast)))
        lab.set_state(conn,spacing_key(account,cfg,symbol),to_iso(now))
        lab.set_state(conn,prefix,{"phase":"Queued an affordable trade; waiting for a later fresh quote",
            "mode":evidence["mode"],"decision_at":to_iso(now),"symbol":symbol,"structure":cfg["structure"],
            "desired_units":desired,"estimated_entry_cost":amount(lab.impact(legs,quotes,desired,True)),
            "forecast":forecast,"note":reason,"exit_days":exit_days,"exit_basis":evidence.get("exit_basis")})
        count += 1
    return count
