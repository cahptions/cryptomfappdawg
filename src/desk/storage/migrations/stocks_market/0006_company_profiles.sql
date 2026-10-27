-- Display-only company context. Never used as a point-in-time quantitative feature.
CREATE TABLE stk_company_profiles (
    cik INTEGER PRIMARY KEY,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    about TEXT,
    source_url TEXT,
    filing_date TEXT,
    requested_at TEXT,
    attempted_at TEXT,
    updated_at TEXT,
    error TEXT
);
