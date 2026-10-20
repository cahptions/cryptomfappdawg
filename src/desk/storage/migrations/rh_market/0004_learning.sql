-- The learning lab's monthly runs (docs/ROBINHOOD_LEARNING.md). Append-only: every past choice stays visible.
CREATE TABLE rh_learning_runs (
    id              INTEGER PRIMARY KEY,
    created_at      TEXT NOT NULL,
    data_end        TEXT NOT NULL,          -- last daily bar the run could see
    lab_sha256      TEXT NOT NULL,          -- how the lab learns (families, folds, thresholds)
    selection_sha256 TEXT NOT NULL,         -- version of what was chosen; learned PAPER positions carry it
    promoted        TEXT,                   -- LEARN_BEST | LEARN_TOP3 | LEARN_MODEL, or NULL when nothing passed
    report_json     TEXT NOT NULL
);
CREATE INDEX rh_learning_runs_time ON rh_learning_runs(created_at);
CREATE TRIGGER rh_learning_runs_no_update BEFORE UPDATE ON rh_learning_runs BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER rh_learning_runs_no_delete BEFORE DELETE ON rh_learning_runs BEGIN SELECT RAISE(ABORT, 'append-only'); END;
