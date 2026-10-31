-- Learning lifecycle experiments (docs/STOCKS_LEARNING.md): one position bought once, its sellable value recorded at
-- real quotes after 1 day, 1 week, 2 weeks, 1, 2, 3, 4 and 6 months, so "is it better to hold longer?" is answered
-- from the same entries. Each checkpoint is written once, when the first fresh quote at or after it arrives.
CREATE TABLE stk_learning_checkpoints (
    position_id  TEXT NOT NULL REFERENCES stk_positions(id),
    days         INTEGER NOT NULL,          -- calendar days after the entry
    at           TEXT NOT NULL,             -- the quote time it was valued at
    value        REAL NOT NULL,             -- what selling then would have returned, after exit fees and impact
    net_return   REAL NOT NULL,             -- on the entry cost and fees
    underlying   REAL,
    PRIMARY KEY (position_id, days)
) WITHOUT ROWID;
CREATE TRIGGER stk_learning_checkpoints_no_update BEFORE UPDATE ON stk_learning_checkpoints
BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER stk_learning_checkpoints_no_delete BEFORE DELETE ON stk_learning_checkpoints
BEGIN SELECT RAISE(ABORT, 'append-only'); END;
