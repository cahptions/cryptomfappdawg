-- Every Plays section replayed on real past option prices (desk.stocks.play_replay; docs/STOCKS_PLAYS.md "Replay on
-- real past prices"). Kept apart from the forward record (stk_plays and friends in the research database): replay
-- results never grade, calibrate or unlock anything forward. Append-only. ``method`` is the replay's rule fingerprint;
-- a changed rule records again under its own fingerprint and the earlier rows stay.

-- One section's replayed picks on one past session (its decision day).
CREATE TABLE stk_replay_plays (
    method        TEXT NOT NULL,
    session       INTEGER NOT NULL,        -- the decision day (ordinal): prices up to its close only
    category      TEXT NOT NULL,
    rank          INTEGER NOT NULL,
    symbol        TEXT NOT NULL,
    structure     TEXT NOT NULL,
    action        TEXT NOT NULL,
    expiration    INTEGER NOT NULL,
    event         TEXT NOT NULL,           -- itm | move, as forward plays
    event_kind    TEXT NOT NULL,
    event_strike  REAL NOT NULL,
    event_width   REAL NOT NULL,
    entry         REAL NOT NULL,           -- per share, at the real close plus (bought) or minus (sold) half the assumed spread
    max_loss      REAL NOT NULL,
    dte           INTEGER NOT NULL,
    legs_json     TEXT NOT NULL,
    chances_json  TEXT NOT NULL,           -- the model's own chances at the pick (KESTREL, market, profit, targets)
    basis         TEXT NOT NULL,           -- chain | lognormal: what the chances were read from
    twin_json     TEXT,                    -- the random twin from the same day's listings, or NULL
    source        TEXT NOT NULL,           -- where the prices came from (massive_daily_bars)
    recorded_at   TEXT NOT NULL,
    PRIMARY KEY (method, session, category, rank)
) WITHOUT ROWID;

-- Which (session, section) pairs were replayed, including the ones with no pick, so a day is never replayed twice.
CREATE TABLE stk_replay_days (
    method       TEXT NOT NULL,
    session      INTEGER NOT NULL,
    category     TEXT NOT NULL,
    plays        INTEGER NOT NULL,
    underlyings  INTEGER NOT NULL,         -- names with a usable replayed chain that day
    recorded_at  TEXT NOT NULL,
    PRIMARY KEY (method, session, category)
) WITHOUT ROWID;

-- Each replayed pick graded at expiry exactly as a forward play: settled at the stock's close, net of fees.
CREATE TABLE stk_replay_outcomes (
    method            TEXT NOT NULL,
    session           INTEGER NOT NULL,
    category          TEXT NOT NULL,
    rank              INTEGER NOT NULL,
    underlying_close  REAL NOT NULL,
    hit               INTEGER NOT NULL CHECK (hit IN (0, 1)),
    payoff            REAL NOT NULL,
    net_return        REAL NOT NULL,
    twin_net_return   REAL,
    resolved_at       TEXT NOT NULL,
    PRIMARY KEY (method, session, category, rank)
) WITHOUT ROWID;

CREATE TRIGGER stk_replay_plays_no_update BEFORE UPDATE ON stk_replay_plays BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER stk_replay_plays_no_delete BEFORE DELETE ON stk_replay_plays BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER stk_replay_days_no_update BEFORE UPDATE ON stk_replay_days BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER stk_replay_days_no_delete BEFORE DELETE ON stk_replay_days BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER stk_replay_outcomes_no_update BEFORE UPDATE ON stk_replay_outcomes BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER stk_replay_outcomes_no_delete BEFORE DELETE ON stk_replay_outcomes BEGIN SELECT RAISE(ABORT, 'append-only'); END;
