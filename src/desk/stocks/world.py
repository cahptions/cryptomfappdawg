"""World watch: events around the world that move supply chains, read from GDELT's 15-minute event files.

GDELT reads news in 65+ languages, machine-translates it and codes each reported event: who did what (fighting,
threats, sanctions, strikes and protests, seizures), where, and in how many articles. KESTREL keeps daily totals
per country and kind, plus the most-covered source articles, and compares the last two days with the 30 days
before them. A country tied to a supply chain (cobalt in DR Congo, leading-edge chips in Taiwan, oil through
Hormuz…) whose coverage of those events jumps is a supply shock for the stocks that depend on it, or a tailwind for
the ones that compete with it.

The supply map below is KESTREL's own, written down as hypotheses with their reasons; the News desk is graded like
every other desk before its view counts. Nothing here is a forecast.
"""

from __future__ import annotations

import csv
import io
import math
import sqlite3
import zipfile
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

import httpx

from desk.core.errors import DeskError
from desk.stocks import calendar as cal

AGENT = "KESTRELStocks/1.0 (personal market research; local app)"
LATEST = "https://data.gdeltproject.org/gdeltv2/lastupdate.txt"
FILE = "https://data.gdeltproject.org/gdeltv2/{stamp}.export.CSV.zip"
BACKFILL_DAYS = 32              # status() compares the last two days with the 30 before them: day today-32 onward
FILES_PER_PASS = 40             # newest first; a first load of 32 days (about 3,100 files) takes a few hours
MAX_FILE = 5_000_000

# CAMEO root/base codes → the kinds of events that disrupt supply
KINDS = {"18": "conflict", "19": "conflict", "20": "conflict", "13": "threats", "14": "unrest", "17": "coercion"}
SANCTIONS = {"163", "172"}      # impose embargo, boycott or sanctions; impose administrative sanctions
WEIGHT = {"conflict": 1.0, "sanctions": 1.0, "threats": 0.6, "unrest": 0.6, "coercion": 0.6}
ALL_KINDS = tuple(WEIGHT)


@dataclass(frozen=True)
class Node:
    id: str
    label: str
    countries: tuple[str, ...]           # GDELT's FIPS country codes; "*" is every country
    why: str
    hurt_sic: tuple[str, ...] = ()       # SIC code prefixes of companies that depend on this supply
    hurt: tuple[str, ...] = ()           # and named companies
    helped_sic: tuple[str, ...] = ()     # companies that compete with it or gain from scarcity
    helped: tuple[str, ...] = ()
    kinds: tuple[str, ...] = ALL_KINDS


NODES = (
    Node("taiwan_chips", "Leading-edge chips · Taiwan", ("TW",),
         "Taiwan makes most of the world's leading-edge chips (TSMC); hardware and car makers depend on them.",
         ("3571", "3572", "3576", "3577", "3661", "3663", "3674", "3679", "3711"), ("AAPL", "NVDA", "AMD", "QCOM"),
         (), ("INTC", "GFS", "TXN")),
    Node("china_minerals", "Rare earths, gallium, germanium, graphite · China", ("CH",),
         "China refines most rare earths and processes most gallium, germanium and battery graphite; its export "
         "controls hit chip, magnet, battery and defence buyers.",
         ("3674", "3621", "3691", "3711", "3714", "3812"), (), (), ("MP", "UAMY"),
         ("sanctions", "coercion", "threats")),
    Node("myanmar_rare_earths", "Heavy rare earths · Myanmar", ("BM",),
         "Myanmar mines much of the heavy rare earths (dysprosium, terbium) used in EV and wind-turbine magnets.",
         ("3621", "3711", "3714"), (), (), ("MP",), ("conflict", "unrest", "coercion")),
    Node("cobalt", "Cobalt · DR Congo & Zambia", ("CG", "ZA"),
         "DR Congo mines about 70% of the world's cobalt, used in EV and phone batteries.",
         ("3691", "3711", "3714")),
    Node("copper", "Copper · Chile & Peru", ("CI", "PE"),
         "Chile and Peru mine about 35% of the world's copper; strikes and unrest there lift its price.",
         ("3351", "3357"), (), ("1000", "1021"), ("FCX", "SCCO", "TECK")),
    Node("lithium", "Lithium · Chile, Argentina, Bolivia, Zimbabwe", ("CI", "AR", "BL", "ZI"),
         "The lithium triangle and Zimbabwe hold most lithium reserves outside Australia.", ("3691", "3711")),
    Node("nickel", "Nickel · Indonesia & Philippines", ("ID", "RP"),
         "Indonesia and the Philippines mine over half the world's nickel (stainless steel, batteries).",
         ("3312", "3691", "3711")),
    Node("neon", "Neon & chip gases · Ukraine", ("UP",),
         "Ukraine supplied about half the world's semiconductor-grade neon before 2022.", ("3674",)),
    Node("russia", "Palladium, enriched uranium, fertilizer, gas · Russia", ("RS",),
         "Russia supplies about 40% of palladium and large shares of enriched uranium, potash, nitrogen fertilizer "
         "and gas; sanctions or conflict raise prices for other producers.",
         ("3714",), (), ("1094", "2870", "1311"), ("CCJ", "SBSW", "CF", "MOS", "NTR", "LNG"),
         ("sanctions", "conflict", "threats", "coercion")),
    Node("hormuz", "Oil through the Strait of Hormuz", ("IR", "IZ", "SA", "KU", "AE"),
         "About a fifth of the world's oil passes the Strait of Hormuz; trouble there lifts oil prices.",
         ("4512", "4513", "4522"), ("DAL", "UAL", "AAL", "LUV"), ("1311", "1381", "1382", "2911", "4412"),
         ("XOM", "CVX", "OXY", "COP", "FRO"), ("conflict", "threats", "coercion", "sanctions")),
    Node("red_sea", "Shipping through the Red Sea & Suez", ("YM", "EG"),
         "About 12% of world trade passes the Red Sea and Suez Canal; attacks reroute ships around Africa, raising "
         "freight rates and delaying imports.",
         ("5311", "5331", "5651"), (), ("4412", "4400"), ("ZIM", "MATX"), ("conflict", "threats", "coercion")),
    Node("venezuela", "Heavy crude · Venezuela", ("VE",),
         "Venezuela holds the largest oil reserves; sanctions or conflict move heavy-crude supply to US refiners.",
         (), (), ("1311",), ("XOM", "CVX", "OXY", "COP"), ("conflict", "threats", "sanctions", "coercion")),
    Node("uranium", "Uranium · Kazakhstan & Niger", ("KZ", "NG"),
         "Kazakhstan mines about 40% of the world's uranium; Niger supplies much of Europe's.", (), (),
         ("1094",), ("CCJ", "UEC", "UUUU", "NXE", "DNN")),
    Node("potash", "Potash · Belarus", ("BO",),
         "Belarus and Russia together supply about 40% of the world's potash fertilizer.", (), (), ("2870", "1474"),
         ("MOS", "NTR", "CF", "IPI"), ("sanctions", "conflict", "threats", "coercion")),
    Node("grain", "Wheat & corn · Ukraine", ("UP",),
         "Ukraine and Russia export about a quarter of the world's wheat; war at the ports lifts grain prices.",
         ("2040", "2050", "2090"), (), (), ("ADM", "BG"), ("conflict",)),
    Node("bauxite", "Bauxite · Guinea", ("GV",),
         "Guinea mines about a quarter of the world's bauxite, the ore of aluminum.", (), (), ("3334", "3350"),
         ("AA", "CENX")),
    Node("global_conflict", "Fighting worldwide", ("*",),
         "When fighting around the world rises, defence makers and gold have historically drawn buyers.", (), (),
         ("3812", "3795", "3760", "3721", "3728", "3489", "1040"),
         ("LMT", "NOC", "RTX", "GD", "LHX", "HII", "KTOS", "NEM", "AEM", "GOLD"), ("conflict",)),
)
BY_ID = {n.id: n for n in NODES}


# ── reading GDELT ─────────────────────────────────────────────────────────────────────────────────
def kind_of(root: str, base: str) -> str | None:
    if base in SANCTIONS:
        return "sanctions"
    return KINDS.get(root)


@dataclass
class Totals:
    days: dict[tuple[int, str, str], list[float]] = field(default_factory=lambda: defaultdict(lambda: [0, 0, 0.0]))
    sources: dict[tuple[int, str, str], dict[str, tuple[int, str]]] = field(default_factory=lambda: defaultdict(dict))
    events: int = 0


def parse_events(data: bytes) -> Totals:
    """One GDELT 2.0 export file (zipped tab-separated, 61 columns) → totals per day, country and kind."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            raw = z.read(z.namelist()[0])
    except (zipfile.BadZipFile, IndexError, KeyError) as exc:
        raise DeskError("GDELT sent an unreadable event file") from exc
    out = Totals()
    for row in csv.reader(io.StringIO(raw.decode("utf-8", "replace")), delimiter="\t"):
        if len(row) < 61:
            continue
        kind = kind_of(row[28], row[27])
        country = row[53]
        if kind is None or not country or len(row[59]) < 8:
            continue
        try:
            day = date(int(row[59][:4]), int(row[59][4:6]), int(row[59][6:8])).toordinal()
            articles, goldstein = int(row[33] or 0), float(row[30] or 0)
        except ValueError:
            continue
        key = (day, country, kind)
        t = out.days[key]
        t[0] += 1
        t[1] += articles
        t[2] += goldstein
        url = row[60]
        if url.startswith("https://") or url.startswith("http://"):
            best = out.sources[key]
            if url not in best or best[url][0] < articles:
                best[url] = (articles, row[52][:120])
        out.events += 1
    return out


def save_file(conn: sqlite3.Connection, name: str, totals: Totals, now: datetime) -> None:
    from desk.storage.db import transaction

    with transaction(conn):
        if conn.execute("SELECT 1 FROM stk_world_files WHERE name=?", (name,)).fetchone():
            return
        for (day, country, kind), (events, articles, goldstein) in totals.days.items():
            conn.execute("INSERT INTO stk_world_daily VALUES (?,?,?,?,?,?) ON CONFLICT(day,country,kind) DO UPDATE SET "
                         "events=events+excluded.events, articles=articles+excluded.articles, "
                         "goldstein=goldstein+excluded.goldstein", (day, country, kind, int(events), int(articles),
                                                                    float(goldstein)))
        for (day, country, kind), best in totals.sources.items():
            for url, (articles, place) in sorted(best.items(), key=lambda kv: -kv[1][0])[:3]:
                conn.execute("INSERT INTO stk_world_sources VALUES (?,?,?,?,?,?) ON CONFLICT DO UPDATE SET "
                             "articles=MAX(articles, excluded.articles)", (day, country, kind, url[:500], articles, place))
            conn.execute("DELETE FROM stk_world_sources WHERE day=? AND country=? AND kind=? AND url NOT IN (SELECT url "
                         "FROM stk_world_sources WHERE day=? AND country=? AND kind=? ORDER BY articles DESC LIMIT 5)",
                         (day, country, kind, day, country, kind))
        conn.execute("INSERT INTO stk_world_files VALUES (?,?,?)", (name, now.isoformat(), totals.events))


def window_start(now: datetime) -> datetime:
    """Midnight UTC, ``BACKFILL_DAYS`` before New York's date: every day ``status`` reads is whole (its days are UTC
    days, and New York's date is never ahead of UTC's), so the oldest baseline day counts too."""
    return datetime.combine(cal.new_york(now).date() - timedelta(days=BACKFILL_DAYS), time(0), UTC)


def stamps(now: datetime, days: int = BACKFILL_DAYS, *, since: datetime | None = None) -> list[str]:
    """Every 15-minute GDELT file name from ``since`` (or ``days`` ago) until about 20 minutes ago, newest first."""
    end = now.astimezone(UTC) - timedelta(minutes=20)
    end = end.replace(minute=end.minute - end.minute % 15, second=0, microsecond=0)
    stop = since if since is not None else end - timedelta(days=days)
    out = []
    t = end
    while t > stop or (since is not None and t == stop):
        out.append(t.strftime("%Y%m%d%H%M%S"))
        t -= timedelta(minutes=15)
    return out


async def collect(conn: sqlite3.Connection, client: httpx.AsyncClient, now: datetime, *,
                  limit: int = FILES_PER_PASS) -> dict[str, Any]:
    """Fetch the newest missing files first, a few dozen a pass: the 32-day backfill fills in behind, and so does any
    hole inside the window (a pass missed while the computer was off, a file that failed to download)."""
    have = {r[0] for r in conn.execute("SELECT name FROM stk_world_files")}
    missing = [s for s in stamps(now, since=window_start(now)) if s not in have]
    done = failed = 0
    for stamp in missing[:limit]:
        try:
            response = await client.get(FILE.format(stamp=stamp), headers={"User-Agent": AGENT})
        except httpx.HTTPError:
            failed += 1
            continue
        if response.status_code == 404:          # GDELT occasionally skips a 15-minute file: record it as empty
            save_file(conn, stamp, Totals(), now)
            done += 1
            continue
        if response.status_code != 200 or len(response.content) > MAX_FILE:
            failed += 1
            continue
        save_file(conn, stamp, parse_events(response.content), now)
        done += 1
    return {"files": done, "failed": failed, "missing": max(0, len(missing) - done)}


# ── what the totals mean ──────────────────────────────────────────────────────────────────────────
def _files_by_day(conn: sqlite3.Connection, first: int, last: int) -> dict[int, int]:
    out: dict[int, int] = defaultdict(int)
    for (name,) in conn.execute("SELECT name FROM stk_world_files"):
        day = date(int(name[:4]), int(name[4:6]), int(name[6:8])).toordinal()
        if first <= day <= last:
            out[day] += 1
    return out


def status(conn: sqlite3.Connection, today: date) -> list[dict[str, Any]]:
    """Each supply node: coverage of disruptive events in the last two days against the 30 days before, per
    15-minute file collected (so missed files never read as calm), and the most-covered source articles."""
    first, last = today.toordinal() - 32, today.toordinal()
    files = _files_by_day(conn, first, last)
    rows = conn.execute("SELECT day, country, kind, articles FROM stk_world_daily WHERE day BETWEEN ? AND ?",
                        (first, last)).fetchall()
    out = []
    for node in NODES:
        daily: dict[int, float] = defaultdict(float)
        for day, country, kind, articles in rows:
            if kind in node.kinds and ("*" in node.countries or country in node.countries):
                daily[day] += WEIGHT[kind] * articles
        recent_days = [last - 1, last]
        base_days = [d for d in range(first, last - 1) if files.get(d, 0) >= 72]       # 75%+ of a day's files
        recent_files = sum(files.get(d, 0) for d in recent_days)
        base_files = sum(files.get(d, 0) for d in base_days)
        recent = sum(daily.get(d, 0.0) for d in recent_days) / recent_files * 96 if recent_files else None
        base = sum(daily.get(d, 0.0) for d in base_days) / base_files * 96 if base_files else None
        ratio = recent / base if recent is not None and base else None
        ready = len(base_days) >= 5
        spike = bool(ready and ratio is not None and ratio >= 1.8 and recent is not None and recent >= 30)
        sources = []
        if node.countries != ("*",):
            marks = ",".join("?" * len(node.countries))
            kinds = ",".join("?" * len(node.kinds))
            sources = [{"url": r[0], "articles": r[1], "place": r[2], "kind": r[3], "day": date.fromordinal(r[4]).isoformat()}
                       for r in conn.execute(f"SELECT url, articles, place, kind, day FROM stk_world_sources WHERE day>=? "  # noqa: S608
                                             f"AND country IN ({marks}) AND kind IN ({kinds}) ORDER BY articles DESC LIMIT 6",
                                             (last - 1, *node.countries, *node.kinds))]
        out.append({"id": node.id, "label": node.label, "why": node.why, "countries": list(node.countries),
                    "recent_per_day": None if recent is None else round(recent), "baseline_per_day":
                    None if base is None else round(base), "ratio": None if ratio is None else round(ratio, 2),
                    "spike": spike, "strength": round(min(1.0, math.log2(ratio) / 2), 2) if spike and ratio else 0.0,
                    "baseline_days": len(base_days), "ready": ready, "sources": sources,
                    "hurt": list(node.hurt), "helped": list(node.helped)})
    return sorted(out, key=lambda n: (not n["spike"], -(n["ratio"] or 0)))


def exposure(node: Node, symbol: str, sic: str | int | None) -> int:
    """+1 helped, −1 hurt, 0 untouched, by name first and SIC code second."""
    if symbol in node.helped:
        return 1
    if symbol in node.hurt:
        return -1
    code = str(sic or "")
    if code and any(code.startswith(p) for p in node.helped_sic):
        return 1
    if code and any(code.startswith(p) for p in node.hurt_sic):
        return -1
    return 0


__all__ = ["BY_ID", "NODES", "Node", "Totals", "collect", "exposure", "kind_of", "parse_events", "save_file", "stamps",
           "status"]
