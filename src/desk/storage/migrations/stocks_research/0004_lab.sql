-- strategies the learning lab promoted to PAPER (desk.stocks.paper.lab): kept forever, so a retired one's account
-- still exits its open positions and its record stays readable
CREATE TABLE stk_lab_strategies (
    id             TEXT PRIMARY KEY,
    created_at     TEXT NOT NULL,
    spec_json      TEXT NOT NULL,
    spec_sha256    TEXT NOT NULL,
    evidence_json  TEXT NOT NULL,
    retired_at     TEXT
);
CREATE TRIGGER stk_lab_strategies_no_delete BEFORE DELETE ON stk_lab_strategies BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER stk_lab_strategies_frozen BEFORE UPDATE ON stk_lab_strategies
WHEN NEW.spec_json IS NOT OLD.spec_json OR NEW.spec_sha256 IS NOT OLD.spec_sha256 OR NEW.evidence_json IS NOT OLD.evidence_json
  OR NEW.created_at IS NOT OLD.created_at OR (OLD.retired_at IS NOT NULL AND NEW.retired_at IS NOT OLD.retired_at)
BEGIN SELECT RAISE(ABORT, 'a promoted strategy never changes'); END;
