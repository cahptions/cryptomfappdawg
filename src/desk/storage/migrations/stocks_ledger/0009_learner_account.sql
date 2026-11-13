-- The Learner PAPER account's decisions (desk.stocks.learner.policy; docs/STOCKS_LEARNER.md → The Learner account).
-- They live in the PAPER ledger beside the orders they place, so a decision and its order commit together. Every
-- table is append-only: a decision is never changed once recorded, whatever happens to its order.

-- One row per account and closing session: whether the policy traded, waited (and why) or found nothing.
CREATE TABLE stk_learner_days (
    session         INTEGER NOT NULL,          -- the decision session (ordinal)
    account         TEXT NOT NULL,             -- learner | learner_random
    decided_at      TEXT NOT NULL,
    policy_version  TEXT NOT NULL,             -- learner.policy.version_hash()
    model_id        TEXT,                      -- the frozen model scored with (NULL when none could be used)
    model_code      TEXT,                      -- learner.model.version_hash() of that model
    status          TEXT NOT NULL CHECK (status IN ('traded', 'waiting', 'nothing')),
    why             TEXT NOT NULL,             -- plain words
    detail_json     TEXT NOT NULL,             -- budgets, candidates scored, filters, timing
    source          TEXT NOT NULL,             -- where the candidates came from: 'forward' (that session's chains)
    PRIMARY KEY (session, account)
) WITHOUT ROWID;

-- Each trade the policy chose (and the alternatives it weighed), with the scores that chose it.
CREATE TABLE stk_learner_decisions (
    id                 INTEGER PRIMARY KEY,
    session            INTEGER NOT NULL,
    account            TEXT NOT NULL,
    decided_at         TEXT NOT NULL,
    policy_version     TEXT NOT NULL,
    model_id           TEXT,                   -- NULL for the random account
    symbol             TEXT NOT NULL,
    action_key         TEXT NOT NULL,          -- learner.actions key (structure|delta|width|bucket)
    mode               TEXT NOT NULL CHECK (mode IN ('exploit', 'explore', 'random')),
    exit_rule          TEXT NOT NULL,          -- learner.actions.EXIT_RULES id
    units              INTEGER NOT NULL CHECK (units >= 1),
    risk_usd           REAL NOT NULL CHECK (risk_usd > 0),   -- the most the order can lose, fees and limit included
    equity_usd         REAL NOT NULL CHECK (equity_usd > 0), -- the account's equity when decided
    regime             TEXT NOT NULL,          -- learner.regimes label of the session
    scores_json        TEXT NOT NULL,          -- the model's view, its uncertainty, the evidence count, the draw
    alternatives_json  TEXT NOT NULL,          -- the best other candidates weighed, with their scores
    reason             TEXT NOT NULL,
    paper_decision_id  INTEGER NOT NULL REFERENCES stk_decisions(id),
    source             TEXT NOT NULL           -- 'forward': that session's stored chains
);
CREATE INDEX stk_learner_decisions_session ON stk_learner_decisions(account, session);

CREATE TRIGGER stk_learner_days_no_update BEFORE UPDATE ON stk_learner_days BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER stk_learner_days_no_delete BEFORE DELETE ON stk_learner_days BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER stk_learner_decisions_no_update BEFORE UPDATE ON stk_learner_decisions BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER stk_learner_decisions_no_delete BEFORE DELETE ON stk_learner_decisions BEGIN SELECT RAISE(ABORT, 'append-only'); END;
