-- Network fees reported by real Jupiter orders built for a read-only taker (nothing is signed or sent).
CREATE TABLE fee_observations (
    id                          INTEGER PRIMARY KEY,
    observed_at                 TEXT NOT NULL,
    observed_at_ms              INTEGER NOT NULL,
    route                       TEXT NOT NULL,
    router                      TEXT,
    signature_fee_lamports      INTEGER NOT NULL CHECK (signature_fee_lamports >= 0),
    prioritization_fee_lamports INTEGER NOT NULL CHECK (prioritization_fee_lamports >= 0),
    rent_fee_lamports           INTEGER CHECK (rent_fee_lamports >= 0),
    fee_bps                     INTEGER,
    raw_sha256                  TEXT
);
CREATE INDEX idx_fee_observations_time ON fee_observations(observed_at_ms);
CREATE TRIGGER fee_observations_no_update BEFORE UPDATE ON fee_observations BEGIN
    SELECT RAISE(ABORT, 'fee observations are append-only');
END;
CREATE TRIGGER fee_observations_no_delete BEFORE DELETE ON fee_observations BEGIN
    SELECT RAISE(ABORT, 'fee observations are append-only');
END;
