-- Per-symbol chart observations are not a complete universe session or a PAPER input.
CREATE TABLE stk_stock_views (
    symbol TEXT PRIMARY KEY,
    requested_at TEXT NOT NULL,
    history_at TEXT,
    history_attempt_at TEXT,
    history_zlib BLOB,
    quote_at TEXT,
    quote_attempt_at TEXT,
    quote_json TEXT,
    history_error TEXT,
    quote_error TEXT
);
