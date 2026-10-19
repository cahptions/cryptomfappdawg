-- M6-13 local model scores: one point-in-time score per recorded PAPER decision, append-only.
CREATE TABLE model_scores (
    decision_hash        TEXT PRIMARY KEY,
    decided_at           TEXT NOT NULL,
    scored_at            TEXT NOT NULL,
    model_version        TEXT NOT NULL,
    training_end         TEXT NOT NULL,      -- latest close time of any training label (strictly before decided_at)
    training_trades      INTEGER NOT NULL,
    feature_set_version  TEXT NOT NULL,
    inputs_hash          TEXT NOT NULL,
    score                REAL                 -- NULL = model abstained (missing feature or no model yet)
);
CREATE TRIGGER model_scores_no_update BEFORE UPDATE ON model_scores BEGIN
    SELECT RAISE(ABORT, 'model scores are append-only');
END;
CREATE TRIGGER model_scores_no_delete BEFORE DELETE ON model_scores BEGIN
    SELECT RAISE(ABORT, 'model scores are append-only');
END;
