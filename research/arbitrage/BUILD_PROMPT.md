# KESTREL Arbitrage — research and implementation prompt

Act as the principal investigator and lead engineer for KESTREL Arbitrage. Apply the breadth and skepticism of
an institutional team spanning quantitative finance, statistics, retail operations, supply chains, marketplace
selling and full-stack engineering. The ambition is the quality of 300 Harvard finance researchers and 50 MIT
engineers, not a claim that those people performed work. Convert that ambition into a small, verifiable system
that works on my actual hardware and data budget.

Build a separate KESTREL desk for finding currently purchasable retail, online, wholesale and liquidation goods
that may profitably resell through Amazon or Walmart. It should help me choose products, understand demand,
allocate available capital, track inventory and learn from actual net results. Do not promise income or mistake
an apparent discount for executable, profitable arbitrage.

## Existing project and boundaries

Repository: cahptions/cryptomfappdawg. Inspect the actual current branch, AGENTS.md if present, PROGRESS.md,
CONTINUATION_HANDOFF.md and the Stocks reliability handoff before designing anything. Inspect real data contracts,
worker controls, caches, app launchers and UI. Reuse KESTREL's strong dark visual identity, lime accents, charts,
research dossiers and clear evidence states. Treat Stocks, Robinhood Crypto, Solana and Arbitrage as separate desks.
Do not change their account balances, fees, workers, databases or trading behavior.

Hardware: Ryzen 7 7730U, 16 GiB shared RAM/integrated Radeon, Linux Mint 22.3, about 290 GiB free SSD, home Wi-Fi,
possible sleep/outages. No GPU, always-on server or domain is assumed. Initial additional data/API/cloud budget
is $0; no paid data, trials that become paid, scraping proxies or models without a later explicit decision.
Seller-platform subscriptions, fulfillment costs, inventory and working capital are separate expenses that must
be disclosed and configured, never assumed free. Do not assume the trading desks' $500 belongs to this business.
No LLM belongs in the operational ranking/calculation path. No automatic purchases, listings, spending, repricing
or seller-account changes; begin with research and read-only integrations.

## Establish what evidence is actually obtainable first

Explain FBA (Fulfillment by Amazon), FBM (seller fulfillment), WFS (Walmart Fulfillment Services), seller approvals,
relevant plan/account requirements and which costs or inventory movements each model introduces. I am a beginner;
give actionable setup inside the application, not unexplained acronyms or a dozen terminal commands.

Research current primary documentation for Amazon, Walmart and a practical initial set of sourcing retailers.
Provide citations, retrieval dates, access limitations and exact endpoint/data entitlements. Do not invent access,
approvals, free historical data or current fee schedules. Audit potential services such as Keepa and marketplace
APIs rather than assuming they are free or expose completed transactions. If browsing is unavailable, say so and
mark those facts unverified; do not represent remembered terms as current.

Create a feasibility matrix for price, offer, stock, product identity, actual completed sale price, actual units
sold, demand proxies, shipping, fees, category restrictions, returns and lead times. For each, specify lawful
source/access, licensing/account dependency, cost, coverage, refresh rate and fallback. Separate:

1. Observed asking/offer prices.
2. Confirmed completed transaction prices with quantity, date and provenance.
3. My own settled sales, cancellations and returns.
4. Inferred demand or sales-rank changes, explicitly identified as proxies.

Never label a rank drop as a sale, a listing price as a realized sale, or an estimate as confirmed volume. Public
Amazon/Walmart product pages do not establish complete historical transactions. Sales on another marketplace do
not automatically establish demand on the intended destination. If the required history is unavailable at $0,
identify the precise limitation and build useful observed-price tracking/own-sales ingestion without fabricating
the missing history. Obtain only data permitted for the integration; do not design CAPTCHA, login or block evasion.

## Product matching and availability are core financial controls

Match exact products using GTIN/UPC/EAN, ASIN, Walmart identifiers, brand, model, variation, pack count, size,
condition and region. Handle bundles, multipacks, refurbished goods, shipping terms and seller identity. A cheap
different variant is not a deal. Ambiguous mappings require review and cannot enter a purchase-ready ranking.

Store source URLs, first/last observed times, effective price, currency, destination ZIP/region, shipping,
quantity constraints, offer expiry, product identity evidence and availability evidence. Handle membership,
coupons, one-per-customer limits, minimum order sizes, backorders and estimated arrival. Uncertain cashback and
tax exemptions cannot silently reduce purchase cost.

Use explicit states: observed, verification pending, currently verified in stock, unavailable, stale, restricted
and unknown. Define source-specific freshness limits from measured price/stock changes and request budgets.
Recheck product, delivered price and availability before promoting an opportunity and again on click/inspection
when the permitted source allows it. Demote expired or unverifiable offers immediately. Display verification time.
An inability to check is UNKNOWN, never IN STOCK. Do not promise that remote inventory cannot change after a check.
Specify how false availability and price mismatches will be measured and reduced.

## Profitability, demand and capital allocation

Maintain versioned, dated fee schedules by platform, category, size/weight and fulfillment model. Calculate unit
economics with purchase price, nonrecoverable tax, shipping to me/prep center, prep/labels/packaging, inbound
fulfillment/shipment placement, referral and fulfillment fees, storage/aged inventory, outbound costs where
applicable, returns, damage, removals, markdowns, subscriptions and relevant overhead. Avoid double counting.
Missing material costs produce incomplete economics, not an apparently high confirmed profit.

Show gross revenue, contribution profit, fully loaded net estimates, ROI on landed cost, margin, break-even
sale price and downside scenarios. Analyze realizable price and sell-through after inbound lead time, not merely
today's highest listing or the historical maximum. Model competition, Featured Offer eligibility, Amazon/Walmart
as competing sellers, price compression and late arrival. Weight recent observations appropriately and preserve
seasonal/promotion context. Use uncertainty intervals only when supported; otherwise use transparent scenarios.

Identify mechanisms beyond a simple percent-off alert: exact-item relative mispricing, temporary clearance,
cross-channel price differences, replenishable stock and seasonal dislocations. Test whether complexity improves
actual net outcomes over simple discount/profit baselines. Reject unsupported claims of predictive skill.

Rank with conservative expected net profit, demand evidence, capital tied up, days to cash, uncertainty, source
reliability, restrictions, return risk and concentration. Do not let a large historical discount overpower negative
economics. Do not label an arbitrary score a probability. Provide a complete worked numerical example with
independently checked accounting and sensitivity to price decline, higher fees, delayed sales and returns.

Suggestions use an explicitly entered available-capital figure after cash reserves, outstanding commitments and
inventory already purchased. Account for settlement delays, inbound transit, unsold stock and return reserves.
Allocate integer units without leverage or double-spending. Constrain per-product, brand, category, retailer and
marketplace exposure; justify defaults as experimental controls. Show both a proposed basket and a defensible
KEEP CASH alternative. Profit alone cannot justify larger commitments. Multi-unit proposals need evidence of
quantity availability and sufficient plausible demand, not an extrapolation from one sale.

## Required application experience

Build these working pages with real adapters and honest data states:

- Home: observed market activity, verified opportunities, capital/inventory snapshot, freshness/source coverage,
  alerts, category trends and opportunities that changed since last review.
- Research: searchable exact-product dossiers with separate confirmed-sales and asking-price charts, volume or
  clearly labeled proxies, distributions, seasonality, competition, availability and provenance.
- Profitability: editable platform/fulfillment scenarios, itemized cost waterfalls, break-even and downside cases.
- Inventory: proposed, ordered, inbound, received, listed, sold, returned, damaged and liquidated quantities;
  auditable cost basis, age, cash committed and realized net proceeds. Imports must be idempotent.
- Suggestions: capital-constrained purchase baskets with reasons, uncertainty, quantity caps and a no-buy option.
- Top 100: up to 100 eligible current opportunities, plus top-10 category/niche views chosen from evidence.
  Never pad rankings, substitute stale deals, or pretend there are 100 opportunities when only 12 qualify.
- Sources/Settings: local private credentials, seller requirements, geography, capital, fee provenance, worker
  controls, current job, last successful observation and actionable errors.

Use dense, polished, responsive KESTREL styling. Show a clear mockup before extensive UI construction. Any mock
data must be prominently labeled illustrative and kept out of production evidence. Show usable observed listings
while verified arbitrage remains pending, with clear separation between those states. Load cached observations
immediately and refresh progressively: unrelated slow feeds must not hold the entire homepage hostage. No unexplained
blank dashboard. Opening this research desk should start/resume its authorized bounded scanning automatically,
respect a persistent Pause, and never start a different desk or purchase anything.

## Engineering, validation and delivery

Implement a bounded adapter architecture, exact-item registry, timestamped observations, availability verifier,
historical store, fee engine, deterministic ranking, capital allocator, inventory ledger and dashboard. Use separate
Arbitrage storage/configuration/locks/desktop entry. Share audited infrastructure where appropriate, with regression
tests for existing desks. Make migrations additive, writes atomic, retries/backoff finite, jobs resumable and controls
honest across restarts. Respect per-source limits; cap queue size, browser work, storage and polling. Use adaptive
refresh for high-value/frequently changing offers rather than opening hundreds of tabs. Explain what stops updating
when the laptop sleeps. Tailscale does not supply always-on compute.

Keep an experiment registry. Evaluate prospectively captured opportunities, including sold-out, restricted, false
matches and unprofitable items that were rejected. Compare unchanged simple baselines with proposed ranking and
allocation at matched capital. Account for selection bias, sparse/dependent observations, data leakage, changing fees
and unsold inventory. Report availability precision, matching errors, realized versus estimated sale price, net
profit after returns, sell-through time, capital turnover, drawdown and dead-stock losses. Paper opportunities,
historical simulations and actual completed sales are different evidence. No backfilled pretend purchases.

Fault-test changed pages, wrong product packs, stale coupons, unknown shipping/tax, 429/5xx, timeouts, malformed
feeds, credential expiry, partial backfills, SQLite busy/disk-full, sleep/resume, process death, duplicate starts,
refunds and duplicate sale imports. Verify that cancellation preserves completed data and that missing inputs cannot
manufacture profitable recommendations. Test financial logic against independent accounting identities. Run unit,
integration, browser, build/type/lint and measured resource checks. Keep a durable fault registry and continuation
handoff. Provide one bounded sanitized diagnostic command instead of repeated requests for terminal outputs.

Start with repository inspection and a current data-feasibility decision, then implement the smallest useful
vertical slice with real present-day observations, verified links, honest costs, a product dossier and capital-aware
recommendations. Expand only after that slice works. Do not stop at an architecture essay or replace unavailable
data with fabricated demonstrations. Work autonomously on authorized local implementation; ask only for essential
missing inputs or external account actions. Keep me informed while working.

Finish with the actual source/fee limitations, functional pages and workflows, test/resource evidence, remaining
seller/API dependencies, exact startup/update commands, observable acceptance criteria, and a short prioritized
continuation plan. Commit and publish tested changes when repository authorization permits. Never describe
software tests as evidence that the business will make large amounts of money.
