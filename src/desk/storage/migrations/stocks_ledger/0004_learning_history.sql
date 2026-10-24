-- Learning scheduling and reporting must remain bounded as immutable evidence accumulates.
CREATE INDEX stk_learning_outcomes_time ON stk_learning_outcomes(matured_at,experiment_id,block);
CREATE INDEX stk_learning_intents_experiment ON stk_learning_intents(experiment_id,source_key);
CREATE INDEX stk_learning_fills_position ON stk_learning_fills(position_id);
CREATE INDEX stk_learning_nav_account ON stk_learning_nav(account,at);

CREATE TRIGGER stk_learning_experiments_retained BEFORE DELETE ON stk_learning_experiments
 BEGIN SELECT RAISE(ABORT,'prior hypotheses must be retained'); END;
CREATE TRIGGER stk_learning_outcomes_retained BEFORE DELETE ON stk_learning_outcomes
 BEGIN SELECT RAISE(ABORT,'unfavorable outcomes must be retained'); END;
CREATE TRIGGER stk_learning_sources_retained BEFORE DELETE ON stk_learning_sources
 BEGIN SELECT RAISE(ABORT,'decision-time observations must be retained'); END;
CREATE TRIGGER stk_learning_checks_retained BEFORE DELETE ON stk_learning_checks
 BEGIN SELECT RAISE(ABORT,'check-ins must be retained'); END;
CREATE TRIGGER stk_learning_fills_retained BEFORE DELETE ON stk_learning_fills
 BEGIN SELECT RAISE(ABORT,'execution history must be retained'); END;
CREATE TRIGGER stk_learning_intents_retained BEFORE DELETE ON stk_learning_intents
 BEGIN SELECT RAISE(ABORT,'order intents must be retained'); END;
CREATE TRIGGER stk_learning_checks_frozen BEFORE UPDATE ON stk_learning_checks
 BEGIN SELECT RAISE(ABORT,'check-ins are immutable'); END;
CREATE TRIGGER stk_learning_fills_frozen BEFORE UPDATE ON stk_learning_fills
 BEGIN SELECT RAISE(ABORT,'execution history is immutable'); END;
CREATE TRIGGER stk_learning_intents_frozen BEFORE UPDATE ON stk_learning_intents
 BEGIN SELECT RAISE(ABORT,'order intents are immutable'); END;
