-- Explicit dashboard POSTs request market-data research, never trades or watchlist changes.
CREATE TABLE stk_option_requests (
    symbol TEXT PRIMARY KEY,
    requested_at TEXT NOT NULL,
    attempted_at TEXT
);
