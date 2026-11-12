-- Phone alerts (desk.stocks.alerts; docs/STOCKS.md → Phone alerts): every alert sent, failed or tested, append-only.
-- ``key`` names the one event an alert is about (a sign-in, a tracked play's take-profit, a filing's accession...), so
-- the same event is never sent twice. ``text`` is the exact short message (it never holds a key, token or address);
-- ``channels`` names the channels that took it (ntfy, telegram), never their addresses.
CREATE TABLE stk_alerts (
    id        INTEGER PRIMARY KEY,
    at        TEXT NOT NULL,
    event     TEXT NOT NULL,
    key       TEXT NOT NULL,
    text      TEXT NOT NULL,
    channels  TEXT NOT NULL,
    outcome   TEXT NOT NULL CHECK (outcome IN ('sent', 'failed', 'test'))
);
CREATE INDEX stk_alerts_key ON stk_alerts(key, outcome);
CREATE INDEX stk_alerts_at ON stk_alerts(at);
CREATE TRIGGER stk_alerts_no_update BEFORE UPDATE ON stk_alerts BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER stk_alerts_no_delete BEFORE DELETE ON stk_alerts BEGIN SELECT RAISE(ABORT, 'append-only'); END;
