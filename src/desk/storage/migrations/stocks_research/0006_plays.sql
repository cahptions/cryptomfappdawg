-- Plays (desk.stocks.plays, docs/STOCKS_PLAYS.md): each options session's plays are recorded once, after that
-- session closes, and graded once at expiration. Both are append-only: the track record cannot be rewritten.

CREATE TABLE stk_play_days (
    session      INTEGER NOT NULL,
    category     TEXT NOT NULL,
    size         INTEGER NOT NULL,
    method       TEXT NOT NULL,
    recorded_at  TEXT NOT NULL,
    PRIMARY KEY (session, category)
) WITHOUT ROWID;

CREATE TABLE stk_plays (
    session          INTEGER NOT NULL,
    category         TEXT NOT NULL,
    rank             INTEGER NOT NULL,
    symbol           TEXT NOT NULL,
    action           TEXT NOT NULL CHECK (action IN ('BUY', 'SELL')),
    structure        TEXT NOT NULL,
    expiration       INTEGER NOT NULL,
    event            TEXT NOT NULL CHECK (event IN ('itm', 'move')),
    event_kind       TEXT NOT NULL CHECK (event_kind IN ('call', 'put', 'straddle')),
    event_strike     REAL NOT NULL,
    event_width      REAL NOT NULL,          -- straddles: the debit either side of the strike; otherwise 0
    entry            REAL NOT NULL,          -- per share: debit paid (BUY) or credit received (SELL), net of legs
    max_loss         REAL NOT NULL,          -- per contract, fees included
    legs_json        TEXT NOT NULL,
    underlying       REAL NOT NULL,
    itm_market       REAL,
    itm_history      REAL,
    itm_kestrel      REAL,
    itm_kestrel_raw  REAL,
    profit_kestrel   REAL,
    play_json        TEXT NOT NULL,
    method           TEXT NOT NULL,
    recorded_at      TEXT NOT NULL,
    PRIMARY KEY (session, category, rank)
) WITHOUT ROWID;
CREATE INDEX stk_plays_expiration ON stk_plays(expiration);

CREATE TABLE stk_play_outcomes (
    session           INTEGER NOT NULL,
    category          TEXT NOT NULL,
    rank              INTEGER NOT NULL,
    resolved_at       TEXT NOT NULL,
    underlying_close  REAL NOT NULL,
    hit               INTEGER NOT NULL CHECK (hit IN (0, 1)),   -- the play's event happened (finished ITM / moved past)
    payoff            REAL NOT NULL,          -- per share at expiration
    net_return        REAL NOT NULL,          -- on the money at risk, fees included
    PRIMARY KEY (session, category, rank)
) WITHOUT ROWID;

CREATE TRIGGER stk_play_days_no_update BEFORE UPDATE ON stk_play_days BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER stk_play_days_no_delete BEFORE DELETE ON stk_play_days BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER stk_plays_no_update BEFORE UPDATE ON stk_plays BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER stk_plays_no_delete BEFORE DELETE ON stk_plays BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER stk_play_outcomes_no_update BEFORE UPDATE ON stk_play_outcomes BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER stk_play_outcomes_no_delete BEFORE DELETE ON stk_play_outcomes BEGIN SELECT RAISE(ABORT, 'append-only'); END;
