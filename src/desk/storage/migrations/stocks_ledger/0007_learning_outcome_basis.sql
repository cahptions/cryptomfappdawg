-- How each Learning outcome was realized (docs/STOCKS_LEARNING.md): 'sold at a quote' (a later executable bid less
-- impact and fees) or 'settled at intrinsic' (an expired position valued on its expiry day's official close). Every
-- earlier outcome was sold at a quote.
ALTER TABLE stk_learning_outcomes ADD COLUMN basis TEXT NOT NULL DEFAULT 'sold at a quote';
