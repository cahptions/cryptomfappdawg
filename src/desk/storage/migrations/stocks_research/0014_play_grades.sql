-- Every probability a recorded play showed, graded once against what happened (desk.stocks.plays.grade_events).
-- take_profit: the exit plan's take-profit reached before its stop and its leave-by date, on quoted closing marks and
-- settlements only (outcome take_profit | stop | neither). profit / target25 / target50 / target100 / max_gain: the
-- return at expiry. touch: the stock's daily highs and lows reached the level before expiry. Append-only.
CREATE TABLE stk_play_grades (
    session        INTEGER NOT NULL,
    category       TEXT NOT NULL,
    rank           INTEGER NOT NULL,
    event          TEXT NOT NULL,
    predicted      REAL NOT NULL,          -- the chance shown when the play was picked
    predicted_raw  REAL NOT NULL,          -- before calibration: what calibration learns from
    model_version  INTEGER NOT NULL,       -- the probability model that made the prediction
    outcome        TEXT NOT NULL,
    hit            INTEGER NOT NULL CHECK (hit IN (0, 1)),
    dte            INTEGER NOT NULL,       -- days to expiry when picked
    resolved_at    TEXT NOT NULL,
    PRIMARY KEY (session, category, rank, event)
) WITHOUT ROWID;
CREATE INDEX stk_play_grades_event ON stk_play_grades(event, model_version);
CREATE TRIGGER stk_play_grades_no_update BEFORE UPDATE ON stk_play_grades BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER stk_play_grades_no_delete BEFORE DELETE ON stk_play_grades BEGIN SELECT RAISE(ABORT, 'append-only'); END;
