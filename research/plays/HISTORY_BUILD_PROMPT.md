# Build prompt — Plays history: track every pick, show its gains, learn from it

Copy everything below the line into a new coding session.

---

You are continuing KESTREL, a personal research desk in the repo `cahptions/cryptomfappdawg`. Start from branch
`claude/funny-rubin-gq99vj` (it contains everything through the **Plays** tab). Read first: `PROGRESS.md` (top entry),
`docs/STOCKS_PLAYS.md`, `src/desk/stocks/plays.py`, `src/desk/stocks/odds.py`, `src/desk/stocks/runner.py`
(`publish_plays`, `cycle`), `src/desk/stocks/collect.py` (`option_chains`, `held=`), `src/desk/stocks/paper/book.py`
(`held_expirations`), the migration `stocks_research/0006_plays.sql`, `docs/STOCKS_PRODUCT_REVIEW.md` and
`research/reliability/HANDOFF.md`. Check the current state yourself before changing anything; others may have worked
on the repo since this was written.

## What I want

Plays already records each closed session's picks once (`stk_plays`) and grades them once at expiration
(`stk_play_outcomes`). I want to **watch every pick live after it was made**: tomorrow I open Plays, click
"Yesterday" (or a week ago, a month ago, a year ago, or any date) and see exactly what that day's plays are doing
now — for example "AAL $12 call, Lottos, picked Oct 27 at $0.18 → $0.31 today, +72%" — with green/red percentages,
how each play's value moved, and how the whole day and each category did. Prices should update live through
Schwab, so a week back is an accurate picture, and every play gets a P/L chart from the day it was suggested (as if
I had bought it then) until it expires, or until today for long-dated contracts. The app must see the same numbers
and use them to learn and refine its picks, without ever rewriting what it showed.

## Build this

**1. Track every recorded play live through Schwab until it closes.**
- The tracked set is every leg of every unexpired recorded play, plus the plays on today's live board. It does not
  depend on the research collector's rotating batch: every tracked contract gets a price, every time.
- During market hours, update tracked contracts live from Schwab: prefer the streaming API's level-one option quotes
  (schwab-py documents `level_one_option_subs` with BID_PRICE, ASK_PRICE, LAST_PRICE, MARK, TOTAL_VOLUME,
  OPEN_INTEREST, VOLATILITY, DELTA, GAMMA, THETA, VEGA, UNDERLYING_PRICE). Fall back to batched REST quotes for
  option symbols, then to the per-expiry chain requests the collector already makes. Verify which of these the
  operator's approved Schwab app actually allows on the laptop (`desk stocks doctor --online`) before relying on
  it; the current parser fixtures are synthetic. Convert between Schwab's padded option symbols and the stored OCC
  symbols with the identity checks `sources/schwab.py` already does. Stay within the request budget (90 requests a
  minute today), share it fairly with research collection, reconnect streams after drops and token refresh, and show
  in System what is being tracked, how fresh each quote is, and anything waiting.
- Keep Schwab's own greeks and IV for tracked contracts alongside the ones KESTREL computes, so the two can be
  compared.
- Store prices in new append-only tables, independent of the chain archive (chains are deleted after 45 days):
  - **Intraday**: a mark at least every 5 minutes per tracked play (bid, ask, mark/mid per leg, stock price, IV,
    greeks, quote time, source), kept in full for at least 30 days so a week back is an accurate intraday view.
  - **Daily close**: one official mark per play per session after the close, kept forever: liquidation value (bid
    for long legs, ask to buy back short legs: the honest "if I closed now" value), mid value, % return on the money
    at risk after fees, and whether it was **quoted**, **modelled** (no fresh quote: Black-Scholes at the last IV,
    clearly flagged) or **settled** (expiration: intrinsic on the close).
  - After 30 days, intraday detail may be compacted to a daily high/low/close of the play's value. Never delete
    or change a daily close mark.
- **Repair gaps automatically when the app opens** (laptop off or asleep, dropped stream, missed session). Schwab
  keeps price history for stocks and ETFs (daily for years, minute bars for recent days) but, per schwab-py's
  documentation, not for option contracts — verify on the laptop. So, for each gap:
  - backfill the **stock's** real path from Schwab price history (minute bars where still available, daily beyond);
  - grade expirations that passed during the gap **exactly**, from the stock's close on the expiry day;
  - fill the **option** marks inside the gap as **estimated**: Black-Scholes on the real stock path, with IV
    interpolated between the last quote before the gap and the first quote after it. Store them as estimated and
    never as quotes; earnings and other events inside a gap make the estimate unreliable, so flag them.
  - Draw quoted marks as a solid line and estimated marks as a dashed line, and say so in the legend. Grades,
    calibration and learning use quoted marks and exact settlements only, never estimates.
  - Show a "repairing N plays" progress note in System and on the history view while this runs, within the request
    budget.
- Holidays and halts are not gaps to fill. Handle splits/adjusted contracts, early assignment risk on short legs
  and delistings explicitly: quarantine anything that can't be valued honestly, with the reason shown.
- Record when each play **first appeared** on the live board and its bid/ask then, so I can see "if bought when it
  was first shown" next to "if bought at the close". Label the two clearly.

**2. A history view in the Plays tab, with a P/L chart for every play.**
- Quick buttons: Today, Yesterday, 1 week, 1 month, 3 months, 1 year, and a date picker limited to recorded
  sessions.
- For the chosen session: every category's plays with entry price, latest value, % gain/loss (green/red), peak
  and worst % so far, days held, status (open / closed in the money / expired worthless / won / lost) and a small
  P/L sparkline.
- Click a play: a **P/L chart** from the moment it was suggested — as if bought then (at the ask when first shown,
  and at the close), in % and in dollars per contract — through to expiration, or through today if it hasn't expired
  yet (LEAPS a year or two out keep extending day by day). A 30-day contract picked a year ago shows its whole life
  ending at expiry with the settled result; nothing is drawn past expiry. Use intraday points where they are stored
  and daily closes beyond that, with range buttons (1D, 1W, 1M, All), a crosshair readout (time, value, P/L %,
  stock price), markers for entry, peak, low and expiry, and estimated stretches drawn dashed. Beside it: the greeks
  and IV then vs now (computed and Schwab's), the reasons and chances it was picked with, and what actually
  happened.
- Session summary: average and median % per category, hit rate, best and worst play, and "if you had put $100 into
  each play" totals.
- A **Performance** view across all sessions: by category, by holding period (1 day, 1 week, 1 month, expiry), by
  days-to-expiry and price buckets, KESTREL's predicted chance vs what actually happened (a calibration chart), and a
  cumulative "$100 into every play" curve per category against simple baselines (random contracts with the same
  expiry/delta, and SPY).
- Desktop and phone, accessible, matching the existing dark Robinhood-style look. Data loads from stored records
  only — no heavy computation on a page request.

**3. Learning and refining, without fooling itself.**
- Use marks and outcomes to: calibrate chances per category and days-to-expiry; learn which exit rule each category
  should use (take profit at +X%, stop at −Y%, time exit), chosen walk-forward on sessions it never saw; and find
  which inputs actually predict a play's return.
- Changes to the rules are **challengers**: run them in shadow alongside the current rules, record their picks
  separately with their own version hash, and promote one only when it beats the current version on later sessions,
  with a stationary block bootstrap by session (plays on nearby days are not independent) and Holm correction across
  everything tested. Keep failed challengers and their results visible.
- Every recorded play keeps the method version it was picked under; never mix versions in one grade, never
  edit past records, and never let later data change an earlier mark.
- Categories that keep failing are labelled as failing, not hidden. Categories that prove themselves can become
  PAPER strategies through the existing PAPER engine.

## Rules

- Research and PAPER only: no brokerage orders, no LIVE changes, no paid APIs, no AI/LLM judging the trades.
- Keys stay in `~/.config/desk/secrets.env`; nothing secret in the repo, logs or test fixtures.
- Laptop budget: Ryzen 7 7730U, 16 GB RAM. Measure the live-tracking request rate, CPU and storage growth for a year
  of plays (intraday and daily), and show storage use in System.
- Append-only SQLite with triggers; numbered migrations; point-in-time correctness tests (a later quote or outcome
  must not change an earlier mark or pick).
- Bump the Stocks UI protocol with the server if the page needs new endpoints.
- Tests: unit tests for valuation of every structure (long call/put, straddle, credit spreads, expiry, missing quotes,
  splits), gap repair (a laptop-off gap is backfilled from stock history, estimated marks are flagged and excluded
  from learning, an expiry inside the gap is graded exactly), a fake Schwab stream and quote endpoint (drops,
  reconnects, token refresh, budget), integration tests
  on the synthetic market in `tests/stocks_world.py` across several sessions, and a
  browser check at 1366 px and 375 px (`tests/browser/`). Run ruff, strict mypy, Prettier and the full suite.
- Update `docs/STOCKS_PLAYS.md` and `PROGRESS.md`; commit and push in steps (live tracking → marks → history and
  P/L charts → performance view → learning). Finish by telling me exactly what to run on the laptop, what I'll see
  tomorrow and after a week, and what is still unproven.
