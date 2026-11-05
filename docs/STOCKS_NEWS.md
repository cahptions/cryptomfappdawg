# KESTREL Stocks — News, world events and policy

The **News and world** desk is the committee's twelfth member. It reads three kinds of news by fixed rules (no AI
reads or judges anything) and, like the Crowd and Options desks, starts as an **observer**: its score is shown on every
stock and graded on live data, and it gets a vote only if its record proves it predicts returns
(`desk.stocks.validation`, `LIVE_ONLY_DESKS`).

## What it reads

| Angle | Source | How often | What it gives a stock |
|---|---|---|---|
| Company news | Massive's market-wide news feed (your existing key): every article once, with the stocks it names and Massive's sentiment for each | every 30 minutes | the week's tone (positive minus negative articles) and attention (articles in 3 days vs its usual) |
| World events | GDELT's 15-minute event files: news in 65+ languages, machine-translated and coded (fighting, threats, sanctions, strikes and protests, seizures), by country, with the source articles | every pass (the last week back-filled first) | a supply shock when coverage of those events where a key supply comes from runs 1.8× its usual level |
| Policy | Federal Register presidential documents (executive orders, proclamations, memoranda) since January 20, 2025, tagged for trade actions, sanctions, sectors and countries | every 6 hours | trade actions in the last 15 days that name a product this company makes (helped) or buys (hurt) |

All three are free; only company news needs a key (Massive, already configured). Research reads only what was
received by its analysis time; the historical rebuild and replay read trade actions by their publication date (stored
from January 20, 2025) and leave company news and world shocks out of past days until those are backfilled.

## The supply map

`desk.stocks.world.NODES` ties a place to a supply and to the companies exposed to it, by name and by SIC code —
written down as hypotheses with their reasons: leading-edge chips (Taiwan), rare earths and chip minerals (China
export controls), heavy rare earths (Myanmar), cobalt (DR Congo, Zambia), copper (Chile, Peru), lithium (Chile,
Argentina, Bolivia, Zimbabwe), nickel (Indonesia, Philippines), chip-grade neon (Ukraine), palladium/uranium/
fertilizer/gas (Russia), oil through Hormuz, shipping through the Red Sea and Suez, Venezuelan heavy crude, uranium
(Kazakhstan, Niger), potash (Belarus), grain (Ukraine), bauxite (Guinea), and fighting worldwide (defence, gold).
A node needs five days of collected history before it can be flagged.

`desk.stocks.policy.TOPICS` maps trade actions to the US companies that make the product (helped) and those that buy
it (hurt): steel, aluminum, copper, lumber, cars and parts, heavy trucks, semiconductors, pharmaceuticals, robotics and
machinery, critical minerals, shipbuilding, wind turbines, furniture.

## Seeing it

- **Events → World watch**: every supply node with its coverage against its usual level, the most-covered source
  articles (often local, non-English outlets), presidential actions of the last 45 days with their tags, and **what
  the market did after trade actions**: SPY over the next 1, 5, 10 and 20 sessions after each action since January
  2025 (actions within three days counted once, measured from the signing date) against ordinary days, and each
  sector's named companies against SPY. This is the evidence for any policy-reaction strategy; it reports, it doesn't
  promise.
- **Stock page → Desk breakdown**: the News and world desk's score and reasons (tone, attention, any supply shock or
  trade action touching the company).

## Limits

The Federal Register publishes days after signing, and announcements often come earlier still (news conferences,
posts), so the study's dates are conservative. GDELT codes events automatically; individual codings can be wrong,
which is why the desk reacts only to large changes in coverage, not single events. Massive's sentiment is its own
model's label. The supply map is KESTREL's judgment, kept in code with its reasons so it can be reviewed and changed.
