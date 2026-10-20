-- robinhood-market.db: what the Robinhood desk observed and decided (docs/ROBINHOOD.md).
-- Everything is append-only except the pair registry, whose changes are logged in rh_pair_events.

CREATE TABLE rh_pairs (
    symbol            TEXT PRIMARY KEY,
    asset_code        TEXT NOT NULL,
    quote_code        TEXT NOT NULL,
    asset_increment   TEXT NOT NULL,
    quote_increment   TEXT NOT NULL,
    max_order_size    TEXT NOT NULL,
    min_order_amount  TEXT,
    status            TEXT NOT NULL,
    is_api_tradable   INTEGER NOT NULL CHECK (is_api_tradable IN (0, 1)),
    first_seen_at     TEXT NOT NULL,
    last_seen_at      TEXT NOT NULL
);

CREATE TABLE rh_pair_events (
    id            INTEGER PRIMARY KEY,
    symbol        TEXT NOT NULL,
    at            TEXT NOT NULL,
    content_json  TEXT NOT NULL
);

-- best bid/ask (v2, partner exchanges, fee excluded), every quote interval for every tradable pair
CREATE TABLE rh_quotes (
    id              INTEGER PRIMARY KEY,
    symbol          TEXT NOT NULL,
    bid             TEXT NOT NULL,
    ask             TEXT NOT NULL,
    received_at     TEXT NOT NULL,
    received_at_ms  INTEGER NOT NULL,
    raw_sha256      TEXT NOT NULL
);
CREATE INDEX rh_quotes_symbol_time ON rh_quotes(symbol, received_at_ms);

-- Robinhood's own estimate of filling an exact quantity: MM = market-maker routing (spread inside the price),
-- EX = exchange routing (fee tier). purpose: PROBE (cost evidence), ENTRY, EXIT.
CREATE TABLE rh_estimates (
    id              INTEGER PRIMARY KEY,
    symbol          TEXT NOT NULL,
    route           TEXT NOT NULL CHECK (route IN ('MM', 'EX')),
    side            TEXT NOT NULL CHECK (side IN ('BUY', 'SELL')),
    quantity        TEXT NOT NULL,
    unit_price      TEXT NOT NULL,
    fee             TEXT NOT NULL,
    total           TEXT NOT NULL,
    fee_ratio       TEXT,
    provider_at     TEXT,
    received_at     TEXT NOT NULL,
    received_at_ms  INTEGER NOT NULL,
    purpose         TEXT NOT NULL CHECK (purpose IN ('PROBE', 'ENTRY', 'EXIT')),
    raw_sha256      TEXT NOT NULL
);
CREATE INDEX rh_estimates_symbol_time ON rh_estimates(symbol, received_at_ms);

-- closed bars only: a bar is stored once it ended before it was received (the forming bar never is)
CREATE TABLE candles (
    source                TEXT NOT NULL,
    product               TEXT NOT NULL,
    granularity           INTEGER NOT NULL,
    start                 TEXT NOT NULL,
    start_ms              INTEGER NOT NULL,
    open                  TEXT NOT NULL,
    high                  TEXT NOT NULL,
    low                   TEXT NOT NULL,
    close                 TEXT NOT NULL,
    volume                TEXT NOT NULL,
    first_received_at     TEXT NOT NULL,
    first_received_at_ms  INTEGER NOT NULL CHECK (first_received_at_ms >= start_ms + granularity * 1000),
    PRIMARY KEY (source, product, granularity, start_ms)
) WITHOUT ROWID;

CREATE TABLE rh_specs (
    spec_sha256    TEXT PRIMARY KEY,
    strategy_id    TEXT NOT NULL,
    version        INTEGER NOT NULL,
    registered_at  TEXT NOT NULL,
    content_json   TEXT NOT NULL,
    UNIQUE (strategy_id, version)
);

-- every ENTER/EXIT a strategy produced, and every blocked signal (SKIP) with its reasons
CREATE TABLE rh_decisions (
    id              INTEGER PRIMARY KEY,
    decided_at      TEXT NOT NULL,
    session_day     TEXT NOT NULL,
    strategy_id     TEXT NOT NULL,
    spec_sha256     TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    action          TEXT NOT NULL CHECK (action IN ('ENTER', 'EXIT', 'SKIP')),
    reasons         TEXT NOT NULL,
    features_json   TEXT NOT NULL,
    inputs_sha256   TEXT NOT NULL,
    decision_hash   TEXT NOT NULL UNIQUE
);
CREATE INDEX rh_decisions_time ON rh_decisions(decided_at);

CREATE TRIGGER rh_pair_events_no_update BEFORE UPDATE ON rh_pair_events BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER rh_pair_events_no_delete BEFORE DELETE ON rh_pair_events BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER rh_quotes_no_update BEFORE UPDATE ON rh_quotes BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER rh_quotes_no_delete BEFORE DELETE ON rh_quotes BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER rh_estimates_no_update BEFORE UPDATE ON rh_estimates BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER rh_estimates_no_delete BEFORE DELETE ON rh_estimates BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER candles_no_update BEFORE UPDATE ON candles BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER candles_no_delete BEFORE DELETE ON candles BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER rh_specs_no_update BEFORE UPDATE ON rh_specs BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER rh_specs_no_delete BEFORE DELETE ON rh_specs BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER rh_decisions_no_update BEFORE UPDATE ON rh_decisions BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER rh_decisions_no_delete BEFORE DELETE ON rh_decisions BEGIN SELECT RAISE(ABORT, 'append-only'); END;
