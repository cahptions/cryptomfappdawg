-- The finite transfer tests must not wait behind a large uncapped experiment queue.
CREATE INDEX stk_pending_account_state ON stk_pending(account,state,id);
CREATE INDEX stk_learning_outcomes_experiment_time ON stk_learning_outcomes(experiment_id,matured_at,block);
