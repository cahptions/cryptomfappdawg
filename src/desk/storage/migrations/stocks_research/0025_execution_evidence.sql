ALTER TABLE stk_track_ticks ADD COLUMN bid_size INTEGER;
ALTER TABLE stk_track_ticks ADD COLUMN ask_size INTEGER;

-- Freeze arm choices and the selection probabilities before collecting any outcome.
CREATE TABLE stk_execution_cohort (
    session INTEGER NOT NULL,
    version TEXT NOT NULL,
    decided_at TEXT NOT NULL,
    manifest_json TEXT NOT NULL,
    PRIMARY KEY (session, version)
) WITHOUT ROWID;

-- Missing evidence is represented by an absent grade until the window ends.
CREATE TABLE stk_execution_grades (
    session INTEGER NOT NULL,
    version TEXT NOT NULL,
    reviewed_at TEXT NOT NULL,
    result_json TEXT NOT NULL,
    PRIMARY KEY (session, version),
    FOREIGN KEY (session, version) REFERENCES stk_execution_cohort(session, version)
) WITHOUT ROWID;

CREATE TRIGGER stk_execution_cohort_no_update BEFORE UPDATE ON stk_execution_cohort BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER stk_execution_cohort_no_delete BEFORE DELETE ON stk_execution_cohort BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER stk_execution_grades_no_update BEFORE UPDATE ON stk_execution_grades BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER stk_execution_grades_no_delete BEFORE DELETE ON stk_execution_grades BEGIN SELECT RAISE(ABORT, 'append-only'); END;

CREATE TABLE stk_execution_windows (
    session INTEGER NOT NULL,
    version TEXT NOT NULL,
    slot TEXT NOT NULL,
    received_at TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    PRIMARY KEY (session, version, slot),
    FOREIGN KEY (session, version) REFERENCES stk_execution_cohort(session, version)
) WITHOUT ROWID;
