-- Plays learning (desk.stocks.play_learning): challenger rules run in shadow beside the live ones. Each closed
-- session records what every challenger would have picked, under its own version hash; each is graded once at
-- expiration exactly like the live picks. A challenger is promoted (stk_play_promotions) only after beating the live
-- rules on later sessions. Everything here is append-only.
CREATE TABLE stk_play_shadow (
    session       INTEGER NOT NULL,
    variant       TEXT NOT NULL,
    category      TEXT NOT NULL,
    rank          INTEGER NOT NULL,
    symbol        TEXT NOT NULL,
    action        TEXT NOT NULL CHECK (action IN ('BUY', 'SELL')),
    structure     TEXT NOT NULL,
    expiration    INTEGER NOT NULL,
    legs_json     TEXT NOT NULL,
    entry         REAL NOT NULL,
    max_loss      REAL NOT NULL,
    variant_hash  TEXT NOT NULL,
    play_json     TEXT NOT NULL,
    recorded_at   TEXT NOT NULL,
    PRIMARY KEY (session, variant, category, rank)
) WITHOUT ROWID;

CREATE TABLE stk_play_shadow_days (
    session      INTEGER NOT NULL,
    variant      TEXT NOT NULL,
    category     TEXT NOT NULL,
    size         INTEGER NOT NULL,
    recorded_at  TEXT NOT NULL,
    PRIMARY KEY (session, variant, category)
) WITHOUT ROWID;

CREATE TABLE stk_play_shadow_outcomes (
    session           INTEGER NOT NULL,
    variant           TEXT NOT NULL,
    category          TEXT NOT NULL,
    rank              INTEGER NOT NULL,
    resolved_at       TEXT NOT NULL,
    underlying_close  REAL NOT NULL,
    payoff            REAL NOT NULL,
    net_return        REAL NOT NULL,
    PRIMARY KEY (session, variant, category, rank)
) WITHOUT ROWID;

CREATE TRIGGER stk_play_shadow_no_update BEFORE UPDATE ON stk_play_shadow BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER stk_play_shadow_no_delete BEFORE DELETE ON stk_play_shadow BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER stk_play_shadow_days_no_update BEFORE UPDATE ON stk_play_shadow_days BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER stk_play_shadow_days_no_delete BEFORE DELETE ON stk_play_shadow_days BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER stk_play_shadow_outcomes_no_update BEFORE UPDATE ON stk_play_shadow_outcomes BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER stk_play_shadow_outcomes_no_delete BEFORE DELETE ON stk_play_shadow_outcomes BEGIN SELECT RAISE(ABORT, 'append-only'); END;
