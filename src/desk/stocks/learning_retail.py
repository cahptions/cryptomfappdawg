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


def policies(conn: sqlite3.Connection, now: datetime) -> list[dict[str, Any]]:
    """Group sizing variants and repeated fills into one family/underlying-day block, then shrink the mean."""
    rows = conn.execute("SELECT o.experiment_id,o.block,AVG(o.net_return) r,MAX(o.matured_at) mature,e.config_json "
        "FROM stk_learning_outcomes o JOIN stk_learning_experiments e ON e.id=o.experiment_id "
        "JOIN stk_positions p ON p.id=o.position_id AND p.account='uncapped_research' "
        "WHERE o.matured_at<? AND o.matured_at>=? GROUP BY o.experiment_id,o.block",
        (to_iso(now),to_iso(now-timedelta(days=LOOKBACK_DAYS)))).fetchall()
    groups: dict[tuple[str,int,int],dict[str,Any]] = {}
    for row in rows:
        cfg = json.loads(row["config_json"])
        key = (cfg["structure"],cfg["hold_minutes"],cfg.get("repeat_minutes",0))
        if key not in groups:
            groups[key] = {"config":cfg,"blocks":defaultdict(list),"experiments":set(),"matured_through":""}
        group = groups[key]
        group["blocks"][row["block"]].append(row["r"])
        group["experiments"].add(row["experiment_id"])
        group["matured_through"] = max(group["matured_through"],row["mature"])
    previous = conn.execute("SELECT payload_json FROM stk_learning_checks WHERE at<? ORDER BY at DESC,id DESC LIMIT 1",
                            (to_iso(now),)).fetchone()
    model = json.loads(previous[0]).get("model",{}) if previous else {}
    out = []
    for key, group in groups.items():
        values = [mean(v) for v in group["blocks"].values()]
        expected = mean(values)*len(values)/(len(values)+10)
        evidence = {"asof":to_iso(now),"matured_through":group["matured_through"],"blocks":len(values),
            "days":len({b.split(":")[0] for b in group["blocks"]}),"expected_net_return":expected,
            "lower_bound":mean(values)-2*stdev(values)/math.sqrt(len(values)) if len(values)>1 else None,
            "experiment_ids":sorted(group["experiments"]),"model_version":model.get("version",0),
            "mode":"research_champion" if model.get("champion")==key[0] else "provisional"}
        evidence["policy_hash"] = sha256_hex(canonical_json({"rule":VERSION,"family":key,"evidence":evidence}))[:20]
        out.append({"config":group["config"],"evidence":evidence})
    if not out:
        # A transparent fixed baseline while research matures, rather than pretending an untrained model learned it.
        seed = conn.execute("SELECT id,config_json FROM stk_learning_experiments "
            "WHERE created_at<=? AND json_extract(config_json,'$.structure')='long_call' "
            "AND json_extract(config_json,'$.hold_minutes')=30 AND json_extract(config_json,'$.repeat_minutes')=30 "
            "ORDER BY created_at,id LIMIT 1",(to_iso(now),)).fetchone()
        if seed:
            out.append({"config":json.loads(seed["config_json"]),"evidence":{"asof":to_iso(now),"mode":"starter",
                "blocks":0,"days":0,"expected_net_return":None,"experiment_ids":[seed["id"]],"model_version":0,
                "policy_hash":VERSION+":starter","matured_through":None,"lower_bound":None}})
    else:
        out = [p for p in out if p["evidence"]["expected_net_return"] > 0]
    return sorted(out,key=lambda p:(p["evidence"]["mode"]!="research_champion",
                  -(p["evidence"]["expected_net_return"] or 0),p["config"]["structure"],p["config"]["hold_minutes"]))


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
                last = lab.state(conn,f"retail_entry:{account}:{cfg['structure']}:{cfg['hold_minutes']}:{symbol}")
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
        plan = {**cfg,"experiment_id":evidence["experiment_ids"][0],"spec_sha256":digest,"symbol":symbol,
            "asset":"option","underlying_price":spot,"legs":legs,"desired_units":desired,"forecast":forecast,
            "source_key":source,"transfer_policy":forecast,"comparison":"Independent finite-capital forward test",
            "target_debit":budget}
        reason = ("Untrained starter long-call policy; no learned edge claimed" if evidence["mode"]=="starter" else
                  f"Prior uncapped research: {cfg['structure']} / {cfg['hold_minutes']} minutes; "
                  f"{evidence['blocks']} underlying/day blocks, expected net return {evidence['expected_net_return']:.2%}")
        if unit>budget:
            reason += "; one whole contract exceeds the 25% sizing target but fits available cash"
        did = book.decide(now,cal.new_york(now).date(),account,digest,symbol,"ENTER",[reason],
                          {"prediction":forecast,"plan":plan})
        oid = book.place(did,account,now,cal.new_york(now).date(),plan)
        conn.execute("INSERT INTO stk_learning_intents VALUES (?,?,?,?,?)",
                     (oid,plan["experiment_id"],source,desired,canonical_json(forecast)))
        lab.set_state(conn,f"retail_entry:{account}:{cfg['structure']}:{cfg['hold_minutes']}:{symbol}",to_iso(now))
        lab.set_state(conn,prefix,{"phase":"Queued an affordable trade; waiting for a later fresh quote",
            "mode":evidence["mode"],"decision_at":to_iso(now),"symbol":symbol,"structure":cfg["structure"],
            "desired_units":desired,"estimated_entry_cost":amount(lab.impact(legs,quotes,desired,True)),
            "forecast":forecast,"note":reason})
        count += 1
    return count
