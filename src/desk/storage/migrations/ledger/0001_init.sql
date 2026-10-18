-- ledger-<mode>.db: the money ledger. One physical file per mode (paper, live) so histories can never mix.
-- M1 creates only the foundation; positions/orders/fills arrive in M5.

CREATE TABLE ledger_meta (
    key     TEXT PRIMARY KEY,
    value   TEXT NOT NULL
);

CREATE TABLE config_snapshots (
    hash           TEXT PRIMARY KEY,
    created_at     TEXT NOT NULL,
    ceilings_hash  TEXT NOT NULL,
    content_json   TEXT NOT NULL
);

CREATE TABLE system_state (
    key         TEXT PRIMARY KEY,     -- e.g. 'entries_halted'
    value       TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
