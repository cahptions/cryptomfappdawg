-- Candidate identity and the evidence supporting each transition are stored atomically.
CREATE TABLE discoveries (
    id TEXT PRIMARY KEY,
    mint TEXT NOT NULL REFERENCES tokens(mint),
    pool_address TEXT NOT NULL DEFAULT '',
    reason TEXT NOT NULL,
    first_observed_at TEXT NOT NULL,
    last_observed_at TEXT NOT NULL,
    token_created_at TEXT,
    pool_created_at TEXT,
    state TEXT NOT NULL CHECK (state IN ('PENDING', 'PASS', 'REJECT', 'DEFER')),
    stage INTEGER NOT NULL DEFAULT 0,
    last_evaluated_at TEXT,
    last_rule_id TEXT,
    UNIQUE(mint, pool_address)
);
CREATE INDEX idx_discoveries_state ON discoveries(state, last_evaluated_at);
CREATE TABLE discovery_observations (
    id INTEGER PRIMARY KEY,
    candidate_id TEXT NOT NULL REFERENCES discoveries(id),
    source TEXT NOT NULL,
    reason TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    token_created_at TEXT,
    pool_created_at TEXT,
    raw_sha256 TEXT,
    UNIQUE(candidate_id, source, reason, observed_at)
);
CREATE TABLE stage_results (
    id INTEGER PRIMARY KEY,
    candidate_id TEXT NOT NULL REFERENCES discoveries(id),
    stage INTEGER NOT NULL CHECK (stage BETWEEN 0 AND 4),
    outcome TEXT NOT NULL CHECK (outcome IN ('PASS', 'REJECT', 'DEFER')),
    rule_id TEXT NOT NULL,
    observed_values_json TEXT NOT NULL,
    thresholds_json TEXT NOT NULL,
    config_hash TEXT NOT NULL,
    decided_at TEXT NOT NULL
);
CREATE INDEX idx_stage_candidate ON stage_results(candidate_id, id);
CREATE TRIGGER stage_results_no_update BEFORE UPDATE ON stage_results BEGIN
    SELECT RAISE(ABORT, 'stage results are append-only');
END;
CREATE TRIGGER stage_results_no_delete BEFORE DELETE ON stage_results BEGIN
    SELECT RAISE(ABORT, 'stage results are append-only');
END;
CREATE TABLE security_assessments (
    id INTEGER PRIMARY KEY,
    candidate_id TEXT NOT NULL REFERENCES discoveries(id),
    assessed_at TEXT NOT NULL,
    config_hash TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE TABLE discovery_cursors (
    stream TEXT PRIMARY KEY,
    last_id INTEGER NOT NULL
);
