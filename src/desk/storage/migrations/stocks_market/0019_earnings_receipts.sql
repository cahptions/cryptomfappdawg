-- Store complete provider calendar receipts so a withdrawn or moved date can be reconstructed.
CREATE TABLE stk_earnings_calendar_receipts (
    observed_at TEXT PRIMARY KEY,
    calendar_json TEXT NOT NULL
) WITHOUT ROWID;
