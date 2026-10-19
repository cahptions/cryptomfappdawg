-- Provider-reported pool reserve amounts (token units, as reported). DexScreener provides these; they are the
-- direct evidence of a drained pool (e.g. liquidity.usd = 0 with quote reserve ~0.00002 SOL, observed 2026-10-19).
-- NULL = not reported by the provider (never "zero").

ALTER TABLE market_snapshots ADD COLUMN liquidity_base_amount TEXT;
ALTER TABLE market_snapshots ADD COLUMN liquidity_quote_amount TEXT;
