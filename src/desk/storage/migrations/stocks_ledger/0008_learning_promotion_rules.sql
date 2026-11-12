-- Stricter Learning promotion (2026-11-13 rules; desk.stocks.learning_promotion, docs/STOCKS_LEARNING.md).
-- Each recipe's experiment manifest is frozen with a hash when it is registered; every promotion decision
-- (provisional, confirmed, failed confirmation, and earlier promotions relabelled) is kept. Both append-only.
CREATE TABLE stk_learning_manifests (
    experiment_id    TEXT PRIMARY KEY,
    registered_at    TEXT NOT NULL,
    manifest_sha256  TEXT NOT NULL,
    manifest_json    TEXT NOT NULL,
    backfilled       INTEGER NOT NULL CHECK (backfilled IN (0, 1))   -- 1: frozen when these rules arrived
);
CREATE TABLE stk_learning_promotions (
    id               INTEGER PRIMARY KEY,
    experiment_id    TEXT NOT NULL,         -- a recipe, or 'structure:<name>' for a promotion before these rules
    at               TEXT NOT NULL,
    rules            TEXT NOT NULL,
    stage            TEXT NOT NULL CHECK (stage IN ('provisional', 'confirmed', 'failed', 'legacy')),
    manifest_sha256  TEXT,
    detail_json      TEXT NOT NULL
);
CREATE INDEX stk_learning_promotions_recipe ON stk_learning_promotions(experiment_id, id);
CREATE TRIGGER stk_learning_manifests_no_update BEFORE UPDATE ON stk_learning_manifests
BEGIN SELECT RAISE(ABORT, 'manifests are frozen'); END;
CREATE TRIGGER stk_learning_manifests_no_delete BEFORE DELETE ON stk_learning_manifests
BEGIN SELECT RAISE(ABORT, 'manifests are frozen'); END;
CREATE TRIGGER stk_learning_promotions_no_update BEFORE UPDATE ON stk_learning_promotions
BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER stk_learning_promotions_no_delete BEFORE DELETE ON stk_learning_promotions
BEGIN SELECT RAISE(ABORT, 'append-only'); END;
