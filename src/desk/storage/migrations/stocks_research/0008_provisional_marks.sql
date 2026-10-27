-- Plays history (desk.stocks.play_marks): marks inside a gap that no later quote brackets yet (the laptop was off
-- and the contract has not been quoted since). They are estimates with implied volatility held at the last quote,
-- replaced on every repair pass, never used for grading or learning, and superseded by permanent marks
-- (stk_play_marks, append-only) once a later quote or the expiry brackets the gap.
CREATE TABLE stk_play_marks_provisional (
    session      INTEGER NOT NULL,
    category     TEXT NOT NULL,
    rank         INTEGER NOT NULL,
    twin         INTEGER NOT NULL CHECK (twin IN (0, 1)),
    day          INTEGER NOT NULL,
    value        REAL NOT NULL,
    mid          REAL,
    pnl          REAL NOT NULL,
    pnl_pct      REAL NOT NULL,
    underlying   REAL,
    note         TEXT,
    computed_at  TEXT NOT NULL,
    PRIMARY KEY (session, category, rank, twin, day)
) WITHOUT ROWID;

CREATE INDEX stk_play_marks_day ON stk_play_marks(day);
CREATE INDEX stk_track_days_session ON stk_track_days(session);
