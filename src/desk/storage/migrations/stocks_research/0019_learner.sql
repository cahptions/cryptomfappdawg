-- The Learner (desk.stocks.learner; docs/STOCKS_LEARNER.md). Research only: nothing here trades, ranks Plays or
-- calibrates anything. Every table is append-only. ``source`` says where a row's prices came from: 'forward' (the
-- chains collected on that session) or 'replay' (Massive's real daily option bars, with the replay's stated spread);
-- the two are never mixed without it.

-- The candidates looked at on one session for one stock: the action grid, the Plays picks and the PAPER/Learning
-- entries of the day (flagged), each with its point-in-time features. One zlib-packed row per stock and session.
CREATE TABLE stk_learn_candidates (
    source            TEXT NOT NULL CHECK (source IN ('forward', 'replay')),
    session           INTEGER NOT NULL,        -- the decision session (ordinal)
    symbol            TEXT NOT NULL,
    actions_version   TEXT NOT NULL,           -- learner.actions.version_hash()
    features_version  TEXT NOT NULL,           -- learner.features.version_hash()
    decided_at        TEXT NOT NULL,           -- when the decision's quotes stood (the chain's observation, or the close)
    price_source      TEXT NOT NULL,           -- the quotes' provider (schwab, tradier, massive_daily_bars, ...)
    candidates        INTEGER NOT NULL,
    explore           INTEGER NOT NULL,        -- rows in the exploration slice
    picks             INTEGER NOT NULL,        -- rows that are Plays picks or PAPER/Learning entries
    expirations       TEXT NOT NULL,           -- the candidates' expiries (ordinals, comma-separated): the labels due
    rows_zlib         BLOB NOT NULL,
    recorded_at       TEXT NOT NULL,
    PRIMARY KEY (source, session, symbol, actions_version)
) WITHOUT ROWID;

-- Which sessions were recorded (one row even when nothing could be), so a session is never recorded twice.
CREATE TABLE stk_learn_days (
    source           TEXT NOT NULL CHECK (source IN ('forward', 'replay')),
    session          INTEGER NOT NULL,
    actions_version  TEXT NOT NULL,
    underlyings      INTEGER NOT NULL,
    candidates       INTEGER NOT NULL,
    explore          INTEGER NOT NULL,
    bytes            INTEGER NOT NULL,
    seconds          REAL NOT NULL,
    recorded_at      TEXT NOT NULL,
    PRIMARY KEY (source, session, actions_version)
) WITHOUT ROWID;

-- What happened to them, one checkpoint at a time: 's1', 's5', 's10', 's21' (that many sessions after the decision,
-- valued at that session's closing quote) and 'exp:<ordinal>' (every candidate expiring then: settled at intrinsic on
-- the official close, excursions and each exit rule's return on the path of checked closes). A missing quote is a
-- missing value, never an estimate.
CREATE TABLE stk_learn_labels (
    source           TEXT NOT NULL CHECK (source IN ('forward', 'replay')),
    session          INTEGER NOT NULL,
    symbol           TEXT NOT NULL,
    actions_version  TEXT NOT NULL,
    labels_version   TEXT NOT NULL,            -- learner.labels.version_hash()
    checkpoint       TEXT NOT NULL,
    day              INTEGER NOT NULL,         -- the session whose closes these are (always after ``session``)
    rows             INTEGER NOT NULL,
    have             INTEGER NOT NULL,         -- rows with a value
    labels_zlib      BLOB NOT NULL,
    labelled_at      TEXT NOT NULL,
    PRIMARY KEY (source, session, symbol, actions_version, labels_version, checkpoint),
    CHECK (day > session)
) WITHOUT ROWID;

-- Every trained model, frozen: its artifact's hash, the data it saw, its held-out results. Never adopted by anything.
CREATE TABLE stk_learn_models (
    id               TEXT PRIMARY KEY,         -- the artifact's sha256 (first 16)
    trained_at       TEXT NOT NULL,
    code_version     TEXT NOT NULL,            -- learner.model.version_hash()
    data_json        TEXT NOT NULL,            -- sources, rows, sessions and date range per source
    features_json    TEXT NOT NULL,
    metrics_json     TEXT NOT NULL,            -- the walk-forward held-out evaluation
    artifact         TEXT NOT NULL,            -- file name under <home>/learner-models
    artifact_sha256  TEXT NOT NULL,
    bytes            INTEGER NOT NULL,
    seconds          REAL NOT NULL,
    peak_mb          REAL,
    note             TEXT
);

-- The shadow view as shown on a session (the model's predictions beside the rules), for later forward confirmation.
CREATE TABLE stk_learn_shadow (
    session       INTEGER NOT NULL,
    model_id      TEXT NOT NULL,
    kind          TEXT NOT NULL CHECK (kind IN ('plays', 'ideas')),
    payload_json  TEXT NOT NULL,
    recorded_at   TEXT NOT NULL,
    PRIMARY KEY (session, model_id, kind)
) WITHOUT ROWID;

CREATE TRIGGER stk_learn_candidates_no_update BEFORE UPDATE ON stk_learn_candidates BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER stk_learn_candidates_no_delete BEFORE DELETE ON stk_learn_candidates BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER stk_learn_days_no_update BEFORE UPDATE ON stk_learn_days BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER stk_learn_days_no_delete BEFORE DELETE ON stk_learn_days BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER stk_learn_labels_no_update BEFORE UPDATE ON stk_learn_labels BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER stk_learn_labels_no_delete BEFORE DELETE ON stk_learn_labels BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER stk_learn_models_no_update BEFORE UPDATE ON stk_learn_models BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER stk_learn_models_no_delete BEFORE DELETE ON stk_learn_models BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER stk_learn_shadow_no_update BEFORE UPDATE ON stk_learn_shadow BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER stk_learn_shadow_no_delete BEFORE DELETE ON stk_learn_shadow BEGIN SELECT RAISE(ABORT, 'append-only'); END;
