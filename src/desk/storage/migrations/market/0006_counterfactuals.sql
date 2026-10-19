-- M6-05 counterfactual tracking: what happened to rejected candidates. Dead tokens stay as NO_DATA rows.
CREATE TABLE counterfactuals (
    candidate_id        TEXT PRIMARY KEY REFERENCES discoveries(id),
    mint                TEXT NOT NULL,
    pool_address        TEXT NOT NULL,
    rule_id             TEXT NOT NULL,
    stage               INTEGER NOT NULL,
    reference_at        TEXT NOT NULL,
    reference_at_ms     INTEGER NOT NULL,
    reference_price_usd TEXT,                       -- NULL when no same-pool observation preceded the rejection
    quote_sample        INTEGER NOT NULL CHECK (quote_sample IN (0, 1))
);
CREATE INDEX idx_counterfactuals_ref ON counterfactuals(reference_at_ms);
CREATE TABLE counterfactual_outcomes (
    id              INTEGER PRIMARY KEY,
    candidate_id    TEXT NOT NULL REFERENCES counterfactuals(candidate_id),
    horizon_seconds INTEGER NOT NULL,
    recorded_at     TEXT NOT NULL,
    observed_at     TEXT,
    price_usd       TEXT,
    liquidity_usd   TEXT,
    return          TEXT,
    status          TEXT NOT NULL CHECK (status IN ('OK', 'ZERO_LIQUIDITY', 'NO_DATA')),
    quote_json      TEXT,
    UNIQUE (candidate_id, horizon_seconds)
);
CREATE TRIGGER counterfactual_outcomes_no_update BEFORE UPDATE ON counterfactual_outcomes BEGIN
    SELECT RAISE(ABORT, 'counterfactual outcomes are append-only');
END;
CREATE TRIGGER counterfactual_outcomes_no_delete BEFORE DELETE ON counterfactual_outcomes BEGIN
    SELECT RAISE(ABORT, 'counterfactual outcomes are append-only');
END;
