-- Display-only company context for a viewed stock (company details, news, analyst ratings, earnings estimates),
-- cached per source. Never a point-in-time research input: rows are overwritten when a source is refreshed.
CREATE TABLE stk_symbol_feeds (
    symbol TEXT NOT NULL,
    feed TEXT NOT NULL,
    requested_at TEXT NOT NULL,
    attempted_at TEXT,
    updated_at TEXT,
    payload_json TEXT,
    error TEXT,
    PRIMARY KEY (symbol, feed)
);
-- A company's square icon (PNG or JPEG only, never SVG), served from this origin so no key reaches the browser.
CREATE TABLE stk_symbol_logos (
    symbol TEXT PRIMARY KEY,
    content_type TEXT NOT NULL,
    body BLOB NOT NULL,
    fetched_at TEXT NOT NULL
);
-- Five sessions of 10-minute candles for the stock page's one-week chart.
ALTER TABLE stk_stock_views ADD COLUMN week_zlib BLOB;
