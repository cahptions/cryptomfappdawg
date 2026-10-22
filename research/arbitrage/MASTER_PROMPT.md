# KESTREL Arbitrage — consolidated research and master build prompt

This supersedes `research/arbitrage/BUILD_PROMPT.md` as the prompt for the proposed Arbitrage desk.

Act as the principal investigator, marketplace-operations researcher, quantitative analyst and lead full-stack
engineer for KESTREL Arbitrage. Apply the breadth and rigor of 300 Harvard finance researchers and 50 MIT engineers
without claiming those people performed work. Turn that ambition into an evidence-driven application that works
on my actual laptop and budget. Evaluate your own proposals skeptically; complexity must earn its place.

## 1. Mission and scope

Build a separate KESTREL desk that discovers currently purchasable retail, online, wholesale and liquidation
products with defensible resale potential. My primary destination marketplaces are **Amazon and Walmart**,
including FBA, FBM, WFS and seller fulfillment where suitable. Explain those terms and operational requirements
in beginner-friendly language inside the app.

Design channel adapters so eBay, Mercari, Poshmark, StockX, GOAT, Whatnot and direct-to-consumer channels can be
evaluated later. Add a channel only when useful data, account eligibility and maintainable integration exist.
Do not replace Amazon/Walmart with other marketplaces merely because their websites appear easier to inspect.
No platform should be assumed universally unrestricted, freely accessible or suitable for every product.

The app should help identify products worth buying, determine where and how to sell them, allocate explicitly
available capital, track inventory and learn from actual realized net results. An attractive discount is a lead,
not proof of demand, availability, seller eligibility or future profit. No income guarantee.
The decision unit is a sourcing offer → selling destination → fulfillment method → quantity → timing combination,
not merely a product with a discount. Identifying the best supported place to sell is a core feature.

## 2. Inspect KESTREL and respect operator constraints

Repository: `cahptions/cryptomfappdawg`. Inspect the actual current branch, applicable AGENTS.md, PROGRESS.md,
CONTINUATION_HANDOFF.md and `research/reliability/HANDOFF.md`. Read relevant implementation and tests before choosing
architecture. Document existing interfaces worth reusing and problems the new desk must avoid.

Keep Stocks, Robinhood Crypto, Solana and Arbitrage separate in storage, locks, accounts, secrets, settings and
automation. Do not alter existing trading behavior or treat another desk's simulated money as business capital.

Hardware: Ryzen 7 7730U; 16 GiB RAM shared with integrated Radeon graphics; Linux Mint 22.3; about 290 GiB free SSD;
home Wi-Fi; possible sleep, outages and forced shutdowns. Initial additional data/API/cloud budget: **$0**.
Do not assume paid models, data subscriptions, proxies, domains, trials that become paid or remote servers.
Seller plans, fulfillment services and inventory expenses are separate business costs; disclose them explicitly.

Use deterministic local calculations. No LLM in operational ranking or allocation. No GPU requirement. Tailscale
provides connectivity, not always-on compute. Explain exactly what becomes stale when the laptop is asleep.

## 3. Data feasibility before architectural promises

Research current primary documentation for Amazon, Walmart and a practical initial set of source retailers.
Evaluate other channels/data vendors where they add useful evidence. Cite source titles, accessible links, access
dates, exact capabilities, costs, licensing and seller/account entitlements. If access or browsing is unavailable,
state that limitation and mark the conclusion unverified. Do not present remembered fee examples as current facts.

Produce a source-capability matrix for identifiers, current delivered price, availability/quantity, historical
offers, actual completed sales, actual units sold, demand proxies, competition, fees, restrictions and payout timing.
For each field specify provenance, permissions, cost, geographic coverage, update delay, rate limits and fallback.
Investigate Keepa and marketplace APIs without assuming they are free or provide confirmed transaction histories.
Blocked sources must become explicit coverage limitations; do not design CAPTCHA, login or access-control evasion.

Preserve separate evidence classes:

- Observed asking prices, offers, MSRP and reference/strikethrough prices.
- Confirmed completed transaction price/quantity, including what was actually observable about final proceeds.
- My own orders, settled sales, refunds, cancellations, returns and payouts.
- Estimated demand, rank movements, indicative bids or other explicitly labeled proxies.

Never convert rank drops into confirmed sales or prices displayed on sold pages into confirmed accepted-offer
prices. Hidden accepted offers, unknown quantities, canceled transactions and immature returns remain uncertain.
Deduplicate repeated listings/transactions. Exclude incompatible variants, conditions, bundles and markets from
comparable-sale statistics. Another channel's sales are separate evidence, not Amazon/Walmart demand by default.

If complete historical prices or unit sales cannot be obtained at $0, identify precisely what is unavailable.
Implement useful current observations, prospective price history and permitted own-sales imports without inventing
the missing data. Research-only operation must remain useful while seller approvals or premium history are absent.
Never imply that a fully confirmed-sales product exists when only a price watcher has been implemented.

## 4. Product identity, sellability and geography

Create an exact-product registry using GTIN/UPC/EAN, ASIN, Walmart identifiers, brand, model, variant, size, color,
pack count, condition, warranty and region. Distinguish product, offer, seller, inventory lot and channel listing.
Multiple marketplace matches are candidates until identity is resolved; preserve evidence and review history.
An ambiguous match cannot enter a purchase-ready basket.

Determine actual eligibility for this seller, product, condition and fulfillment method where an authorized source
supports it. Track brand/category approval, required sourcing documentation, authenticity requirements, expiration
and fulfillment limitations where relevant. A retail receipt must not silently be assumed sufficient documentation.
Unknown sellability is a visible blocker; a profitable spreadsheet is not authorization to sell the item.

Use configured sourcing/delivery geography. Include taxes, currency/FX, destination shipping, local pickup,
membership requirements, order minima, quantity limits, coupons and expiration. International sourcing/resale
requires explicit additional costs and availability support; do not silently mix regions.

## 5. Availability verification and ghost-deal prevention

Store price and availability observations independently: exact offer/variant, source URL, seller, delivery region,
effective delivered cost, available quantity if observable, promotion conditions, source event time, retrieval time,
verification time, evidence method and expiry. A recent page scrape is not automatically recent stock verification.

Use states such as OBSERVED, VERIFICATION_PENDING, VERIFIED_AVAILABLE, UNAVAILABLE, STALE, UNKNOWN and RESTRICTED.
Define which evidence methods qualify for each state. Prefer permitted product/inventory/offer APIs, documented
feeds and lightweight requests. Verify matching variant, delivery eligibility, price and stock before promotion;
reverify on inspection/click when source access permits. Remove stale/unavailable offers from actionable rankings.

Investigate cart-level verification as a stronger evidence tier, but do not implement silent checkout. Adding to
carts, changing authenticated sessions or reserving inventory is a separate opt-in capability requiring explicit
authorization and source support. Never submit an order, payment or shipping information to verify stock. Where
safe read-only verification is unavailable, show the limitation instead of pretending a cart check occurred.

Freshness limits must be source- and opportunity-specific, justified by observed changes and request budgets.
Do not require every source to refresh within an arbitrary 180 seconds. Prioritize expiring/high-value offers;
back off unreliable sources. Unknown stock quantity does not support a large multi-unit recommendation.
Display the last verification and remaining uncertainty. Inventory can change after a valid check; measure false
availability, click-through mismatches and verification coverage rather than promising zero sold-out links.

## 6. Price history, demand, charts and comparable sales

Build separate chart series for asking prices, verified transaction prices, my own net realized sales and demand
proxies. Provide source, coverage, sample count, timestamps, exclusions and observation gaps. Never manufacture
missing history or connect gaps as continuous observations. Support practical short/medium/long windows when data exist.

Estimate a plausible selling-price range for the time inventory will actually be ready to sell. Use exact-item
comparables, condition, geography, shipping, recency, seasonality and promotion context. Show medians/quantiles and
uncertainty where justified. Flag outliers and reference-price manipulation without erasing valid inconvenient sales.

Track permitted competition evidence: seller/offer counts, price dispersion, Amazon/Walmart competing directly,
Featured Offer eligibility and price compression after widespread clearance. Asking prices, bids and completed
transactions are different signals. Analyze arrival/inbound delays rather than assuming immediate resale today.

Define every demand metric mathematically. Sold listings over a window divided by current active listings is a
marketplace supply-demand proxy, not a calibrated probability or cohort sell-through rate. It may exceed 100% and
mix incompatible observation periods. Distinguish listings from units and handle multi-quantity listings.
For my own inventory, define cohort sell-through/time-to-sale consistently and keep unsold items as censored
observations. Absence from a marketplace page does not prove a sale. Missing demand is not zero demand.

## 7. Full channel-specific unit economics

Maintain dated, versioned fee rules by platform, country, category, seller plan/tier, fulfillment method,
dimensions/weight and effective period. Validate against official examples or authorized fee-estimation APIs.
Do not hard-code the other prompt's eBay, Mercari, Poshmark or authentication-market fee examples as current truth.

Include purchase price, nonrecoverable tax, sourcing/inbound freight, prep/labels/packaging, shipment placement,
referral/payment/transaction fees, fulfillment/outbound shipping, storage/aged inventory, optional ads, returns,
damage, authentication/rejection, removals, liquidation, subscriptions and relevant overhead. Apply each rule to
its correct fee base; include buyer-paid shipping and marketplace-collected tax correctly. Avoid double counting.
Uncertain coupons, cashback, tax exemptions or rebates must not silently improve the base case.

Show itemized acquisition cash, landed unit cost, contribution profit, fully loaded net estimates, ROI on landed
cost, net margin, break-even selling price and minimum price for an operator-selected profit hurdle. Handle zero,
missing, negative and nonfinite inputs explicitly. Compare channel/fulfillment economics side by side for the
same matched product. Alternative destinations for one unit are mutually exclusive, not additive profits.
Use decimal or integer minor-unit money arithmetic with explicit currency, rounding, allocation and FX rules.

Model retained-sale, returned, delayed, unsold and liquidation outcomes transparently. Separate profit from cash
flow and inventory value: buying inventory commits cash; unsold stock is not realized profit. Reconcile refunds,
fees, cost of goods, remaining inventory and payouts. Use scenario ranges before sufficient calibrated outcomes
exist. Missing material costs block claims of confirmed positive economics.

Provide independently checked numerical examples, including price compression, fee changes, delayed sales,
authentication delays/rejection and returns. Never represent modeled profit as a guaranteed collectible amount.

### Selling-destination engine — where to sell for maximum supported profitability

For every matched item, evaluate all supported, eligible destination/fulfillment combinations: Amazon FBA/FBM,
Walmart WFS/seller fulfillment, and approved optional channels when evidence exists. Include relevant local or
direct-to-consumer alternatives only when buyer demand, delivery and customer-acquisition costs can be supported;
an empty Shopify store is not automatically a zero-fee route to an existing buyer.

Return an explicit recommendation: **buy from this source, sell through this destination, use this fulfillment
method, consider this quantity**, with matched destination identifier/link, target-price range, break-even,
conservative/base/stress net profit, demand evidence, time to readiness/sale/payout, restrictions, freshness and
the reason it beats the runner-up. Show the full eligible comparison matrix and why other routes were excluded.
The highest asking price or lowest commission does not automatically identify the most profitable destination.

Optimize primarily for conservatively estimated total net profit within the operator's capital, risk and planning
horizon constraints. Also show highest net ROI, fastest likely cash recovery and best profit per capital-day so
the tradeoffs are visible. Do not collapse these different objectives into an unexplained score. Unknown material
inputs cannot win through favorable defaults. If no destination has supported positive economics, recommend no buy.

Distinguish an immediately executable buyer bid from a speculative future listing price. Include bid expiry,
authentication, inbound transit, existing inventory location and rerouting costs. "Best" means best among the
covered eligible routes under displayed assumptions, not a proven global maximum. Re-evaluate routes when fees,
prices, eligibility or availability change. Never create listings or move inventory to execute a recommendation.

## 8. Discovery and evidence-based ranking

Investigate exact-item mispricing, temporary clearance, cross-channel differences, replenishable stock, bulk
discounts and seasonal dislocations. Specify each mechanism, necessary data, failure modes and what would disprove
its usefulness. A large MSRP discount or historical high is not itself an edge.

Start with transparent baselines. Rank with conservative net economics, demand evidence, sellability, verified
availability, capital commitment, days to cash, competition, uncertainty, source reliability and concentration.
Use eligibility gates before ranking. Unknown factors must not be silently assigned favorable defaults.

Do not multiply ROI × an ambiguous sell-through ratio × a stock score and call the result a probability. Explain
units, scales, weights, sensitivity and shared dependencies. More complex models require prospective improvement
over simple conservative-profit/turnover rules. No high-probability-profit label without independent calibration.

## 9. Capital deployment and cash-cycle planning

Use a separately entered business-capital figure after reserves, existing commitments and inventory purchases.
Account for inbound transit, authentication, fulfillment receiving, time to sale, settlement delays, withholding
and return reserves. Track dollar-days of capital committed as well as ROI; show slower payout costs explicitly.

Use a bounded integer knapsack or suitable optimization approach for item quantities, compared with a simple
greedy baseline. Define objective, constraints, solver limits and deterministic fallback; an optimizer cannot fix
unreliable inputs. Constrain total cash, quantity availability, plausible demand and exposure by item, brand,
category, retailer and destination. Account for correlated clearance floods and concentration.
Optimize item quantities and destination assignments jointly. One physical unit can be assigned only once;
multiple channel alternatives cannot each consume the same inventory or contribute duplicate hypothetical profit.

Provide conservative/base/stress baskets and a KEEP CASH alternative. Do not force deployment to satisfy a target
ROI, double-allocate cash or buy more units merely because previous purchases made money. Explain the marginal
cost/benefit and evidence for each proposed additional unit. No leverage by default.

Add a scenario-based cash-flow and reinvestment planner with editable lead times, demand, returns, markdowns and
payouts. Show paths that lose money or leave stock unsold. Do not extrapolate a constant monthly ROI into a promised
compounding income curve or multiply duplicated channel opportunities into headline profit potential.

## 10. Inventory, reservations and future channel synchronization

Use an auditable lot-based inventory/event ledger: proposed, ordered, inbound, received, inspected, available,
reserved, listed, sold, returned, damaged, removed and liquidated. Reconcile physical units, local reservations,
channel commitments, unit costs and actual cash. Do not count one sale twice or release returned units before inspection.

Design local atomic reservations, idempotent event ingestion, duplicate/out-of-order webhook handling, reconciliation,
and an auditable outbound action queue. Evaluate each channel's actual webhook/order/listing permissions.

Initial integrations are read-only/manual. Future cross-listing, quantity changes or delisting need separate explicit
authorization. Do not assume private or unsupported APIs exist. Where later authorized, sold-unit events should
trigger quantity reconciliation and bounded updates, with retries, alerts and visible synchronization uncertainty.
A local transaction cannot guarantee atomic inventory changes across independent marketplaces; do not promise
instant delisting or zero overselling. Measure latency and use conservative per-channel allocations when appropriate.

## 11. Required pages and interaction quality

- **Home:** current observations, verified opportunities, source coverage, changes since review, category activity,
  capital/inventory snapshot and a feasible basket's estimated outcomes without duplicate counting.
- **Research:** exact-product dossiers, identity evidence, source offers, availability, comparable sales, separate
  chart series, demand/proxies, competition, sellability, seasonality and provenance.
- **Profitability:** editable channel/fulfillment comparison matrix, itemized costs, break-even, profit hurdle,
  cost waterfalls, missing inputs and downside scenarios; a prominent **Where to sell** recommendation with the
  best supported net-profit route, runner-up and explanations.
- **Inventory:** lot history, physical/committed/reserved quantities, aging, cost basis, realized results, returns,
  payout reconciliation and any synchronization exceptions.
- **Suggestions:** capital-constrained baskets, recommended quantities, constraints, economic rationale,
  uncertainty, concentration, alternative destinations and a no-buy option.
- **Top 100 / category top 10:** up to the number of genuinely eligible current opportunities. Never pad results,
  fabricate a live feed or retain expired deals. Explain missing coverage separately from a genuinely empty result.
- **Planning:** evidence-based seasonality/watch calendars, sourcing/fulfillment checklist, replenishment watch,
  cash-cycle scenarios and an optional channel-expansion roadmap. No invented events or predictive accuracy.
- **Sources/Settings/System:** beginner setup, local private credentials, geography, business capital, fee versions,
  adapter capabilities, worker phase, last progress, failures, persistent Pause and actionable recovery.

Use KESTREL's strong dark visual identity and lime accents with dense, polished, responsive layouts. Show a
labeled mockup before extensive UI construction; sample content cannot become production evidence. Preserve privacy.

Load cached real observations immediately, refresh progressively and keep optional source outages local. Distinguish
observed listings from qualified opportunities and blocked economics. No unexplained blank dashboard. Opening this
desk starts/resumes its authorized bounded research scanner unless explicitly paused; it never purchases inventory
or starts another desk. Explain expected first useful output and actual prerequisites without an arbitrary wait timer.

## 12. Architecture and resource discipline

Prefer the repository's existing stack: reuse suitable Python/FastAPI, SQLite/WAL and frontend patterns if inspection
supports them. Do not introduce Next.js, PostgreSQL/Timescale, Redis, Celery, distributed browser clusters or paid
proxy networks merely to appear institutional. Add dependencies only for a demonstrated requirement.

Define versioned contracts and an ER model for source capabilities, exact products, offers, availability evidence,
sale observations, demand estimates, fee rules, economics scenarios, opportunities, capital plans, inventory lots,
inventory events, channel listings, synchronization events and job checkpoints. Specify endpoints and interfaces.

Every recommendation must trace to observation IDs, observation/decision times, target channel, fulfillment method,
source coverage, fee/parameter/specification versions and a reproducible input hash. Retain permitted evidence with
bounded storage and licensing-aware policies. Absence of archived inputs limits reproducibility and must be disclosed.

Use one bounded local orchestration path with per-source concurrency and minute/day request budgets, prioritization,
cache/deduplication, incremental checkpoints, finite retries/backoff and cancellation. Prefer lightweight adapters;
use a small browser budget only where necessary and permitted. Adapt scanning priority to demonstrated useful yield,
offer changes and false-availability rates. Thousands of websites are a possible future coverage target, not an MVP
promise. Publish actual source and product coverage.

Measure CPU, peak RAM, database/log growth, disk space, request use and page responsiveness on representative work.
Implement retention/log rotation and storage-pressure behavior. Long waits, sleep and restarts must invalidate stale
state without fabricating observations, duplicating workers or hiding defects behind restart loops. Provide one
bounded sanitized diagnostic command and a separate desktop launcher. No paid infrastructure assumption.

## 13. Validation and prospective learning

Maintain an experiment registry with fixed versions, selection rules and chronological evaluation periods.
Capture opportunities and rejections prospectively, including false matches, inaccessible/sold-out items,
restricted products, unprofitable observations and stock that never sells. Avoid survivorship and winner-only reports.

Distinguish historical backtests, paper baskets and my own completed business outcomes. No retroactive purchases.
Use information available at each decision, account for execution/arrival delays, and never tune on future sale prices.
Evaluate ranking and capital allocation separately against simple baselines at matched cash/risk constraints.
Use dependence-aware uncertainty when justified; sparse data must not yield confident success claims.

Measure identity precision, verification coverage, false availability, delivered-price errors, fee-estimate errors,
realized versus forecast net profit, sell-through/time to sale, payout delay, inventory age, capital turnover,
drawdown, returns and dead-stock losses. Track rejected-item outcomes where observable. Grade proxies as proxies.

## 14. Fault testing and recovery

Test changed pages, variant/pack mismatches, hidden final prices, stale promotions, unavailable quantities,
unknown shipping/tax, nonfinite arithmetic, unauthorized products, 429/5xx, timeouts, malformed/oversized payloads,
expired credentials, partial imports, SQLite busy/disk-full, sleep/resume, process death, duplicate starts,
late/duplicate/out-of-order sale events, returns and synchronization failures.

Verify clear detection, sanitized explanations, preserved completed work, bounded recovery and safe continuation of
unaffected features. Do not mask programming errors or turn missing data into favorable results. Test financial and
inventory logic with independent accounting identities. Run unit, integration, browser, type/lint/build and resource
checks. Test populated, loading, partial, stale, paused, failed and recovery states at laptop and phone sizes.
Keep a durable fault registry and continuation handoff. Do not use operator production data or secrets in fixtures.

## 15. Delivery sequence and acceptance

First inspect the repository and establish current data/access feasibility. Then deliver one useful vertical slice:
permitted real present-day offers → exact matching → availability evidence → honest economics → a product dossier
and capital-aware candidate basket, with explicit blocked fields. Select a small practical source/category set
based on accessible evidence, costs and fulfillment feasibility. A real observation board is useful before full
confirmed-sale coverage; it must not masquerade as a complete arbitrage recommendation engine.

Show the mockup, implement the slice, test it, then expand. Work autonomously on authorized local implementation.
Do not stop at a plan or fill missing capabilities with fake data. Ask only for essential missing inputs or
external account actions, and explain progress. Credentials belong in private local setup, never in chat.

Acceptance requires traceable real observations, working product links and honest verification times, correct
product matches, explicit availability/eligibility/cost gaps, independently checked profit and inventory accounting,
cash-constrained integer baskets, progressive visible results, idempotent recovery and measured resource limits.
No automatic purchases, cross-channel account mutations or changes to existing trading desks are implied.

Deliver source/fee feasibility with citations, a coherent architecture/ER model, versioned machine-readable contracts,
mathematical definitions and examples, working workflows, actual test/resource evidence, outstanding dependencies,
exact startup/update commands, observable acceptance criteria and a prioritized continuation plan. Commit/publish
tested changes when repository authorization permits. Software correctness is not evidence of business profitability.
