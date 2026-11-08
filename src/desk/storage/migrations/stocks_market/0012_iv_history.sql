-- Contracts Massive listed on a past day, for a purpose: 'iv' (the monthly at-the-money call and put the implied
-- volatility history is solved from), 'earnings' (the straddle bought before a past report), 'replay' (a month's real
-- listing for a stock the historical replay trades). ``expiry`` is 0 for a listing that spans several expiries.
CREATE TABLE stk_option_lists (
    purpose    TEXT NOT NULL,
    symbol     TEXT NOT NULL,
    expiry     INTEGER NOT NULL,
    as_of      INTEGER NOT NULL,
    listed     INTEGER NOT NULL,
    contracts  TEXT NOT NULL,
    note       TEXT,
    listed_at  TEXT NOT NULL,
    PRIMARY KEY (purpose, symbol, expiry, as_of)
) WITHOUT ROWID;

-- A constant 30-day at-the-money implied volatility per stock and day, solved from real option closing trades.
CREATE TABLE stk_iv_daily (
    symbol       TEXT NOT NULL,
    day          INTEGER NOT NULL,
    iv30         REAL NOT NULL,
    source       TEXT NOT NULL,
    contracts    INTEGER NOT NULL,
    computed_at  TEXT NOT NULL,
    PRIMARY KEY (symbol, day)
) WITHOUT ROWID;
