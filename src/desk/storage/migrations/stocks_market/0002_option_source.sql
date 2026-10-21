-- Preserve provenance; historical Tradier data's environment was not recorded.
ALTER TABLE stk_option_chains ADD COLUMN source TEXT NOT NULL DEFAULT 'tradier_legacy_unknown_environment';
