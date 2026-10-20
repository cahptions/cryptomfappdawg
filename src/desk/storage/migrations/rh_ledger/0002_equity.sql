-- Portfolio value over time for the Robinhood dashboard chart: cash plus open positions valued at the best bid.
CREATE TABLE rh_equity (
    id                      INTEGER PRIMARY KEY,
    at                      TEXT NOT NULL,
    at_ms                   INTEGER NOT NULL,
    cash_micros             INTEGER NOT NULL,
    positions_value_micros  INTEGER NOT NULL,
    open_positions          INTEGER NOT NULL
);
CREATE INDEX rh_equity_time ON rh_equity(at_ms);
CREATE TRIGGER rh_equity_no_update BEFORE UPDATE ON rh_equity BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER rh_equity_no_delete BEFORE DELETE ON rh_equity BEGIN SELECT RAISE(ABORT, 'append-only'); END;
