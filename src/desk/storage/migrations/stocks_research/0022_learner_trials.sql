-- The Learner's persistent trial counter (docs/STOCKS_LEARNER.md → After accounting for what it tried): every model
-- variant, ensemble member and do / don't rule ever evaluated, with its held-out Sharpe ratio (per day), so the
-- deflated Sharpe ratio of anything chosen among them counts every trial. Append-only: the count only grows.
CREATE TABLE stk_learn_trials (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    at            TEXT NOT NULL,
    model_id      TEXT,
    kind          TEXT NOT NULL CHECK (kind IN ('model', 'finding')),
    name          TEXT NOT NULL,
    sharpe        REAL,
    observations  INTEGER,
    detail_json   TEXT NOT NULL
);
CREATE INDEX stk_learn_trials_kind ON stk_learn_trials(kind);

CREATE TRIGGER stk_learn_trials_no_update BEFORE UPDATE ON stk_learn_trials BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER stk_learn_trials_no_delete BEFORE DELETE ON stk_learn_trials BEGIN SELECT RAISE(ABORT, 'append-only'); END;
