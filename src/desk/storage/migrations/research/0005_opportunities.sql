-- Separate, record-only discovery research. No foreign keys into mutable/retained market data.
CREATE TABLE opportunity_cohorts (
    id TEXT PRIMARY KEY,
    asset TEXT NOT NULL,
    pool TEXT NOT NULL,
    first_snapshot_id INTEGER NOT NULL,
    first_seen_ms INTEGER NOT NULL,
    first_source TEXT NOT NULL,
    venue TEXT NOT NULL,
    UNIQUE(asset, pool, venue)
);
CREATE TABLE opportunity_points (
    id TEXT PRIMARY KEY,
    cohort_id TEXT NOT NULL REFERENCES opportunity_cohorts(id),
    spec_sha256 TEXT NOT NULL,
    snapshot_id INTEGER NOT NULL,
    decided_ms INTEGER NOT NULL,
    bucket INTEGER NOT NULL,
    recorded_at TEXT NOT NULL,
    evidence_kind TEXT NOT NULL,
    primary_state TEXT NOT NULL,
    primary_rule TEXT,
    stance TEXT NOT NULL,
    flow_watch INTEGER NOT NULL,
    price_watch INTEGER NOT NULL,
    reference_price TEXT,
    features_json TEXT NOT NULL,
    inputs_sha256 TEXT NOT NULL,
    UNIQUE(cohort_id, spec_sha256, bucket)
);
CREATE INDEX opportunity_points_due ON opportunity_points(spec_sha256, decided_ms);
CREATE TABLE opportunity_outcomes (
    point_id TEXT NOT NULL REFERENCES opportunity_points(id),
    horizon_seconds INTEGER NOT NULL,
    recorded_at TEXT NOT NULL,
    status TEXT NOT NULL,
    chart_return TEXT,
    payload_json TEXT NOT NULL,
    PRIMARY KEY(point_id, horizon_seconds)
);
CREATE TABLE opportunity_cursors (
    spec_sha256 TEXT PRIMARY KEY,
    snapshot_id INTEGER NOT NULL
);
CREATE TRIGGER opportunity_cohorts_no_update BEFORE UPDATE ON opportunity_cohorts
BEGIN SELECT RAISE(ABORT, 'opportunity cohorts are immutable'); END;
CREATE TRIGGER opportunity_cohorts_no_delete BEFORE DELETE ON opportunity_cohorts
BEGIN SELECT RAISE(ABORT, 'opportunity cohorts are immutable'); END;
CREATE TRIGGER opportunity_points_no_update BEFORE UPDATE ON opportunity_points
BEGIN SELECT RAISE(ABORT, 'opportunity points are immutable'); END;
CREATE TRIGGER opportunity_points_no_delete BEFORE DELETE ON opportunity_points
BEGIN SELECT RAISE(ABORT, 'opportunity points are immutable'); END;
CREATE TRIGGER opportunity_outcomes_no_update BEFORE UPDATE ON opportunity_outcomes
BEGIN SELECT RAISE(ABORT, 'opportunity outcomes are immutable'); END;
CREATE TRIGGER opportunity_outcomes_no_delete BEFORE DELETE ON opportunity_outcomes
BEGIN SELECT RAISE(ABORT, 'opportunity outcomes are immutable'); END;
