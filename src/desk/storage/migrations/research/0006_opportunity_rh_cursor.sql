-- Robinhood candles have a composite primary key WITHOUT ROWID. Resume by actual receipt and identity.
CREATE TABLE opportunity_rh_cursors (
    spec_sha256 TEXT PRIMARY KEY,
    received_ms INTEGER NOT NULL,
    product TEXT NOT NULL,
    start_ms INTEGER NOT NULL
);
