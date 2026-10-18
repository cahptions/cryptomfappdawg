-- runtime.db: cross-process coordination only (rebuildable; not backed up).

CREATE TABLE rate_events (
    bucket  TEXT NOT NULL,
    ts      REAL NOT NULL          -- unix seconds (wall clock)
);
CREATE INDEX idx_rate_events ON rate_events(bucket, ts);

CREATE TABLE rate_backoff (
    bucket           TEXT PRIMARY KEY,
    until_ts         REAL NOT NULL,
    consecutive_429  INTEGER NOT NULL DEFAULT 0,
    updated_at       REAL NOT NULL
);

CREATE TABLE provider_health (
    provider               TEXT PRIMARY KEY,
    state                  TEXT NOT NULL CHECK (state IN ('ok', 'degraded', 'down')),
    consecutive_failures   INTEGER NOT NULL,
    consecutive_successes  INTEGER NOT NULL,
    last_success_at        TEXT,
    last_failure_at        TEXT,
    last_error             TEXT,
    updated_at             TEXT NOT NULL
);

CREATE TABLE provider_health_events (
    id          INTEGER PRIMARY KEY,
    provider    TEXT NOT NULL,
    from_state  TEXT NOT NULL,
    to_state    TEXT NOT NULL,
    at          TEXT NOT NULL,
    reason      TEXT NOT NULL
);

CREATE TABLE heartbeats (
    process  TEXT PRIMARY KEY,
    pid      INTEGER NOT NULL,
    at       TEXT NOT NULL
);
