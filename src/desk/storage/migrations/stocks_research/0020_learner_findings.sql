-- The Learner, phases 3-4 (docs/STOCKS_LEARNER.md): held-out predictions kept with each model, the "do / don't"
-- findings written from them, and the forward promotion record. Every table is append-only; a status change is a new
-- row, never an edit.

-- Each model's walk-forward held-out rows (identity, predictions, realised return), zlib-packed: what its held-out
-- results and the findings rest on. Predictions here were made by models that never saw these rows.
CREATE TABLE stk_learn_heldout (
    model_id      TEXT PRIMARY KEY,
    rows          INTEGER NOT NULL,
    days          INTEGER NOT NULL,
    first_session INTEGER,
    last_session  INTEGER,
    rows_zlib     BLOB NOT NULL,
    recorded_at   TEXT NOT NULL
);

-- A finding: an interpretable region of the action and feature space where held-out outcomes were reliably good
-- ("do") or bad ("don't"), its rule, its words and the evidence it was found on.
CREATE TABLE stk_learn_findings (
    id              TEXT PRIMARY KEY,          -- sha256 (first 16) of the rule
    kind            TEXT NOT NULL CHECK (kind IN ('do', 'dont')),
    rule_json       TEXT NOT NULL,
    words           TEXT NOT NULL,
    rules_version   TEXT NOT NULL,             -- learner.findings.version_hash()
    model_id        TEXT NOT NULL,             -- the model whose held-out rows it was found on
    evidence_json   TEXT NOT NULL,             -- discovery: rows, days, means, ranges, p, correction, sources
    first_session   INTEGER NOT NULL,          -- the discovery window
    last_session    INTEGER NOT NULL,
    found_at        TEXT NOT NULL
);

-- Each finding's stages: candidate (passed on discovery) → confirmed (held on later data) → retired (later data
-- contradicted it); failed (later data did not confirm it). The latest row is its status.
CREATE TABLE stk_learn_finding_status (
    finding_id     TEXT NOT NULL REFERENCES stk_learn_findings(id),
    at             TEXT NOT NULL,
    status         TEXT NOT NULL CHECK (status IN ('candidate', 'confirmed', 'failed', 'retired')),
    model_id       TEXT NOT NULL,
    first_session  INTEGER,                    -- the window this stage was judged on
    last_session   INTEGER,
    evidence_json  TEXT NOT NULL,
    PRIMARY KEY (finding_id, status)
) WITHOUT ROWID;

-- A Learner version (model code + policy) is provisional until an untouched forward period confirms it beat Random
-- and the rule-based strategies; failures are kept.
CREATE TABLE stk_learn_promotions (
    model_code      TEXT NOT NULL,
    policy_version  TEXT NOT NULL,
    stage           TEXT NOT NULL CHECK (stage IN ('provisional', 'confirmed', 'failed')),
    at              TEXT NOT NULL,
    detail_json     TEXT NOT NULL,
    PRIMARY KEY (model_code, policy_version, stage)
) WITHOUT ROWID;

CREATE TRIGGER stk_learn_heldout_no_update BEFORE UPDATE ON stk_learn_heldout BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER stk_learn_heldout_no_delete BEFORE DELETE ON stk_learn_heldout BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER stk_learn_findings_no_update BEFORE UPDATE ON stk_learn_findings BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER stk_learn_findings_no_delete BEFORE DELETE ON stk_learn_findings BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER stk_learn_finding_status_no_update BEFORE UPDATE ON stk_learn_finding_status BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER stk_learn_finding_status_no_delete BEFORE DELETE ON stk_learn_finding_status BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER stk_learn_promotions_no_update BEFORE UPDATE ON stk_learn_promotions BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER stk_learn_promotions_no_delete BEFORE DELETE ON stk_learn_promotions BEGIN SELECT RAISE(ABORT, 'append-only'); END;
