-- the latest mark of each leg (price, implied volatility, whether it was modelled): updated daily while open, so the
-- frozen entry legs never change
ALTER TABLE stk_positions ADD COLUMN marks_json TEXT NOT NULL DEFAULT '{}';
