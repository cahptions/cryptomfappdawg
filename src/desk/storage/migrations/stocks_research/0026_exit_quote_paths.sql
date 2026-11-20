-- Preserve exact receipt times, provider timestamps and displayed sizes alongside the legacy minute chart.
-- The parent path's 35-day compaction also clears these compressed rows.
CREATE TABLE stk_track_quote_receipts (
    occ TEXT NOT NULL,
    session INTEGER NOT NULL,
    packed BLOB NOT NULL,
    PRIMARY KEY (occ, session),
    FOREIGN KEY (occ, session) REFERENCES stk_track_intraday(occ, session) ON DELETE CASCADE
) WITHOUT ROWID;
