-- M5 PAPER learning book. All amounts are integer lamports (SOL) or raw token units, stored as INTEGER.
-- Portfolio state follows the conservative execution profile; observed/stress are parallel per-trade accountings.

CREATE TABLE positions (
    id                  TEXT PRIMARY KEY,
    intent_id           TEXT NOT NULL UNIQUE,              -- duplicate intents are refused by the database
    candidate_id        TEXT NOT NULL,
    mint                TEXT NOT NULL,
    pool_address        TEXT NOT NULL,
    strategy_id         TEXT NOT NULL,
    strategy_version    TEXT NOT NULL,
    decision_hash       TEXT NOT NULL,
    regime_label        TEXT,                              -- NULL = uncalibrated at decision time
    state               TEXT NOT NULL CHECK (state IN ('OPEN', 'EXITING', 'EXIT_BLOCKED', 'IMPAIRED', 'CLOSED')),
    opened_at           TEXT NOT NULL,
    ticket_lamports     INTEGER NOT NULL CHECK (ticket_lamports > 0),
    tokens_raw          INTEGER NOT NULL CHECK (tokens_raw > 0),
    entry_fees_lamports INTEGER NOT NULL CHECK (entry_fees_lamports >= 0),
    rent_lamports       INTEGER NOT NULL CHECK (rent_lamports >= 0),
    entry_json          TEXT NOT NULL,                     -- quotes, profiles, decision, sizing inputs
    peak_value_lamports INTEGER,
    last_mark_lamports  INTEGER,
    last_mark_at        TEXT,
    last_mark_kind      TEXT CHECK (last_mark_kind IN ('EXECUTABLE', 'ESTIMATED', 'NO_ROUTE')),
    last_route_at       TEXT,                              -- last time any sell route existed
    exit_attempts       INTEGER NOT NULL DEFAULT 0,
    exit_failed_fees_lamports INTEGER NOT NULL DEFAULT 0, -- simulated failed exit transactions so far
    exit_reason         TEXT,
    closed_at           TEXT,
    exit_lamports       INTEGER,
    exit_fees_lamports  INTEGER,
    realized_pnl_lamports INTEGER,                         -- conservative profile, after all fees, rent refunded
    profiles_json       TEXT,                              -- observed / conservative / stress results at close
    CHECK ((state = 'CLOSED') = (closed_at IS NOT NULL)),
    CHECK ((state = 'CLOSED') = (realized_pnl_lamports IS NOT NULL))
);
-- one supervised position per mint: concurrent entry attempts on the same mint cannot both succeed
CREATE UNIQUE INDEX positions_one_open_per_mint ON positions(mint) WHERE state != 'CLOSED';
CREATE INDEX positions_state ON positions(state);
CREATE INDEX positions_closed ON positions(closed_at);

-- Append-only cash journal: the balance is the sum of entries; nothing is updated or deleted.
CREATE TABLE cash_entries (
    id              INTEGER PRIMARY KEY,
    at              TEXT NOT NULL,
    kind            TEXT NOT NULL CHECK (kind IN ('DEPOSIT', 'ENTRY_TICKET', 'ENTRY_FEES', 'RENT_LOCK',
                                                  'EXIT_PROCEEDS', 'EXIT_FEES', 'RENT_REFUND', 'FAILED_TX_FEE')),
    amount_lamports INTEGER NOT NULL,
    position_id     TEXT REFERENCES positions(id),
    note            TEXT NOT NULL DEFAULT ''
);
CREATE TRIGGER cash_entries_no_update BEFORE UPDATE ON cash_entries BEGIN
    SELECT RAISE(ABORT, 'cash journal is append-only');
END;
CREATE TRIGGER cash_entries_no_delete BEFORE DELETE ON cash_entries BEGIN
    SELECT RAISE(ABORT, 'cash journal is append-only');
END;

-- Append-only audit trail for every step of every position (and refused entries).
CREATE TABLE paper_events (
    id           INTEGER PRIMARY KEY,
    at           TEXT NOT NULL,
    position_id  TEXT,
    candidate_id TEXT,
    kind         TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE INDEX paper_events_position ON paper_events(position_id, id);
CREATE INDEX paper_events_kind_time ON paper_events(kind, at);
CREATE TRIGGER paper_events_no_update BEFORE UPDATE ON paper_events BEGIN
    SELECT RAISE(ABORT, 'paper events are append-only');
END;
CREATE TRIGGER paper_events_no_delete BEFORE DELETE ON paper_events BEGIN
    SELECT RAISE(ABORT, 'paper events are append-only');
END;

CREATE TABLE marks (
    id           INTEGER PRIMARY KEY,
    position_id  TEXT NOT NULL REFERENCES positions(id),
    at           TEXT NOT NULL,
    kind         TEXT NOT NULL CHECK (kind IN ('EXECUTABLE', 'ESTIMATED', 'NO_ROUTE')),
    value_lamports INTEGER,
    quote_sha256 TEXT,
    CHECK ((kind = 'NO_ROUTE') = (value_lamports IS NULL))
);
CREATE INDEX marks_position ON marks(position_id, id);
