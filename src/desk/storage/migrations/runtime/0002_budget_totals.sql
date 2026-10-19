-- Monotonic shared allocation counters survive sliding-window event cleanup.
CREATE TABLE rate_totals (
    bucket TEXT PRIMARY KEY,
    allocated INTEGER NOT NULL DEFAULT 0
);
