-- Notable market events (desk.stocks.market_events): appended once per (rules, day, kind, subject), never rewritten.
CREATE TABLE stk_market_events (
    spec_sha256  TEXT NOT NULL,
    day          INTEGER NOT NULL,
    kind         TEXT NOT NULL,
    subject      TEXT NOT NULL,
    family       TEXT NOT NULL,
    severity     INTEGER NOT NULL,
    direction    TEXT NOT NULL,
    tone         TEXT NOT NULL,
    title        TEXT NOT NULL,
    summary      TEXT NOT NULL,
    detail_json  TEXT NOT NULL,
    detected_at  TEXT NOT NULL,
    basis        TEXT NOT NULL,       -- close: logged within a day of the session; history: found later in stored data
    PRIMARY KEY (spec_sha256, day, kind, subject)
);
CREATE INDEX stk_market_events_day ON stk_market_events(spec_sha256, day);

-- which sessions (prices) and days (rates) each rule set has judged, events or not
CREATE TABLE stk_market_event_scans (
    spec_sha256  TEXT NOT NULL,
    source       TEXT NOT NULL,
    day          INTEGER NOT NULL,
    scanned_at   TEXT NOT NULL,
    PRIMARY KEY (spec_sha256, source, day)
);

-- headlines published around a big single-stock move, for reading only (never used to decide an event)
CREATE TABLE stk_market_event_news (
    day           INTEGER NOT NULL,
    subject       TEXT NOT NULL,
    attempted_at  TEXT NOT NULL,
    fetched_at    TEXT,
    items_json    TEXT,
    error         TEXT,
    PRIMARY KEY (day, subject)
);

CREATE TABLE stk_market_event_state (
    key         TEXT PRIMARY KEY,
    value_json  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
