-- stocks-research.db: what KESTREL Stocks concluded. Lists and score history are append-only: a track record
-- that can't be rewritten (docs/STOCKS_METHOD.md).

-- today's full desk detail per stock (replaced each refresh)
CREATE TABLE stk_scores_latest (
    symbol       TEXT PRIMARY KEY,
    asof         INTEGER NOT NULL,
    computed_at  TEXT NOT NULL,
    detail_json  TEXT NOT NULL
);

-- every day's desk scores, compact, forever
CREATE TABLE stk_score_history (
    asof         INTEGER NOT NULL,
    symbol       TEXT NOT NULL,
    scores_json  TEXT NOT NULL,
    PRIMARY KEY (asof, symbol)
) WITHOUT ROWID;

CREATE TABLE stk_list_snapshots (
    asof         INTEGER NOT NULL,
    list_id      TEXT NOT NULL,
    rank         INTEGER NOT NULL,
    symbol       TEXT NOT NULL,
    value        REAL,
    reason       TEXT NOT NULL,
    price        REAL,
    spec_sha256  TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    PRIMARY KEY (asof, list_id, rank)
);
CREATE INDEX stk_list_snapshots_symbol ON stk_list_snapshots(symbol, asof);

CREATE TABLE stk_runs (
    id           INTEGER PRIMARY KEY,
    kind         TEXT NOT NULL,
    started_at   TEXT NOT NULL,
    finished_at  TEXT NOT NULL,
    asof         INTEGER,
    ok           INTEGER NOT NULL CHECK (ok IN (0, 1)),
    detail_json  TEXT NOT NULL
);

CREATE TABLE stk_validation (
    id             INTEGER PRIMARY KEY,
    created_at     TEXT NOT NULL,
    asof           INTEGER NOT NULL,
    method_sha256  TEXT NOT NULL,
    report_json    TEXT NOT NULL
);

CREATE TABLE stk_watchlist (
    symbol    TEXT PRIMARY KEY,
    added_at  TEXT NOT NULL,
    note      TEXT NOT NULL DEFAULT ''
);

CREATE TRIGGER stk_score_history_no_update BEFORE UPDATE ON stk_score_history BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER stk_score_history_no_delete BEFORE DELETE ON stk_score_history BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER stk_list_snapshots_no_update BEFORE UPDATE ON stk_list_snapshots BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER stk_list_snapshots_no_delete BEFORE DELETE ON stk_list_snapshots BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER stk_runs_no_update BEFORE UPDATE ON stk_runs BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER stk_runs_no_delete BEFORE DELETE ON stk_runs BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER stk_validation_no_update BEFORE UPDATE ON stk_validation BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER stk_validation_no_delete BEFORE DELETE ON stk_validation BEGIN SELECT RAISE(ABORT, 'append-only'); END;
