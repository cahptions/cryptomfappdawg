-- The user's real Schwab account, read only (desk.stocks.account; docs/SCHWAB.md → Reading your Schwab account).
-- Never an account number: each account is a one-way key derived from Schwab's own hashValue plus its last four
-- digits for display. All three tables are append-only; nothing here can place or change an order.

-- one row per read of the account (a pass), whatever its outcome
CREATE TABLE stk_account_pulls (
    id           INTEGER PRIMARY KEY,
    at           TEXT NOT NULL,
    slot         TEXT NOT NULL,             -- e.g. 2026-11-12 09:45 (New York), or 'manual'
    outcome      TEXT NOT NULL CHECK (outcome IN ('ok', 'not_enabled', 'signed_out', 'error')),
    detail       TEXT,                      -- a plain message, never a response body
    accounts     INTEGER NOT NULL DEFAULT 0,
    positions    INTEGER NOT NULL DEFAULT 0,
    fills_new    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX stk_account_pulls_at ON stk_account_pulls(at);

-- one row per position per account per pass
CREATE TABLE stk_account_positions (
    pull_id        INTEGER NOT NULL REFERENCES stk_account_pulls(id),
    account_key    TEXT NOT NULL,
    account_last4  TEXT NOT NULL,
    taken_at       TEXT NOT NULL,
    source         TEXT NOT NULL,           -- schwab_trader_api
    asset_type     TEXT NOT NULL,           -- EQUITY, OPTION, COLLECTIVE_INVESTMENT, ...
    symbol         TEXT NOT NULL,           -- the stock, or the OCC option symbol without spaces
    underlying     TEXT,
    put_call       TEXT,
    strike         REAL,
    expiration     TEXT,
    quantity       REAL NOT NULL,           -- long minus short
    average_price  REAL,
    market_value   REAL,
    day_pnl        REAL,
    day_pnl_pct    REAL,
    PRIMARY KEY (pull_id, account_key, symbol)
) WITHOUT ROWID;

-- every executed trade, once (Schwab's activity id), for grading decisions
CREATE TABLE stk_account_fills (
    account_key     TEXT NOT NULL,
    activity_id     TEXT NOT NULL,
    leg             INTEGER NOT NULL,
    account_last4   TEXT NOT NULL,
    traded_at       TEXT NOT NULL,
    trade_day       TEXT NOT NULL,          -- New York date
    asset_type      TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    underlying      TEXT,
    put_call        TEXT,
    strike          REAL,
    expiration      TEXT,
    quantity        REAL NOT NULL,          -- signed: + bought, − sold (contracts for options)
    price           REAL NOT NULL,
    fees            REAL NOT NULL DEFAULT 0,
    position_effect TEXT,                   -- OPENING, CLOSING or NULL when Schwab does not say
    recorded_at     TEXT NOT NULL,
    PRIMARY KEY (account_key, activity_id, leg)
) WITHOUT ROWID;
CREATE INDEX stk_account_fills_day ON stk_account_fills(trade_day);

CREATE TRIGGER stk_account_pulls_no_update BEFORE UPDATE ON stk_account_pulls
BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER stk_account_pulls_no_delete BEFORE DELETE ON stk_account_pulls
BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER stk_account_positions_no_update BEFORE UPDATE ON stk_account_positions
BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER stk_account_positions_no_delete BEFORE DELETE ON stk_account_positions
BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER stk_account_fills_no_update BEFORE UPDATE ON stk_account_fills
BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER stk_account_fills_no_delete BEFORE DELETE ON stk_account_fills
BEGIN SELECT RAISE(ABORT, 'append-only'); END;
