-- M6-11 champion/challenger: operator approvals and champion history, both append-only.
CREATE TABLE approvals (
    id            TEXT PRIMARY KEY,
    created_at    TEXT NOT NULL,
    kind          TEXT NOT NULL,              -- CHAMPION, LIVE_STRATEGY, READINESS_OVERRIDE, ...
    subject       TEXT NOT NULL,              -- e.g. the strategy version id
    confirmation  TEXT NOT NULL,              -- the exact typed confirmation
    payload_json  TEXT NOT NULL
);
CREATE TRIGGER approvals_no_update BEFORE UPDATE ON approvals BEGIN
    SELECT RAISE(ABORT, 'approvals are append-only');
END;
CREATE TRIGGER approvals_no_delete BEFORE DELETE ON approvals BEGIN
    SELECT RAISE(ABORT, 'approvals are append-only');
END;
CREATE TABLE champions (
    id                INTEGER PRIMARY KEY,
    created_at        TEXT NOT NULL,
    strategy_version  TEXT NOT NULL,
    previous_version  TEXT,
    approval_id       TEXT NOT NULL REFERENCES approvals(id),
    comparison_id     TEXT NOT NULL REFERENCES experiments(id)
);
CREATE TRIGGER champions_no_update BEFORE UPDATE ON champions BEGIN
    SELECT RAISE(ABORT, 'champion history is append-only');
END;
CREATE TRIGGER champions_no_delete BEFORE DELETE ON champions BEGIN
    SELECT RAISE(ABORT, 'champion history is append-only');
END;
