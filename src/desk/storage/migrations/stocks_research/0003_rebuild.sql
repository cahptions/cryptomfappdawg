-- Point-in-time rebuilds: every desk's score and every rebuildable list as of a past month's end, computed from only
-- what was public then (desk.stocks.validation). A cache of a deterministic computation, keyed by the methodology's
-- hash: a new methodology gets new rows and the old ones stay. Not a track record (that is stk_list_snapshots).
CREATE TABLE stk_rebuild_months (
    asof           INTEGER NOT NULL,
    method_sha256  TEXT NOT NULL,
    built_at       TEXT NOT NULL,
    universe       INTEGER NOT NULL,
    scores_zlib    BLOB NOT NULL,      -- {"desks": [...], "rows": {symbol: [sector, score, confidence, ...]}}
    lists_json     TEXT NOT NULL,      -- {list_id: [symbol, ...]}
    PRIMARY KEY (method_sha256, asof)
) WITHOUT ROWID;

-- one row per list per day, including days a list was empty, so "recorded once" holds for empty lists too
CREATE TABLE stk_list_days (
    asof         INTEGER NOT NULL,
    list_id      TEXT NOT NULL,
    size         INTEGER NOT NULL,
    spec_sha256  TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    PRIMARY KEY (asof, list_id)
) WITHOUT ROWID;
INSERT INTO stk_list_days(asof, list_id, size, spec_sha256, created_at)
    SELECT asof, list_id, COUNT(*), MIN(spec_sha256), MIN(created_at) FROM stk_list_snapshots GROUP BY asof, list_id;
CREATE TRIGGER stk_list_days_no_update BEFORE UPDATE ON stk_list_days BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER stk_list_days_no_delete BEFORE DELETE ON stk_list_days BEGIN SELECT RAISE(ABORT, 'append-only'); END;
