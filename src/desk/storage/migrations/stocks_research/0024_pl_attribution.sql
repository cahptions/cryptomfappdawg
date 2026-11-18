-- Where each trade's money came from (desk.stocks.attribution; docs/STOCKS_LEARNING.md): every closed PAPER / Learner
-- option trade and every graded play split into direction, volatility, time decay, costs and what is unexplained, from
-- stored quotes only. One row per trade and method version (a trade that cannot be graded is a row with no parts and
-- the reason). Append-only.
CREATE TABLE stk_pl_attribution (
    kind          TEXT NOT NULL CHECK (kind IN ('paper', 'play')),
    ref           TEXT NOT NULL,              -- the PAPER position id, or "<session>:<category>:<rank>" of a play
    version       INTEGER NOT NULL,           -- attribution.VERSION
    grp           TEXT NOT NULL,              -- the PAPER account, or the Plays section
    symbol        TEXT NOT NULL,
    structure     TEXT NOT NULL,
    closed_day    INTEGER NOT NULL,           -- the exit day, or the play's expiry
    total         REAL,                       -- the recorded profit or loss after fees (dollars; plays: per contract)
    direction     REAL,
    volatility    REAL,
    time          REAL,
    costs         REAL,
    unexplained   REAL,
    detail_json   TEXT NOT NULL,
    computed_at   TEXT NOT NULL,
    PRIMARY KEY (kind, ref, version)
) WITHOUT ROWID;
CREATE INDEX stk_pl_attribution_group ON stk_pl_attribution(kind, grp);

CREATE TRIGGER stk_pl_attribution_no_update BEFORE UPDATE ON stk_pl_attribution BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER stk_pl_attribution_no_delete BEFORE DELETE ON stk_pl_attribution BEGIN SELECT RAISE(ABORT, 'append-only'); END;
