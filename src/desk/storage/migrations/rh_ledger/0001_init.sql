-- robinhood-ledger-paper.db: the Robinhood desk's PAPER book. Money is integer micro-dollars (1e-6 USD).
-- Cash and events are append-only; a position's entry never changes and a CLOSED position never changes.

CREATE TABLE ledger_meta (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);

CREATE TABLE rh_state (
    key         TEXT PRIMARY KEY,      -- entries_halted
    value       TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE rh_cash (
    id             INTEGER PRIMARY KEY,
    at             TEXT NOT NULL,
    kind           TEXT NOT NULL CHECK (kind IN ('DEPOSIT', 'BUY', 'SELL')),
    amount_micros  INTEGER NOT NULL,   -- signed: BUY negative, SELL and DEPOSIT positive
    position_id    TEXT,
    note           TEXT NOT NULL DEFAULT ''
);

CREATE TABLE rh_positions (
    id                       TEXT PRIMARY KEY,
    strategy_id              TEXT NOT NULL,
    spec_sha256              TEXT NOT NULL,
    symbol                   TEXT NOT NULL,
    decision_hash            TEXT NOT NULL UNIQUE,
    opened_at                TEXT NOT NULL,
    session_day              TEXT NOT NULL,
    quantity                 TEXT NOT NULL,
    entry_route              TEXT NOT NULL CHECK (entry_route IN ('MM', 'EX')),
    entry_unit_price         TEXT NOT NULL,
    entry_fee                TEXT NOT NULL,
    entry_cost_micros        INTEGER NOT NULL CHECK (entry_cost_micros > 0),
    entry_estimate_sha256    TEXT NOT NULL,
    roundtrip_cost_fraction  TEXT NOT NULL,
    stop_price               TEXT NOT NULL,
    plan_json                TEXT NOT NULL,
    peak_close               TEXT NOT NULL,      -- highest daily close since entry; moves only while OPEN
    state                    TEXT NOT NULL CHECK (state IN ('OPEN', 'CLOSED')),
    closed_at                TEXT,
    exit_route               TEXT CHECK (exit_route IS NULL OR exit_route IN ('MM', 'EX')),
    exit_unit_price          TEXT,
    exit_fee                 TEXT,
    exit_credit_micros       INTEGER,
    exit_reason              TEXT,
    realized_pnl_micros      INTEGER,
    CHECK (state = 'OPEN' OR (closed_at IS NOT NULL AND exit_credit_micros IS NOT NULL
                              AND realized_pnl_micros IS NOT NULL AND exit_reason IS NOT NULL))
);
CREATE INDEX rh_positions_state ON rh_positions(state, strategy_id);

CREATE TABLE rh_events (
    id            INTEGER PRIMARY KEY,
    at            TEXT NOT NULL,
    kind          TEXT NOT NULL,       -- BOOK_OPENED, ENTRY, EXIT, SKIP, HALT, RESUME
    position_id   TEXT,
    payload_json  TEXT NOT NULL
);

CREATE TRIGGER rh_cash_no_update BEFORE UPDATE ON rh_cash BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER rh_cash_no_delete BEFORE DELETE ON rh_cash BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER rh_events_no_update BEFORE UPDATE ON rh_events BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER rh_events_no_delete BEFORE DELETE ON rh_events BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER rh_positions_no_delete BEFORE DELETE ON rh_positions BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER rh_positions_closed_frozen BEFORE UPDATE ON rh_positions WHEN OLD.state = 'CLOSED'
BEGIN SELECT RAISE(ABORT, 'a closed position never changes'); END;
CREATE TRIGGER rh_positions_entry_frozen BEFORE UPDATE ON rh_positions
WHEN NEW.id IS NOT OLD.id OR NEW.strategy_id IS NOT OLD.strategy_id OR NEW.spec_sha256 IS NOT OLD.spec_sha256
  OR NEW.symbol IS NOT OLD.symbol OR NEW.decision_hash IS NOT OLD.decision_hash OR NEW.opened_at IS NOT OLD.opened_at
  OR NEW.session_day IS NOT OLD.session_day OR NEW.quantity IS NOT OLD.quantity
  OR NEW.entry_route IS NOT OLD.entry_route OR NEW.entry_unit_price IS NOT OLD.entry_unit_price
  OR NEW.entry_fee IS NOT OLD.entry_fee OR NEW.entry_cost_micros IS NOT OLD.entry_cost_micros
  OR NEW.entry_estimate_sha256 IS NOT OLD.entry_estimate_sha256
  OR NEW.roundtrip_cost_fraction IS NOT OLD.roundtrip_cost_fraction OR NEW.plan_json IS NOT OLD.plan_json
BEGIN SELECT RAISE(ABORT, 'a position entry never changes'); END;
