-- A durable per-coin candle-close checkpoint. Claim before any PAPER execution await: a crash
-- can miss a simulated opportunity but cannot replay that check on restart. No real orders exist.
CREATE TABLE rh_frame_checks (
    id INTEGER PRIMARY KEY,
    symbol TEXT NOT NULL,
    bar_end TEXT NOT NULL,
    spec_bundle_sha256 TEXT NOT NULL,
    decided_at TEXT NOT NULL,
    input_sha256 TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    UNIQUE(symbol, bar_end, spec_bundle_sha256)
);
CREATE TRIGGER rh_frame_checks_no_update BEFORE UPDATE ON rh_frame_checks
BEGIN SELECT RAISE(ABORT, 'frame checks are immutable'); END;
CREATE TRIGGER rh_frame_checks_no_delete BEFORE DELETE ON rh_frame_checks
BEGIN SELECT RAISE(ABORT, 'frame checks are immutable'); END;
