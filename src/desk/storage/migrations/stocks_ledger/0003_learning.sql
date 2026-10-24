-- Used by the separate stocks-learning-paper.db. Existing forward accounts are not converted.
CREATE TABLE stk_learning_experiments (
 id TEXT PRIMARY KEY, created_at TEXT NOT NULL, hypothesis TEXT NOT NULL,
 spec_sha256 TEXT NOT NULL, config_json TEXT NOT NULL
);
CREATE TABLE stk_learning_sources (
 source_key TEXT PRIMARY KEY, underlying TEXT NOT NULL, observed_at TEXT NOT NULL, payload_zlib BLOB NOT NULL
);
CREATE TABLE stk_learning_intents (
 order_id INTEGER PRIMARY KEY REFERENCES stk_pending(id), experiment_id TEXT NOT NULL,
 source_key TEXT NOT NULL REFERENCES stk_learning_sources(source_key), desired_units INTEGER NOT NULL,
 forecast_json TEXT NOT NULL, UNIQUE(experiment_id,source_key,order_id)
);
CREATE TABLE stk_learning_fills (
 order_id INTEGER NOT NULL REFERENCES stk_pending(id), position_id TEXT NOT NULL REFERENCES stk_positions(id),
 units INTEGER NOT NULL, at TEXT NOT NULL, PRIMARY KEY(order_id,position_id)
);
CREATE TABLE stk_learning_capacity (
 account TEXT NOT NULL, quote_key TEXT NOT NULL, used INTEGER NOT NULL, PRIMARY KEY(account,quote_key)
);
CREATE TABLE stk_learning_outcomes (
 position_id TEXT PRIMARY KEY REFERENCES stk_positions(id), experiment_id TEXT NOT NULL,
 entered_at TEXT NOT NULL, matured_at TEXT NOT NULL, block TEXT NOT NULL,
 net_return REAL NOT NULL, pnl REAL NOT NULL, requested_units INTEGER NOT NULL, filled_units INTEGER NOT NULL
);
CREATE TABLE stk_learning_checks (
 id INTEGER PRIMARY KEY, at TEXT NOT NULL, kind TEXT NOT NULL, payload_json TEXT NOT NULL
);
CREATE TABLE stk_learning_nav (
 at TEXT NOT NULL, account TEXT NOT NULL, equity REAL NOT NULL, contributed REAL NOT NULL,
 pnl REAL NOT NULL, PRIMARY KEY(at,account)
);
CREATE TABLE stk_learning_state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TRIGGER stk_learning_experiments_frozen BEFORE UPDATE ON stk_learning_experiments
 BEGIN SELECT RAISE(ABORT,'versioned experiments are immutable'); END;
CREATE TRIGGER stk_learning_outcomes_frozen BEFORE UPDATE ON stk_learning_outcomes
 BEGIN SELECT RAISE(ABORT,'matured outcomes are immutable'); END;
CREATE TRIGGER stk_learning_sources_frozen BEFORE UPDATE ON stk_learning_sources
 BEGIN SELECT RAISE(ABORT,'observations are immutable'); END;
