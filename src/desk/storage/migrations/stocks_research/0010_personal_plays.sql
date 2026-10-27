-- Explicit personal PAPER tracking is separate from the automatic Plays record and its learning cohorts.
CREATE TABLE stk_play_ideas (
    id TEXT PRIMARY KEY, identified_at TEXT NOT NULL, session INTEGER NOT NULL,
    category TEXT NOT NULL, legs_key TEXT NOT NULL, play_json TEXT NOT NULL, method TEXT NOT NULL
);
CREATE TABLE stk_play_selections (
    id TEXT PRIMARY KEY, request_id TEXT UNIQUE NOT NULL, idea_id TEXT NOT NULL REFERENCES stk_play_ideas(id),
    confirmed_at TEXT NOT NULL, eligible_at TEXT NOT NULL, quantity INTEGER NOT NULL CHECK(quantity > 0)
);
CREATE TABLE stk_play_selection_events (
    id INTEGER PRIMARY KEY, selection_id TEXT NOT NULL REFERENCES stk_play_selections(id),
    kind TEXT NOT NULL CHECK(kind IN ('ENTRY','CLOSE','CANCEL','CLOSE_REQUEST','EXPIRED_NO_ENTRY')),
    at TEXT NOT NULL, detail_json TEXT NOT NULL, UNIQUE(selection_id, kind)
);
CREATE INDEX stk_play_selections_idea ON stk_play_selections(idea_id);
CREATE INDEX stk_play_selection_events_ticket ON stk_play_selection_events(selection_id, kind);
CREATE TRIGGER stk_play_ideas_no_update BEFORE UPDATE ON stk_play_ideas BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER stk_play_ideas_no_delete BEFORE DELETE ON stk_play_ideas BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER stk_play_selections_no_update BEFORE UPDATE ON stk_play_selections BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER stk_play_selections_no_delete BEFORE DELETE ON stk_play_selections BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER stk_play_selection_events_no_update BEFORE UPDATE ON stk_play_selection_events BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER stk_play_selection_events_no_delete BEFORE DELETE ON stk_play_selection_events BEGIN SELECT RAISE(ABORT, 'append-only'); END;
