-- KESTREL's earnings model (desk.stocks.earnings_model; docs/STOCKS_PLAYS.md "Earnings: priced vs expected move").

-- One past results report per stock: how far the stock actually moved (split-adjusted, close to close, timing-aware)
-- and, when a chain was stored for the session before the results, the move its options priced. Filled by the daily
-- job ``earnings_moves`` (resumable, a bounded number of reports a pass). A NULL is missing, never zero; ``note`` says
-- why. Rows are only ever added (a later model version re-computes into a new pass, never in place).
CREATE TABLE stk_earnings_moves (
    symbol             TEXT NOT NULL,
    report_date        INTEGER NOT NULL,       -- the day results were released
    timing             TEXT,                   -- before the open | during market hours | after the close | NULL unknown
    source             TEXT NOT NULL,          -- finnhub (announced calendar) | sec_8k (results filing, item 2.02)
    entry_day          INTEGER NOT NULL,       -- the last session whose close came before the results
    exit_day           INTEGER NOT NULL,       -- the first session whose close reflects them
    realized           REAL,                   -- exit close / entry close − 1, split-adjusted (signed)
    implied            REAL,                   -- the event move options priced: σ √(2/π) (Dubinsky–Johannes)
    straddle           REAL,                   -- at-the-money straddle mid / stock price, first expiry after the results
    basis              TEXT,                   -- pre_expiry | post_expiries: how the normal daily variance was read
    expiry             INTEGER,                -- the first expiry after the results
    chain_observed_at  TEXT,                   -- the chain the implied move came from
    version            INTEGER NOT NULL,       -- earnings_model.VERSION
    note               TEXT,
    computed_at        TEXT NOT NULL,
    PRIMARY KEY (symbol, report_date)
) WITHOUT ROWID;
CREATE INDEX stk_earnings_moves_exit ON stk_earnings_moves(exit_day);
