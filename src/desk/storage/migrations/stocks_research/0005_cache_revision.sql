-- A historical date can gain previously uncollected filings. Mutable caches must track their source revision.
ALTER TABLE stk_fund_cache ADD COLUMN input_hash TEXT;
