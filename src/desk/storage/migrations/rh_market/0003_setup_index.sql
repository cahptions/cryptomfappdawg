-- Keep hourly-setup deduplication bounded as the immutable decision journal grows.
CREATE INDEX rh_decisions_setup ON rh_decisions(strategy_id, symbol, spec_sha256, action);
