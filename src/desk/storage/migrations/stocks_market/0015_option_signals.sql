-- KESTREL's option-implied stock signals (desk.stocks.option_signals; docs/STOCKS_METHOD.md "Options desk").

-- One stock's raw option measures on one session, from the newest chain of that session: the after-close chain scan
-- ('scan', desk.stocks.chain_scan) or the chain collected during the day ('collected'). Values that need other days
-- (option-to-stock volume against its own median, changes in implied volatility) are derived when read, from earlier
-- rows only. ``detail_json`` is the compact chain snapshot: per expiry, its days, at-the-money call and put IV, volume
-- and open interest, plus why any measure is missing. A NULL measure is missing, never zero.
CREATE TABLE stk_option_signals (
    symbol          TEXT NOT NULL,
    day             INTEGER NOT NULL,
    source          TEXT NOT NULL,          -- scan | collected
    version         INTEGER NOT NULL,       -- option_signals.VERSION that computed the row
    price           REAL NOT NULL,          -- the stock price the chain was read against
    iv_spread       REAL,                   -- OI-weighted mean (call IV − put IV), matched pairs 10-60 days out
    pairs           INTEGER NOT NULL,       -- liquid call-put pairs behind iv_spread
    skew            REAL,                   -- ~0.2-0.25 delta put IV − at-the-money call IV, expiry nearest 30 days
    call_iv30       REAL,                   -- at-the-money call IV at a constant 30 days
    put_iv30        REAL,                   -- at-the-money put IV at a constant 30 days
    iv30            REAL,
    iv90            REAL,
    rv22            REAL,                   -- realized volatility, last 22 sessions (vol_forecast), annualized
    option_volume   INTEGER,                -- contracts traded that session, expiries within 120 days
    stock_volume    REAL,                   -- shares traded that session
    borrow_rate     REAL,                   -- Schwab htbRate (percent a year) when sent
    hard_to_borrow  INTEGER,                -- Schwab isHardToBorrow (1/0) when sent
    contracts       INTEGER NOT NULL,
    liquid          INTEGER NOT NULL,       -- contracts passing the quote filters
    detail_json     TEXT NOT NULL,
    observed_at     TEXT NOT NULL,          -- when the chain was quoted
    computed_at     TEXT NOT NULL,
    PRIMARY KEY (symbol, day)
) WITHOUT ROWID;
CREATE INDEX stk_option_signals_day ON stk_option_signals(day);

-- The after-close chain scan's outcome per stock and session, so a pass resumes where the last stopped and a stock
-- without listed options is not asked again for a month. Pruned after 60 days.
CREATE TABLE stk_option_scan (
    day      INTEGER NOT NULL,
    symbol   TEXT NOT NULL,
    status   TEXT NOT NULL CHECK (status IN ('scanned', 'no_options', 'failed', 'thin')),
    note     TEXT,
    at       TEXT NOT NULL,
    PRIMARY KEY (day, symbol)
) WITHOUT ROWID;
