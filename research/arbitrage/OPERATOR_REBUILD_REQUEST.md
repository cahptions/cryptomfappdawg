Act as the principal product engineer, experienced retail-arbitrage operator, marketplace researcher, data engineer, quantitative analyst, and UX designer responsible for rebuilding KESTREL Arbitrage into a useful business application.

Research, implement, test, and deliver the working improvements. Work autonomously within the authorized scope. Do not stop at a plan, competitor report, cosmetic redesign, search links, or another list of unimplemented features.

Treat this as a comprehensive product reassessment. The examples below describe some failures; they do not define the limit of your assignment. Identify important needs I have not known to ask for.

PROJECT AND CURRENT FAILURE

Repository: cahptions/cryptomfappdawg
Working branch: codex/arbitrage-v1

Inspect the actual current repository before relying on this description. Read applicable AGENTS.md files, relevant project handoffs, documentation, configuration, schemas, implementation, and tests, including:

- docs/ARBITRAGE.md
- research/arbitrage/MASTER_PROMPT.md
- research/arbitrage/PRODUCT_CORRECTION.md
- research/arbitrage/FEASIBILITY.md
- research/arbitrage/HANDOFF.md

A recent operator screenshot showed:
61 fresh leads → 1 observed product → 0 costed comparisons → 0 checked opportunities.

The application currently leaves much of the sourcing and resale research to the operator. That is inadequate for the intended business.

Previously audited problems included a narrow retailer adapter, small first-page eBay searches, restrictive catalog limits, incomplete automatic enrichment, manually entered resale scenarios, and a GUI that does not make commercial decisions easy. Verify what remains true and fix the underlying problems.

Preserve existing Stocks, Crypto, and Solana data, credentials, accounts, and trading behavior. Arbitrage is a separate business.

1. THINK LIKE A FRUSTRATED ARBITRAGE OPERATOR

Evaluate every workflow as someone spending their own money, managing limited capital, and trying to earn worthwhile profit without creating an exhausting second job.

Ask:
- What work is the application making me do that it should automate?
- What information would prevent a costly buying mistake?
- What would let me investigate substantially more products in the same time?
- What would an experienced reseller immediately recognize as missing?
- Where could apparent profit disappear during sourcing, fulfillment, selling, returns, or settlement?
- Which screens and metrics help me decide what to do?
- Which features merely display information or add complexity?

Walk through the entire business:
discover → investigate → compare sources → establish resale evidence →
check sellability → calculate economics → choose destination and quantity →
purchase decision → inventory → sale → returns → settlement →
replenishment and learning.

Record friction, missing information, broken handoffs, unnecessary input, and misleading implications. Label simulated walkthroughs honestly; do not present them as human usability studies.

2. RESEARCH OTHER ARBITRAGE AND RESELLER PRODUCTS

Study relevant established products such as SellerAmp SAS, Tactical Arbitrage, Keepa, RevSeller, BuyBotPro, InventoryLab, SmartScout, ScoutIQ, and credible alternatives you discover.

Use accessible official documentation, demonstrations, help centers, screenshots, and credible operator reviews. Record what you actually examined, its date, and access limitations. Do not invent hands-on experience, current capabilities, subscription access, or numerical results.

Investigate:
- Broad and reverse sourcing.
- Retailer/category scans.
- Saved searches and replenishment.
- Product and variant matching.
- Historical pricing and sales evidence.
- Competition and price compression.
- Seller restrictions and eligibility.
- Fees, fulfillment, returns, and break-even analysis.
- Inventory, purchase lists, alerts, and accounting.
- Daily workflows and interface design.

For each useful idea explain:
the operator problem → evidence of the competing approach →
its limitations → a better KESTREL implementation →
required data/access/cost → measurable acceptance.

Learn functional principles and interaction patterns. Develop original code, wording, and visual design.

A paid competitor can teach a useful workflow without making its licensed data available to us. Distinguish transferable ideas from unavailable capabilities.

Do not blindly add every feature. Prioritize operator value, data feasibility, maintenance burden, and measurable improvement.

3. BUILD AUTOMATIC SOURCING AT MEANINGFUL SCALE

Design for thousands of distinct genuinely priced offers across useful categories.

Select sources using actual accessibility, coverage, identifiers, price quality, availability information, permissions, cost, and maintenance effort.

Investigate practical combinations of:
- Official retailer and marketplace APIs.
- Published merchant catalogs and product feeds.
- Permitted structured product-page data.
- Clearance, outlet, open-box, wholesale, and liquidation sources.
- Public deal feeds that lead to actual merchant offers.
- Authorized marketplace and seller-account integrations.
- Useful permitted structured imports.

Community mentions should enter an automatic enrichment pipeline where supported:
published lead → direct merchant offer → exact product/variant →
current source price and declared availability →
candidate resale matches → price evidence → destination economics.

Do not require me to research every item manually just to populate the application. Reserve human attention for unresolved exceptions and purchasing decisions.

Implement:
- Genuine pagination and persistent checkpoints.
- Incremental collection and refresh scheduling.
- Deduplication and stable product/offer identities.
- Per-source budgets, caching, retries, and backoff.
- Prioritization of changed, expiring, and promising offers.
- Visible errors and independent continuation of unaffected sources.

Do not repeatedly fetch only the first page.
Do not count repeated observations as new products.
Do not count community mentions as verified merchant offers.
Do not inflate coverage with duplicate listings or incompatible variants.

Show truthful pipeline counts for distinct offers discovered, offers priced, products resolved, products with resale comparables, costed opportunities, checked opportunities, and blocked/stale/unmatched records.

Identify the actual bottleneck at every stage.

4. PUT PURCHASE AND COMPARABLE PRICES TOGETHER

Each product card or ranked row should show, when supported:

- Product photograph.
- Exact product/model, variant, condition, and pack count.
- Source retailer and direct product link.
- Current purchase price.
- Shipping, purchase tax, and landed acquisition cost.
- Comparable resale LOW.
- Comparable resale AVERAGE.
- Comparable resale HIGH.
- Comparable resale MEDIAN.
- Comparable count, time window, marketplace, and evidence type.
- Price-history/chart access.
- Availability status and last check.
- Estimated net dollars per unit.
- Downside-case net profit.
- ROI on landed cost.
- Relevant workload and cash-cycle timing.

Also support comparison of acquisition offers for the same exact item.

Preserve visibly separate evidence classes:
current asking prices, historical asking prices, reported sold-listing prices,
independently supported transactions, my own realized net sales,
and manufacturer/reference prices.

Never label asking prices as completed sales.
Never assume a displayed sold-listing price reveals a hidden accepted offer.
Never use MSRP or a historical high as the default achievable resale price.

Normalize identity, condition, quantity, pack size, currency, geography, shipping, and time windows.

Define every statistic. Show raw observed extremes where useful, but use robust distributions for valuation. One extreme listing must not inflate the expected resale price.

Charts need meaningful axes, units, windows, timestamps, sample counts, provenance, and visible gaps.

When data is missing, identify the specific missing evidence and the path to acquiring it. Missing values must not become invented estimates or favorable defaults.

5. OPTIMIZE FOR WORTHWHILE PROFIT AND MANAGEABLE WORK

I prefer substantial dollar profit and manageable overhead.

Do not optimize solely for cheap products, percentage discounts, ROI, popularity, or MSRP reductions.

An expensive product can be worthwhile when supported profit justifies the capital, shipping, handling, selling work, returns, and uncertainty.

Provide separate interpretable rankings for:
- Highest conservative net dollars per unit.
- Best supported return on capital.
- Strongest supported acquisition-price dislocations.
- Lower-workload opportunities.
- Faster supported cash recovery.
- Category/niche opportunities.
- Replenishable opportunities with supported repeated supply and demand.

Do not hide these differences inside an arbitrary “AI score.”

Account for purchase price, taxes, freight, prep, fulfillment, selling/payment fees, storage, returns, damage, overhead, and explicit labor assumptions.

Distinguish gross spread, contribution profit, fully loaded net profit, ROI, and net margin.

Investigate overlooked commercial intelligence, including:
- Reverse sourcing from supported demand to cheaper acquisition offers.
- Competition growth after widespread clearance.
- Marketplace or dominant-retailer competition.
- Price compression and promotion dependence.
- Pack, warranty, condition, and regional differences.
- Fragile/bulky handling and storage.
- Conditional coupons, memberships, rebates, order minima, and quantity limits.
- Documentation, authenticity, brand/category restrictions, and actual seller eligibility.
- Inventory age, markdowns, liquidation, and cash trapped in unsold stock.
- Source reliability, replenishment reliability, and useful-opportunity yield.
- Alerts that reduce work without overwhelming me.

Evaluate these ideas and implement the useful feasible ones. Their presence on this list does not automatically justify building them.

6. DETERMINE WHERE AND HOW TO SELL

The decision unit is:
exact product + source offer + selling destination +
fulfillment method + quantity + timing.

Compare relevant destinations, including Amazon, Walmart, eBay, specialist marketplaces, and inexpensive local channels when supported. Do not assume one marketplace always wins.

Show:
- Best supported destination and runner-up.
- Matched source and destination links.
- Resale-price evidence.
- Complete costs and fee basis.
- Base and downside net proceeds.
- Break-even price.
- Seller eligibility and documentation requirements.
- Fulfillment workload.
- Time until inventory is ready to sell.
- Supported cash-recovery timing.
- Why alternative destinations were rejected.

Automate supported comparisons. Keep unresolved identity, eligibility, costs, and demand visible.

“Best” means best among the covered eligible alternatives under displayed assumptions. Do not imply a proven global maximum or guaranteed sale.

7. REDESIGN THE GUI AROUND COMMERCIAL DECISIONS

Create a coherent application an experienced reseller would choose to use daily.

The main page must surface useful products and price comparisons quickly. Repeated headings, large status panels, explanations, and settings must not push the inventory research below the fold.

Use a polished visual system with readable typography, strong contrast, consistent spacing, clear hierarchy, and restrained accents. Avoid green-on-green surfaces.

Provide:
- A dense ranked list with thumbnails.
- An optional visual card view.
- Search and useful category, price, profit, condition, source, and evidence filters.
- Persistent sorting, filters, scroll position, and unfinished research.
- Saved searches, watchlists, and meaningful changes since my last visit.
- Progressive loading while backfills run.
- Fast product inspection with deeper evidence available on demand.
- Side-by-side source and destination comparisons.
- Useful price charts.
- Specific next actions and recovery guidance.

The interface should answer:
“What is it?”
“What can I buy it for?”
“What does comparable resale evidence show?”
“What might I keep?”
“What could go wrong?”
“What should I do next?”

Normal workflows must not require internal IDs, decimal fee fractions, manually typed timestamps, or terminal commands.

Use helpful defaults and progressive disclosure. Collect private credentials only for implemented integrations that use them.

Settings should explain what a connection unlocks, whether it works, what it costs, and the exact next step. A list of unavailable integrations is not successful onboarding.

Explain FBA, FBM, and WFS briefly where relevant.

Inspect any available operator-approved mockup. Present a clearly labeled populated design preview before extensive GUI construction. Keep demonstration content isolated from production evidence.

8. SUPPORT THE ENTIRE BUSINESS WORKFLOW

Support practical purchase shortlists, lot-based landed costs, inbound inventory, available/reserved units, sales, refunds, returns, aging inventory, and reconciliation.

Separate profit from cash flow:
buying inventory commits cash;
unsold stock is not realized profit;
settlement and returns can change the outcome.

Compare forecasts with actual results by source, category, destination, and strategy. Identify which activities earn their workload and capital.

Use explicit separate Arbitrage business capital. A recent screenshot showed $2,000; read the actual configuration and do not confuse it with any trading account.

Respect reserves, existing commitments, stock quantities, supported demand, and exposure limits. Explain binding constraints instead of returning an unexplained empty plan.

No automatic purchases, checkout, cart changes, reservations, listings, repricing, or seller-account mutations are authorized.

9. KEEP IT PRACTICAL ON MY HARDWARE AND BUDGET

Ryzen 7 7730U.
16 GiB shared RAM.
Integrated graphics.
Linux Mint 22.3.
Approximately 290 GiB free SSD.
Home Wi-Fi with possible sleep, outages, and restarts.

Additional recurring API/data/cloud budget: $0.
Do not treat my ChatGPT subscription as authorization to purchase external services.

Use the existing lightweight stack where appropriate. No GPU requirement, paid AI judge, paid proxy service, or distributed browser farm.

Implement indexed storage, server-side pagination, bounded responses, incremental updates, lazy images, persistent checkpoints, finite request budgets, sensible retention, cancellation, and visible recovery.

Do not return the whole database every few seconds or silently drop records from the interface.

Distinguish the complete catalog from the currently visible page and from the financially evaluated subset.

Test capacity with at least 10,000 synthetic distinct offers. Separately establish actual real-source coverage. Synthetic capacity does not prove real catalog population.

Do not bypass logins, CAPTCHAs, access controls, or provider restrictions.

10. PRIORITIZE AND TEST BUSINESS VALUE

Build a prioritized backlog from the operator audit, competitive research, and data feasibility.

For each improvement identify:
operator benefit, avoided loss or wasted time, data dependencies,
implementation effort, maintenance burden, and observable acceptance.

Implement the highest-value feasible changes as complete workflows.

If the architecture prevents sourcing scale or a usable workflow, perform a justified refactor while preserving records and desk boundaries.

Measure:
- Genuine distinct offers examined automatically.
- Successful price and identity enrichment.
- Products with useful resale comparisons.
- Acquisition/enrichment throughput and bottlenecks.
- Manual work required per investigated product.
- False matches, stale links, and false availability.
- Predicted versus realized net results.
- Capital recovery, inventory aging, and dead-stock losses.
- Alert usefulness and noise.
- Browser responsiveness, RAM, CPU, and storage growth.

Acceptance must demonstrate:
- Real source access produces genuine priced offers.
- Pagination advances and survives restart.
- Enrichment runs automatically where supported.
- Images actually render under the security policy.
- High/low/average/median reconcile with the underlying comparables.
- Source and resale identities match.
- Displayed purchase prices match the observations used in profit calculations.
- Fees, shipping, returns, currencies, packs, and time windows are handled correctly.
- Missing/stale data cannot improve an opportunity.
- An apparent bargain can correctly become unprofitable after costs.
- Normal workflows work through actual browser interactions.
- Source failures remain local.
- Sleep, disconnects, rate limits, malformed responses, duplicate starts, and process death recover safely.

Use meaningful unit, integration, browser, accounting, and resource tests.

Keep software tests, real-source observations, and realized business outcomes separate.

Do not call the full build complete while the operator still has one priced product and no automatic resale research. If access prevents the required real coverage, complete unaffected work and report the exact remaining blocker without fabricating success.

DELIVERY

Lead with what genuinely works and the measured coverage.

Deliver:
- Working implementation.
- Actual redesigned interface screenshots.
- Competitive findings translated into implemented improvements.
- Source/capability matrix with accessible documentation.
- Real catalog counts and measured pipeline throughput.
- Explicit price-comparison formulas and evidence classes.
- Test and resource results.
- Exact update/start commands.
- Durable handoff and specific unresolved dependencies.

Ask only for essential account actions, missing access, or decisions that cannot reasonably be inferred. Prepare concrete, reviewable work before requesting necessary external approvals.

Never claim access to my laptop, credentials, private communities, or provider accounts unless that access actually exists.

The standard is an application that materially improves my ability to find, research, source, sell, and replenish worthwhile inventory. Challenge the current product, learn from strong competitors, identify overlooked requirements, and implement improvements that earn their place through reliable data and practical business value.