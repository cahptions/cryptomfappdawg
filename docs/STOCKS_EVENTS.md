# Notable events

The **Events** tab is a dated log of unusual market days: big index, sector and theme moves, big moves in the most
traded stocks, broad sell-offs and rallies, corrections and bear markets, long-term trend breaks, VIX spikes and sharp
moves in Treasury yields and credit spreads. Home shows the latest few, and each stock page lists its own entries
under **Notable moves**.

It is context for reading results, such as why a play or a PAPER account had a bad week. It is not a research input
and not a trade signal. Nothing in the log has been tested for predicting what happens next.

## How an event is found

Only the fixed rules in `src/desk/stocks/market_events.py` decide what is logged. Headlines and models never decide
it. Each session is judged only from data through its own close:

- **Usual move.** The standard deviation of the prior 63 sessions' daily returns. At least 40 returns are required.
- **Most traded.** Rank by median dollar volume over the 20 sessions **before** the day.

| Kind | Rule (version 1) |
|---|---|
| Index move | S&P 500, Nasdaq 100, Russell 2000 or Dow moves ≥ 1.5% **and** ≥ 2.5× its usual move |
| Sector or asset move | A sector SPDR, long Treasuries (TLT) or gold (GLD) moves ≥ 2% and ≥ 3× its usual move |
| Theme move | Semiconductors (SMH) or a theme basket moves ≥ 2.5% and ≥ 2.5× its usual move |
| Stock move | One of the 100 most traded US stocks moves ≥ 7% and ≥ 3.5× its own usual move |
| Multi-day move | Over 5 sessions, an index moves ≥ 4%, or SMH or a theme ≥ 7%, and ≥ 3× the usual 5-session move. Logged on the first day it qualifies, not again for 5 sessions |
| Broad day | ≥ 85% of actively traded stocks (above $5, over $20M a day) fall, or rise, together |
| Fall from a high | The S&P 500, Nasdaq 100, SMH or AI stocks first close 10% (a correction) or 20% (a bear market) below the 52-week high. Logged again when they return to a 52-week high after a fall of at least 10% |
| Trend break | The S&P 500 or Nasdaq 100 closes on the other side of its 200-day average after at least 20 sessions on one side |
| Volatility | VIX closes above 30 or 40, or jumps ≥ 30% in a day to 20 or more |
| Rates and credit | The 10-year Treasury yield moves ≥ 0.15 percentage points in a day, or the high-yield credit spread ≥ 0.25 |

**Severity.** Each event is *Notable*, *Major* or *Extreme*, set by size: a move of at least the "big" size, or twice
the minimum multiple, is Major. Twice the big size is Extreme.

**Stock-move notes.** A stock move notes when it is the stock's largest one-day move in stored prices. This needs at
least 250 earlier sessions, and the note names the date stored prices start. It also notes when the company filed
results that session (SEC 8-K item 2.02, using the same "first session that reflects the filing" rule as the
earnings desk).

### Theme baskets

Theme baskets are equal-weighted and rebalanced daily. A day counts when at least 60% of members traded.

- **AI stocks:** NVDA, AMD, AVGO, TSM, MU, ARM, MRVL, ASML, SMCI, DELL, ANET, VRT, ORCL, MSFT, GOOGL, META, AMZN,
  PLTR and CRWV.
- **Magnificent Seven:** AAPL, MSFT, GOOGL, AMZN, META, NVDA and TSLA.

The AI members were chosen in October 2026, so earlier history is measured with today's members. The card shows every
member's move, biggest movers first.

### Grouping on the page

On a broad day several indexes or sector funds can cross the same rule. The page shows them as one card, with the
rest under **Also**.

## Timing and the record

The PAPER worker runs the scan after each collection pass.

- **Price sessions.** A session is judged once it is stored and splits were refreshed after it, or two days later at
  the latest, so a split is never read as a crash.
- **Rates.** VIX, yields and credit spreads are judged once each series has an observation on or after the day,
  because FRED publishes the next morning. After seven days a day is judged anyway.
- **First run.** The first run goes back over all stored history; those rows say *Found in stored history*. Later
  sessions say *Logged* with the time.

Rows are written once per (rules fingerprint, day, kind, subject) and never rewritten. Changing a rule needs a new
`VERSION`. The new rules then build their own log from stored history, and the old rows stay in the database under
the old fingerprint.

## Headlines

For big moves in single stocks in the last 30 days, the worker fetches up to five Massive headlines published between
the previous close and the end of the event day. It fetches at most two per pass, inside Massive's shared free-tier
budget. Headlines are saved with the event for reading only. A failed fetch rests six hours before retrying.

## Where things stand

The Events tab opens with each watched index and theme's distance from its 52-week high at the newest stored close:
*at its high*, *near its high*, *pullback* (5% or more), *correction* (10% or more) or *bear market* (20% or more).
The meter marks the 10% and 20% lines.
