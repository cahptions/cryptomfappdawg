-- M3 records research decisions in market.db. SHADOW never writes positions or fills.
CREATE TABLE strategy_versions (
    spec_hash TEXT PRIMARY KEY,
    strategy_id TEXT NOT NULL,
    version TEXT NOT NULL,
    parent_version TEXT,
    registered_at TEXT NOT NULL,
    content_json TEXT NOT NULL,
    UNIQUE(strategy_id, version)
);
CREATE TRIGGER strategy_versions_no_update BEFORE UPDATE ON strategy_versions BEGIN
    SELECT RAISE(ABORT, 'strategy versions are immutable; register a new version');
END;
CREATE TRIGGER strategy_versions_no_delete BEFORE DELETE ON strategy_versions BEGIN
    SELECT RAISE(ABORT, 'strategy versions are immutable');
END;

CREATE TABLE feature_snapshots (
    snapshot_hash TEXT PRIMARY KEY,
    mint TEXT NOT NULL,
    pool_address TEXT NOT NULL,
    as_of TEXT NOT NULL,
    feature_set_version TEXT NOT NULL,
    content_json TEXT NOT NULL
);
CREATE TRIGGER feature_snapshots_no_update BEFORE UPDATE ON feature_snapshots BEGIN
    SELECT RAISE(ABORT, 'feature snapshots are append-only');
END;
CREATE TRIGGER feature_snapshots_no_delete BEFORE DELETE ON feature_snapshots BEGIN
    SELECT RAISE(ABORT, 'feature snapshots are append-only');
END;

CREATE TABLE shadow_runs (
    id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    settings_json TEXT NOT NULL,
    stats_json TEXT
);
CREATE TRIGGER shadow_runs_fixed_inputs BEFORE UPDATE OF id, started_at, settings_json ON shadow_runs BEGIN
    SELECT RAISE(ABORT, 'SHADOW run inputs are immutable');
END;
CREATE TRIGGER shadow_runs_completed_no_update BEFORE UPDATE ON shadow_runs WHEN OLD.ended_at IS NOT NULL BEGIN
    SELECT RAISE(ABORT, 'completed SHADOW runs are immutable');
END;
CREATE TRIGGER shadow_runs_no_delete BEFORE DELETE ON shadow_runs BEGIN
    SELECT RAISE(ABORT, 'SHADOW runs are append-only');
END;
CREATE TABLE shadow_decisions (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES shadow_runs(id),
    candidate_id TEXT NOT NULL REFERENCES discoveries(id),
    as_of TEXT NOT NULL,
    mode TEXT NOT NULL CHECK (mode = 'SHADOW'),
    snapshot_hash TEXT NOT NULL REFERENCES feature_snapshots(snapshot_hash),
    inputs_hash TEXT NOT NULL,
    config_hash TEXT NOT NULL,
    decision_hash TEXT NOT NULL,
    context_json TEXT NOT NULL,
    result_json TEXT NOT NULL,
    UNIQUE(run_id, candidate_id, as_of)
);
CREATE INDEX idx_shadow_run_time ON shadow_decisions(run_id, as_of, id);
CREATE TRIGGER shadow_decisions_no_update BEFORE UPDATE ON shadow_decisions BEGIN
    SELECT RAISE(ABORT, 'shadow decisions are append-only');
END;
CREATE TRIGGER shadow_decisions_no_delete BEFORE DELETE ON shadow_decisions BEGIN
    SELECT RAISE(ABORT, 'shadow decisions are append-only');
END;
CREATE TRIGGER security_assessments_no_update BEFORE UPDATE ON security_assessments BEGIN
    SELECT RAISE(ABORT, 'security assessments are append-only');
END;
CREATE TRIGGER security_assessments_no_delete BEFORE DELETE ON security_assessments BEGIN
    SELECT RAISE(ABORT, 'security assessments are append-only');
END;
