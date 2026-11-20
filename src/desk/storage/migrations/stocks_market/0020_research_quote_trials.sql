-- One discretionary quote slot per session. Assignments and outcomes survive a worker restart.
CREATE TABLE stk_research_quote_trials (
    session INTEGER PRIMARY KEY,
    started_at TEXT NOT NULL,
    assigned TEXT NOT NULL CHECK (assigned IN ('follow_up','rotation')),
    follow_up TEXT NOT NULL,
    rotation TEXT NOT NULL,
    source TEXT NOT NULL,
    decision_id INTEGER NOT NULL,
    account TEXT NOT NULL,
    finished_at TEXT,
    result_json TEXT
);
