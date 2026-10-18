-- research.db: experiments and evaluations. The experiment registry is append-only (PLAN.md §14.2).

CREATE TABLE experiments (
    id            TEXT PRIMARY KEY,
    created_at    TEXT NOT NULL,
    kind          TEXT NOT NULL,
    payload_json  TEXT NOT NULL
);

CREATE TRIGGER experiments_no_update BEFORE UPDATE ON experiments
BEGIN
    SELECT RAISE(ABORT, 'experiments are append-only');
END;

CREATE TRIGGER experiments_no_delete BEFORE DELETE ON experiments
BEGIN
    SELECT RAISE(ABORT, 'experiments are append-only');
END;
