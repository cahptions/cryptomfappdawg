-- M3 market context: one append-only row per measurement; unknown inputs stay NULL with a recorded reason.
CREATE TABLE market_context (
    id INTEGER PRIMARY KEY,
    observed_at TEXT NOT NULL,
    observed_at_ms INTEGER NOT NULL,
    version TEXT NOT NULL,
    sol_return_1h TEXT,
    sol_volatility_1h TEXT,
    watchlist_volume_usd_1h TEXT,
    migrations_per_hour TEXT,
    hazard_rate_24h TEXT,
    coverage_json TEXT NOT NULL,
    raw_sha256 TEXT
);
CREATE INDEX idx_context_time ON market_context(observed_at_ms);
CREATE TRIGGER market_context_no_update BEFORE UPDATE ON market_context BEGIN
    SELECT RAISE(ABORT, 'market context is append-only');
END;
CREATE TRIGGER market_context_no_delete BEFORE DELETE ON market_context BEGIN
    SELECT RAISE(ABORT, 'market context is append-only');
END;
-- Hazard cohort: the first DexScreener observation of each pool with positive reported liquidity.
CREATE TABLE hazard_cohort (
    pool_address TEXT PRIMARY KEY,
    base_mint TEXT NOT NULL,
    entry_at TEXT NOT NULL,
    entry_at_ms INTEGER NOT NULL,
    entry_liquidity_usd TEXT NOT NULL
);
CREATE INDEX idx_hazard_entry ON hazard_cohort(entry_at_ms);
INSERT OR IGNORE INTO hazard_cohort(pool_address, base_mint, entry_at, entry_at_ms, entry_liquidity_usd)
    SELECT pool_address, base_mint, observed_at, observed_at_ms, liquidity_usd FROM market_snapshots
    WHERE source = 'dexscreener.tokens_v1' AND liquidity_status = 'ok' AND CAST(liquidity_usd AS REAL) > 0
    ORDER BY observed_at_ms, id;
-- Time-range scans for the watchlist volume and migration counts.
CREATE INDEX idx_snap_source_time ON market_snapshots(source, observed_at_ms);
CREATE INDEX idx_discovery_obs_reason_time ON discovery_observations(reason, observed_at);
