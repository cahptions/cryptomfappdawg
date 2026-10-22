# Stocks activity and chart correction — 2026-10-22

New descriptive market/options lists use 65% options / 35% equity activity percentile weights, zero social/committee
inputs. These are correlated activity measures, heuristic priorities, not calibrated probabilities, directional
predictions or demonstrated profitable strategies. They rank only sampled qualified names, not the entire market.
Old social list remains available separately. Frozen old lists are retained; volume-ratio wording receives v2.

Quote gates: standard-contract provider inputs; DTE 2–90; same session; timestamps no later than observation +5s;
no older than 30 minutes relative to collection or regular close; positive bid, uncrossed midpoint and <=25% spread.
Aggregate >=4 qualifying contracts, >=100 volume, >=100 OI and >=$10,000 volume×midpoint×100 proxy.
The proxy is not actual premium traded; volume/OI is not relative historical volume. No inferred buying/opening,
smart money, or bullishness. Sampled expiries and bounded rotating coverage remain explicit limitations.

Option collection prioritization now rotates by underlying dollar liquidity rather than committee/social influence.
Per-symbol provider failures are retained while unaffected names continue. Pre-open collection uses last closed
session; provider quote timestamps are retained. Existing primary strategy/PAPER selection behavior is preserved;
these lists do not acquire trading influence. Optional observation cutoffs are exposed for research callers, but
existing historical option summaries are not newly certified point-in-time or forward evidence.

ETFs receive an on-demand read-only market/risk/options profile rather than inappropriate company fundamentals or
an invented committee score. Stored company scoring and PAPER universes remain separate.

Charts: pointer/touch inspection updates headline close, selected date and change since first observed point of
selected range. Leaving/canceling restores latest stored close and change versus previous observed close. Keyboard
left/right/Home/End inspect observations, Escape resets. Line/candles and range redraws reset correctly. No filled
candles or invented intraday prices.

Validation: new unit checks cover quote/session/dedup, social independence, premium ranking across >25 names, ETF
read-only profile. Stocks analysis + PAPER regression pass (16 tests before the final ETF test addition). Browser
regression was extended for headline scrubbing. Execution of its local fixture server was rejected because automatic
approval review could not run due to an account usage limit; this is not a safety determination. Browser execution
remains outstanding. Do not describe browser interaction as verified until that test runs.
