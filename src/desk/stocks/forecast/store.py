"""Stored weekly fits of the forecast models (market migration 0017): append-only fit records with their evaluation,
and the newest ``KEEP`` fits' pickled models (hash-checked on load)."""

from __future__ import annotations

import hashlib
import json
import pickle
import sqlite3
import zlib
from bisect import bisect_right
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from desk.core.clock import to_iso
from desk.storage.db import transaction

KEEP = 8                    # fits whose models are kept, per model


@dataclass(frozen=True)
class Stored:
    model: str
    version: int
    fit_day: date
    sha: str
    live: dict[int, bool]
    evaluation: dict[str, Any]
    rows: int
    stocks: int
    seconds: float
    peak_mb: float | None
    fitted_at: str


def pack(value: Any) -> tuple[bytes, str]:
    blob = zlib.compress(pickle.dumps(value, protocol=5), 6)
    return blob, hashlib.sha256(blob).hexdigest()


def save(conn: sqlite3.Connection, model: str, version: int, fit_day: date, value: Any, live: dict[int, bool],
         evaluation: dict[str, Any], rows: int, stocks: int, seconds: float, peak_mb: float | None,
         now: datetime) -> bool:
    """Append one fit (once per fit day) and keep only the newest ``KEEP`` fits' models. Returns whether it was added."""
    blob, sha = pack(value)
    with transaction(conn):
        cur = conn.execute("INSERT OR IGNORE INTO stk_forecast_fits VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                           (model, version, fit_day.toordinal(), sha,
                            json.dumps({str(h): bool(v) for h, v in sorted(live.items())}),
                            json.dumps(evaluation, allow_nan=False), rows, stocks, round(seconds, 2),
                            None if peak_mb is None else round(peak_mb, 1), to_iso(now)))
        if not cur.rowcount:
            return False
        conn.execute("INSERT OR REPLACE INTO stk_forecast_blobs VALUES (?,?,?,?,?)",
                     (model, version, fit_day.toordinal(), sha, blob))
        kept = [r[0] for r in conn.execute("SELECT fit_day FROM stk_forecast_blobs WHERE model=? AND version=? "
                                           "ORDER BY fit_day DESC", (model, version))]
        for old in kept[KEEP:]:
            conn.execute("DELETE FROM stk_forecast_blobs WHERE model=? AND version=? AND fit_day=?",
                         (model, version, old))
    return True


def _row(r: sqlite3.Row | tuple[Any, ...]) -> Stored:
    return Stored(r[0], int(r[1]), date.fromordinal(int(r[2])), r[3], {int(h): bool(v) for h, v in
                                                                       json.loads(r[4]).items()},
                  json.loads(r[5]), int(r[6]), int(r[7]), float(r[8]), None if r[9] is None else float(r[9]), r[10])


def fits(conn: sqlite3.Connection, model: str, version: int) -> list[Stored]:
    try:
        return [_row(r) for r in conn.execute("SELECT * FROM stk_forecast_fits WHERE model=? AND version=? "
                                              "ORDER BY fit_day", (model, version))]
    except sqlite3.OperationalError:            # a database from before the forecast models
        return []


def latest(conn: sqlite3.Connection, model: str, version: int) -> Stored | None:
    found = fits(conn, model, version)
    return found[-1] if found else None


def newest_day(conn: sqlite3.Connection, model: str, version: int) -> date | None:
    try:
        row = conn.execute("SELECT MAX(fit_day) FROM stk_forecast_fits WHERE model=? AND version=?",
                           (model, version)).fetchone()
    except sqlite3.OperationalError:
        return None
    return None if row is None or row[0] is None else date.fromordinal(int(row[0]))


class Loader:
    """The newest fit on or before a day, its models unpickled once (hash-checked)."""

    def __init__(self, conn: sqlite3.Connection, model: str, version: int) -> None:
        self.conn, self.model, self.version = conn, model, version
        self._days: list[int] | None = None
        self._cache: dict[int, tuple[Stored, Any] | None] = {}

    def on(self, d: date) -> tuple[Stored, Any] | None:
        """(the fit record, its models) of the newest fit on or before ``d`` whose models are still kept, else None."""
        if self._days is None:
            try:
                self._days = [int(r[0]) for r in self.conn.execute(
                    "SELECT fit_day FROM stk_forecast_fits WHERE model=? AND version=? ORDER BY fit_day",
                    (self.model, self.version))]
            except sqlite3.OperationalError:
                self._days = []
        k = bisect_right(self._days, d.toordinal())
        if not k:
            return None
        day = self._days[k - 1]
        if day not in self._cache:
            self._cache[day] = self._load(day)
        return self._cache[day]

    def _load(self, day: int) -> tuple[Stored, Any] | None:
        rec = self.conn.execute("SELECT * FROM stk_forecast_fits WHERE model=? AND version=? AND fit_day=?",
                                (self.model, self.version, day)).fetchone()
        blob = self.conn.execute("SELECT sha, blob FROM stk_forecast_blobs WHERE model=? AND version=? AND fit_day=?",
                                 (self.model, self.version, day)).fetchone()
        if rec is None or blob is None or hashlib.sha256(blob[1]).hexdigest() != blob[0] or blob[0] != rec[3]:
            return None
        return _row(rec), pickle.loads(zlib.decompress(blob[1]))  # noqa: S301 - our own hash-checked models


__all__ = ["KEEP", "Loader", "Stored", "fits", "latest", "newest_day", "pack", "save"]
