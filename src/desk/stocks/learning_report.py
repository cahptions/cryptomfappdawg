"""The Learning report: everything KESTREL's learning loops know, in plain sentences with real numbers
(docs/STOCKS_LEARNING.md → Learning report).

It reads the Plays grades and calibration, the Plays learning report (exit rules, inputs, challengers, labels), the
Learning workspace book, the PAPER report, the Evidence evaluation, the replays and the jobs' health, and writes one
report: a headline, what it has learned, how it behaves, what it gets wrong, achievements, surprises, what is coming
up, what changed since last week's snapshot, and the replays kept apart.

Honesty rules, enforced here:

* every finding carries its evidence (items and distinct sessions or days) and a strength: **early** (below the loop's
  own threshold, or seen on fewer than ``MIN_DAYS`` separate days whatever its count), **suggestive**, or **tested**
  (only where that loop's own significance test or grade already says so);
* estimated and modelled marks are never outcomes (only graded results, quoted marks and settlements are read);
* replays are labelled and kept in their own section, never mixed with forward results;
* read-only: every database is opened read-only; nothing is traded, graded, adopted, fetched or asked of an LLM.

Queries are bounded: aggregates run in SQLite, row reads are limited, and per-play reads cover the last 90 days.
The weekly snapshot (``weekly``) is written by the collection worker at most once a week, never on a GET.
"""

from __future__ import annotations

import json
import math
import sqlite3
from collections import Counter, defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from desk.core.clock import to_iso, utc_now
from desk.core.paths import DeskPaths
from desk.stocks import calendar as cal
from desk.stocks import odds as od

VERSION = 1
MIN_DAYS = 10            # a pattern seen on fewer separate days than this is "early", whatever its count
RECENT_DAYS = 90         # behaviour and data health read the last 90 days
PACE_DAYS = 28           # "about N weeks" estimates use the last four weeks' pace
TABLE = "stk_learning_reports"
SECTIONS = (("learned", "What it has learned"), ("behaviour", "How it behaves"),
            ("wrong", "What it's getting wrong"), ("achievements", "Achievements"),
            ("surprises", "Surprises — worth a look"), ("coming", "Coming up"),
            ("changed", "Changed since last week"), ("replay", "Replays on past data — not forward results"))
STRENGTHS = ("early", "suggestive", "tested")
EVENTS = {  # event: (the chance of …, what happened)
    "itm": ("finishing in the money", "finished in the money"),
    "move": ("moving past break-even", "moved past break-even"),
    "take_profit": ("reaching the take-profit first", "reached the take-profit first"),
    "profit": ("a profit at expiry", "made a profit at expiry"),
    "target25": ("+25% at expiry", "made +25% at expiry"),
    "target50": ("+50% at expiry", "made +50% at expiry"),
    "target100": ("+100% at expiry", "made +100% at expiry"),
    "max_gain": ("the maximum gain", "made the maximum gain"),
    "touch": ("touching the strike", "touched the strike"),
}
MILESTONES = (100, 250, 500, 1_000, 2_500, 5_000, 10_000, 25_000, 50_000, 100_000, 250_000, 1_000_000)
PLAYS, PERFORMANCE, LEARNING, PAPER, PROOF, SYSTEM = (
    "#plays", "#plays/performance", "#learning", "#paper", "#proof", "#system")


# ── formatting ───────────────────────────────────────────────────────────────────────────────────
def pct(x: float | None, digits: int = 0) -> str:
    return "—" if x is None else f"{x:.{digits}%}"


def signed(x: float | None, digits: int = 0) -> str:
    return "—" if x is None else f"{'+' if x > 0 else '−' if x < 0 else ''}{abs(x):.{digits}%}"


def money(x: float | None) -> str:
    return "—" if x is None else f"{'+' if x > 0 else '−' if x < 0 else ''}${abs(x):,.0f}"


def points(x: float) -> str:
    n = round(abs(x) * 100)
    return f"{n} point{'s' if n != 1 else ''}"


def first_upper(text: str) -> str:
    return text[:1].upper() + text[1:]


def count(n: int, noun: str) -> str:
    return f"{n:,} {noun if n == 1 else noun + 's'}"


def short(day: date | str | int) -> str:
    d = date.fromordinal(day) if isinstance(day, int) else date.fromisoformat(day[:10]) if isinstance(day, str) else day
    return f"{d:%b} {d.day}, {d.year}"


def weeks(n: float | None) -> str:
    if n is None:
        return "no new results in the last four weeks, so no date yet"
    if n <= 1:
        return "about a week" if n > 0.5 else "within a week"
    return f"about {math.ceil(n)} weeks"


def strength(items: int, days: int, *, need: int, need_days: int = MIN_DAYS, tested: bool = False) -> str:
    """``tested`` only when the loop's own test or grade says so; otherwise early below the loop's own threshold or
    the report's floor of separate days (30 plays from 2 sessions are early), else suggestive."""
    if tested:
        return "tested"
    return "early" if items < need or days < need_days else "suggestive"


def section_title(category: str) -> str:
    from desk.stocks.plays import BY_CATEGORY, HORIZONS

    base, _, band = category.partition("@")
    title = BY_CATEGORY[base].title if base in BY_CATEGORY else base.replace("_", " ").capitalize()
    bands = {b: label for b, label, _, _ in HORIZONS}
    return f"{title} in the {bands.get(band, band)} band" if band else title


# ── the report being assembled ───────────────────────────────────────────────────────────────────
@dataclass
class Sources:
    """Read-only connections (None when that database does not exist yet) and the saved views."""
    research: sqlite3.Connection | None = None
    market: sqlite3.Connection | None = None
    learning: sqlite3.Connection | None = None
    views: dict[str, Any] = field(default_factory=dict)


@dataclass
class Report:
    now: datetime
    items: list[dict[str, Any]] = field(default_factory=list)
    metrics: dict[str, dict[str, Any]] = field(default_factory=dict)
    notes: dict[str, Any] = field(default_factory=dict)          # facts one loop leaves for another

    def add(self, section: str, headline: str, detail: str, *, items: int, days: int, strength: str, link: str,
            unit: str = "plays", day_unit: str = "sessions", key: str | None = None, weight: float = 1.0) -> None:
        assert section in dict(SECTIONS) and strength in STRENGTHS
        self.items.append({"section": section, "headline": headline, "detail": detail,
                           "evidence": {"items": int(items), "days": int(days), "unit": unit, "day_unit": day_unit},
                           "strength": strength, "link": link, "key": key or headline, "weight": weight})

    def wait(self, loop: str, text: str, link: str) -> None:
        """A loop with nothing to report yet says what it is waiting for."""
        self.add("coming", f"{loop}: waiting.", text, items=0, days=0, strength="early", link=link,
                 key=f"wait:{loop}", weight=0.1)

    def metric(self, key: str, label: str, value: Any, *, items: int = 0, days: int = 0, strength: str = "early",
               unit: str = "plays", day_unit: str = "sessions") -> None:
        self.metrics[key] = {"label": label, "value": value, "items": items, "days": days, "strength": strength,
                             "unit": unit, "day_unit": day_unit}


def _rows(conn: sqlite3.Connection, sql: str, args: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
    return conn.execute(sql, args).fetchall()


def _has(conn: sqlite3.Connection | None, table: str) -> bool:
    return conn is not None and conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


# ── 1. Plays odds: calibration of every graded chance ────────────────────────────────────────────
def _dte_case(column: str) -> str:
    from desk.stocks.plays import CALIBRATION_DTE

    return "CASE " + " ".join(f"WHEN {column} <= {hi} THEN '{label}'" for _, hi, label in CALIBRATION_DTE) + " END"


SECTION_SQL = "CASE WHEN instr(category, '@') > 0 THEN substr(category, 1, instr(category, '@') - 1) ELSE category END"


def plays_calibration(src: Sources, r: Report) -> None:
    research = src.research
    if not _has(research, "stk_play_outcomes"):
        r.wait("Plays odds", "No Plays have been recorded yet; each session's picks are graded at expiry.", PERFORMANCE)
        return
    assert research is not None
    recent = to_iso(r.now - timedelta(days=PACE_DAYS))
    # Every graded chance in one place, the model's own number before calibration beside the one shown at the time.
    # The current probability model only, as ``plays.calibration`` (version 1 is also every play without a version).
    version = "" if od.MODEL_VERSION == 1 else (
        " AND COALESCE(json_extract(p.play_json, '$.odds_version'), 1) = :version")
    grades = ("UNION ALL SELECT event, category, session, dte, predicted, predicted_raw, hit, resolved_at "
              "FROM stk_play_grades WHERE model_version = :version") if _has(research, "stk_play_grades") else ""
    research.execute("DROP TABLE IF EXISTS temp.report_graded")
    research.execute(
        f"CREATE TEMP TABLE report_graded AS SELECT event, {SECTION_SQL} section, {_dte_case('dte')} bucket, session, "  # noqa: S608
        "shown, raw, hit, resolved_at >= :recent recent FROM ("
        "SELECT p.event event, p.category category, p.session session, p.expiration - p.session dte, "
        "COALESCE(p.itm_kestrel, p.itm_kestrel_raw) shown, p.itm_kestrel_raw raw, o.hit hit, o.resolved_at resolved_at "
        "FROM stk_plays p JOIN stk_play_outcomes o USING (session, category, rank) "
        f"WHERE p.itm_kestrel_raw IS NOT NULL{version} {grades})",
        {"version": od.MODEL_VERSION, "recent": recent})
    try:
        _calibration_findings(research, r)
    finally:
        research.execute("DROP TABLE IF EXISTS temp.report_graded")


def _calibration(rows: list[dict[str, Any]]) -> od.Calibration:
    """``odds.Calibration.learn`` from per-tenth aggregates of the model's own number (the same buckets, without
    reading every play into memory)."""
    if sum(x["n"] for x in rows) < od.MIN_CALIBRATION:
        return od.Calibration()
    ordered = sorted(rows, key=lambda x: x["tenth"])
    return od.Calibration(tuple((x["s_raw"] / x["n"], x["n"], x["hits"] / x["n"]) for x in ordered))


def _calibration_findings(research: sqlite3.Connection, r: Report) -> None:
    total = research.execute("SELECT COUNT(*), COUNT(DISTINCT session) FROM report_graded").fetchone()
    if not total[0]:
        r.wait("Plays odds", "No chance has been graded yet: a play's odds are graded once its outcome is decided "
                             "(at expiry, or when its take-profit or stop is reached on quoted closing marks).",
               PERFORMANCE)
        return
    # the model's own number in tenths: what calibration learns from, at the three levels the Plays page uses
    raw_rows: dict[tuple[str, ...], list[sqlite3.Row]] = defaultdict(list)
    for x in _rows(research, "SELECT event, section, bucket, MIN(CAST(raw * 10 AS INTEGER), 9) tenth, COUNT(*) n, "
                             "SUM(raw) s_raw, SUM(hit) hits FROM report_graded GROUP BY 1, 2, 3, 4"):
        raw_rows[(x["event"], x["section"], x["bucket"])].append(x)
    merged: dict[tuple[str, ...], dict[int, list[float]]] = defaultdict(lambda: defaultdict(lambda: [0.0, 0.0, 0.0]))
    for (event, section, bucket), xs in raw_rows.items():
        for x in xs:
            for key in ((event,), (event, section), (event, section, bucket)):
                acc = merged[key][x["tenth"]]
                acc[0] += x["n"]
                acc[1] += x["s_raw"]
                acc[2] += x["hits"]
    calibrations = {key: _calibration([{"tenth": t, "n": int(v[0]), "s_raw": v[1], "hits": v[2]} for t, v in tenths.items()])
                    for key, tenths in merged.items()}

    def calibrator(event: str, section: str, bucket: str) -> od.Calibration:
        for key in ((event, section, bucket), (event, section), (event,)):
            found = calibrations.get(key)
            if found is not None and found.buckets:
                return found
        return od.Calibration()

    groups = {(x["event"], x["section"], x["bucket"]): x for x in _rows(
        research, "SELECT event, section, bucket, COUNT(*) n, COUNT(DISTINCT session) days, SUM(recent) recent "
                  "FROM report_graded GROUP BY 1, 2, 3")}
    sections = {(x["event"], x["section"]): x for x in _rows(
        research, "SELECT event, section, COUNT(*) n, COUNT(DISTINCT session) days, SUM(recent) recent, "
                  "AVG(shown) shown, AVG(hit) rate FROM report_graded GROUP BY 1, 2")}
    events = {x["event"]: x for x in _rows(
        research, "SELECT event, COUNT(*) n, COUNT(DISTINCT session) days, AVG(shown) shown, AVG(hit) rate, "
                  "AVG((shown - hit) * (shown - hit)) b_shown, AVG((raw - hit) * (raw - hit)) b_raw, "
                  "SUM(ABS(shown - raw) > 0.005) adjusted FROM report_graded GROUP BY 1")}
    r.notes["section_itm"] = {s: x for (e, s), x in sections.items() if e == "itm"}
    calibrated = sorted(k for k, c in calibrations.items() if c.buckets)
    r.metric("plays.graded_chances", "Graded chances", int(total[0]), items=int(total[0]), days=int(total[1]),
             strength=strength(int(total[0]), int(total[1]), need=od.MIN_CALIBRATION))
    r.metric("plays.calibrated_groups", "Calibrated odds groups", len(calibrated))

    # what the miscalibration is, event by event: the three largest on their own, the rest in one line
    off: list[tuple[float, str, str, str, sqlite3.Row, str]] = []
    for event, x in sorted(events.items(), key=lambda kv: -kv[1]["n"]):
        name, happened = EVENTS.get(event, (event, event))
        n, days, gap = int(x["n"]), int(x["days"]), x["rate"] - x["shown"]
        s = strength(n, days, need=od.MIN_CALIBRATION)
        r.metric(f"plays.brier.{event}", f"Brier score, {name}", round(x["b_shown"], 4), items=n, days=days, strength=s)
        if abs(gap) >= 0.05 and n >= 10:
            off.append((abs(gap) * math.sqrt(n), event, name, happened, x, s))
        if x["adjusted"] and n >= 10:
            better = x["b_shown"] < x["b_raw"] - 0.001
            worse = x["b_shown"] > x["b_raw"] + 0.001
            if better or worse:
                text = (f"Brier score {x['b_shown']:.3f} for the chances shown against {x['b_raw']:.3f} for the "
                        f"uncalibrated model on the same {n:,} plays (lower is better; each shown chance was "
                        "calibrated only on plays resolved before it).")
                if better:
                    r.add("achievements", f"Calibration is making its odds of {name} more accurate.", text, items=n,
                          days=days, strength=s, link=PERFORMANCE, key=f"brier-better:{event}",
                          weight=2 + (s != "early"))
                else:
                    r.add("wrong", f"Calibration made its odds of {name} less accurate so far.", text, items=n,
                          days=days, strength=s, link=PERFORMANCE, key=f"brier-worse:{event}", weight=2)
    off.sort(key=lambda o: -o[0])
    for _, event, name, happened, x, s in off[:3]:
        n, days, gap = int(x["n"]), int(x["days"]), x["rate"] - x["shown"]
        over = gap < 0
        r.add("wrong", f"{'Over' if over else 'Under'}-confident on {name}: it showed {pct(x['shown'])} on average "
                       f"and {pct(x['rate'])} {happened} — {points(gap)} too {'high' if over else 'low'}.",
              f"{count(n, 'graded play')} over {count(days, 'session')}, every section together. Calibration "
              "corrects a group once it has 30 resolved plays.", items=n, days=days, strength=s,
              link=PERFORMANCE, key=f"calibration:{event}", weight=2 + 8 * abs(gap) + (s != "early"))
    if len(off) > 3:
        rest = off[3:]
        n, days = sum(int(o[4]["n"]) for o in rest), max(int(o[4]["days"]) for o in rest)
        r.add("wrong", f"Also off by 5 points or more: {', '.join(o[2] for o in rest)}.",
              first_upper("; ".join(f"{o[2]}: said {pct(o[4]['shown'])}, {pct(o[4]['rate'])} happened" for o in rest)) + ".",
              items=n, days=days, unit="graded chances",
              strength=min((o[5] for o in rest), key=STRENGTHS.index), link=PERFORMANCE,
              key="calibration:rest", weight=1)
    if calibrated:
        r.add("achievements", f"{count(len(calibrated), 'odds group')} now calibrated on its own history.",
              "A group (a chance, a section, a days-to-expiry range) is calibrated once 30 of its plays have resolved; "
              "the chance shown is then pulled toward how often that group really hit. Groups: "
              + "; ".join(_group_name(k) for k in calibrated[:6]) + ("…" if len(calibrated) > 6 else "."),
              items=int(total[0]), days=int(total[1]), unit="graded chances",
              strength=strength(int(total[0]), int(total[1]), need=od.MIN_CALIBRATION), link=PERFORMANCE,
              key="calibrated-groups", weight=1.5)

    # the cells: "when it said 60–70% for …, 41% happened; it now shows about 45%"
    cells = []
    for x in _rows(research, "SELECT event, section, bucket, MIN(CAST(shown * 10 AS INTEGER), 9) tenth, COUNT(*) n, "
                             "COUNT(DISTINCT session) days, AVG(shown) shown, AVG(raw) raw, AVG(hit) rate "
                             "FROM report_graded GROUP BY 1, 2, 3, 4 HAVING n >= 10"):
        now = calibrator(x["event"], x["section"], x["bucket"]).apply(x["raw"])
        cells.append({**dict(x), "now": now, "gap": x["rate"] - x["shown"]})
    learned = sorted((c for c in cells if abs(c["now"] - c["raw"]) >= 0.03), key=lambda c: -abs(c["now"] - c["raw"]) * c["n"])
    used = set()
    for c in learned[:3]:
        used.add((c["event"], c["section"], c["bucket"], c["tenth"]))
        r.add("learned", _cell_sentence(c) + f" It now shows those as about {pct(c['now'])}.",
              "Calibrated on that group's resolved plays (shrunk by 20 pseudo-observations, so a few outcomes move it "
              "only a little).", items=c["n"], days=c["days"], strength=strength(c["n"], c["days"], need=od.MIN_CALIBRATION),
              link=PERFORMANCE, key=f"cell:{c['event']}:{c['section']}:{c['bucket']}:{c['tenth']}", weight=2)
    misses = sorted((c for c in cells if abs(c["gap"]) >= 0.1 and (c["event"], c["section"], c["bucket"], c["tenth"])
                     not in used), key=lambda c: -abs(c["gap"]) * math.sqrt(c["n"]))
    for c in misses[:2]:
        group = groups[(c["event"], c["section"], c["bucket"])]
        fixed = calibrator(c["event"], c["section"], c["bucket"]).buckets
        r.add("wrong", _cell_sentence(c),
              (f"It now shows those as about {pct(c['now'])}." if fixed else
               f"That group is not calibrated yet: {group['n']} of {od.MIN_CALIBRATION} resolved."),
              items=c["n"], days=c["days"], strength=strength(c["n"], c["days"], need=od.MIN_CALIBRATION),
              link=PERFORMANCE, key=f"miss:{c['event']}:{c['section']}:{c['bucket']}:{c['tenth']}",
              weight=1.5 + 5 * abs(c["gap"]))

    # coming up: the groups closest to their first calibration
    pending = []
    for key, x in [*(((e, s), v) for (e, s), v in sections.items()), *groups.items()]:
        n = int(x["n"])
        if 0 < n < od.MIN_CALIBRATION and not calibrations.get(key, od.Calibration()).buckets:
            pace = (x["recent"] or 0) / (PACE_DAYS / 7)
            pending.append((key, n, int(x["days"]), None if pace <= 0 else (od.MIN_CALIBRATION - n) / pace))
    pending.sort(key=lambda p: (p[3] is None, p[3] or 0, -p[1]))
    for key, n, days, eta in pending[:3]:
        r.add("coming", f"{first_upper(_group_name(key))} calibration: {n} of {od.MIN_CALIBRATION} resolved, "
                        f"{weeks(eta)}.", "Estimated from the last four weeks' pace of resolved plays in that group.",
              items=n, days=days, strength="early", link=PERFORMANCE, key=f"next-calibration:{':'.join(key)}",
              weight=1 + (1 / (1 + (eta if eta is not None else 99))))
    _market_view(research, r)


def _group_name(key: tuple[str, ...]) -> str:
    name = {"itm": "in the money", "move": "break-even move", "take_profit": "take-profit", "profit": "profit",
            "target25": "+25%", "target50": "+50%", "target100": "+100%", "max_gain": "maximum gain",
            "touch": "touch"}.get(key[0], key[0])
    if len(key) == 1:
        return f"{name} (every play)"
    return f"{name} for {section_title(key[1])}" + (f", {key[2]} to expiry" if len(key) > 2 else "")


def _cell_sentence(c: dict[str, Any]) -> str:
    lo = 10 * min(int(c["shown"] * 10), 9)
    happened = EVENTS.get(c["event"], (c["event"], c["event"]))[1]
    return (f"When KESTREL said {lo}–{lo + 10}% for {section_title(c['section'])} ({c['bucket']} to expiry), "
            f"{pct(c['rate'])} {happened} ({count(c['n'], 'play')} over {count(c['days'], 'session')}).")


def _market_view(research: sqlite3.Connection, r: Report) -> None:
    """How KESTREL's in-the-money odds compare with the option price's own: more bullish? more confident? more right?"""
    rows = _rows(research, "SELECT p.event_kind kind, COUNT(*) n, COUNT(DISTINCT p.session) days, "
                           "AVG(COALESCE(p.itm_kestrel, p.itm_kestrel_raw) - p.itm_market) bias, "
                           "AVG(ABS(COALESCE(p.itm_kestrel, p.itm_kestrel_raw) - 0.5) - ABS(p.itm_market - 0.5)) sure, "
                           "SUM((COALESCE(p.itm_kestrel, p.itm_kestrel_raw) - o.hit) * "
                           "(COALESCE(p.itm_kestrel, p.itm_kestrel_raw) - o.hit)) b_k, "
                           "SUM((p.itm_market - o.hit) * (p.itm_market - o.hit)) b_m "
                           "FROM stk_plays p JOIN stk_play_outcomes o USING (session, category, rank) "
                           "WHERE p.event = 'itm' AND p.itm_market IS NOT NULL AND p.itm_kestrel_raw IS NOT NULL "
                           "GROUP BY p.event_kind")
    n = sum(x["n"] for x in rows)
    if not n:
        return
    days = int(research.execute("SELECT COUNT(DISTINCT p.session) FROM stk_plays p JOIN stk_play_outcomes o "
                                "USING (session, category, rank) WHERE p.event = 'itm' AND p.itm_market IS NOT NULL"
                                ).fetchone()[0])
    s = strength(n, days, need=od.MIN_CALIBRATION)
    parts = []
    for x in rows:
        if x["n"] < 5 or abs(x["bias"]) < 0.01:
            continue
        mood = ("more bullish" if x["bias"] > 0 else "more bearish") if x["kind"] == "call" else (
            "more bearish" if x["bias"] > 0 else "more bullish") if x["kind"] == "put" else (
            "expecting bigger moves" if x["bias"] > 0 else "expecting smaller moves")
        parts.append(f"on {x['kind']}s its chances run {points(x['bias'])} {'above' if x['bias'] > 0 else 'below'} "
                     f"the market's ({mood}; {count(x['n'], 'play')})")
    sure = sum(x["sure"] * x["n"] for x in rows) / n
    b_k, b_m = sum(x["b_k"] for x in rows) / n, sum(x["b_m"] for x in rows) / n
    if parts:
        r.add("behaviour", "Against the market's own odds: " + "; ".join(parts) + ".",
              f"It is {'more' if sure > 0 else 'less'} confident than the option prices (further from 50/50 by "
              f"{points(sure)} on average). Graded plays only.", items=n, days=days, strength=s, link=PERFORMANCE,
              key="vs-market", weight=1.5)
    if abs(b_k - b_m) > 0.002:
        text = (f"Brier score {b_k:.3f} for KESTREL against {b_m:.3f} for the option price's own odds on the same "
                f"{n:,} plays finishing in the money (lower is better).")
        if b_k < b_m:
            r.add("achievements", "KESTREL's in-the-money odds beat the market's own.", text, items=n, days=days,
                  strength=s, link=PERFORMANCE, key="beats-market-odds", weight=2 + (s != "early"))
        else:
            r.add("wrong", "The market's own odds have been more accurate than KESTREL's.", text, items=n, days=days,
                  strength=s, link=PERFORMANCE, key="market-odds-better", weight=2 + (s != "early"))


# ── 2–3. Plays learning: exit rules, inputs, challengers, labels; records, lottos, misses ───────
def plays_learning(src: Sources, r: Report) -> None:
    research = src.research
    if not _has(research, "stk_play_learning"):
        return
    assert research is not None
    from desk.stocks import play_learning as pln

    resolved = {x["category"]: (int(x["n"]), int(x["days"])) for x in _rows(
        research, "SELECT category, COUNT(*) n, COUNT(DISTINCT session) days FROM stk_play_outcomes "
                  "WHERE instr(category, '@') = 0 GROUP BY category")}
    rep = pln.latest(research)
    if rep is None:
        r.wait("Plays learning", "No Plays learning report yet: the first is written after a session closes with "
                                 "recorded plays (exit rules, inputs, challengers and section verdicts).", PERFORMANCE)
        return
    r.notes["plays_learning_session"] = rep.get("session")
    labels: dict[str, Any] = rep.get("labels") or {}
    states = Counter(v.get("state") for v in labels.values())
    r.metric("plays.proven", "Sections proven against random twins", states.get("proven", 0), strength="tested")
    r.metric("plays.failing", "Sections failing against random twins", states.get("failing", 0), strength="tested")
    close: list[tuple[float, str, dict[str, Any]]] = []
    for category, v in labels.items():
        title, settled, sessions = section_title(category), int(v.get("settled") or 0), int(v.get("sessions") or 0)
        mean, twins = v.get("mean"), v.get("vs_twins")
        r.metric(f"plays.label.{category}", f"{title} verdict", v.get("state"), items=settled, days=sessions,
                 strength="tested" if v.get("state") in ("proven", "failing") else strength(
                     settled, sessions, need=pln.MIN_RESOLVED, need_days=pln.MIN_SESSIONS))
        if v.get("state") == "proven":
            r.add("achievements", f"{title} is proven: it beats its random twins by {signed(twins, 1)} a play.",
                  v.get("text") or "", items=settled, days=sessions, strength="tested", link=PERFORMANCE,
                  key=f"proven:{category}", weight=6)
        elif v.get("state") == "failing":
            r.add("wrong", f"{title} is failing: {signed(mean, 1)} a play at expiry, {signed(twins, 1)} against its "
                           "random twins.", v.get("text") or "", items=settled, days=sessions, strength="tested",
                  link=PERFORMANCE, key=f"failing:{category}", weight=6)
        else:
            s = strength(settled, sessions, need=pln.MIN_RESOLVED, need_days=pln.MIN_SESSIONS)
            if twins is not None and settled >= 5 and abs(twins) >= 0.02:
                where = "wrong" if twins < 0 else "achievements"
                r.add(where, f"{title} {'trails' if twins < 0 else 'is ahead of'} its random twins by "
                             f"{signed(abs(twins), 1)[1:]} a play — not significant yet.",
                      f"Average {signed(mean, 1)} a play at expiry. A verdict needs {pln.MIN_RESOLVED} settled plays "
                      f"over {pln.MIN_SESSIONS} sessions and a significant difference.", items=settled, days=sessions,
                      strength=s, link=PERFORMANCE, key=f"twins:{category}", weight=1 + 4 * abs(twins))
            itm = r.notes.get("section_itm", {}).get(category)
            if (mean is not None and mean < -0.05 and itm is not None and itm["n"] >= od.MIN_CALIBRATION
                    and abs(itm["rate"] - itm["shown"]) < 0.04):
                r.add("surprises", f"{title}'s odds are about right, yet it loses money.",
                      f"It said {pct(itm['shown'])} on average and {pct(itm['rate'])} finished in the money "
                      f"({count(int(itm['n']), 'play')}), but its plays average {signed(mean, 1)} at expiry: the "
                      "chances are fair, the prices paid are not.", items=settled, days=sessions, strength=s,
                      link=PERFORMANCE, key=f"fair-odds-losing:{category}", weight=2)
            need = max(pln.MIN_RESOLVED - settled, 0) / pln.MIN_RESOLVED + max(pln.MIN_SESSIONS - sessions, 0) / pln.MIN_SESSIONS
            if settled:
                close.append((need, category, v))
    for _, category, v in sorted(close)[:2]:
        sessions = int(v.get("sessions") or 0)
        left = max(pln.MIN_SESSIONS - sessions, 0)
        r.add("coming", f"Verdict for {section_title(category)} against its random twins: {v.get('settled', 0)} of "
                        f"{pln.MIN_RESOLVED} settled plays, {sessions} of {pln.MIN_SESSIONS} sessions"
                        + (f", {weeks(left / 5)} at one session a day." if left else ", once enough have settled."),
              "Proven or failing needs a significant difference after correcting for every section tested.",
              items=int(v.get("settled") or 0), days=sessions, strength="early", link=PERFORMANCE,
              key=f"next-verdict:{category}", weight=1)

    for category, e in sorted((rep.get("exits") or {}).items()):
        title = section_title(category)
        n, _ = resolved.get(category, (0, 0))
        unseen = int(e.get("test_sessions") or 0)
        best = e.get("best_in_sample") or {}
        if e.get("suggested"):
            r.add("learned", f"Exit rule for {title}: {best.get('rule')} beat holding to expiry by "
                             f"{signed(e.get('mean_improvement'), 1)} a play on sessions it never saw.",
                  f"Walk-forward over {unseen} unseen sessions, Holm-corrected p {e.get('p_holm'):.3f}; applied to its "
                  f"{e.get('dte_bucket')} plays.", items=n, days=unseen, strength="tested", link=PERFORMANCE,
                  key=f"exit:{category}", weight=5)
        elif best and best.get("mean") is not None and best.get("hold_mean") is not None and (
                best["mean"] > best["hold_mean"] + 0.02) and unseen < pln.MIN_SESSIONS:
            r.add("coming", f"Exit rule for {title}: in hindsight \"{best.get('rule')}\" did best "
                            f"({signed(best['mean'], 1)} a play against {signed(best['hold_mean'], 1)} holding); "
                            f"{unseen} of {pln.MIN_SESSIONS} unseen sessions tested.",
                  "A rule is suggested only after it beats holding on 20 sessions it never saw (Holm p < 0.05).",
                  items=n, days=unseen, strength="early", link=PERFORMANCE, key=f"exit-pending:{category}", weight=0.8)
        elif unseen >= pln.MIN_SESSIONS and (e.get("mean_improvement") or 0) <= 0:
            r.add("learned", f"For {title}, no exit rule beats holding to expiry on unseen sessions.",
                  f"Walk-forward over {unseen} unseen sessions: {signed(e.get('mean_improvement'), 1)} a play.",
                  items=n, days=unseen, strength="suggestive", link=PERFORMANCE, key=f"exit-none:{category}",
                  weight=1)
    r.metric("plays.exit_rules", "Exit rules suggested", sum(bool(e.get("suggested")) for e in (rep.get("exits") or {}).values()),
             strength="tested")

    for f in rep.get("features") or []:
        n, ic, p = int(f.get("sessions") or 0), f.get("ic"), f.get("p")
        if ic is None or p is None or n < pln.MIN_SESSIONS:
            continue
        if p < 0.05 and ic > 0:
            r.add("learned", f"{f['label']} predicts a play's return a week later (rank correlation {ic:+.2f}).",
                  f"Averaged over {count(n, 'session')}, one-sided p {p:.3f}; not Holm-corrected across inputs.",
                  items=n, days=n, unit="sessions", strength="suggestive", link=PERFORMANCE,
                  key=f"input:{f['feature']}", weight=1.5)
        elif p > 0.95 and ic < 0:
            r.add("surprises", f"{f['label']} points the wrong way: higher values went with worse week returns "
                               f"(rank correlation {ic:+.2f}).", f"Averaged over {count(n, 'session')}.",
                  items=n, days=n, unit="sessions", strength="suggestive", link=PERFORMANCE,
                  key=f"input-backwards:{f['feature']}", weight=1.5)

    tests = rep.get("challengers") or []
    failed = [t for t in tests if t.get("status") == "failed"]
    for t in tests:
        title, sessions = section_title(t["category"]), int(t.get("sessions") or 0)
        if t.get("status") == "promoted":
            r.add("achievements", f"Promotion: \"{t['title']}\" now runs {title}'s live picks.",
                  f"It beat the live rules over {sessions} later sessions, Holm-corrected across every challenger.",
                  items=sessions, days=sessions, unit="sessions", strength="tested", link=PERFORMANCE,
                  key=f"promoted:{t['variant']}:{t['category']}", weight=5)
        elif t.get("status") == "beats the live rules":
            r.add("learned", f"\"{t['title']}\" beats {title}'s live rules by {signed(t.get('mean_difference'), 1)} a "
                             "play; it is promoted at the next closing build.",
                  f"{sessions} later sessions, Holm p {t.get('p_holm')}.", items=sessions, days=sessions,
                  unit="sessions", strength="tested", link=PERFORMANCE,
                  key=f"beats:{t['variant']}:{t['category']}", weight=4)
    if failed:
        r.add("learned", f"{count(len(failed), 'rule change')} tried in shadow and rejected.",
              "No better than the live rules after 40 sessions: " + "; ".join(
                  f"{t['title']} for {section_title(t['category'])}" for t in failed[:4]) + ("…" if len(failed) > 4 else "."),
              items=len(failed), days=max(int(t.get("sessions") or 0) for t in failed), unit="challengers",
              strength="tested", link=PERFORMANCE, key="challengers-failed", weight=1)
    testing = sorted((t for t in tests if str(t.get("status", "")).startswith("testing") and t.get("sessions")),
                     key=lambda t: -(t.get("sessions") or 0))
    for t in testing[:2]:
        sessions = int(t.get("sessions") or 0)
        r.add("coming", f"Challenger \"{t['title']}\" for {section_title(t['category'])}: {sessions} of "
                        f"{pln.MIN_SESSIONS} sessions, {weeks((pln.MIN_SESSIONS - sessions) / 5)} to a first verdict.",
              f"So far {signed(t.get('mean_difference'), 1)} a play against the live picks.", items=sessions,
              days=sessions, unit="sessions", strength="early", link=PERFORMANCE,
              key=f"next-challenger:{t['variant']}:{t['category']}", weight=0.8)
    first = research.execute("SELECT MIN(created_at), COUNT(*) FROM stk_play_promotions").fetchone()
    r.metric("plays.promotions", "Challenger promotions", int(first[1]), strength="tested", unit="promotions")


def plays_records(src: Sources, r: Report) -> None:
    research = src.research
    if not _has(research, "stk_play_outcomes"):
        return
    assert research is not None
    total = research.execute("SELECT COUNT(*), COUNT(DISTINCT session) FROM stk_play_outcomes").fetchone()
    n, days = int(total[0]), int(total[1])
    r.metric("plays.graded", "Plays graded at expiry", n, items=n, days=days,
             strength=strength(n, days, need=od.MIN_CALIBRATION))
    reached = [m for m in MILESTONES if m <= n]
    if reached:
        r.add("achievements", f"Milestone: {reached[-1]:,} plays graded at expiry.",
              f"{n:,} so far, over {count(days, 'session')}; each is judged on the stock's official close.",
              items=n, days=days, strength=strength(n, days, need=od.MIN_CALIBRATION), link=PERFORMANCE,
              key=f"milestone:{reached[-1]}", weight=1)
    # the Expiry filter's own cohorts, by section and band
    bands = _rows(research, "SELECT category, COUNT(*) n, COUNT(DISTINCT session) days, AVG(net_return) mean, "
                            "AVG(hit) rate FROM stk_play_outcomes WHERE instr(category, '@') > 0 "
                            "GROUP BY category HAVING n >= 10 ORDER BY mean DESC")
    if bands:
        best, worst = bands[0], bands[-1]
        if best["mean"] > 0:
            r.add("achievements", f"Best Expiry-filter cohort: {section_title(best['category'])}, {signed(best['mean'], 1)} "
                                  "a play at expiry.", f"{pct(best['rate'])} finished in the money; graded on the stock's "
                                                       "close at expiry (no daily marks or twins for band picks).",
                  items=best["n"], days=best["days"], strength=strength(best["n"], best["days"], need=30),
                  link=PERFORMANCE, key=f"band-best:{best['category']}", weight=1.5)
        if worst["mean"] < 0 and worst["category"] != best["category"]:
            r.add("wrong", f"Worst Expiry-filter cohort: {section_title(worst['category'])}, {signed(worst['mean'], 1)} "
                           "a play at expiry.", f"{pct(worst['rate'])} finished in the money.",
                  items=worst["n"], days=worst["days"], strength=strength(worst["n"], worst["days"], need=30),
                  link=PERFORMANCE, key=f"band-worst:{worst['category']}", weight=1 + min(2.0, abs(worst["mean"])))
    play = ("SELECT o.session, o.category, o.net_return, o.hit, p.symbol, p.event_kind, p.action, "
            "COALESCE(p.itm_kestrel, p.itm_kestrel_raw) chance FROM stk_play_outcomes o JOIN stk_plays p "
            "USING (session, category, rank) ")
    for x in _rows(research, play + "WHERE (o.category = 'lottos' OR o.category LIKE 'lottos@%') AND o.net_return > 0 "
                                    "ORDER BY o.net_return DESC LIMIT 1"):
        r.add("achievements", f"Biggest lotto: {x['symbol']} {x['event_kind']} picked {short(x['session'])} paid "
                              f"{1 + x['net_return']:.1f}× its cost ({signed(x['net_return'])}).",
              "At expiry, after fees, on the stock's official close.", items=1, days=1, strength="early",
              link=PERFORMANCE, key="lotto-best", weight=1.5 + min(3.0, x["net_return"] / 5))
    for x in _rows(research, play + "WHERE o.category NOT LIKE 'lottos%' AND o.net_return > 0.5 "
                                    "ORDER BY o.net_return DESC LIMIT 1"):
        r.add("achievements", f"Biggest winner: {x['symbol']} {x['event_kind']} ({section_title(x['category'])}) "
                              f"picked {short(x['session'])}, {signed(x['net_return'])} at expiry.",
              "On the money at risk, after fees.", items=1, days=1, strength="early", link=PERFORMANCE,
              key="play-best", weight=1.5)
    sure = research.execute("SELECT COUNT(*) n, SUM(1 - o.hit) missed, COUNT(DISTINCT o.session) days FROM "
                            "stk_play_outcomes o JOIN stk_plays p USING (session, category, rank) WHERE "
                            "COALESCE(p.itm_kestrel, p.itm_kestrel_raw) >= 0.7").fetchone()
    worst_misses = _rows(research, play + "WHERE o.hit = 0 AND COALESCE(p.itm_kestrel, p.itm_kestrel_raw) >= 0.7 "
                                          "ORDER BY chance DESC, o.net_return ASC LIMIT 3")
    if worst_misses:
        r.add("wrong", f"{sure['missed']} of {sure['n']} plays it gave 70% or more did not happen.",
              "Biggest high-confidence misses: " + "; ".join(
                  f"{x['symbol']} {x['event_kind']} ({section_title(x['category'])}) picked {short(x['session'])} at "
                  f"{pct(x['chance'])}, {signed(x['net_return'])} at expiry" for x in worst_misses) + ".",
              items=int(sure["n"]), days=int(sure["days"]),
              strength=strength(int(sure["n"]), int(sure["days"]), need=od.MIN_CALIBRATION), link=PERFORMANCE,
              key="high-confidence-misses", weight=1 + 3 * (sure["missed"] / max(sure["n"], 1)))


def plays_behaviour(src: Sources, r: Report) -> None:
    """What it picks: the last 90 days of recorded sections (not the Expiry filter's band cohorts)."""
    research = src.research
    if not _has(research, "stk_plays"):
        return
    assert research is not None
    from desk.stocks.play_performance import DTE_BUCKETS, PRICE_BUCKETS

    since = (cal.new_york(r.now).date() - timedelta(days=RECENT_DAYS)).toordinal()
    rows = _rows(research, "SELECT session, category, event_kind, action, expiration - session dte, entry, symbol "
                           "FROM stk_plays WHERE session >= ? AND instr(category, '@') = 0 LIMIT 200000", (since,))
    if not rows:
        return
    n, days = len(rows), len({x["session"] for x in rows})
    s = strength(n, days, need=30)

    def share(values: list[Any], top: int = 3) -> str:
        return ", ".join(f"{k} {v / len(values):.0%}" for k, v in Counter(values).most_common(top))

    def bucket(value: float, edges: Any) -> str:
        return str(next((label for lo, hi, label in edges if lo <= value <= hi), edges[-1][2]))

    kinds = Counter(x["event_kind"] for x in rows)
    bought = sum(x["action"] == "BUY" for x in rows) / n
    r.add("behaviour", "What it picks most: " + share([section_title(x["category"]) for x in rows]) + ".",
          f"{count(n, 'recorded play')} over the last {RECENT_DAYS} days ({count(days, 'session')}).",
          items=n, days=days, strength=s, link=PLAYS, key="picks-sections", weight=1.2)
    r.add("behaviour", f"Calls {kinds['call'] / n:.0%}, puts {kinds['put'] / n:.0%}, straddles "
                       f"{kinds['straddle'] / n:.0%}; it buys {bought:.0%} of its plays and sells {1 - bought:.0%}.",
          "Days to expiry: " + share([bucket(x["dte"], DTE_BUCKETS) for x in rows], 2) + ". Price per share: "
          + share([bucket(x["entry"], PRICE_BUCKETS) for x in rows], 2) + ".",
          items=n, days=days, strength=s, link=PLAYS, key="picks-shape", weight=1.1)
    symbols = Counter(x["symbol"] for x in rows)
    sectors = _sectors(src.market, list(symbols))
    text = "Most picked stocks: " + ", ".join(f"{k} ({v})" for k, v in symbols.most_common(5)) + "."
    if sectors:
        by: Counter[str] = Counter()
        for sym, k in symbols.items():
            by[sectors.get(sym, "Other")] += k
        text = "Sectors: " + ", ".join(f"{k} {v / n:.0%}" for k, v in by.most_common(4)) + ". " + text
    top_share = symbols.most_common(1)[0][1] / n
    r.add("behaviour", f"Concentration: its most picked stock is {top_share:.0%} of its plays across "
                       f"{count(len(symbols), 'stock')}.", text, items=n, days=days, strength=s, link=PLAYS,
          key="picks-names", weight=1)


def _sectors(market: sqlite3.Connection | None, symbols: list[str]) -> dict[str, str]:
    if not symbols or not _has(market, "stk_companies"):
        return {}
    assert market is not None
    from desk.stocks.sectors import LABELS, sector

    out: dict[str, str] = {}
    for i in range(0, len(symbols), 500):
        chunk = symbols[i:i + 500]
        for x in market.execute(f"SELECT l.symbol, c.sic FROM stk_listings l JOIN stk_companies c ON c.cik = l.cik "  # noqa: S608
                                f"WHERE l.symbol IN ({','.join('?' * len(chunk))})", chunk):
            out[x["symbol"]] = LABELS.get(sector(x["sic"]), "Other")
    return out


# ── 4. The Learning workspace ────────────────────────────────────────────────────────────────────
def workspace(src: Sources, r: Report) -> None:
    conn = src.learning
    if not _has(conn, "stk_learning_experiments") or not _has(conn, "stk_learning_checkpoints"):
        r.wait("Learning workspace", "The Learning book has not started: it opens with the dashboard during market "
                                     "hours once an options provider is connected.", LEARNING)
        return
    assert conn is not None
    from desk.stocks import learning as lab
    from desk.stocks.paper.book import MICRO, PaperBook

    ev = lab.evidence_progress(conn, r.now)
    blocks, days, matured = int(ev["underlying_day_blocks"]), int(ev["entry_days"]), int(ev["matured_outcomes"])
    r.metric("learning.matured", "Finished research positions", matured, items=matured, days=days,
             strength=strength(matured, days, need=lab.MIN_COMPARISON_BLOCKS, need_days=lab.MIN_COMPARISON_DAYS),
             unit="positions", day_unit="entry days")
    model = lab.state(conn, "model", {}) or {}
    if model.get("promoted") or model.get("champion"):
        r.add("achievements", f"Learning promoted {str(model.get('champion')).replace('_', ' ')}s in its "
                              "chronological comparison.",
              f"Held-out mean {signed(model.get('test_net_return'), 1)} over {model.get('test_blocks', 0)} "
              "underlying/day blocks; advisory, capacity is top-of-book only.", items=matured, days=days,
              unit="positions", day_unit="entry days", strength="tested", link=LEARNING, key="learning-promotion",
              weight=4)
    if not ev["ready_for_comparison"]:
        r.add("coming", f"Learning's first formal comparison: {days} of {lab.MIN_COMPARISON_DAYS} entry days, "
                        f"{blocks} of {lab.MIN_COMPARISON_BLOCKS} underlying/day blocks.",
              " ".join(ev["blockers"][:2]) or "Waiting for held-out entries.", items=matured, days=days,
              unit="positions", day_unit="entry days", strength="early", link=LEARNING, key="next-learning-comparison",
              weight=1.2)
    if ev["closed_without_outcome"]:
        r.add("wrong", f"{ev['closed_without_outcome']} closed research trades have no outcome record.",
              "They are not counted as evidence until that is repaired.", items=int(ev["closed_without_outcome"]),
              days=0, unit="trades", day_unit="days", strength="early", link=LEARNING, key="learning-missing-outcomes",
              weight=1)

    # the books: research, the $500,000 reference and the small accounts, trading P/L excluding funding
    book = PaperBook(conn)
    accounts: dict[str, dict[str, Any]] = {}
    for account, title, capital in lab.ACCOUNTS:
        x = conn.execute("SELECT COALESCE(SUM(CASE WHEN state='OPEN' THEN last_value_micros END), 0) marked, "
                         "SUM(state='CLOSED') closed, COUNT(DISTINCT entry_day) days, COUNT(*) positions, "
                         "SUM(state='CLOSED' AND closed_at >= ?) week FROM stk_positions WHERE account = ?",
                         (to_iso(r.now - timedelta(days=7)), account)).fetchone()
        contributed = book.deposits(account)
        equity = book.cash(account) + (x["marked"] or 0) / MICRO
        accounts[account] = {"title": title, "capital": capital, "contributed": contributed, "equity": equity,
                             "pnl": equity - contributed if contributed else 0.0, "closed": int(x["closed"] or 0),
                             "days": int(x["days"] or 0), "positions": int(x["positions"] or 0),
                             "week": int(x["week"] or 0), "marked": (x["marked"] or 0) / MICRO}
        if contributed:
            r.metric(f"learning.pnl.{account}", f"{title} trading P/L", round(equity - contributed, 2),
                     items=int(x["closed"] or 0), days=int(x["days"] or 0), unit="closed trades", day_unit="entry days",
                     strength=strength(int(x["closed"] or 0), int(x["days"] or 0), need=20))
    for account in lab.SMALL_ACCOUNTS:
        a = accounts[account]
        if a["contributed"] and a["pnl"] < 0 and a["closed"]:
            r.add("wrong", f"The {a['title']} is down {money(a['pnl'])[1:]} ({signed(a['pnl'] / a['capital'], 1)}).",
                  f"{count(a['closed'], 'closed trade')} since it started; losses are kept, never topped up.",
                  items=a["closed"], days=a["days"], unit="closed trades", day_unit="entry days",
                  strength=strength(a["closed"], a["days"], need=20), link=LEARNING, key=f"small-losing:{account}",
                  weight=1.5 + min(2.0, abs(a["pnl"]) / a["capital"]))
        elif a["contributed"] and a["pnl"] > 0 and a["closed"]:
            r.add("achievements", f"The {a['title']} is up {money(a['pnl'])[1:]} ({signed(a['pnl'] / a['capital'], 1)}).",
                  f"{count(a['closed'], 'closed trade')} on its own cash, no top-ups.", items=a["closed"],
                  days=a["days"], unit="closed trades", day_unit="entry days",
                  strength=strength(a["closed"], a["days"], need=20), link=LEARNING, key=f"small-winning:{account}",
                  weight=1.5)
    research, reference = accounts[lab.RESEARCH], accounts[lab.REFERENCE]
    if research["positions"]:
        mix = Counter({x[0]: x[1] for x in conn.execute(
            "SELECT structure, COUNT(*) FROM stk_positions WHERE account = ? GROUP BY structure", (lab.RESEARCH,))})
        total = sum(mix.values())
        invested = research["marked"] / research["equity"] if research["equity"] > 0 else None
        r.add("behaviour", "Learning's research account: " + ", ".join(
            f"{k.replace('_', ' ')}s {v / total:.0%}" for k, v in mix.most_common()) + f" of {total:,} positions.",
              f"{pct(invested)} of its equity is in open positions now; {research['week']} positions closed in the "
              f"last 7 days.", items=total, days=research["days"], unit="positions", day_unit="entry days",
              strength=strength(total, research["days"], need=30), link=LEARNING, key="learning-mix", weight=1)
    if research["contributed"] and reference["contributed"] and (research["closed"] or reference["closed"]):
        short_of_cash = conn.execute("SELECT COUNT(*) FROM stk_pending WHERE account = ? AND (note LIKE "
                                     "'%insufficient cash%' OR note LIKE '%buying power%')", (lab.REFERENCE,)).fetchone()[0]
        r.add("behaviour", f"Research made {money(research['pnl'])}; the $500,000 reference, on the same orders with "
                           f"finite cash, made {money(reference['pnl'])}.",
              f"The reference could not afford {count(short_of_cash, 'order')} in full. The $1,000 and $5,000 "
              "accounts choose their own affordable contracts.", items=research["closed"], days=research["days"],
              unit="closed trades", day_unit="entry days", strength=strength(research["closed"], research["days"], need=20),
              link=LEARNING, key="research-vs-reference", weight=1)

    pnl = "(exit_credit_micros - COALESCE(exit_fees_micros, 0) - entry_cost_micros - entry_fees_micros)"
    closed = conn.execute(f"SELECT COUNT(*), COALESCE(SUM({pnl}), 0) FROM stk_positions "  # noqa: S608
                          "WHERE account = ? AND state = 'CLOSED'", (lab.RESEARCH,)).fetchone()
    top = _rows(conn, f"SELECT symbol, structure, entry_day, {pnl} pnl, entry_cost_micros + entry_fees_micros cost "  # noqa: S608
                      "FROM stk_positions WHERE account = ? AND state = 'CLOSED' ORDER BY pnl DESC LIMIT 5",
                (lab.RESEARCH,))
    if closed[0] and top:
        realized, best5 = closed[1] / MICRO, sum(x["pnl"] for x in top) / MICRO
        days_closed = research["days"]
        r.add("behaviour", f"Concentration: its best {len(top)} research trades made {money(best5)} of "
                           f"{money(realized)} realized.", f"Without them: {money(realized - best5)}, over "
                                                         f"{count(int(closed[0]), 'closed trade')}.",
              items=int(closed[0]), days=days_closed, unit="closed trades", day_unit="entry days",
              strength=strength(int(closed[0]), days_closed, need=20), link=LEARNING, key="learning-concentration",
              weight=1)
        if realized > 0 and realized - best5 < 0:
            r.add("surprises", f"Learning's research profit rests on {len(top)} trades: without them it would be "
                               f"{money(realized - best5)}.", "A few large winners carry the result; it may not repeat.",
                  items=int(closed[0]), days=days_closed, unit="closed trades", day_unit="entry days",
                  strength=strength(int(closed[0]), days_closed, need=20), link=LEARNING, key="learning-few-winners",
                  weight=2)
        b = top[0]
        if b["pnl"] > 0:
            r.add("achievements", f"Best Learning trade: {b['symbol']} {b['structure'].replace('_', ' ')} opened "
                                  f"{short(b['entry_day'])}, {money(b['pnl'] / MICRO)} "
                                  f"({signed(b['pnl'] / b['cost'] if b['cost'] else None)}).",
                  "Research account, sold at a later executable quote or settled at expiry.", items=1, days=1,
                  unit="trade", day_unit="day", strength="early", link=LEARNING, key="learning-best", weight=1.2)
    _lifecycle(conn, r)


def _lifecycle(conn: sqlite3.Connection, r: Report) -> None:
    """Does holding longer pay? Each recipe's checkpoints, with how many positions and separate entry days each rests on."""
    from desk.stocks import learning as lab
    from desk.stocks.learning_retail import MIN_CHECKPOINT

    recipes = {x["id"]: json.loads(x["config_json"]) for x in conn.execute(
        "SELECT id, config_json FROM stk_learning_experiments WHERE json_extract(config_json, '$.kind') = 'lifecycle'")}
    if not recipes:
        return
    steps: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for x in _rows(conn, "SELECT json_extract(p.plan_json, '$.experiment_id') experiment, c.days hold, COUNT(*) n, "
                         "COUNT(DISTINCT p.entry_day) days, AVG(c.net_return) mean, AVG(c.net_return > 0) wins "
                         "FROM stk_learning_checkpoints c JOIN stk_positions p ON p.id = c.position_id "
                         "WHERE p.account = ? GROUP BY 1, 2", (lab.RESEARCH,)):
        if x["experiment"] in recipes:
            steps[x["experiment"]].append(x)
    finals = {x["experiment_id"]: x for x in _rows(
        conn, "SELECT o.experiment_id, COUNT(*) n, COUNT(DISTINCT substr(o.block, 1, 10)) days, AVG(o.net_return) mean "
              "FROM stk_learning_outcomes o JOIN stk_positions p ON p.id = o.position_id AND p.account = ? "
              "GROUP BY 1", (lab.RESEARCH,))}

    def name(cfg: dict[str, Any]) -> str:
        bucket = lab.BUCKETS.get(cfg.get("bucket") or "", ("",))[0]
        return f"{bucket.lower()} {cfg['structure'].replace('long_', '').replace('_', ' ')}s".strip()

    best_found, pending = [], []
    for ident, cfg in recipes.items():
        ready = [x for x in steps.get(ident, []) if x["n"] >= MIN_CHECKPOINT]
        if ready:
            best = max(ready, key=lambda x: (x["mean"], -x["hold"]))
            best_found.append((cfg, best, ready))
        elif steps.get(ident):
            most = max(steps[ident], key=lambda x: x["n"])
            pending.append((MIN_CHECKPOINT - most["n"], cfg, most))
        others = [x for x in steps.get(ident, []) if x["n"] >= 5]
        winners = [x for x in others if x["mean"] > 0]
        if len(others) >= 3 and len(winners) == 1:
            w = winners[0]
            r.add("surprises", f"{first_upper(name(cfg))} pay only when held {count(w['hold'], 'day')} "
                               f"({signed(w['mean'], 1)}); every other holding time lost money.",
                  "Checkpoints: " + ", ".join(f"{x['hold']}d {signed(x['mean'])}"
                                              for x in sorted(others, key=lambda x: x["hold"])) + ".",
                  items=w["n"], days=w["days"], unit="positions", day_unit="entry days",
                  strength=strength(w["n"], w["days"], need=MIN_CHECKPOINT), link=LEARNING,
                  key=f"one-hold:{ident}", weight=1.5)
        f = finals.get(ident)
        if f is not None and f["n"] >= 5 and f["mean"] < 0:
            r.add("wrong", f"{first_upper(name(cfg))} lose money: {signed(f['mean'], 1)} on average when sold.",
                  f"{count(f['n'], 'finished position')}; sold at a later executable quote or settled at expiry.",
                  items=f["n"], days=f["days"], unit="positions", day_unit="entry days",
                  strength=strength(f["n"], f["days"], need=MIN_CHECKPOINT), link=LEARNING, key=f"recipe-losing:{ident}",
                  weight=1 + min(2.0, abs(f["mean"])))
    best_found.sort(key=lambda t: -t[1]["n"])
    for cfg, best, ready in best_found[:4]:
        cells = ", ".join(f"{x['hold']}d {signed(x['mean'])}" for x in sorted(ready, key=lambda x: x["hold"]))
        r.add("learned", f"Best holding time so far for {name(cfg)}: {count(best['hold'], 'day')} "
                         f"({signed(best['mean'], 1)} on average, {pct(best['wins'])} won).",
              f"Every checkpoint with {MIN_CHECKPOINT}+ positions: {cells}. The small accounts sell at this holding "
              "time.", items=best["n"], days=best["days"], unit="positions", day_unit="entry days",
              strength=strength(best["n"], best["days"], need=MIN_CHECKPOINT), link=LEARNING,
              key=f"best-hold:{cfg.get('structure')}:{cfg.get('bucket')}", weight=2)
        r.metric(f"learning.best_hold.{cfg.get('structure')}.{cfg.get('bucket')}", f"Best holding time, {name(cfg)}",
                 f"{best['hold']} days", items=best["n"], days=best["days"], unit="positions", day_unit="entry days",
                 strength=strength(best["n"], best["days"], need=MIN_CHECKPOINT))
    for _, cfg, most in sorted(pending, key=lambda t: t[0])[:2]:
        r.add("coming", f"Best holding time for {name(cfg)}: {most['n']} of {MIN_CHECKPOINT} positions valued at "
                        f"{count(most['hold'], 'day')}.", "Named once a checkpoint has 10 positions.",
              items=most["n"], days=most["days"], unit="positions", day_unit="entry days", strength="early",
              link=LEARNING, key=f"next-hold:{cfg.get('structure')}:{cfg.get('bucket')}", weight=0.9)


# ── 5. PAPER forward strategies, and the lab ─────────────────────────────────────────────────────
def paper_forward(src: Sources, r: Report) -> None:
    from desk.stocks.paper.report import MIN_SESSIONS, MIN_SESSIONS_PORTFOLIO, MIN_TRADES

    rep = src.views.get("paper") or {}
    accounts = [a for a in rep.get("accounts") or [] if isinstance(a, dict) and not a.get("baseline")]
    if not accounts:
        r.wait("PAPER strategies", "No PAPER report yet: strategies trade once a closed session's prices are "
                                   "stored and analysed.", PAPER)
    early = []
    for a in accounts:
        g = (a.get("grade") or {}).get("grade")
        title, sessions, trades = a.get("title") or a.get("id"), int(a.get("sessions") or 0), int(a.get("trades") or 0)
        r.metric(f"paper.grade.{a.get('id')}", f"PAPER {title}", g, items=trades, days=sessions, unit="trades",
                 strength="tested" if g in ("working", "backwards") else "early" if g == "early" else "suggestive")
        detail = (f"{signed(a.get('return'), 1)} against SPY's {signed(a.get('spy_return'), 1)} over the same "
                  f"{count(sessions, 'session')}; {count(trades, 'closed trade')}. "
                  + str((a.get("grade") or {}).get("text") or ""))
        common: dict[str, Any] = {"items": trades, "days": sessions, "unit": "closed trades", "link": PAPER}
        if g == "working":
            r.add("achievements", f"PAPER: {title} is working.", detail, strength="tested",
                  key=f"paper-working:{a.get('id')}", weight=6, **common)
        elif g == "promising":
            r.add("achievements", f"PAPER: {title} looks promising.", detail, strength="suggestive",
                  key=f"paper-promising:{a.get('id')}", weight=3, **common)
        elif g == "backwards":
            r.add("wrong", f"PAPER: {title} is doing the opposite of its idea.", detail, strength="tested",
                  key=f"paper-backwards:{a.get('id')}", weight=6, **common)
        elif g == "not_working" and (a.get("return") or 0) < (a.get("spy_return") or 0):
            r.add("wrong", f"PAPER: {title} is behind SPY and no better than its baseline.", detail,
                  strength="suggestive", key=f"paper-behind:{a.get('id')}", weight=2, **common)
        elif g == "early":
            need = MIN_SESSIONS_PORTFOLIO if a.get("style") in ("rebalance", "hold") else MIN_SESSIONS
            if (a.get("return") or 0) > (a.get("spy_return") or 0) and sessions >= 20:
                r.add("achievements", f"PAPER: {title} is ahead of SPY so far.", detail, strength="early",
                      key=f"paper-ahead:{a.get('id')}", weight=1.2, **common)
            early.append((need - sessions, a, need))
    for left, a, need in sorted(early, key=lambda t: t[0])[:2]:
        sessions, trades = int(a.get("sessions") or 0), int(a.get("trades") or 0)
        wait = f"{sessions} of {need} sessions" + (
            f", {trades} of {MIN_TRADES} closed trades" if a.get("style") == "event" else "")
        r.add("coming", f"First PAPER grade for {a.get('title')}: {wait}"
                        + (f", {weeks(max(left, 0) / 5)}." if left > 0 else "."),
              "Graded against its random baseline and SPY with a bootstrap, Holm-corrected across strategies.",
              items=trades, days=sessions, unit="closed trades", strength="early", link=PAPER,
              key=f"next-paper:{a.get('id')}", weight=0.9)
    best = max((a for a in accounts if (a.get("best") or {}).get("pnl")), key=lambda a: a["best"]["pnl"], default=None)
    if best is not None and best["best"]["pnl"] > 0:
        r.add("achievements", f"Best PAPER trade: {best['best']['symbol']} in {best.get('title')}, "
                              f"{money(best['best']['pnl'])}.", "A closed forward PAPER trade.", items=1, days=1,
              unit="trade", strength="early", link=PAPER, key="paper-best", weight=1.2)
    invested = [a["invested"] for a in accounts if isinstance(a.get("invested"), (int, float))]
    if invested:
        r.add("behaviour", f"PAPER strategies are {sum(invested) / len(invested):.0%} invested on average.",
              f"Across {count(len(invested), 'forward strategy')} on their latest session.", items=len(invested),
              days=max(int(a.get("sessions") or 0) for a in accounts), unit="strategies", strength="suggestive",
              link=PAPER, key="paper-invested", weight=0.5)
    if _has(src.research, "stk_lab_strategies"):
        assert src.research is not None
        for x in _rows(src.research, "SELECT id, created_at, retired_at, evidence_json FROM stk_lab_strategies "
                                     "ORDER BY created_at DESC LIMIT 3"):
            evidence = json.loads(x["evidence_json"] or "{}")
            months = int(evidence.get("oos_months") or evidence.get("months") or 0)
            r.add("learned", f"The lab promoted {x['id']} on {short(x['created_at'])}"
                             + (" (since retired)." if x["retired_at"] else "; it trades in PAPER."),
                  "Its picks beat the committee's top 10 on months it never saw; now it must prove itself forward.",
                  items=months, days=months, unit="unseen months", day_unit="months", strength="tested",
                  link=PAPER, key=f"lab:{x['id']}", weight=2.5)


# ── 6. Evidence: list and desk grades, weights, options truth ───────────────────────────────────
GRADE_WORDS = {"working": "working", "promising": "promising", "backwards": "backwards", "not_working": "not working",
               "early": "too early", "info": "no claim"}


def evidence(src: Sources, r: Report) -> None:
    from desk.stocks.validation import MIN_INDEPENDENT, PRIMARY

    rep = src.views.get("evidence")
    adopted = None
    if _has(src.research, "stk_validation"):
        assert src.research is not None
        row = src.research.execute("SELECT report_json, created_at FROM stk_validation ORDER BY id DESC LIMIT 1").fetchone()
        if row is not None:
            stored = json.loads(row["report_json"])
            adopted = stored.get("committee_weights")
            rep = rep or stored
    if not rep:
        r.wait("Evidence", "No evidence evaluation yet: it runs by itself once a week while collection is on.", PROOF)
        return
    early = []
    for kind, records in (("list", rep.get("lists") or {}), ("desk", rep.get("desks") or {})):
        for ident, v in records.items():
            g = v.get("grade") or {}
            source = g.get("source", "live")
            stat = (v.get(source) or {}).get(PRIMARY if kind == "list" else "ic") or {}
            n, months = int(stat.get("n") or 0), round(stat.get("independent") or 0)
            title = v.get("title") or v.get("label") or ident
            replay = source == "rebuilt"
            label = f"{'Rebuilt history' if replay else 'Live record'}: the {title} {kind}"
            common: dict[str, Any] = {"items": n, "days": months, "unit": "observations", "day_unit": "independent months",
                      "link": PROOF}
            r.metric(f"evidence.{kind}.{ident}", f"{title} ({kind})", g.get("grade"), items=n, days=months,
                     unit="observations", day_unit="independent months",
                     strength="tested" if g.get("grade") in ("working", "backwards") else "early")
            if g.get("grade") == "working":
                r.add("replay" if replay else "achievements", f"{label} is working.", g.get("text", ""),
                      strength="tested", key=f"evidence-working:{kind}:{ident}", weight=5, **common)
            elif g.get("grade") == "backwards":
                r.add("replay" if replay else "wrong", f"{label} is backwards: it did the opposite of its claim.",
                      g.get("text", ""), strength="tested", key=f"evidence-backwards:{kind}:{ident}", weight=5,
                      **common)
            elif g.get("grade") == "promising":
                r.add("replay" if replay else "learned", f"{label} is promising.", g.get("text", ""),
                      strength="suggestive", key=f"evidence-promising:{kind}:{ident}", weight=2, **common)
            elif g.get("grade") == "early":
                early.append((MIN_INDEPENDENT - months, title, kind, n, months))
            periods = v.get("periods") or {}
            later, earlier = (periods.get("survivorship_free") or {}), (periods.get("earlier") or {})
            a, b = later.get("mean_excess_spy"), earlier.get("mean_excess_spy")
            if a is not None and b is not None and a * b < 0 and kind == "list":
                r.add("replay", f"Rebuilt history: the {title} list flips sign across the survivorship boundary.",
                      f"{signed(a, 1)} a month against SPY in the survivorship-free years, {signed(b, 1)} in the "
                      "earlier survivors-only years, which flatter lists that buy.",
                      items=int(later.get("n") or 0) + int(earlier.get("n") or 0),
                      days=round((later.get("independent") or 0) + (earlier.get("independent") or 0)),
                      unit="observations", day_unit="independent months", strength="early", link=PROOF,
                      key=f"survivorship:{ident}", weight=1)
    for _, title, kind, n, months in sorted(early, key=lambda t: t[0])[:1]:
        r.add("coming", f"First grade for the {title} {kind}: {months} of {MIN_INDEPENDENT} independent months.",
              "One independent month is a non-overlapping month of results.", items=n, days=months,
              unit="observations", day_unit="independent months", strength="early", link=PROOF,
              key=f"next-evidence:{kind}", weight=0.6)
    committee = rep.get("committee") or {}
    if committee.get("candidate"):
        weights = ", ".join(f"{k} {v:.2f}" for k, v in sorted(committee["candidate"].items(), key=lambda kv: -kv[1]) if v)
        state = "adopted by you" if adopted else "proposed, not adopted (adopting stays your choice)"
        r.add("replay", f"Rebuilt history: learned committee weights — {state}.",
              f"{weights}. {committee.get('why', '')}", items=int(committee.get("oos_months") or 0),
              days=int(committee.get("oos_months") or 0), unit="unseen months", day_unit="months",
              strength=strength(int(committee.get("oos_months") or 0), int(committee.get("oos_months") or 0),
                                need=6, need_days=6), link=PROOF, key="committee-weights", weight=1.5)
    options = rep.get("options") or {}
    ivr = options.get("iv_vs_realized") or {}
    n, below = int(ivr.get("n") or 0), ivr.get("realized_below_implied")
    if n and below is not None:
        days = _option_days(src.market, r.now)
        rich = below >= 0.5
        r.add("learned", f"Options were usually {'rich' if rich else 'cheap'}: realized volatility came in "
                         f"{'below' if rich else 'above'} the implied volatility {pct(below if rich else 1 - below)} "
                         "of the time over the following month.",
              f"{n:,} stock-days whose month has passed. Median implied minus realized "
              f"{signed(ivr.get('median_iv_minus_rv'), 1)} (volatility points).", items=n, days=days,
              unit="stock-days", day_unit="days", strength=strength(n, days, need=30), link=PROOF,
              key="iv-rich-cheap", weight=1.5)
    cheap = (options.get("cheap_iv") or {}).get("grade") or {}
    if cheap.get("grade") in ("working", "backwards"):
        r.add("achievements" if cheap["grade"] == "working" else "wrong",
              f"The cheap-IV list's claim is {GRADE_WORDS[cheap['grade']]}.", cheap.get("text", ""),
              items=int((options.get("cheap_iv") or {}).get("n") or 0),
              days=round((options.get("cheap_iv") or {}).get("independent") or 0), unit="observations",
              day_unit="independent months", strength="tested", link=PROOF, key="cheap-iv-claim", weight=4)


def _option_days(market: sqlite3.Connection | None, now: datetime) -> int:
    if not _has(market, "stk_option_daily"):
        return 0
    assert market is not None
    before = (cal.new_york(now).date() - timedelta(days=30)).toordinal()
    return int(market.execute("SELECT COUNT(DISTINCT day) FROM stk_option_daily WHERE day <= ?", (before,)).fetchone()[0])


# ── 7. Replays: kept apart, always labelled ──────────────────────────────────────────────────────
def replays(src: Sources, r: Report) -> None:
    found = False
    backtest = src.views.get("backtest") or {}
    for a in backtest.get("accounts") or []:
        if not isinstance(a, dict) or a.get("baseline"):
            continue
        g = (a.get("grade") or {}).get("grade")
        if g in ("working", "backwards", "promising"):
            found = True
            title = a.get("title") or a.get("id")
            verdict = {"working": "worked", "backwards": "did the opposite of its idea"}.get(g, "looked promising")
            r.add("replay", f"Replay: {title} {verdict} on past sessions.",
                  f"{signed(a.get('return'), 1)} against SPY's {signed(a.get('spy_return'), 1)}; replayed history, "
                  "never a forward grade or a promotion.", items=int(a.get("trades") or 0),
                  days=int(a.get("sessions") or 0), unit="trades", strength="tested" if g != "promising" else "suggestive",
                  link=PAPER, key=f"replay-grade:{a.get('id')}", weight=1)
    real = (backtest.get("real_price") or {}).get("strategies") or {}
    for ident, s in real.items():
        if not isinstance(s, dict) or not s.get("trades") or ident == "random_calls":
            continue
        found = True
        r.add("replay", f"Replay on real option prices: {ident.replace('_', ' ')} returned {signed(s.get('return'), 1)}.",
              f"{count(int(s['trades']), 'trade')}, {pct(s.get('win_rate'))} won, from {s.get('first')} to {s.get('last')}.",
              items=int(s["trades"]), days=int(s["trades"]), unit="trades", day_unit="trades",
              strength=strength(int(s["trades"]), int(s["trades"]), need=20), link=PAPER, key=f"real:{ident}", weight=1)
    research = src.views.get("option_research") or {}
    for ident, s in (research.get("strategies") or {}).items():
        trades = int(s.get("trades") or s.get("weeks") or 0)
        if not trades:
            continue
        found = True
        versus = s.get("versus_baseline") or s.get("versus_put_write") or {}
        tested = bool(versus) and (versus.get("p_not_better") or 1) < 0.05 and (versus.get("mean_gap") or 0) > 0
        result = (f"returned {signed(s.get('return'), 1)}" if s.get("return") is not None else
                  f"averaged {signed(s.get('mean_weekly'), 2)} a week")
        r.add("replay", f"Preregistered replay: {s.get('title') or ident.replace('_', ' ')} {result}.",
              f"{count(trades, 'trade' if s.get('trades') else 'week')} on real past prices; "
              + (f"{signed(versus.get('mean_gap'), 1)} a day against its baseline over "
                 f"{count(int(versus.get('days') or 0), 'day')}." if versus else "no baseline comparison yet."),
              items=trades, days=int(versus.get("days") or trades), unit="trades" if s.get("trades") else "weeks",
              day_unit="days", strength=strength(trades, int(versus.get("days") or 0), need=20, tested=tested),
              link=PAPER, key=f"option-research:{ident}", weight=1)
    lotto = src.views.get("lotto_history") or {}
    run = (lotto.get("kestrel_vs_random") or {}).get("run") or {}
    best = ((lotto.get("variants") or {}).get("kestrel_run") or {}).get("best") or {}
    if run or best:
        found = True
        tested = bool(run) and (run.get("p_not_better") or 1) < 0.05 and (run.get("mean_gap") or 0) > 0
        picks = int(((lotto.get("variants") or {}).get("kestrel_run") or {}).get("picks") or 0)
        r.add("replay", "Lotto replay on real past prices: KESTREL's picks "
                        + (f"{'beat' if (run.get('mean_gap') or 0) > 0 else 'trailed'} random lottos by "
                           f"{signed(abs(run.get('mean_gap') or 0), 0)[1:]} a pick a day" if run else "are replayed")
                        + (f"; best {best.get('symbol')} paid {best.get('multiple', 0):.1f}×." if best else "."),
              f"{count(picks, 'pick')} over {count(int(lotto.get('sessions') or 0), 'session')}; replayed, not forward.",
              items=picks, days=int(run.get("days") or lotto.get("sessions") or 0),
              strength=strength(picks, int(run.get("days") or 0), need=20, tested=tested), link=PAPER,
              key="lotto-replay", weight=1)
    if not found:
        r.add("replay", "No replay results yet.", "Replays run on downloaded past prices (Massive) and are shown here "
                                                  "apart from the forward record.", items=0, days=0, strength="early",
              link=PAPER, key="replay-waiting", weight=0)


# ── 8. Operational health that weakens the evidence ─────────────────────────────────────────────
def health(src: Sources, r: Report) -> None:
    since = (cal.new_york(r.now).date() - timedelta(days=RECENT_DAYS)).toordinal()
    if _has(src.research, "stk_play_marks"):
        assert src.research is not None
        marks = {x["status"]: (int(x["n"]), int(x["days"])) for x in _rows(
            src.research, "SELECT status, COUNT(*) n, COUNT(DISTINCT day) days FROM stk_play_marks "
                          "WHERE day >= ? AND twin = 0 GROUP BY status", (since,))}
        total = sum(n for n, _ in marks.values())
        weak = sum(marks.get(s, (0, 0))[0] for s in ("estimated", "modelled"))
        days = max((d for _, d in marks.values()), default=0)
        if total:
            r.metric("health.estimated_share", "Share of play marks estimated or modelled", round(weak / total, 4),
                     items=total, days=days, unit="marks", day_unit="days")
        if total and weak / total >= 0.05:
            r.add("wrong", f"{weak / total:.0%} of the last {RECENT_DAYS} days' play marks were estimated or "
                           "modelled, not quoted.",
                  f"{weak:,} of {total:,} daily marks (the laptop was off, or a leg had no closing quote). They are "
                  "never graded or learned from, so learning waits for quoted marks.", items=total, days=days,
                  unit="marks", day_unit="days", strength=strength(total, days, need=30), link="#plays/history",
                  key="estimated-marks", weight=1 + 3 * weak / total)
        if _has(src.research, "stk_play_quarantine"):
            q = src.research.execute("SELECT COUNT(*) FROM stk_play_quarantine WHERE twin = 0").fetchone()[0]
            if q:
                r.add("wrong", f"{count(int(q), 'play')} quarantined (a split or a halt ended its record).",
                      "Quarantined plays are never graded on mismatched prices.", items=int(q), days=0,
                      strength="early", link="#plays/history", key="quarantine", weight=0.5)
    if _has(src.market, "stk_jobs"):
        assert src.market is not None
        jobs = _rows(src.market, "SELECT name, last_run_at, last_ok_at, last_error, detail_json FROM stk_jobs")
        failing = sorted(x["name"] for x in jobs if x["last_error"] and (
            x["last_ok_at"] is None or (x["last_run_at"] or "") > x["last_ok_at"]))
        if failing:
            r.add("wrong", f"{count(len(failing), 'collection job')} failing: {', '.join(failing[:6])}"
                           + ("…" if len(failing) > 6 else "."),
                  "Missing data means fewer graded plays and quotes; System shows each job's last error.",
                  items=len(failing), days=0, unit="jobs", day_unit="days", strength="early", link=SYSTEM,
                  key="jobs-failing", weight=1.5)
        for x in jobs:
            if x["name"] == "schwab_history":
                refused = int((json.loads(x["detail_json"] or "{}") or {}).get("refused") or 0)
                if refused:
                    r.add("wrong", f"Schwab's earlier price history was refused for {count(refused, 'stock')}.",
                          "Their closes did not reconcile with stored prices, so their earlier years stay out of "
                          "rebuilt months and replays.", items=refused, days=0, unit="stocks", day_unit="days",
                          strength="early", link=SYSTEM, key="schwab-refused", weight=0.8)


# ── changed since last week, and the headline ────────────────────────────────────────────────────
def changes(r: Report, previous: dict[str, Any] | None) -> None:
    if not previous:
        r.add("changed", "No earlier weekly snapshot to compare with yet.",
              "A snapshot is saved once a week, after Friday's close; next week this section lists what changed.",
              items=0, days=0, strength="early", link=LEARNING, key="changed-waiting", weight=0)
        return
    before: dict[str, Any] = previous.get("metrics") or {}
    when = short(previous.get("generated_at") or to_iso(r.now))
    for key, m in r.metrics.items():
        old = (before.get(key) or {}).get("value")
        new = m["value"]
        if old == new or (old is None and new in (None, 0)):
            continue
        if isinstance(new, (int, float)) and isinstance(old, (int, float)) and not isinstance(new, bool):
            delta = new - old
            if isinstance(new, float) and abs(delta) < 1e-3:
                continue
            text = (f"{m['label']}: {_fmt(old)} → {_fmt(new)} ({'+' if delta > 0 else '−'}{_fmt(abs(delta))}).")
        else:
            was = GRADE_WORDS.get(str(old), old) if old is not None else "new"
            text = f"{m['label']}: {was} → {GRADE_WORDS.get(str(new), new)}."
        r.add("changed", text, f"Since the snapshot of {when}.", items=m["items"], days=m["days"], unit=m["unit"],
              day_unit=m["day_unit"], strength=m["strength"], link=LEARNING, key=f"changed:{key}",
              weight=2 if not isinstance(new, (int, float)) else 1)
    seen = {i["key"] for s in previous.get("sections") or [] for i in s.get("items") or []}
    fresh = [i for i in r.items if i["section"] in ("wrong", "achievements", "learned") and i["key"] not in seen]
    for i in sorted(fresh, key=lambda i: -i["weight"])[:5]:
        r.add("changed", f"New: {i['headline']}", i["detail"], items=i["evidence"]["items"], days=i["evidence"]["days"],
              unit=i["evidence"]["unit"], day_unit=i["evidence"]["day_unit"], strength=i["strength"], link=i["link"],
              key=f"changed-new:{i['key']}", weight=i["weight"])


def _fmt(x: float) -> str:
    return f"{x:,}" if isinstance(x, int) else f"{x:,.4g}" if abs(x) < 10 else f"{x:,.0f}"


def headline(r: Report) -> list[str]:
    """Two to four sentences: the scale of the evidence, then the most important wrong, achievement and next step."""
    graded = (r.metrics.get("plays.graded") or {}).get("value") or 0
    sessions = (r.metrics.get("plays.graded") or {}).get("days") or 0
    matured = (r.metrics.get("learning.matured") or {}).get("value") or 0
    groups = (r.metrics.get("plays.calibrated_groups") or {}).get("value") or 0
    if graded or matured:
        first = (f"KESTREL has graded {graded:,} plays over {count(sessions, 'session')}"
                 + (f" and calibrated {count(groups, 'odds group')}" if groups else ", none calibrated yet")
                 + (f"; Learning has {matured:,} finished research positions." if matured else "."))
    else:
        first = "KESTREL is still collecting: no play has been graded and no Learning position has finished yet."
    out = [first]

    def top(section: str) -> dict[str, Any] | None:
        found = [i for i in r.items if i["section"] == section and i["evidence"]["items"] > 0]
        return max(found, key=lambda i: (i["weight"] + STRENGTHS.index(i["strength"])), default=None)

    for section, lead in (("wrong", "Biggest problem"), ("achievements", "Best news")):
        i = top(section)
        if i is not None:
            out.append(f"{lead} ({i['strength']}): {i['headline']}")
    coming = [i for i in r.items if i["section"] == "coming" and i["evidence"]["items"] > 0]
    if coming:
        out.append("Next: " + max(coming, key=lambda i: i["weight"])["headline"])
    return out[:4]


# ── build ────────────────────────────────────────────────────────────────────────────────────────
LOOPS = (plays_calibration, plays_learning, plays_records, plays_behaviour, workspace, paper_forward, evidence,
         replays, health)


def compose(src: Sources, now: datetime, previous: dict[str, Any] | None = None) -> dict[str, Any]:
    r = Report(now)
    for loop in LOOPS:
        try:
            loop(src, r)
        except sqlite3.Error:      # a database from before that loop existed, or one being repaired
            r.add("wrong", f"Part of the report could not be read ({loop.__name__.replace('_', ' ')}).",
                  "That database may be from an older version or in use; the rest of the report is unaffected.",
                  items=0, days=0, strength="early", link=SYSTEM, key=f"unreadable:{loop.__name__}", weight=0.2)
    changes(r, previous)
    sections = []
    for ident, title in SECTIONS:
        items = sorted((i for i in r.items if i["section"] == ident), key=lambda i: -i["weight"])
        sections.append({"id": ident, "title": title, "items": items})
    return {"version": VERSION, "generated_at": to_iso(now), "mode": "PAPER", "headline": headline(r),
            "sections": sections, "metrics": r.metrics,
            "previous": None if not previous else previous.get("generated_at"),
            "note": "Computed from this computer's own databases, read-only. Strength: early (below that loop's own "
                    f"threshold or seen on fewer than {MIN_DAYS} separate days), suggestive, tested (that loop's own "
                    "significance test or grade). Replays are kept apart from forward results."}


@contextmanager
def sources(paths: DeskPaths) -> Iterator[Sources]:
    from desk.stocks import learning as lab
    from desk.stocks import lotto_history, option_research
    from desk.stocks.evidence_job import document
    from desk.stocks.repo import MARKET_FILE, RESEARCH_FILE
    from desk.stocks.runner import saved

    def open_ro(name: str) -> sqlite3.Connection | None:
        path = paths.db_dir / name
        if not path.exists():
            return None
        conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    src = Sources(open_ro(RESEARCH_FILE), open_ro(MARKET_FILE), open_ro(lab.FILE))
    try:
        try:
            src.views["paper"] = saved(paths, "paper")
        except Exception:  # an unreadable saved view: that loop says it is waiting
            src.views["paper"] = None
        src.views["evidence"] = document(paths, "report")
        src.views["backtest"] = document(paths, "report", kind="backtest")
        src.views["option_research"] = option_research.load(paths.home)
        src.views["lotto_history"] = lotto_history.load(paths.home / lotto_history.REPORT)
        yield src
    finally:
        for conn in (src.research, src.market, src.learning):
            if conn is not None:
                conn.close()


def week_of(now: datetime) -> str:
    year, week, _ = cal.new_york(now).date().isocalendar()
    return f"{year}-W{week:02d}"


def previous_snapshot(research: sqlite3.Connection | None, now: datetime) -> dict[str, Any] | None:
    """The newest weekly snapshot from an earlier week than ``now``'s."""
    if not _has(research, TABLE):
        return None
    assert research is not None
    row = research.execute(f"SELECT report_json FROM {TABLE} WHERE week < ? ORDER BY week DESC LIMIT 1",  # noqa: S608
                           (week_of(now),)).fetchone()
    return None if row is None else dict(json.loads(row[0]))


def snapshots(research: sqlite3.Connection | None, limit: int = 12) -> list[dict[str, Any]]:
    if not _has(research, TABLE):
        return []
    assert research is not None
    return [{"week": x["week"], "created_at": x["created_at"], "headline": json.loads(x["headline"] or "[]")}
            for x in research.execute(f"SELECT week, created_at, json_extract(report_json, '$.headline') headline "  # noqa: S608
                                      f"FROM {TABLE} ORDER BY week DESC LIMIT ?", (limit,))]


def build(paths: DeskPaths, now: datetime | None = None) -> dict[str, Any]:
    """The report from the live databases, read-only."""
    now = now or utc_now()
    with sources(paths) as src:
        out = compose(src, now, previous_snapshot(src.research, now))
        out["snapshots"] = snapshots(src.research)
        return out


def snapshot_due(now: datetime) -> bool:
    """After Friday's close (16:30 New York) or over the weekend: the week's results are in."""
    local = cal.new_york(now)
    return (local.weekday() == 4 and (local.hour, local.minute) >= (16, 30)) or local.weekday() >= 5


def weekly(paths: DeskPaths, research: sqlite3.Connection, now: datetime) -> bool:
    """Append this week's snapshot, at most once a week (``research`` is the worker's writable connection)."""
    if not snapshot_due(now):
        return False
    week = week_of(now)
    if research.execute(f"SELECT 1 FROM {TABLE} WHERE week = ?", (week,)).fetchone():  # noqa: S608
        return False
    report = build(paths, now)
    report.pop("snapshots", None)
    research.execute(f"INSERT OR IGNORE INTO {TABLE}(week, created_at, report_json) VALUES (?, ?, ?)",  # noqa: S608
                     (week, to_iso(now), json.dumps(report, allow_nan=False, default=str)))
    return True


# ── Markdown ─────────────────────────────────────────────────────────────────────────────────────
def evidence_text(i: dict[str, Any]) -> str:
    e = i["evidence"]
    if not e["items"]:
        return str(i["strength"])
    unit = e["unit"] if e["items"] != 1 else e["unit"].rstrip("s")
    days = f" · {e['days']:,} {e['day_unit'] if e['days'] != 1 else e['day_unit'].rstrip('s')}" if e["days"] else ""
    return f"{e['items']:,} {unit}{days} · {i['strength']}"


def render_markdown(report: dict[str, Any]) -> str:
    at = datetime.fromisoformat(report["generated_at"])
    out = ["# KESTREL Learning report", "",
           f"{cal.new_york(at):%b %d, %Y %H:%M} ET · PAPER and research only · computed from this computer's own "
           "databases", "", "## Headline", "", " ".join(report["headline"]), ""]
    for s in report["sections"]:
        if not s["items"]:
            continue
        out += [f"## {s['title']}", ""]
        for i in s["items"]:
            out.append(f"- **{i['headline']}** {i['detail']} _({evidence_text(i)})_".replace(" ** ", " "))
        out.append("")
    out += ["---", "", report["note"], ""]
    return "\n".join(out)


__all__ = ["MIN_DAYS", "SECTIONS", "STRENGTHS", "Report", "Sources", "build", "compose", "headline",
           "previous_snapshot", "render_markdown", "snapshot_due", "snapshots", "sources", "strength", "week_of",
           "weekly"]
