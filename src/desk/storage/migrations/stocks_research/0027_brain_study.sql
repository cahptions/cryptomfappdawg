-- One immutable protocol and inception per version. A restart cannot move the holdout calendar.
CREATE TABLE stk_brain_protocols (
    version TEXT PRIMARY KEY, first_session INTEGER NOT NULL, registered_at TEXT NOT NULL, protocol TEXT NOT NULL
) WITHOUT ROWID;
CREATE TABLE stk_brain_decisions (
    version TEXT NOT NULL REFERENCES stk_brain_protocols(version), session INTEGER NOT NULL,
    session_index INTEGER NOT NULL, entry_session INTEGER NOT NULL, exit_session INTEGER NOT NULL,
    frozen_at TEXT NOT NULL, decision TEXT NOT NULL CHECK(decision IN ('proposed','abstained')), evidence TEXT NOT NULL,
    PRIMARY KEY(version, session), UNIQUE(version, session_index)
) WITHOUT ROWID;
CREATE TABLE stk_brain_verdicts (
    version TEXT NOT NULL, session INTEGER NOT NULL, graded_at TEXT NOT NULL,
    verdict TEXT NOT NULL CHECK(verdict IN ('graded','inconclusive')), evidence TEXT NOT NULL,
    PRIMARY KEY(version, session), FOREIGN KEY(version, session) REFERENCES stk_brain_decisions(version, session)
) WITHOUT ROWID;
CREATE TRIGGER stk_brain_protocols_fixed BEFORE UPDATE ON stk_brain_protocols BEGIN SELECT RAISE(ABORT,'frozen study protocol'); END;
CREATE TRIGGER stk_brain_protocols_kept BEFORE DELETE ON stk_brain_protocols BEGIN SELECT RAISE(ABORT,'frozen study protocol'); END;
CREATE TRIGGER stk_brain_decisions_fixed BEFORE UPDATE ON stk_brain_decisions BEGIN SELECT RAISE(ABORT,'frozen prediction'); END;
CREATE TRIGGER stk_brain_decisions_kept BEFORE DELETE ON stk_brain_decisions BEGIN SELECT RAISE(ABORT,'frozen prediction'); END;
CREATE TRIGGER stk_brain_verdicts_fixed BEFORE UPDATE ON stk_brain_verdicts BEGIN SELECT RAISE(ABORT,'frozen verdict'); END;
CREATE TRIGGER stk_brain_verdicts_kept BEFORE DELETE ON stk_brain_verdicts BEGIN SELECT RAISE(ABORT,'frozen verdict'); END;
