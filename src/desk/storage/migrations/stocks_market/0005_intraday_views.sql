-- Timestamped minute candles belong to the per-stock chart, never complete daily sessions or PAPER inputs.
ALTER TABLE stk_stock_views ADD COLUMN intraday_at TEXT;
ALTER TABLE stk_stock_views ADD COLUMN intraday_attempt_at TEXT;
ALTER TABLE stk_stock_views ADD COLUMN intraday_zlib BLOB;
ALTER TABLE stk_stock_views ADD COLUMN intraday_error TEXT;
