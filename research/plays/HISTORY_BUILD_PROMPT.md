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
how each play's value moved day by day, and how the whole day and each category did. The app must see the same
numbers and use them to learn and refine its picks, without ever rewriting what it showed.

## Build this

**1. Mark every recorded play every session until it closes.**
- After each session closes, value every unexpired recorded play from that session's own quotes: what it could be
  sold for now (bid for long legs, ask to buy back short legs — the honest "if I closed today" value) and the mid.
- Store each mark in a new append-only table (e.g. `stk_play_marks`, one row per play per session): the quotes
  used, quote time, source, underlying price, IV and greeks then, liquidation value, mid value, % return on the
  money at risk after fees, and whether the value was **quoted**, **modelled** (no fresh quote: Black-Scholes at
  the last IV, clearly flagged) or **settled** (expiration, intrinsic on the close).
- Stored chains are deleted after 45 days and only a rotating batch of names is collected, so marks must keep their
  own copy of the quotes. Add tracked plays to collection the way PAPER's `held_expirations` already is: every
  unexpired play's (underlying, expiration) gets re-quoted once each session after the close, within the Schwab
  budget (90 requests/min). Show the queue and its progress; never silently drop a play.
- Gaps (no quote, halted stock, holiday, laptop off) stay visible as gaps and are filled at the next session; do not
  invent prices. Handle splits/adjusted contracts, early assignment risk on short legs and delistings explicitly:
  quarantine anything that can't be valued honestly, with the reason shown.
- Also record when each play **first appeared** on the live board during the session and its ask/bid then, so I can
  see "if bought when it was first shown" next to "if bought at the close". Label the two clearly.

**2. A history view in the Plays tab.**
- Quick buttons: Today, Yesterday, 1 week, 1 month, 3 months, 1 year, and a date picker limited to recorded
  sessions.
- For the chosen session: every category's plays with entry price, today's value, % gain/loss (green/red), peak
  and worst % so far, days held, status (open / closed in the money / expired worthless / won / lost) and a small
  value-path sparkline.
- Click a play: a detail panel with its full value path chart (entry, every daily mark, peak, low, expiry), its
  greeks and IV then vs now, the reasons and chances it was picked with, and what actually happened.
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
- Laptop budget: Ryzen 7 7730U, 16 GB RAM. Measure the marking pass time and storage growth for a year of plays.
- Append-only SQLite with triggers; numbered migrations; point-in-time correctness tests (a later quote or outcome
  must not change an earlier mark or pick).
- Bump the Stocks UI protocol with the server if the page needs new endpoints.
- Tests: unit tests for valuation of every structure (long call/put, straddle, credit spreads, expiry, missing quotes,
  splits), integration tests on the synthetic market in `tests/stocks_world.py` across several sessions, and a
  browser check at 1366 px and 375 px (`tests/browser/`). Run ruff, strict mypy, Prettier and the full suite.
- Update `docs/STOCKS_PLAYS.md` and `PROGRESS.md`; commit and push in steps (marks → collection → history view →
  performance view → learning). Finish by telling me exactly what to run on the laptop, what I'll see tomorrow and
  after a week, and what is still unproven.
