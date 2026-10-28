-- All identified ideas are a discovery archive, separate from the immutable closing evaluation cohorts.
CREATE INDEX stk_play_ideas_sightings ON stk_play_ideas(session, category, legs_key, identified_at);
CREATE INDEX stk_play_ideas_identified ON stk_play_ideas(identified_at);
CREATE INDEX stk_play_ideas_expiration ON stk_play_ideas(json_extract(play_json, '$.expiration'));
CREATE TABLE stk_play_archive_skips (
    session INTEGER NOT NULL, category TEXT NOT NULL, key TEXT NOT NULL,
    reason TEXT NOT NULL, recorded_at TEXT NOT NULL, PRIMARY KEY(session,category,key)
);
CREATE TRIGGER stk_play_archive_skips_no_update BEFORE UPDATE ON stk_play_archive_skips
BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER stk_play_archive_skips_no_delete BEFORE DELETE ON stk_play_archive_skips
BEGIN SELECT RAISE(ABORT, 'append-only'); END;
