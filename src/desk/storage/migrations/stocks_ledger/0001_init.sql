-- stocks-ledger-paper.db: KESTREL Stocks PAPER books (docs/STOCKS_PAPER.md). Each strategy trades its own paper
-- account, so strategies never compete for cash and every one has a clean record. Money is integer micro-dollars.
-- Cash, decisions and events are append-only; an opened position's entry and a closed position never change.

CREATE TABLE ledger_meta (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);

CREATE TABLE stk_cash (
    id             INTEGER PRIMARY KEY,
    account        TEXT NOT NULL,
    at             TEXT NOT NULL,
    day            INTEGER NOT NULL,
    amount_micros  INTEGER NOT NULL,
    reason         TEXT NOT NULL CHECK (reason IN ('DEPOSIT', 'OPEN', 'CLOSE', 'DIVIDEND', 'EXPIRE', 'FEE')),
    position_id    TEXT
);
CREATE INDEX stk_cash_account ON stk_cash(account, id);

CREATE TABLE stk_decisions (
    id             INTEGER PRIMARY KEY,
    at             TEXT NOT NULL,
    asof           INTEGER NOT NULL,
    account        TEXT NOT NULL,
    spec_sha256    TEXT NOT NULL,
    symbol         TEXT NOT NULL,
    action         TEXT NOT NULL CHECK (action IN ('ENTER', 'EXIT', 'SKIP')),
    reasons        TEXT NOT NULL,
    evidence_json  TEXT NOT NULL
);
CREATE INDEX stk_decisions_account ON stk_decisions(account, asof);

-- orders decided after a close fill at the next session's open (stocks) or the next option snapshot
CREATE TABLE stk_pending (
    id              INTEGER PRIMARY KEY,
    decision_id     INTEGER NOT NULL REFERENCES stk_decisions(id),
    account         TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    fill_after_day  INTEGER NOT NULL,
    order_json      TEXT NOT NULL,
    state           TEXT NOT NULL CHECK (state IN ('PENDING', 'FILLED', 'CANCELLED')),
    resolved_at     TEXT,
    note            TEXT
);
CREATE INDEX stk_pending_state ON stk_pending(state, fill_after_day);

CREATE TABLE stk_positions (
    id                  TEXT PRIMARY KEY,
    account             TEXT NOT NULL,
    spec_sha256         TEXT NOT NULL,
    asset               TEXT NOT NULL CHECK (asset IN ('stock', 'option')),
    symbol              TEXT NOT NULL,
    structure           TEXT NOT NULL,
    legs_json           TEXT NOT NULL,
    decision_id         INTEGER NOT NULL REFERENCES stk_decisions(id),
    opened_at           TEXT NOT NULL,
    entry_day           INTEGER NOT NULL,
    entry_cost_micros   INTEGER NOT NULL,
    entry_fees_micros   INTEGER NOT NULL,
    max_loss_micros     INTEGER,
    stop_price          REAL,
    expires_day         INTEGER,
    plan_json           TEXT NOT NULL,
    state               TEXT NOT NULL CHECK (state IN ('OPEN', 'CLOSED')),
    last_value_micros   INTEGER,
    peak_value_micros   INTEGER,
    closed_at           TEXT,
    exit_day            INTEGER,
    exit_credit_micros  INTEGER,
    exit_fees_micros    INTEGER,
    exit_reason         TEXT,
    exit_evidence_json  TEXT
);
CREATE INDEX stk_positions_account ON stk_positions(account, state);

CREATE TABLE stk_events (
    id            INTEGER PRIMARY KEY,
    at            TEXT NOT NULL,
    account       TEXT,
    kind          TEXT NOT NULL,
    position_id   TEXT,
    payload_json  TEXT NOT NULL
);

CREATE TABLE stk_equity (
    day               INTEGER NOT NULL,
    account           TEXT NOT NULL,
    at                TEXT NOT NULL,
    cash_micros       INTEGER NOT NULL,
    positions_micros  INTEGER NOT NULL,
    equity_micros     INTEGER NOT NULL,
    PRIMARY KEY (day, account)
);

CREATE TRIGGER stk_cash_no_update BEFORE UPDATE ON stk_cash BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER stk_cash_no_delete BEFORE DELETE ON stk_cash BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER stk_decisions_no_update BEFORE UPDATE ON stk_decisions BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER stk_decisions_no_delete BEFORE DELETE ON stk_decisions BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER stk_events_no_update BEFORE UPDATE ON stk_events BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER stk_events_no_delete BEFORE DELETE ON stk_events BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER stk_equity_no_update BEFORE UPDATE ON stk_equity BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER stk_equity_no_delete BEFORE DELETE ON stk_equity BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER stk_pending_no_delete BEFORE DELETE ON stk_pending BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER stk_pending_resolved_frozen BEFORE UPDATE ON stk_pending WHEN OLD.state != 'PENDING'
BEGIN SELECT RAISE(ABORT, 'a resolved order never changes'); END;
CREATE TRIGGER stk_pending_order_frozen BEFORE UPDATE ON stk_pending
WHEN NEW.order_json IS NOT OLD.order_json OR NEW.decision_id IS NOT OLD.decision_id OR NEW.account IS NOT OLD.account
  OR NEW.fill_after_day IS NOT OLD.fill_after_day
BEGIN SELECT RAISE(ABORT, 'an order never changes'); END;
CREATE TRIGGER stk_positions_no_delete BEFORE DELETE ON stk_positions BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER stk_positions_closed_frozen BEFORE UPDATE ON stk_positions WHEN OLD.state = 'CLOSED'
BEGIN SELECT RAISE(ABORT, 'a closed position never changes'); END;
CREATE TRIGGER stk_positions_entry_frozen BEFORE UPDATE ON stk_positions
WHEN NEW.id IS NOT OLD.id OR NEW.account IS NOT OLD.account OR NEW.spec_sha256 IS NOT OLD.spec_sha256
  OR NEW.asset IS NOT OLD.asset OR NEW.symbol IS NOT OLD.symbol OR NEW.structure IS NOT OLD.structure
  OR NEW.legs_json IS NOT OLD.legs_json OR NEW.decision_id IS NOT OLD.decision_id OR NEW.opened_at IS NOT OLD.opened_at
  OR NEW.entry_day IS NOT OLD.entry_day OR NEW.entry_cost_micros IS NOT OLD.entry_cost_micros
  OR NEW.entry_fees_micros IS NOT OLD.entry_fees_micros OR NEW.max_loss_micros IS NOT OLD.max_loss_micros
  OR NEW.plan_json IS NOT OLD.plan_json
BEGIN SELECT RAISE(ABORT, 'an opened position''s entry never changes'); END;
