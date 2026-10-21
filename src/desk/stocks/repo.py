"""KESTREL Stocks storage: stocks-market.db (what was collected), stocks-research.db (what was concluded) and
stocks-ledger-paper.db (the PAPER books, desk.stocks.paper). Days are stored as ``date.toordinal()``."""

from __future__ import annotations

import json
import sqlite3
import zlib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from desk.core.clock import to_iso
from desk.core.hashing import canonical_json, sha256_hex
from desk.core.paths import DeskPaths
from desk.stocks.contracts import (
    Bar,
    Company,
    Dividend,
    Fact,
    Fail,
    InsiderTrade,
    Listing,
    Mention,
    OptionQuote,
    RatePoint,
    ShortInterest,
    ShortVolume,
    Split,
)
from desk.storage.db import DbKind, StorageError, connect, integrity_check, journal_mode, migrate, transaction

MARKET_FILE = "stocks-market.db"
RESEARCH_FILE = "stocks-research.db"
LEDGER_FILE = "stocks-ledger-paper.db"
FILING_FORMS = frozenset({"10-K", "10-Q", "10-K/A", "10-Q/A", "8-K", "8-K/A", "20-F", "6-K", "S-1", "S-1/A", "S-3",
                          "S-3ASR", "424B1", "424B2", "424B3", "424B4", "424B5", "SC 13D", "SC 13D/A", "SC 13G",
                          "SC 13G/A", "NT 10-K", "NT 10-Q", "DEF 14A", "25-NSE", "15-12B", "SCHEDULE 13D",
                          "SCHEDULE 13D/A", "SCHEDULE 13G", "SCHEDULE 13G/A"})


def day_of(d: date) -> int:
    return d.toordinal()


def date_of(n: int) -> date:
    return date.fromordinal(n)


@dataclass
class StocksDatabases:
    market: sqlite3.Connection
    research: sqlite3.Connection
    ledger: sqlite3.Connection
    runtime: sqlite3.Connection

    @classmethod
    def open(cls, paths: DeskPaths, *, check_same_thread: bool = True) -> StocksDatabases:
        paths.ensure()
        market = connect(paths.db_dir / MARKET_FILE, DbKind.STOCKS_MARKET, check_same_thread=check_same_thread)
        research = connect(paths.db_dir / RESEARCH_FILE, DbKind.STOCKS_RESEARCH, check_same_thread=check_same_thread)
        ledger = connect(paths.db_dir / LEDGER_FILE, DbKind.STOCKS_LEDGER, check_same_thread=check_same_thread)
        runtime = connect(paths.db_dir / "runtime.db", DbKind.RUNTIME, check_same_thread=check_same_thread)
        for conn, kind in ((market, DbKind.STOCKS_MARKET), (research, DbKind.STOCKS_RESEARCH),
                           (ledger, DbKind.STOCKS_LEDGER), (runtime, DbKind.RUNTIME)):
            migrate(conn, kind)
        row = ledger.execute("SELECT value FROM ledger_meta WHERE key = 'mode'").fetchone()
        if row is None:
            ledger.execute("INSERT INTO ledger_meta(key, value) VALUES ('mode', 'PAPER')")
        elif row["value"] != "PAPER":
            raise StorageError("the stocks ledger is PAPER only")
        return cls(market, research, ledger, runtime)

    def health(self) -> dict[str, dict[str, str]]:
        return {name: {"integrity": integrity_check(conn), "journal_mode": journal_mode(conn)}
                for name, conn in (("stocks-market", self.market), ("stocks-research", self.research),
                                   ("stocks-ledger-paper", self.ledger))}

    def close(self) -> None:
        for conn in (self.market, self.research, self.ledger, self.runtime):
            conn.close()


def _pack(obj: Any) -> bytes:
    return zlib.compress(canonical_json(obj).encode(), 6)


def _unpack(blob: bytes) -> Any:
    return json.loads(zlib.decompress(blob))


# ── market ──────────────────────────────────────────────────────────────────────────────────────
class StocksMarket:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    # listings ─────────────────────────────────────────────────────────────────────────────────────
    def save_listings(self, listings: Sequence[Listing], ciks: dict[str, int], at: datetime) -> dict[str, int]:
        """Upsert today's listings; a symbol missing from the files is marked inactive (never deleted)."""
        seen = {x.symbol for x in listings}
        counts = {"new": 0, "changed": 0, "inactive": 0}
        with transaction(self.conn):
            for x in listings:
                content = {"name": x.name, "exchange": x.exchange, "kind": x.kind, "etf": int(x.etf), "test": int(x.test),
                           "cik": ciks.get(x.symbol)}
                old = self.conn.execute("SELECT * FROM stk_listings WHERE symbol = ?", (x.symbol,)).fetchone()
                if old is None:
                    counts["new"] += 1
                    self._listing_event(x.symbol, at, "LISTED", content)
                elif {k: old[k] for k in content} != content or not old["active"]:
                    counts["changed"] += 1
                    self._listing_event(x.symbol, at, "CHANGED", content)
                self.conn.execute(
                    "INSERT INTO stk_listings(symbol, name, exchange, kind, etf, test, cik, active, first_seen, last_seen)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?) ON CONFLICT(symbol) DO UPDATE SET name = excluded.name,"
                    " exchange = excluded.exchange, kind = excluded.kind, etf = excluded.etf, test = excluded.test,"
                    " cik = COALESCE(excluded.cik, stk_listings.cik), active = 1, last_seen = excluded.last_seen",
                    (x.symbol, x.name, x.exchange, x.kind, int(x.etf), int(x.test), ciks.get(x.symbol), to_iso(at), to_iso(at)))
            for row in self.conn.execute("SELECT symbol FROM stk_listings WHERE active = 1").fetchall():
                if row["symbol"] not in seen:
                    counts["inactive"] += 1
                    self._listing_event(row["symbol"], at, "UNLISTED", {})
                    self.conn.execute("UPDATE stk_listings SET active = 0 WHERE symbol = ?", (row["symbol"],))
        return counts

    def _listing_event(self, symbol: str, at: datetime, event: str, content: dict[str, Any]) -> None:
        self.conn.execute("INSERT INTO stk_listing_events(symbol, at, event, content_json) VALUES (?, ?, ?, ?)",
                          (symbol, to_iso(at), event, canonical_json(content)))

    def listings(self, *, active: bool = True) -> list[sqlite3.Row]:
        if active:
            return self.conn.execute("SELECT * FROM stk_listings WHERE active = 1 ORDER BY symbol").fetchall()
        return self.conn.execute("SELECT * FROM stk_listings ORDER BY symbol").fetchall()

    # companies, filings and facts ─────────────────────────────────────────────────────────────────
    def save_company(self, company: Company, sector: str | None, at: datetime) -> int:
        """Store submissions metadata and the filings the desk reads; returns how many filings were new."""
        new = 0
        with transaction(self.conn):
            self.conn.execute(
                "INSERT INTO stk_companies(cik, name, sic, sic_description, sector, fiscal_year_end, category, submissions_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(cik) DO UPDATE SET name = excluded.name, sic = excluded.sic,"
                " sic_description = excluded.sic_description, sector = excluded.sector,"
                " fiscal_year_end = excluded.fiscal_year_end, category = excluded.category,"
                " submissions_at = excluded.submissions_at",
                (company.cik, company.name, company.sic, company.sic_description, sector, company.fiscal_year_end,
                 company.category, to_iso(at)))
            for f in company.filings:
                if f.form not in FILING_FORMS:
                    continue
                cur = self.conn.execute(
                    "INSERT OR IGNORE INTO stk_filings(accession, cik, form, filed, accepted_at, report_date, items,"
                    " primary_document, first_seen) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (f.accession, f.cik, f.form, day_of(f.filed), to_iso(f.accepted) if f.accepted else None,
                     day_of(f.report_date) if f.report_date else None, ",".join(f.items), f.primary_document, to_iso(at)))
                new += cur.rowcount
        return new

    def company(self, cik: int) -> sqlite3.Row | None:
        row: sqlite3.Row | None = self.conn.execute("SELECT cik, name, sic, sic_description, sector, fiscal_year_end, category,"
                                 " submissions_at, facts_at FROM stk_companies WHERE cik = ?", (cik,)).fetchone()
        return row

    def companies(self) -> dict[int, sqlite3.Row]:
        return {r["cik"]: r for r in self.conn.execute(
            "SELECT cik, name, sic, sic_description, sector, fiscal_year_end, category, submissions_at, facts_at"
            " FROM stk_companies")}

    def filings(self, cik: int | None = None, *, forms: Iterable[str] | None = None, since: date | None = None,
                until: date | None = None) -> list[sqlite3.Row]:
        sql = "SELECT * FROM stk_filings WHERE 1 = 1"
        args: list[Any] = []
        if cik is not None:
            sql += " AND cik = ?"
            args.append(cik)
        if forms is not None:
            wanted = list(forms)
            sql += f" AND form IN ({','.join('?' * len(wanted))})"
            args += wanted
        if since is not None:
            sql += " AND filed >= ?"
            args.append(day_of(since))
        if until is not None:
            sql += " AND filed <= ?"
            args.append(day_of(until))
        return self.conn.execute(sql + " ORDER BY filed, accession", args).fetchall()

    def save_facts(self, cik: int, facts: dict[str, list[Fact]], raw_sha256: str, at: datetime) -> bool:
        """Replace the company's stored facts (every value keeps its filing date); False when unchanged."""
        old = self.conn.execute("SELECT facts_sha256 FROM stk_companies WHERE cik = ?", (cik,)).fetchone()
        if old is not None and old["facts_sha256"] == raw_sha256:
            self.conn.execute("UPDATE stk_companies SET facts_at = ? WHERE cik = ?", (to_iso(at), cik))
            return False
        packed = {concept: [[f.unit, day_of(f.start) if f.start else None, day_of(f.end), f.value, f.form,
                             day_of(f.filed), f.fy, f.fp, f.accn] for f in rows] for concept, rows in facts.items()}
        with transaction(self.conn):
            self.conn.execute("INSERT INTO stk_companies(cik, name) VALUES (?, '') ON CONFLICT(cik) DO NOTHING", (cik,))
            self.conn.execute("UPDATE stk_companies SET facts_at = ?, facts_sha256 = ?, facts_zlib = ? WHERE cik = ?",
                              (to_iso(at), raw_sha256, _pack(packed), cik))
        return True

    def facts(self, cik: int) -> dict[str, list[Fact]]:
        row = self.conn.execute("SELECT facts_zlib FROM stk_companies WHERE cik = ?", (cik,)).fetchone()
        if row is None or row["facts_zlib"] is None:
            return {}
        return {concept: [Fact(concept, r[0], date_of(r[1]) if r[1] is not None else None, date_of(r[2]), float(r[3]), r[4],
                               date_of(r[5]), r[6], r[7], r[8]) for r in rows]
                for concept, rows in _unpack(row["facts_zlib"]).items()}

    def facts_age(self) -> dict[int, str | None]:
        return {r["cik"]: r["facts_at"] for r in self.conn.execute("SELECT cik, facts_at FROM stk_companies")}

    # bars ─────────────────────────────────────────────────────────────────────────────────────────
    def save_bars(self, day: date, bars: Sequence[Bar], source: str, at: datetime) -> int:
        """Insert-once: a stored bar is never rewritten. The day is recorded as complete (even a holiday: 0 rows)."""
        with transaction(self.conn):
            cur = self.conn.executemany(
                "INSERT OR IGNORE INTO stk_bars(symbol, day, open, high, low, close, volume, vwap, trades)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(b.symbol, day_of(b.day), b.open, b.high, b.low, b.close, b.volume, b.vwap, b.trades) for b in bars])
            self.conn.execute("INSERT OR IGNORE INTO stk_bar_days(day, source, rows, received_at) VALUES (?, ?, ?, ?)",
                              (day_of(day), source, len(bars), to_iso(at)))
        return cur.rowcount

    def bar_days(self) -> list[date]:
        return [date_of(r["day"]) for r in self.conn.execute("SELECT day FROM stk_bar_days WHERE rows > 0 ORDER BY day")]

    def checked_days(self) -> set[date]:
        return {date_of(r["day"]) for r in self.conn.execute("SELECT day FROM stk_bar_days")}

    def bars(self, symbol: str, since: date | None = None) -> list[Bar]:
        rows = self.conn.execute("SELECT * FROM stk_bars WHERE symbol = ? AND day >= ? ORDER BY day",
                                 (symbol, day_of(since) if since else 0)).fetchall()
        return [Bar(r["symbol"], date_of(r["day"]), r["open"], r["high"], r["low"], r["close"], r["volume"], r["vwap"],
                    r["trades"]) for r in rows]

    def bar_rows(self, since: date, until: date | None = None) -> sqlite3.Cursor:
        return self.conn.execute("SELECT symbol, day, open, high, low, close, volume FROM stk_bars WHERE day >= ?"
                                 " AND day <= ?", (day_of(since), day_of(until or date.max)))

    def save_splits(self, splits: Sequence[Split], at: datetime) -> int:
        with transaction(self.conn):
            cur = self.conn.executemany("INSERT OR IGNORE INTO stk_splits(symbol, execution_date, split_from, split_to,"
                                        " received_at) VALUES (?, ?, ?, ?, ?)",
                                        [(s.symbol, day_of(s.execution_date), s.split_from, s.split_to, to_iso(at))
                                         for s in splits])
        return cur.rowcount

    def splits(self) -> list[Split]:
        return [Split(r["symbol"], date_of(r["execution_date"]), r["split_from"], r["split_to"])
                for r in self.conn.execute("SELECT * FROM stk_splits ORDER BY execution_date")]

    def save_dividends(self, dividends: Sequence[Dividend], at: datetime) -> int:
        with transaction(self.conn):
            cur = self.conn.executemany(
                "INSERT OR IGNORE INTO stk_dividends(symbol, ex_date, cash, pay_date, frequency, kind, received_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                [(d.symbol, day_of(d.ex_date), d.cash_amount, day_of(d.pay_date) if d.pay_date else None, d.frequency,
                  d.distribution_type, to_iso(at)) for d in dividends])
        return cur.rowcount

    def dividends(self, since: date | None = None) -> list[Dividend]:
        return [Dividend(r["symbol"], date_of(r["ex_date"]), r["cash"], date_of(r["pay_date"]) if r["pay_date"] else None,
                         r["frequency"], r["kind"])
                for r in self.conn.execute("SELECT * FROM stk_dividends WHERE ex_date >= ? ORDER BY ex_date",
                                           (day_of(since) if since else 0,))]

    # insiders, shorts, crowd, rates ───────────────────────────────────────────────────────────────
    def save_insider_trades(self, rows: Sequence[tuple[InsiderTrade, date]]) -> int:
        """(trade, filing day) pairs; lines are numbered per filing and owner in the order given."""
        lines: dict[tuple[str, int], int] = {}
        values = []
        for t, filed in rows:
            key = (t.accession, t.owner_cik)
            line = lines.get(key, 0)
            lines[key] = line + 1
            values.append((t.accession, t.owner_cik, line, t.issuer_cik, t.symbol, t.owner, int(t.is_director),
                           int(t.is_officer), t.officer_title, int(t.is_ten_percent), day_of(t.day), t.code, t.shares,
                           t.price, int(t.acquired), t.owned_after, int(t.direct), int(t.planned), day_of(filed)))
        with transaction(self.conn):
            cur = self.conn.executemany(
                "INSERT OR IGNORE INTO stk_insider_trades(accession, owner_cik, line, issuer_cik, symbol, owner, director,"
                " officer, title, ten_percent, day, code, shares, price, acquired, owned_after, direct, planned, filed)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", values)
        return cur.rowcount

    def insider_accessions(self, since: date) -> set[str]:
        return {r["accession"] for r in self.conn.execute(
            "SELECT DISTINCT accession FROM stk_insider_trades WHERE filed >= ?", (day_of(since),))}

    def insider_trades(self, since: date, until: date | None = None, symbol: str | None = None) -> list[sqlite3.Row]:
        sql = "SELECT * FROM stk_insider_trades WHERE filed >= ? AND filed <= ?"
        args: list[Any] = [day_of(since), day_of(until or date.max)]
        if symbol is not None:
            sql += " AND symbol = ?"
            args.append(symbol)
        return self.conn.execute(sql + " ORDER BY filed, accession, line", args).fetchall()

    def save_short_interest(self, rows: Sequence[ShortInterest], at: datetime) -> int:
        with transaction(self.conn):
            cur = self.conn.executemany(
                "INSERT OR IGNORE INTO stk_short_interest(symbol, settlement, short_shares, previous, avg_volume,"
                " days_to_cover, received_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                [(r.symbol, day_of(r.settlement), r.short_shares, r.previous_short_shares, r.avg_daily_volume,
                  r.days_to_cover, to_iso(at)) for r in rows])
        return cur.rowcount

    def short_interest_dates(self) -> list[date]:
        return [date_of(r["settlement"]) for r in self.conn.execute(
            "SELECT DISTINCT settlement FROM stk_short_interest ORDER BY settlement")]

    def short_interest(self, symbol: str | None = None) -> list[sqlite3.Row]:
        if symbol is None:
            return self.conn.execute("SELECT * FROM stk_short_interest ORDER BY settlement").fetchall()
        return self.conn.execute("SELECT * FROM stk_short_interest WHERE symbol = ? ORDER BY settlement",
                                 (symbol,)).fetchall()

    def save_short_volume(self, rows: Sequence[ShortVolume], keep: set[str] | None = None) -> int:
        with transaction(self.conn):
            cur = self.conn.executemany(
                "INSERT OR IGNORE INTO stk_short_volume(symbol, day, short_volume, exempt, total) VALUES (?, ?, ?, ?, ?)",
                [(r.symbol, day_of(r.day), r.short_volume, r.exempt_volume, r.total_volume) for r in rows
                 if keep is None or r.symbol in keep])
        return cur.rowcount

    def short_volume_days(self) -> set[date]:
        return {date_of(r["day"]) for r in self.conn.execute("SELECT DISTINCT day FROM stk_short_volume")}

    def save_fails(self, rows: Sequence[Fail]) -> int:
        with transaction(self.conn):
            cur = self.conn.executemany(
                "INSERT OR IGNORE INTO stk_fails(symbol, settlement, cusip, quantity, price) VALUES (?, ?, ?, ?, ?)",
                [(r.symbol, day_of(r.settlement), r.cusip, r.quantity, r.price) for r in rows])
        return cur.rowcount

    def save_mentions(self, rows: Sequence[Mention], hour: int) -> int:
        with transaction(self.conn):
            cur = self.conn.executemany(
                "INSERT OR IGNORE INTO stk_mentions(source, hour, symbol, rank, mentions, upvotes, rank_24h, mentions_24h)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [(m.source, hour, m.symbol, m.rank, m.mentions, m.upvotes, m.rank_24h_ago, m.mentions_24h_ago) for m in rows])
        return cur.rowcount

    def latest_mentions(self, source: str, *, before_hour: int | None = None) -> tuple[int | None, list[sqlite3.Row]]:
        row = self.conn.execute("SELECT MAX(hour) AS h FROM stk_mentions WHERE source = ? AND hour <= ?",
                                (source, before_hour if before_hour is not None else 2**40)).fetchone()
        if row is None or row["h"] is None:
            return None, []
        return row["h"], self.conn.execute("SELECT * FROM stk_mentions WHERE source = ? AND hour = ? ORDER BY rank",
                                           (source, row["h"])).fetchall()

    def mention_history(self, symbol: str, source: str, since_hour: int) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM stk_mentions WHERE symbol = ? AND source = ? AND hour >= ? ORDER BY hour",
                                 (symbol, source, since_hour)).fetchall()

    def save_rates(self, points: Sequence[RatePoint]) -> int:
        with transaction(self.conn):
            cur = self.conn.executemany("INSERT OR IGNORE INTO stk_rates(series, day, value) VALUES (?, ?, ?)",
                                        [(p.series, day_of(p.day), p.value) for p in points])
        return cur.rowcount

    def rates(self, series: str, since: date | None = None) -> list[RatePoint]:
        return [RatePoint(series, date_of(r["day"]), r["value"]) for r in self.conn.execute(
            "SELECT day, value FROM stk_rates WHERE series = ? AND day >= ? ORDER BY day",
            (series, day_of(since) if since else 0))]

    # options ──────────────────────────────────────────────────────────────────────────────────────
    def save_option_chain(self, underlying: str, day: date, expiration: date, observed_at: datetime,
                          underlying_price: float | None, quotes: Sequence[OptionQuote]) -> None:
        rows = [[q.occ, q.kind, q.strike, q.bid, q.ask, q.last, q.volume, q.open_interest, q.average_volume,
                 to_iso(q.quoted_at) if q.quoted_at else None] for q in quotes]
        self.conn.execute(
            "INSERT OR REPLACE INTO stk_option_chains(underlying, day, expiration, observed_at, underlying_price, contracts,"
            " chain_zlib) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (underlying, day_of(day), day_of(expiration), to_iso(observed_at), underlying_price, len(rows), _pack(rows)))

    def option_chain(self, underlying: str, day: date | None = None) -> tuple[date | None, dict[date, list[OptionQuote]]]:
        """The newest stored chain for ``underlying`` (on or before ``day``), every expiration."""
        row = self.conn.execute("SELECT MAX(day) AS d FROM stk_option_chains WHERE underlying = ? AND day <= ?",
                                (underlying, day_of(day) if day else 2**31)).fetchone()
        if row is None or row["d"] is None:
            return None, {}
        out: dict[date, list[OptionQuote]] = {}
        for r in self.conn.execute("SELECT * FROM stk_option_chains WHERE underlying = ? AND day = ? ORDER BY expiration",
                                   (underlying, row["d"])):
            expiration = date_of(r["expiration"])
            out[expiration] = [OptionQuote(q[0], underlying, q[1], q[2], expiration, q[3], q[4], q[5], q[6], q[7], q[8],
                                           datetime.fromisoformat(q[9]) if q[9] else None)
                               for q in _unpack(r["chain_zlib"])]
        return date_of(row["d"]), out

    def chain_underlying_price(self, underlying: str, day: date) -> float | None:
        row = self.conn.execute("SELECT underlying_price FROM stk_option_chains WHERE underlying = ? AND day = ? LIMIT 1",
                                (underlying, day_of(day))).fetchone()
        return None if row is None else row["underlying_price"]

    def prune_option_chains(self, before: date) -> int:
        return self.conn.execute("DELETE FROM stk_option_chains WHERE day < ?", (day_of(before),)).rowcount

    def save_option_daily(self, underlying: str, day: date, observed_at: datetime, summary: dict[str, Any]) -> None:
        self.conn.execute("INSERT OR REPLACE INTO stk_option_daily(underlying, day, observed_at, summary_json)"
                          " VALUES (?, ?, ?, ?)", (underlying, day_of(day), to_iso(observed_at), canonical_json(summary)))

    def option_daily(self, underlying: str, since: date | None = None) -> list[tuple[date, dict[str, Any]]]:
        return [(date_of(r["day"]), json.loads(r["summary_json"])) for r in self.conn.execute(
            "SELECT day, summary_json FROM stk_option_daily WHERE underlying = ? AND day >= ? ORDER BY day",
            (underlying, day_of(since) if since else 0))]

    def option_daily_on(self, day: date) -> dict[str, dict[str, Any]]:
        return {r["underlying"]: json.loads(r["summary_json"]) for r in self.conn.execute(
            "SELECT underlying, summary_json FROM stk_option_daily WHERE day = ?", (day_of(day),))}

    def option_daily_all(self, since: date | None = None) -> Iterable[tuple[str, date, dict[str, Any]]]:
        for r in self.conn.execute("SELECT underlying, day, summary_json FROM stk_option_daily WHERE day >= ?"
                                   " ORDER BY day, underlying", (day_of(since) if since else 0,)):
            yield r["underlying"], date_of(r["day"]), json.loads(r["summary_json"])

    def latest_option_day(self) -> date | None:
        row = self.conn.execute("SELECT MAX(day) AS d FROM stk_option_daily").fetchone()
        return None if row is None or row["d"] is None else date_of(row["d"])

    # jobs and specs ───────────────────────────────────────────────────────────────────────────────
    def job(self, name: str, at: datetime, *, error: str | None = None, detail: dict[str, Any] | None = None) -> None:
        self.conn.execute(
            "INSERT INTO stk_jobs(name, last_run_at, last_ok_at, last_error, detail_json) VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT(name) DO UPDATE SET last_run_at = excluded.last_run_at,"
            " last_ok_at = COALESCE(excluded.last_ok_at, stk_jobs.last_ok_at), last_error = excluded.last_error,"
            " detail_json = excluded.detail_json",
            (name, to_iso(at), None if error else to_iso(at), error, canonical_json(detail or {})))

    def jobs(self) -> dict[str, dict[str, Any]]:
        return {r["name"]: {"last_run_at": r["last_run_at"], "last_ok_at": r["last_ok_at"], "last_error": r["last_error"],
                            "detail": json.loads(r["detail_json"])} for r in self.conn.execute("SELECT * FROM stk_jobs")}

    def register_spec(self, name: str, version: int, content: dict[str, Any], at: datetime) -> str:
        digest = sha256_hex(canonical_json(content))
        existing = self.conn.execute("SELECT spec_sha256 FROM stk_specs WHERE name = ? AND version = ?",
                                     (name, version)).fetchone()
        if existing is not None and existing["spec_sha256"] != digest:
            raise StorageError(f"{name} v{version} changed without a new version")
        self.conn.execute("INSERT OR IGNORE INTO stk_specs(spec_sha256, name, version, registered_at, content_json)"
                          " VALUES (?, ?, ?, ?, ?)", (digest, name, version, to_iso(at), canonical_json(content)))
        return digest

    def save_cusips(self, mapping: dict[str, str | None], at: datetime) -> None:
        with transaction(self.conn):
            self.conn.executemany("INSERT OR REPLACE INTO stk_cusips(cusip, symbol, mapped_at) VALUES (?, ?, ?)",
                                  [(c, s, to_iso(at)) for c, s in mapping.items()])


# ── research ────────────────────────────────────────────────────────────────────────────────────
class StocksResearch:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def save_scores(self, asof: date, detail: dict[str, dict[str, Any]], compact: dict[str, dict[str, Any]],
                    at: datetime) -> None:
        with transaction(self.conn):
            self.conn.execute("DELETE FROM stk_scores_latest")
            self.conn.executemany("INSERT INTO stk_scores_latest(symbol, asof, computed_at, detail_json) VALUES (?, ?, ?, ?)",
                                  [(s, day_of(asof), to_iso(at), canonical_json(d)) for s, d in detail.items()])
            self.conn.executemany("INSERT OR IGNORE INTO stk_score_history(asof, symbol, scores_json) VALUES (?, ?, ?)",
                                  [(day_of(asof), s, canonical_json(c)) for s, c in compact.items()])

    def latest(self, symbol: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT detail_json FROM stk_scores_latest WHERE symbol = ?", (symbol,)).fetchone()
        return None if row is None else json.loads(row["detail_json"])

    def latest_all(self) -> dict[str, dict[str, Any]]:
        return {r["symbol"]: json.loads(r["detail_json"]) for r in self.conn.execute("SELECT * FROM stk_scores_latest")}

    def latest_asof(self) -> date | None:
        row = self.conn.execute("SELECT MAX(asof) AS d FROM stk_scores_latest").fetchone()
        return None if row is None or row["d"] is None else date_of(row["d"])

    def score_history(self, symbol: str) -> list[tuple[date, dict[str, Any]]]:
        return [(date_of(r["asof"]), json.loads(r["scores_json"])) for r in self.conn.execute(
            "SELECT asof, scores_json FROM stk_score_history WHERE symbol = ? ORDER BY asof", (symbol,))]

    def save_list(self, asof: date, list_id: str, rows: Sequence[dict[str, Any]], spec_sha256: str, at: datetime) -> bool:
        """Append a day's list once (an empty list too); a list already recorded for that day is never replaced."""
        with transaction(self.conn):
            cur = self.conn.execute("INSERT OR IGNORE INTO stk_list_days(asof, list_id, size, spec_sha256, created_at)"
                                    " VALUES (?, ?, ?, ?, ?)", (day_of(asof), list_id, len(rows), spec_sha256, to_iso(at)))
            if cur.rowcount == 0:
                return False
            self.conn.executemany(
                "INSERT INTO stk_list_snapshots(asof, list_id, rank, symbol, value, reason, price, spec_sha256, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(day_of(asof), list_id, i + 1, r["symbol"], r.get("value"), r["reason"], r.get("price"), spec_sha256,
                  to_iso(at)) for i, r in enumerate(rows)])
        return True

    def list_on(self, list_id: str, asof: date | None = None) -> tuple[date | None, list[sqlite3.Row]]:
        row = self.conn.execute("SELECT MAX(asof) AS d FROM stk_list_days WHERE list_id = ? AND asof <= ?",
                                (list_id, day_of(asof) if asof else 2**31)).fetchone()
        if row is None or row["d"] is None:
            return None, []
        return date_of(row["d"]), self.conn.execute(
            "SELECT * FROM stk_list_snapshots WHERE list_id = ? AND asof = ? ORDER BY rank", (list_id, row["d"])).fetchall()

    def list_history(self, list_id: str) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM stk_list_snapshots WHERE list_id = ? ORDER BY asof, rank",
                                 (list_id,)).fetchall()

    def lists_for(self, symbol: str, asof: date) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT list_id, rank, reason FROM stk_list_snapshots WHERE symbol = ? AND asof = ?",
                                 (symbol, day_of(asof))).fetchall()

    def snapshot_days(self) -> list[date]:
        return [date_of(r["asof"]) for r in self.conn.execute("SELECT DISTINCT asof FROM stk_list_days ORDER BY asof")]

    def record_run(self, kind: str, started: datetime, finished: datetime, asof: date | None, ok: bool,
                   detail: dict[str, Any]) -> int:
        cur = self.conn.execute(
            "INSERT INTO stk_runs(kind, started_at, finished_at, asof, ok, detail_json) VALUES (?, ?, ?, ?, ?, ?)",
            (kind, to_iso(started), to_iso(finished), day_of(asof) if asof else None, int(ok), canonical_json(detail)))
        return int(cur.lastrowid or 0)

    def runs(self, kind: str | None = None, limit: int = 20) -> list[sqlite3.Row]:
        if kind is None:
            return self.conn.execute("SELECT * FROM stk_runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return self.conn.execute("SELECT * FROM stk_runs WHERE kind = ? ORDER BY id DESC LIMIT ?", (kind, limit)).fetchall()

    def save_validation(self, asof: date, method_sha256: str, report: dict[str, Any], at: datetime) -> int:
        cur = self.conn.execute("INSERT INTO stk_validation(created_at, asof, method_sha256, report_json) VALUES (?, ?, ?, ?)",
                                (to_iso(at), day_of(asof), method_sha256, canonical_json(report)))
        return int(cur.lastrowid or 0)

    def latest_validation(self) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM stk_validation ORDER BY id DESC LIMIT 1").fetchone()
        if row is None:
            return None
        return {"id": row["id"], "created_at": row["created_at"], "asof": date_of(row["asof"]).isoformat(),
                "method_sha256": row["method_sha256"], **json.loads(row["report_json"])}

    def snapshots(self) -> dict[str, list[tuple[date, list[str]]]]:
        """Every recorded list, by list, oldest first: (day, symbols in rank order)."""
        out: dict[str, dict[int, list[str]]] = {}
        for r in self.conn.execute("SELECT asof, list_id FROM stk_list_days ORDER BY list_id, asof"):
            out.setdefault(r["list_id"], {})[r["asof"]] = []
        for r in self.conn.execute("SELECT asof, list_id, symbol FROM stk_list_snapshots ORDER BY list_id, asof, rank"):
            out[r["list_id"]][r["asof"]].append(r["symbol"])
        return {k: [(date_of(d), syms) for d, syms in days.items()] for k, days in out.items()}

    def score_days(self) -> list[date]:
        return [date_of(r["asof"]) for r in self.conn.execute("SELECT DISTINCT asof FROM stk_score_history ORDER BY asof")]

    def scores_on(self, asof: date) -> dict[str, dict[str, Any]]:
        return {r["symbol"]: json.loads(r["scores_json"]) for r in self.conn.execute(
            "SELECT symbol, scores_json FROM stk_score_history WHERE asof = ?", (day_of(asof),))}

    # point-in-time rebuilds ───────────────────────────────────────────────────────────────────────
    def rebuilt_days(self, method_sha256: str) -> list[date]:
        return [date_of(r["asof"]) for r in self.conn.execute(
            "SELECT asof FROM stk_rebuild_months WHERE method_sha256 = ? ORDER BY asof", (method_sha256,))]

    def save_rebuild(self, asof: date, method_sha256: str, scores: dict[str, Any], lists: dict[str, list[str]],
                     universe: int, at: datetime) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO stk_rebuild_months(asof, method_sha256, built_at, universe, scores_zlib, lists_json)"
            " VALUES (?, ?, ?, ?, ?, ?)", (day_of(asof), method_sha256, to_iso(at), universe,
                                          zlib.compress(canonical_json(scores).encode(), 6), canonical_json(lists)))

    def rebuilds(self, method_sha256: str) -> list[tuple[date, int, dict[str, Any], dict[str, list[str]]]]:
        return [(date_of(r["asof"]), r["universe"], json.loads(zlib.decompress(r["scores_zlib"])), json.loads(r["lists_json"]))
                for r in self.conn.execute("SELECT * FROM stk_rebuild_months WHERE method_sha256 = ? ORDER BY asof",
                                           (method_sha256,))]

    def fund_cached(self, ciks: Sequence[int], asof: date) -> dict[int, dict[str, Any]]:
        out: dict[int, dict[str, Any]] = {}
        for chunk in range(0, len(ciks), 500):
            part = list(ciks[chunk:chunk + 500])
            marks = ",".join("?" * len(part))           # placeholders only; values are bound
            for r in self.conn.execute(f"SELECT cik, base_json FROM stk_fund_cache WHERE asof = ? AND cik IN ({marks})",  # noqa: S608
                                       (day_of(asof), *part)):
                out[r["cik"]] = json.loads(r["base_json"])
        return out

    def cache_fund(self, rows: dict[int, dict[str, Any]], asof: date) -> None:
        with transaction(self.conn):
            self.conn.executemany("INSERT OR REPLACE INTO stk_fund_cache(cik, asof, base_json) VALUES (?, ?, ?)",
                                  [(cik, day_of(asof), canonical_json(b)) for cik, b in rows.items()])

    def watchlist(self) -> list[str]:
        return [r["symbol"] for r in self.conn.execute("SELECT symbol FROM stk_watchlist ORDER BY added_at")]

    def watch(self, symbol: str, at: datetime, *, add: bool) -> None:
        if add:
            self.conn.execute("INSERT OR IGNORE INTO stk_watchlist(symbol, added_at) VALUES (?, ?)", (symbol, to_iso(at)))
        else:
            self.conn.execute("DELETE FROM stk_watchlist WHERE symbol = ?", (symbol,))


__all__ = ["FILING_FORMS", "LEDGER_FILE", "MARKET_FILE", "RESEARCH_FILE", "StocksDatabases", "StocksMarket",
           "StocksResearch", "date_of", "day_of"]
