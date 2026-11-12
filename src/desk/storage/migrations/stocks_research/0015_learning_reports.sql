-- The Learning report's weekly snapshots (desk.stocks.learning_report.weekly; docs/STOCKS_LEARNING.md → Learning
-- report): one per New York ISO week, written by the collection worker after Friday's close, so "Changed since last
-- week" compares with what the report said then. Append-only.
CREATE TABLE stk_learning_reports (
    week         TEXT PRIMARY KEY,          -- e.g. 2026-W46
    created_at   TEXT NOT NULL,
    report_json  TEXT NOT NULL
) WITHOUT ROWID;
CREATE TRIGGER stk_learning_reports_no_update BEFORE UPDATE ON stk_learning_reports
BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER stk_learning_reports_no_delete BEFORE DELETE ON stk_learning_reports
BEGIN SELECT RAISE(ABORT, 'append-only'); END;
