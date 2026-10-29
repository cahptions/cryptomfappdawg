-- Preserve source-specific list facts alongside the immutable daily ranking.
ALTER TABLE stk_list_snapshots ADD COLUMN detail_json TEXT NOT NULL DEFAULT '{}';
