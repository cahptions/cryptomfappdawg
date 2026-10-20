-- Evidence council (research only, zero influence): the SOL/USD reference minutes the collector already requests
-- for market context, normalized so 15-minute benchmark returns can be aligned with token bars. Bar time is the
-- interval START (documented and measured, RESEARCH.md). A bar is stored only once it had been closed for longer
-- than the provider's 60-second response cache when received; that first observation is immutable evidence of
-- what was known and when. Minutes without swaps are absent, never filled.
CREATE TABLE benchmark_bars (
    id INTEGER PRIMARY KEY,
    pool_address TEXT NOT NULL,
    instrument TEXT NOT NULL CHECK (instrument = 'SOL/USD'),
    source TEXT NOT NULL CHECK (source = 'geckoterminal.ohlcv'),
    timeframe TEXT NOT NULL CHECK (timeframe = '1m'),
    bar_start TEXT NOT NULL,
    bar_start_ms INTEGER NOT NULL CHECK (bar_start_ms % 60000 = 0),
    open TEXT NOT NULL,
    high TEXT NOT NULL,
    low TEXT NOT NULL,
    close TEXT NOT NULL,
    volume_usd TEXT NOT NULL,
    first_received_at TEXT NOT NULL,
    first_received_at_ms INTEGER NOT NULL CHECK (first_received_at_ms >= bar_start_ms + 120000),
    raw_sha256 TEXT NOT NULL,
    UNIQUE(pool_address, timeframe, bar_start_ms)
);
CREATE INDEX idx_benchmark_bars_time ON benchmark_bars(pool_address, timeframe, bar_start_ms);
CREATE TRIGGER benchmark_bars_no_update BEFORE UPDATE ON benchmark_bars BEGIN
    SELECT RAISE(ABORT, 'benchmark evidence is immutable');
END;
CREATE TRIGGER benchmark_bars_no_delete BEFORE DELETE ON benchmark_bars BEGIN
    SELECT RAISE(ABORT, 'benchmark evidence is immutable');
END;
