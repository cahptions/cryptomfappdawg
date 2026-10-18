-- market.db: observations of the outside world. Large; retention applies to undecided data.
-- Amounts that can exceed 2^63 (u64 token amounts) are stored as TEXT. Decimals are TEXT (exact).

CREATE TABLE tokens (
    mint            TEXT PRIMARY KEY,
    first_seen_at   TEXT NOT NULL,
    last_seen_at    TEXT NOT NULL,
    symbol          TEXT,                 -- sanitised untrusted text
    name            TEXT                  -- sanitised untrusted text
);

CREATE TABLE pools (
    address             TEXT PRIMARY KEY,
    base_mint           TEXT NOT NULL REFERENCES tokens(mint),
    quote_mint          TEXT,
    venue               TEXT NOT NULL,
    venue_raw           TEXT NOT NULL,
    pool_created_at     TEXT,             -- provider claim; separate from first_seen_at
    first_seen_at       TEXT NOT NULL,    -- when WE first observed it
    first_seen_source   TEXT NOT NULL,
    last_seen_at        TEXT NOT NULL
);
CREATE INDEX idx_pools_base ON pools(base_mint);

CREATE TABLE market_snapshots (
    id                  INTEGER PRIMARY KEY,
    pool_address        TEXT NOT NULL,
    base_mint           TEXT NOT NULL,
    source              TEXT NOT NULL,
    observed_at         TEXT NOT NULL,
    observed_at_ms      INTEGER NOT NULL,
    price_usd           TEXT,  price_usd_status     TEXT NOT NULL,
    price_native        TEXT,  price_native_status  TEXT NOT NULL,
    liquidity_usd       TEXT,  liquidity_status     TEXT NOT NULL,
    fdv_usd             TEXT,  fdv_status           TEXT NOT NULL,
    market_cap_usd      TEXT,  market_cap_status    TEXT NOT NULL,
    locked_liquidity_pct TEXT,
    windows_json        TEXT NOT NULL,
    flags               TEXT NOT NULL,
    raw_sha256          TEXT,
    CHECK ((price_usd IS NULL) = (price_usd_status = 'missing')),
    CHECK ((liquidity_usd IS NULL) = (liquidity_status = 'missing'))
);
CREATE INDEX idx_snap_mint_time ON market_snapshots(base_mint, observed_at_ms);
CREATE INDEX idx_snap_pool_time ON market_snapshots(pool_address, observed_at_ms);

CREATE TABLE token_claims (
    id              INTEGER PRIMARY KEY,
    mint            TEXT NOT NULL,
    source          TEXT NOT NULL,
    observed_at     TEXT NOT NULL,
    observed_at_ms  INTEGER NOT NULL,
    claims_json     TEXT NOT NULL,
    raw_sha256      TEXT
);
CREATE INDEX idx_claims_mint_time ON token_claims(mint, observed_at_ms);

CREATE TABLE quotes (
    id              INTEGER PRIMARY KEY,
    source          TEXT NOT NULL,
    outcome         TEXT NOT NULL CHECK (outcome IN ('quote', 'unavailable')),
    input_mint      TEXT NOT NULL,
    output_mint     TEXT NOT NULL,
    in_amount_raw   TEXT NOT NULL,
    out_amount_raw  TEXT,
    received_at     TEXT NOT NULL,
    received_at_ms  INTEGER NOT NULL,
    payload_json    TEXT NOT NULL,
    raw_sha256      TEXT
);
CREATE INDEX idx_quotes_pair_time ON quotes(input_mint, output_mint, received_at_ms);

CREATE TABLE mint_accounts (
    id               INTEGER PRIMARY KEY,
    mint             TEXT NOT NULL,
    slot             INTEGER NOT NULL,
    observed_at      TEXT NOT NULL,
    token_program    TEXT NOT NULL,
    decimals         INTEGER NOT NULL,
    supply_raw       TEXT NOT NULL,
    mint_authority   TEXT,              -- NULL = no authority on-chain (verified), not "unknown"
    freeze_authority TEXT,
    extensions_json  TEXT NOT NULL,
    raw_sha256       TEXT
);
CREATE INDEX idx_mint_accounts_mint ON mint_accounts(mint, slot);

CREATE TABLE raw_responses (
    sha256          TEXT PRIMARY KEY,
    provider        TEXT NOT NULL,
    endpoint        TEXT NOT NULL,
    http_status     INTEGER NOT NULL,
    received_at     TEXT NOT NULL,
    size_bytes      INTEGER NOT NULL,
    rel_path        TEXT NOT NULL,
    keep            INTEGER NOT NULL DEFAULT 0   -- 1 = linked to a decision; never purged
);
CREATE INDEX idx_raw_received ON raw_responses(received_at);

CREATE TABLE ingest_errors (
    id          INTEGER PRIMARY KEY,
    source      TEXT NOT NULL,
    at          TEXT NOT NULL,
    error       TEXT NOT NULL,
    raw_sha256  TEXT
);

CREATE TABLE collector_runs (
    id           TEXT PRIMARY KEY,
    started_at   TEXT NOT NULL,
    ended_at     TEXT,
    config_hash  TEXT NOT NULL,
    stats_json   TEXT
);
