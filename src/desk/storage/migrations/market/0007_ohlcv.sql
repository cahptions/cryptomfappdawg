-- Actual, closed provider candles. The first accepted observation is immutable evidence.
CREATE TABLE ohlcv_bars (
    id INTEGER PRIMARY KEY,
    pool_address TEXT NOT NULL REFERENCES pools(address),
    mint TEXT NOT NULL REFERENCES tokens(mint),
    timeframe TEXT NOT NULL CHECK (timeframe = '5m'),
    bar_at TEXT NOT NULL,
    bar_at_ms INTEGER NOT NULL,
    open TEXT NOT NULL,
    high TEXT NOT NULL,
    low TEXT NOT NULL,
    close TEXT NOT NULL,
    volume_usd TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    raw_sha256 TEXT NOT NULL,
    source TEXT NOT NULL CHECK (source = 'geckoterminal.ohlcv'),
    UNIQUE(pool_address, timeframe, bar_at_ms)
);
CREATE INDEX idx_ohlcv_mint_pool_time ON ohlcv_bars(mint,pool_address,timeframe,bar_at_ms);
CREATE INDEX idx_ohlcv_pool_time ON ohlcv_bars(pool_address,timeframe,bar_at_ms);
CREATE TRIGGER ohlcv_no_update BEFORE UPDATE ON ohlcv_bars BEGIN
    SELECT RAISE(ABORT, 'OHLCV evidence is immutable');
END;
CREATE TRIGGER ohlcv_no_delete BEFORE DELETE ON ohlcv_bars BEGIN
    SELECT RAISE(ABORT, 'OHLCV evidence is immutable');
END;
CREATE TABLE ohlcv_fetch_state (
    pool_address TEXT PRIMARY KEY REFERENCES pools(address),
    mint TEXT NOT NULL REFERENCES tokens(mint),
    last_attempt_at TEXT NOT NULL,
    last_success_at TEXT
);
