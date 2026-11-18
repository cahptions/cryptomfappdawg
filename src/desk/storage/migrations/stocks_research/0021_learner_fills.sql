-- The Learner, phase 5 (docs/STOCKS_LEARNER.md → Costs: the expected fill). Append-only.

-- Fill observations, one packed row per session: for each tracked contract, its first saved quote of the session (the
-- anchor) and whether a later saved quote of the same session crossed a limit at each fraction of the way from the mid
-- to the far side (buy: a later ask at or below it; sell: a later bid at or above it), with the anchor's spread, the
-- previous session's open interest and volume, days to expiry and moneyness. Derived from stk_track_intraday, which
-- is compacted after 35 days; these rows are kept.
CREATE TABLE stk_learn_fill_obs (
    session        INTEGER NOT NULL,
    fills_version  TEXT NOT NULL,             -- learner.fills.version_hash()
    rows           INTEGER NOT NULL,
    obs_zlib       BLOB NOT NULL,
    recorded_at    TEXT NOT NULL,
    PRIMARY KEY (session, fills_version)
) WITHOUT ROWID;

-- Each forward candidate's legs at its decision quotes (mid, half spread, open interest, volume, days to expiry,
-- moneyness), read from the stored chain of its session before that chain can be pruned: what the expected fill of
-- a limit order is computed from. A candidate whose recomputed entry does not match its recorded one has no row.
CREATE TABLE stk_learn_entry (
    source           TEXT NOT NULL,
    session          INTEGER NOT NULL,
    symbol           TEXT NOT NULL,
    actions_version  TEXT NOT NULL,
    status           TEXT NOT NULL CHECK (status IN ('quoted', 'missing')),
    rows             INTEGER NOT NULL,
    have             INTEGER NOT NULL,
    legs_zlib        BLOB,
    recorded_at      TEXT NOT NULL,
    PRIMARY KEY (source, session, symbol, actions_version)
) WITHOUT ROWID;

CREATE TRIGGER stk_learn_fill_obs_no_update BEFORE UPDATE ON stk_learn_fill_obs BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER stk_learn_fill_obs_no_delete BEFORE DELETE ON stk_learn_fill_obs BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER stk_learn_entry_no_update BEFORE UPDATE ON stk_learn_entry BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER stk_learn_entry_no_delete BEFORE DELETE ON stk_learn_entry BEGIN SELECT RAISE(ABORT, 'append-only'); END;
