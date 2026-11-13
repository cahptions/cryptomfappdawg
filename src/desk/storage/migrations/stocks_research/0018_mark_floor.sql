-- Accuracy (desk.stocks.accuracy, docs/STOCKS_PLAYS.md → Accuracy checks): a recorded play's loss stops at the money at
-- risk. A bought position whose bid was below the exit fee is left to expire (it costs nothing), and a sold structure
-- whose buy-back at the asks would cost more than its width is held to expiry instead (it loses at most its maximum
-- loss). desk.stocks.play_marks.profit values marks that way from 2026-11-13; this corrects the marks recorded before
-- it, which showed -101% to -120%. For each of them the corrected loss is exactly the money at risk (pnl / pnl_pct),
-- so nothing else about the mark changes: its value, status and day stay as recorded. Settlements were never affected.
DROP TRIGGER stk_play_marks_no_update;
UPDATE stk_play_marks SET pnl = -(pnl / pnl_pct), pnl_pct = -1.0
 WHERE pnl_pct < -1.0 AND pnl < 0 AND status != 'settled';
CREATE TRIGGER stk_play_marks_no_update BEFORE UPDATE ON stk_play_marks BEGIN SELECT RAISE(ABORT, 'append-only'); END;
UPDATE stk_play_marks_provisional SET pnl = -(pnl / pnl_pct), pnl_pct = -1.0 WHERE pnl_pct < -1.0 AND pnl < 0;
