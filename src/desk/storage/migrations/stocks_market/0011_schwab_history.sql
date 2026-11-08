-- Earlier daily prices from Schwab's price history (up to 20 years), for days before the first stored Massive day.
-- Rows go into stk_bars, unadjusted like Massive's, and their days into stk_bar_days with source
-- 'schwab_pricehistory'; Massive's rows and days are never touched. One row per symbol says what was checked: whether
-- Schwab's candles were split-adjusted (proved on the overlap with Massive's unadjusted bars and the stored splits),
-- what was stored, or why the symbol was refused.
CREATE TABLE stk_history_symbols (
    symbol      TEXT PRIMARY KEY,
    source      TEXT NOT NULL,
    status      TEXT NOT NULL CHECK (status IN ('stored', 'refused', 'none', 'failed')),
    basis       TEXT,
    overlap     INTEGER NOT NULL DEFAULT 0,
    first_day   INTEGER,
    last_day    INTEGER,
    rows        INTEGER NOT NULL DEFAULT 0,
    years       INTEGER NOT NULL DEFAULT 0,      -- price_history_years when checked (a longer span checks again)
    note        TEXT,
    checked_at  TEXT NOT NULL
) WITHOUT ROWID;

-- Companies delisted during Massive's two years (its reference tickers, active=false), so the point-in-time universe
-- of those years includes them: their grouped-daily bars up to the delisting day are kept like any other stock's.
-- A ticker that is listed again today (reused by another company) is left out: its bars couldn't be told apart.
CREATE TABLE stk_delisted (
    symbol       TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    kind         TEXT NOT NULL,
    cik          INTEGER,
    delisted     INTEGER NOT NULL,
    source       TEXT NOT NULL,
    received_at  TEXT NOT NULL
) WITHOUT ROWID;
