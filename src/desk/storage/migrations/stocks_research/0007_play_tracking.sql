-- Plays history (desk.stocks.play_tracking, docs/STOCKS_PLAYS.md): every recorded play is followed after it was
-- picked. Live quotes land in a working table during the session; at the close they are folded into one packed
-- intraday row and one permanent daily row per contract. Per-play marks, first sightings, twins (random matched
-- contracts used as a baseline), quarantines and learning reports are append-only.

-- today's live quotes of tracked contracts, one row per contract per pass (working table: folded and cleared)
CREATE TABLE stk_track_ticks (
    occ              TEXT NOT NULL,
    at               TEXT NOT NULL,
    session          INTEGER NOT NULL,
    bid              REAL,
    ask              REAL,
    last             REAL,
    mark             REAL,
    volume           INTEGER,
    open_interest    INTEGER,
    schwab_iv        REAL,             -- the provider's own figures, kept beside KESTREL's (computed at the fold)
    schwab_delta     REAL,
    schwab_gamma     REAL,
    schwab_theta     REAL,
    schwab_vega      REAL,
    underlying       REAL,
    quoted_at        TEXT,
    source           TEXT NOT NULL,
    PRIMARY KEY (occ, at)
) WITHOUT ROWID;
CREATE INDEX stk_track_ticks_session ON stk_track_ticks(session);

-- one packed row per contract per session: [[minute of day ET, bid, ask, underlying, Schwab IV], ...]
CREATE TABLE stk_track_intraday (
    occ        TEXT NOT NULL,
    session    INTEGER NOT NULL,
    status     TEXT NOT NULL CHECK (status IN ('quoted', 'estimated')),
    points     INTEGER NOT NULL,
    packed     BLOB NOT NULL,
    folded_at  TEXT NOT NULL,
    PRIMARY KEY (occ, session)
) WITHOUT ROWID;

-- one permanent close per contract per session
CREATE TABLE stk_track_days (
    occ            TEXT NOT NULL,
    session        INTEGER NOT NULL,
    status         TEXT NOT NULL CHECK (status IN ('quoted', 'modelled', 'estimated', 'settled')),
    bid            REAL,
    ask            REAL,
    mark           REAL,
    last           REAL,
    volume         INTEGER,
    open_interest  INTEGER,
    underlying     REAL,
    iv             REAL,             -- KESTREL's, solved from the mid (Black-Scholes, no dividends)
    delta          REAL,
    gamma          REAL,
    theta          REAL,
    vega           REAL,
    schwab_iv      REAL,             -- Schwab's own, when the quote carried them
    schwab_delta   REAL,
    schwab_gamma   REAL,
    schwab_theta   REAL,
    schwab_vega    REAL,
    high_mid       REAL,
    low_mid        REAL,
    quoted_at      TEXT,
    source         TEXT NOT NULL,
    note           TEXT,
    recorded_at    TEXT NOT NULL,
    PRIMARY KEY (occ, session)
) WITHOUT ROWID;

-- stock closes fetched from Schwab history when the bulk bars lack a session (gap repair)
CREATE TABLE stk_track_closes (
    symbol      TEXT NOT NULL,
    session     INTEGER NOT NULL,
    close       REAL NOT NULL,
    source      TEXT NOT NULL,
    fetched_at  TEXT NOT NULL,
    PRIMARY KEY (symbol, session)
) WITHOUT ROWID;

-- random matched contracts (same structure, expiry and delta on another stock): the baseline each play must beat
CREATE TABLE stk_play_twins (
    session      INTEGER NOT NULL,
    category     TEXT NOT NULL,
    rank         INTEGER NOT NULL,
    symbol       TEXT NOT NULL,
    action       TEXT NOT NULL CHECK (action IN ('BUY', 'SELL')),
    structure    TEXT NOT NULL,
    expiration   INTEGER NOT NULL,
    legs_json    TEXT NOT NULL,
    entry        REAL NOT NULL,
    max_loss     REAL NOT NULL,
    recorded_at  TEXT NOT NULL,
    PRIMARY KEY (session, category, rank)
) WITHOUT ROWID;

-- one mark per play (or its twin) per session, from that session's close
CREATE TABLE stk_play_marks (
    session      INTEGER NOT NULL,
    category     TEXT NOT NULL,
    rank         INTEGER NOT NULL,
    twin         INTEGER NOT NULL CHECK (twin IN (0, 1)),
    day          INTEGER NOT NULL,
    status       TEXT NOT NULL CHECK (status IN ('quoted', 'modelled', 'estimated', 'settled')),
    value        REAL NOT NULL,          -- per share: what closing would bring (long) or cost (short), net of legs
    mid          REAL,
    pnl          REAL NOT NULL,          -- per contract, fees included
    pnl_pct      REAL NOT NULL,          -- on the money at risk
    underlying   REAL,
    note         TEXT,
    recorded_at  TEXT NOT NULL,
    PRIMARY KEY (session, category, rank, twin, day)
) WITHOUT ROWID;

-- when a play first appeared on the live board, and what it cost then
CREATE TABLE stk_play_first_seen (
    session    INTEGER NOT NULL,
    category   TEXT NOT NULL,
    key        TEXT NOT NULL,           -- the play's legs: occ:side,...
    symbol     TEXT NOT NULL,
    seen_at    TEXT NOT NULL,
    entry      REAL NOT NULL,
    legs_json  TEXT NOT NULL,
    PRIMARY KEY (session, category, key)
) WITHOUT ROWID;

-- plays (or twins) that cannot be valued honestly, and why
CREATE TABLE stk_play_quarantine (
    session   INTEGER NOT NULL,
    category  TEXT NOT NULL,
    rank      INTEGER NOT NULL,
    twin      INTEGER NOT NULL CHECK (twin IN (0, 1)),
    reason    TEXT NOT NULL,
    at        TEXT NOT NULL,
    PRIMARY KEY (session, category, rank, twin)
) WITHOUT ROWID;

CREATE TABLE stk_play_learning (
    id           INTEGER PRIMARY KEY,
    created_at   TEXT NOT NULL,
    session      INTEGER NOT NULL,
    method       TEXT NOT NULL,
    report_json  TEXT NOT NULL
);

CREATE TABLE stk_play_promotions (
    id             INTEGER PRIMARY KEY,
    created_at     TEXT NOT NULL,
    category       TEXT NOT NULL,
    variant        TEXT NOT NULL,
    params_json    TEXT NOT NULL,
    evidence_json  TEXT NOT NULL
);

CREATE TABLE stk_track_state (
    key         TEXT PRIMARY KEY,
    value_json  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TRIGGER stk_track_intraday_no_update BEFORE UPDATE ON stk_track_intraday BEGIN SELECT RAISE(ABORT, 'append-only'); END;
-- intraday detail may be compacted only once it is more than 30 days old (ordinal day = julian day - 1721424.5)
CREATE TRIGGER stk_track_intraday_keep_30_days BEFORE DELETE ON stk_track_intraday
WHEN OLD.session >= CAST(julianday('now') - 1721424.5 AS INTEGER) - 30
BEGIN SELECT RAISE(ABORT, 'intraday detail is kept for 30 days'); END;
CREATE TRIGGER stk_track_days_no_update BEFORE UPDATE ON stk_track_days BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER stk_track_days_no_delete BEFORE DELETE ON stk_track_days BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER stk_track_closes_no_update BEFORE UPDATE ON stk_track_closes BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER stk_track_closes_no_delete BEFORE DELETE ON stk_track_closes BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER stk_play_twins_no_update BEFORE UPDATE ON stk_play_twins BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER stk_play_twins_no_delete BEFORE DELETE ON stk_play_twins BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER stk_play_marks_no_update BEFORE UPDATE ON stk_play_marks BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER stk_play_marks_no_delete BEFORE DELETE ON stk_play_marks BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER stk_play_first_seen_no_update BEFORE UPDATE ON stk_play_first_seen BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER stk_play_first_seen_no_delete BEFORE DELETE ON stk_play_first_seen BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER stk_play_quarantine_no_update BEFORE UPDATE ON stk_play_quarantine BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER stk_play_quarantine_no_delete BEFORE DELETE ON stk_play_quarantine BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER stk_play_learning_no_update BEFORE UPDATE ON stk_play_learning BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER stk_play_learning_no_delete BEFORE DELETE ON stk_play_learning BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER stk_play_promotions_no_update BEFORE UPDATE ON stk_play_promotions BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER stk_play_promotions_no_delete BEFORE DELETE ON stk_play_promotions BEGIN SELECT RAISE(ABORT, 'append-only'); END;
