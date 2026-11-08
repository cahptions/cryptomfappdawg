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

-- Announced earnings dates (Finnhub's free calendar, read daily when its optional key is set): one row per stock and
-- date with when it was first and last listed, so a moved date drops out of the newest calendar.
CREATE TABLE stk_earnings_dates (
    symbol       TEXT NOT NULL,
    report_date  INTEGER NOT NULL,
    hour         TEXT,
    source       TEXT NOT NULL,
    first_seen   TEXT NOT NULL,
    last_seen    TEXT NOT NULL,
    PRIMARY KEY (symbol, report_date)
) WITHOUT ROWID;
CREATE INDEX stk_earnings_dates_seen ON stk_earnings_dates(last_seen);

-- Stock minute bars kept after Schwab's ten days (desk.stocks.minute_archive): one compressed row per stock and
-- finished session, only ever added to; capped by size, oldest sessions pruned first.
CREATE TABLE stk_minute_bars (
    symbol       TEXT NOT NULL,
    day          INTEGER NOT NULL,
    source       TEXT NOT NULL,
    minutes      INTEGER NOT NULL,
    bytes        INTEGER NOT NULL,
    bars_zlib    BLOB NOT NULL,
    received_at  TEXT NOT NULL,
    PRIMARY KEY (symbol, day)
) WITHOUT ROWID;
CREATE INDEX stk_minute_bars_day ON stk_minute_bars(day);

-- Company news counted per stock and New York day as published (desk.stocks.news_history), for rebuilt and replayed
-- days: from the stored articles, and earlier days back-filled from Massive's feed. A day is counted once, complete.
CREATE TABLE stk_news_daily (
    day        INTEGER NOT NULL,
    symbol     TEXT NOT NULL,
    articles   INTEGER NOT NULL,
    positive   INTEGER NOT NULL,
    negative   INTEGER NOT NULL,
    neutral    INTEGER NOT NULL,
    PRIMARY KEY (day, symbol)
) WITHOUT ROWID;
CREATE TABLE stk_news_days (
    day       INTEGER PRIMARY KEY,
    articles  INTEGER NOT NULL,
    source    TEXT NOT NULL
);
-- articles of the day being paged backward, until all of that day is in
CREATE TABLE stk_news_staging (
    id            TEXT NOT NULL,
    symbol        TEXT NOT NULL,
    published_at  TEXT NOT NULL,
    sentiment     TEXT,
    PRIMARY KEY (id, symbol)
) WITHOUT ROWID;
