-- What the Learner tries next (docs/STOCKS_LEARNER.md → From findings to trials; learner/trials.py): each confirmed
-- "do" finding becomes a frozen, fingerprinted trial (a Plays section and a PAPER account), at most five live. Every
-- table is append-only: a trial is never edited, its stages (live, retired) are new rows.
CREATE TABLE stk_learn_trial_specs (
    id             TEXT PRIMARY KEY,                -- learned_<finding id prefix>: the Plays section and PAPER account
    finding_id     TEXT NOT NULL UNIQUE,            -- a finding gives at most one trial, ever
    version        INTEGER NOT NULL,                -- trials.VERSION
    fingerprint    TEXT NOT NULL,                   -- sha256 of the spec (derived from the finding, never edited)
    spec_json      TEXT NOT NULL,
    evidence_json  TEXT NOT NULL,                   -- the finding's discovery and confirmation, as shown
    created_at     TEXT NOT NULL
) WITHOUT ROWID;

CREATE TABLE stk_learn_trial_status (
    trial_id       TEXT NOT NULL REFERENCES stk_learn_trial_specs(id),
    status         TEXT NOT NULL CHECK (status IN ('live', 'retired')),
    at             TEXT NOT NULL,
    reason         TEXT NOT NULL,
    evidence_json  TEXT NOT NULL,
    PRIMARY KEY (trial_id, status)
) WITHOUT ROWID;

-- The Learner PAPER account's graduation to a Plays section (learner/graduation.py): graduated once its version is
-- confirmed (promotion v2), demoted when its latest forward window falls below the same bar, and back again.
CREATE TABLE stk_learn_graduation (
    model_code      TEXT NOT NULL,
    policy_version  TEXT NOT NULL,
    at              TEXT NOT NULL,
    stage           TEXT NOT NULL CHECK (stage IN ('graduated', 'demoted')),
    detail_json     TEXT NOT NULL,
    PRIMARY KEY (model_code, policy_version, at, stage)
) WITHOUT ROWID;

CREATE TRIGGER stk_learn_trial_specs_no_update BEFORE UPDATE ON stk_learn_trial_specs BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER stk_learn_trial_specs_no_delete BEFORE DELETE ON stk_learn_trial_specs BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER stk_learn_trial_status_no_update BEFORE UPDATE ON stk_learn_trial_status BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER stk_learn_trial_status_no_delete BEFORE DELETE ON stk_learn_trial_status BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER stk_learn_graduation_no_update BEFORE UPDATE ON stk_learn_graduation BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER stk_learn_graduation_no_delete BEFORE DELETE ON stk_learn_graduation BEGIN SELECT RAISE(ABORT, 'append-only'); END;
