-- Evidence council (research/council): exactly two SHADOW advisors with zero influence. These tables are records
-- only; no process that decides, sizes, signs or exits reads them.
CREATE TABLE council_specs (
    spec_sha256 TEXT PRIMARY KEY,
    advisor_id TEXT NOT NULL CHECK (advisor_id IN ('C1_BRC', 'C2_ECDR')),
    version INTEGER NOT NULL CHECK (version >= 1),
    registered_at TEXT NOT NULL,
    content_json TEXT NOT NULL,
    UNIQUE(advisor_id, version)
);
CREATE TRIGGER council_specs_no_update BEFORE UPDATE ON council_specs BEGIN
    SELECT RAISE(ABORT, 'council specifications are immutable; register a new version');
END;
CREATE TRIGGER council_specs_no_delete BEFORE DELETE ON council_specs BEGIN
    SELECT RAISE(ABORT, 'council specifications are immutable');
END;

-- Daily frozen cohort: members stay for their day even after they fail or disappear.
CREATE TABLE council_members (
    profile TEXT NOT NULL,
    day TEXT NOT NULL,
    mint TEXT NOT NULL,
    pool_address TEXT NOT NULL,
    admitted_at TEXT NOT NULL,
    primary_decision_hash TEXT NOT NULL,
    PRIMARY KEY (profile, day, mint, pool_address)
);
CREATE TRIGGER council_members_no_update BEFORE UPDATE ON council_members BEGIN
    SELECT RAISE(ABORT, 'council membership is append-only');
END;
CREATE TRIGGER council_members_no_delete BEFORE DELETE ON council_members BEGIN
    SELECT RAISE(ABORT, 'council membership is append-only');
END;

CREATE TABLE council_advisories (
    advisory_id TEXT PRIMARY KEY,
    advisor_id TEXT NOT NULL CHECK (advisor_id IN ('C1_BRC', 'C2_ECDR')),
    spec_sha256 TEXT NOT NULL REFERENCES council_specs(spec_sha256),
    source TEXT NOT NULL CHECK (source IN ('SHADOW', 'PAPER', 'LIVE')),
    primary_decision_hash TEXT NOT NULL,
    primary_action TEXT NOT NULL CHECK (primary_action IN ('NO_TRADE', 'LONG_CANDIDATE', 'UNKNOWN')),
    mint TEXT NOT NULL,
    pool_address TEXT NOT NULL,
    decision_at TEXT NOT NULL,
    computed_at TEXT NOT NULL,
    stance TEXT NOT NULL CHECK (stance IN ('SUPPORT', 'CAUTION', 'OPPOSE', 'ABSTAIN')),
    reasons TEXT NOT NULL,
    input_sha256 TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    UNIQUE(advisor_id, spec_sha256, source, primary_decision_hash)
);
CREATE INDEX idx_council_advisories_time ON council_advisories(decision_at);
CREATE INDEX idx_council_advisories_decision ON council_advisories(primary_decision_hash);
CREATE TRIGGER council_advisories_no_update BEFORE UPDATE ON council_advisories BEGIN
    SELECT RAISE(ABORT, 'advisories are immutable records');
END;
CREATE TRIGGER council_advisories_no_delete BEFORE DELETE ON council_advisories BEGIN
    SELECT RAISE(ABORT, 'advisories are immutable records');
END;

-- Operational cursor of the record-only runner (not evidence): the last primary decision row read per source.
CREATE TABLE council_progress (
    source TEXT PRIMARY KEY CHECK (source IN ('SHADOW', 'PAPER', 'LIVE')),
    last_row_id INTEGER NOT NULL,
    updated_at TEXT NOT NULL
);
