# KESTREL — portable conversation and project context

Prepared: 2026-10-22 (UTC).

## What this document is

This is a structured handoff from the visible conversation and inspected repository. It is **not a verbatim chat export**: many earlier assistant replies are unavailable in the current conversation history. It preserves the user's stated goals, corrections, constraints, recent implementation work and remaining uncertainty without inventing missing messages.

Private API credentials, OAuth codes, client secrets, account identifiers and unnecessary personal information are omitted. Earlier screenshots included credentials; those values are intentionally absent. This document contains no authority to operate another app's accounts or make purchases/trades.

The user is building another app in a separate AI chat. Use this document to understand their expectations and learn from the workflow failures. Do not assume the other app has the same domain or copy KESTREL's architecture without inspecting that app.

## The user's central expectations

- Build an application that does useful work automatically after the necessary connections are configured. Do not leave default PAPER collection/trading or sourcing disabled without an actual reason.
- Make available data appear progressively. A 24-hour acceptance test is evidence collection, not a reason to leave the rest of the app unbuilt or hide incoming data.
- Work through complete workflows, rather than repeatedly producing plans, prompts, cosmetic changes or terminal instructions.
- Communicate clearly during work. The user has repeatedly objected when no progress or concrete output was visible.
- Explain technical instructions in plain language, with exact field labels and copyable commands. Do not assume familiarity with browser internal URLs, terminal process ownership, API products or fulfillment terminology.
- A polished populated mockup is desirable, but a mockup is not a data integration. Clearly label synthetic previews and connect the finished interface to real observations.
- Avoid manual data-entry work that the application could perform. Request human review for actual ambiguity, eligibility or purchasing decisions.
- Put API keys, source URLs, saved-search setup, connection checks and guides in **Settings**.
- **Sources is a product-browsing page**, grouping collected products by retailer or marketplace. It must not be a source-configuration form.
- Show concrete evidence, useful next actions, photographs where supplied, high/low/average/median prices and actual dollar economics. Avoid arbitrary AI scores, empty widgets and vague 'awaiting data' messages.
- Favor meaningful net dollars and manageable work. A cheap item or a large percentage discount alone is not a good business opportunity.
- Provide working downloadable/reviewable files when requested. A filename mentioned in chat does not place a file in the user's Downloads folder.
- Do not tell the user the build is complete while essential workflows or real-source acceptance remain unfinished.
- The user's latest instruction before this export: recheck the work thoroughly; they believed they had accidentally selected a lighter effort setting. Do not claim you can see or change the UI's effort/usage settings unless you actually can.

## Hardware, operating environment and budget

- Laptop: Dell, AMD Ryzen 7 7730U, 16 GiB RAM, integrated Radeon graphics sharing system memory.
- Linux Mint 22.3; approximately 290 GiB free SSD space at the time specifications were supplied.
- Home Wi-Fi; the laptop may sleep, lose connectivity, have its lid shut, freeze or restart unexpectedly.
- No GPU-dependent research, paid AI judge, paid proxy farm, remote-server requirement or LLM in trading decisions.
- Additional recurring data/API/cloud budget: **$0 now**. A possible future budget around $20/month was discussed only after substantial realized profit; it is not currently available.
- Existing Jupiter, Helius, Robinhood and Schwab credentials remain private on the laptop. eBay production credentials are not present in this cloud session.
- Tailscale is optional connectivity/HTTPS infrastructure. It does not keep an application running when its host laptop is asleep or switched off, and no paid domain is required merely because HTTPS is needed.
- Earlier long-running collection was interrupted by a closed lid. Do not claim sleep, power-loss or resume recovery is physically validated on the Dell based only on cloud tests.

## Project and desk boundaries

Repository: `cahptions/cryptomfappdawg`.

User laptop checkout: `~/kestrel`.

Current cloud checkout: `/workspace/kestrel-schwab`.

Current working branch: `codex/arbitrage-v1`. Earlier work also involved `codex/continue-claude` and `codex/finish-schwab-stocks`. Inspect the actual branch and commits before issuing pull/switch commands.

KESTREL contains separate businesses/experiments:

1. Solana/memecoin discovery and research.
2. Robinhood cryptocurrency desk.
3. Stocks and options research/PAPER desk, including Schwab market data.
4. Retail/online/other product-arbitrage business.

Preserve each desk's data, accounts, configuration, credentials and authorization boundaries. Product-arbitrage capital and inventory are not trading capital.

The user requested changing a trading reference baseline from $100 to $500. Do not blindly convert every simulated book to $500: actual configurations and distinct experiments matter. Historical context included a $100,000 PAPER learning book with a separate small-account replica; Stocks has separate stock/options books. The Arbitrage screenshot showed $2,000 business capital, but read actual settings rather than silently resetting them to that amount.

## Trading goals and guardrails

The user wants research that can identify worthwhile opportunities early, reject noise, compare decisions with passive benchmarks and manage losses and gains intelligently. They do not want a pile of existing dashboard indicators dressed up as independent research.

Position growth should depend on qualified evidence, account-relative risk, market liquidity and meaningful confidence. Profit by itself is not a reason to increase exposure. A score is not a probability without calibration.

Earlier research instructions requested exactly two independent deterministic quantitative advisors, initially SHADOW-only with zero entry/exit/sizing influence. They required primary research, explicit transfer hypotheses from equities to crypto, realistic execution costs, point-in-time data, walk-forward evaluation, purging/embargo for overlapping labels, multiple-testing control, untouched evaluation and matched-risk ablations. Those specifications are research requirements, not evidence that an edge has been proven.

Equity execution costs differ from cryptocurrency fee models. Do not transplant crypto fees or sniping assumptions into stocks. Also do not conclude that stock fees, spread, impact, options bid/ask friction or account constraints are zero.

Preserve STOP, guardian, risk limits and LIVE authorization. No task in this research/build pass authorizes funding wallets, live brokerage orders, automatic retail purchases, checkout, cart changes, listings, repricing or other marketplace mutations.

## Important history and lessons

### Collection and availability

A 60-minute Solana collection report showed 100% polling success for its listed jobs, zero unhandled exceptions/suspend gaps and about 50.9 MB memory. A later 24-hour acceptance report failed because the run was still active or did not finish cleanly. These observations do not establish strategy profitability.

The user's dashboard repeatedly remained empty despite having downloaded price sessions. A real Stocks failure was a division by zero in fundamental gross-margin calculations. Another free-source problem returned the St. Louis Fed's HTML maintenance page instead of CSV; that is a provider outage, not an invalid user command or a reason to manufacture values.

The user expects progressive population and explicit bottleneck diagnostics. Missing, unsupported, excluded, stale, not-yet-computed and genuinely nonqualifying are different states.

### Browser and laptop memory

A Firefox Process Manager screenshot showed the ChatGPT tab around 2 GB and separate Claude/YouTube/other site processes. `isolatedWeb` is a browser process class; one screenshot alone does not prove a memory leak. Do not invent access to the user's system analyzer or assume the Python collector caused browser RAM usage.

### Schwab

The user successfully registered a Schwab Market Data Production app using the local callback `https://127.0.0.1:8182/oauth/callback`.

In the developer portal, Client ID is the app key. Broker authorization uses brokerage login credentials rather than assuming the separate developer-portal login works. After authorizing, Firefox warned about the local self-signed certificate; the user subsequently saw authorization saved.

A supplied `uv run desk stocks doctor --online` report showed connected Schwab authorization, a successfully received quote, PAPER mode and `orders_enabled: false`. Local HTTPS worked in that case; Tailscale was not required for that observed connection.

The old guide said 'stop the worker with Ctrl+C', but the dashboard and collector were separate processes. Stopping the dashboard caused network errors. Normal credential setup should not require guessing which terminal to stop. Blank secret inputs can preserve saved secrets; masked fields should not imply that the app exposes saved secrets.

An `invalid_client` screenshot followed incorrect credential entry. Do not copy any credential values or OAuth URLs from screenshots into documentation or logs.

### UI and product quality

The user liked a prior Claude mockup and strongly disliked green-on-green surfaces, huge status panels, sparse cards, missing photos and interfaces that required manual investigation of every item.

The user explicitly wants a Stocks home page with useful ranked lists and stock/options evidence. WSB/social mentions must not dominate the default Top 10.

The latest chart correction: inspecting historical points by pointer, drag or keyboard must update the large displayed price and selected date. Leaving/resetting returns to the latest price. A small tooltip alone does not satisfy the requested behavior.

## Arbitrage product requirements

The business workflow is:

`discover -> inspect exact product -> compare source offers -> establish resale evidence -> check sellability -> calculate full costs -> choose destination/fulfillment/quantity -> human purchase decision -> inventory -> sale/returns/settlement -> replenishment and learning`.

The user initially mentioned Amazon FBA and Walmart, but explicitly expanded the scope to other inexpensive marketplaces and specialist/local channels where supported. Determine the best **covered eligible alternative**, not an imaginary global maximum.

Briefly explain fulfillment terms in the UI: FBA means Amazon stores, packs and ships inventory for fees; FBM means seller fulfillment; WFS is Walmart's fulfillment service. These are not automatic entitlements or free services.

### Required sourcing and evidence

- Thousands of distinct genuinely priced offers, not thousands of duplicate polls or community mentions.
- Public deal feeds may supply leads, but must feed automatic merchant enrichment where supported.
- Real pagination, persistent checkpoints, incremental refresh, deduplication, finite budgets, visible backoff and independent source failure handling.
- Exact variant, pack count, condition, identifiers, currency, delivery region and timestamp.
- Source photograph and direct purchase link where supported.
- Buy price and landed acquisition cost beside resale low, average, high and median.
- Sample count, time window, source/marketplace and evidence class with every price distribution.
- Current asking prices, historical asking prices, reported sold listings, transaction records, own net sales and reference/MSRP prices stay separate.
- Hidden accepted offers are unknown. Asking prices and MSRP do not become confirmed transactions or default achievable exit prices.
- Demand/volume/popularity/competition heat ratings require actual supporting observations. Attention is not sales volume, and missing evidence is not a favorable score.
- Stale or sold-out observations cannot become buy-ready recommendations. Current stock cannot be guaranteed indefinitely; display checks and age honestly.

### Economics and business operation

- Rank separately by conservative net dollars, return on capital, supported price dislocation, effort, cash recovery and category.
- Include shipping, tax, prep, selling/payment fees, fulfillment, storage, returns, damage, labor and overhead where relevant.
- Separate gross spread, contribution/net profit, margin, ROI, inventory cost, committed cash and realized proceeds.
- Compare destinations and show runner-up/rejected alternatives, remaining checks, break-even and downside.
- Support lot-based inventory, reservations, sales, refunds, quarantined returns, write-offs, inventory aging and eventual settlement/reconciliation.
- Do not turn a profitable-looking spreadsheet scenario into evidence of realized profit or an executable order.

### Navigation and HCI

- Home: dense useful sourcing shortlist first, with optional card view.
- Product research: exact identity, source offers, resale distributions/charts, costs and specific next actions.
- Sources: products grouped by retailer or marketplace, with filtering and direct product links.
- Settings: all API credentials, URLs, saved searches, connection setup, links, guides and source diagnostics.
- Other useful pages: destination profitability, capital planning, inventory, top/category opportunities.
- Preserve filters, sorting, unfinished work and scroll. Support server pagination and progressive population rather than returning the entire catalog every few seconds.
- Use a restrained visual system, readable contrast and compact photographs; no green-on-green interface or giant explanatory panels above the products.

## Implementation status at this export

**The build is not fully accepted or proven in production.** The current local work includes changes that have not yet been pushed from this session.

### Previous pushed baseline

The baseline inspected at the start of this pass was commit `3b9e305` on `codex/arbitrage-v1`. Earlier work had a separate Arbitrage desk, lot/event inventory, explicit channel-cost scenarios, an optional eBay connection, two public feed candidates, a narrow Adafruit product-page parser, basic image cards, private setup and safe desk-specific `dashboard --restart --open` support.

That baseline's earlier full offline test report was 1210 passed / five live tests excluded. **Do not apply that old result to the new working-tree changes.**

### New Stocks changes in the working tree

- Added descriptive market/options activity rankings with no social or committee input; the default home list uses market/options activity and keeps social attention separate.
- Options/market weights are heuristic activity priorities, not an independently validated predictive model or probability. Correlated metrics remain a limitation.
- Added quote timestamp/session, bid/spread, DTE and aggregate sample/liquidity gates.
- Distinguishes volume/open-interest from volume/average historical volume, and labels midpoint premium as a proxy rather than actual traded premium or direction.
- Rotates sampled options by underlying dollar liquidity, not committee/social rank.
- A symbol failure no longer discards unaffected symbol requests; total provider failure is no longer a successful options job.
- ETF pages receive a read-only market/risk/options profile without company valuation or an invented company committee score. ETF lookback warm-up is explicit.
- Chart pointer/touch/keyboard inspection updates headline price/date/change and resets appropriately. Browser regression code was extended, but browser execution remains blocked.
- Existing primary PAPER selection behavior and LIVE/order authorization boundaries are preserved.

### New Arbitrage changes in the working tree

- Added public merchant catalog candidates: Adafruit sitemap traversal and an Arduino public Shopify-style catalog parser. Robots/access failures stay failures; currency must be explicitly published, not guessed. Actual endpoint compatibility is not verified here.
- Persistent merchant URL queue, bounded enrichment, fair new/refresh batches, source-pause checks and request reservations.
- eBay searches now support up to 200 listings/page and durable offsets inside the 10,000-result search window. Arbitrary provider next URLs are not followed.
- Increased catalog capacity to 50,000 with indexed server-side 50-product pagination and global search/category/source filtering. Protected/owned/researched data must not be evicted.
- Added a current costed-opportunity index, invalidated when source/product/route/settings inputs change and bounded by scenario/availability expiration.
- Added exact declared GTIN/pack/condition resale research using optional production eBay item details. Missing identity fields remain unresolved; it does not fuzzy-match titles into false products.
- Added separate comparable price classes and low/average/high/median/lower-quartile statistics. Latest per listing is counted once. Wrong condition/pack/currency/region and future records are excluded.
- Automatic matching can create an **unreviewed draft** resale scenario; missing fees, demand, eligibility, delivery or identity review still block qualification.
- Added atomic comparable CSV import for supported records. It retains imported source claims and does not independently certify a transaction.
- Added compact list-first presentation, optional cards, side-by-side purchase/resale evidence, persistent preferences and direct product access outside the current page.
- Moved source configuration and diagnostics into Settings. Sources now groups actual collected offers by merchant/marketplace host, independent of which feed/search discovered them.
- Tightened purchase qualification to require known positive quantity. Economics gate specification advanced to v2.
- Full capital planning reads all configured selling-route products and current inventory commitments rather than silently using only the visible catalog page.

### Important limitations still open

- Zero newly verified real retailer/API offers in this cloud session. Do not present the 10,000 synthetic capacity records as real sourcing coverage.
- Public merchant access/markup/currency and production eBay permissions/quotas/terms remain unverified here; laptop credentials are unavailable.
- Completed-sale history, account-specific Amazon/Walmart fee and eligibility integrations, actual demand, independent trade verification and realized profitability are not established.
- Full settlement/payout reconciliation, replenishment evidence, broad retailer matching and many advanced commercial features remain unfinished. Existing inventory event accounting is not proof of a complete operational business.
- Current competitor documentation/demos could not be independently reviewed because cloud network access excludes their domains. No paid competitor capabilities or hands-on experience should be invented.
- No actual Dell power-loss, sleep/resume, browser responsiveness or full real-market business validation occurred.

## Validation and environment blockers

The final targeted modified-module regression run passed **104 tests**, with five threaded/full-HTTP tests deselected (19.35 seconds). This is not a complete repository or browser test pass. Ruff and strict Mypy passed across 231 source files. JavaScript syntax checks passed.

A 10,000-product synthetic capacity experiment verified distinct products, persistent queues, first/middle/last 50-product pages, full-catalog search and bounded response size. The final recorded capacity check took approximately 0.02–0.025 seconds/page, returned 86,640 bytes for its last page, and peaked at 37.52 MiB process RSS. Database files totaled 17,162,240 bytes for that fixture. These are cloud synthetic measurements, not production or Dell measurements.

Browser/server verification is blocked in this environment. An escalated fixture-server command was rejected because **automatic approval review hit an account usage limit**, not because it determined the action was unsafe. A later non-escalated Chromium check failed with an OS `setsockopt: Operation not permitted` error. In-memory FastAPI/sync-worker tests also stalled under the restricted environment and were bounded with timeouts. A broad regression attempt did not finish before its timeout; it is not a passing full-suite result.

The active cloud network policy allows crypto hosts and package/GitHub presets, but not the retailer/eBay/competitor hosts needed for real-source acceptance. An earlier additive network draft exists but is not published. Saving a draft is not applying the network policy. Do not claim that opening an edit panel or 'allow all' automatically changed the active policy.

A normal Git fetch failed in this session because of network access. Verify publication before telling the user to pull new changes. Do not issue a command that assumes these working-tree changes are on origin when they are not.

## Where the next AI should start

1. Inspect actual repository, working tree, branch and applicable instructions. Do not reset or discard the uncommitted work described here.
2. Read the current durable research/handoff files and run appropriate checks that the environment actually permits.
3. Finish the Stocks chart/ranking/profile verification and Arbitrage Sources/Settings/browser flows, clearly distinguishing pending tests from passed tests.
4. Reconcile current catalog/ranking/price/availability inputs, exact matching, stale-data expiration and financial accounting before adding more indicators or arbitrary scores.
5. Complete real-source acceptance using permitted domains and the user's locally configured connections. Do not ask for secrets in chat or bypass source restrictions.
6. Research accessible official competitor documentation/demos and convert only feasible useful ideas into complete workflows. Clearly label unavailable capabilities/data.
7. Publish or provide a concrete reviewable update only after checking the actual branch. Give exact update/start commands that match the delivered code.
8. Keep a factual handoff. Do not substitute synthetic success, a screenshot or passing unit tests for real forward/business evidence.

## Useful commands — conditional on the correct code being available

These are known command forms, **not proof that the new changes have been pushed**:

```bash
cd ~/kestrel
uv sync --frozen
uv run desk stocks dashboard --restart --open
```

```bash
cd ~/kestrel
uv run desk arbitrage dashboard --restart --open
```

Do not use broad `pkill` commands or guess PIDs. Do not stop another desk's collector to update a dashboard. Keep live credentials private.

## Repository files relevant to this pass

- `research/STOCKS_ACTIVITY_FIXES.md`
- `research/STOCKS_AND_ARBITRAGE_QUEUE.md`
- `research/arbitrage/OPERATOR_REBUILD_REQUEST.md`
- `research/arbitrage/HANDOFF.md`
- `research/arbitrage/validation/capacity-10000.json`
- `research/arbitrage/validation/operator-preview.html` (explicit synthetic design preview, not a production screenshot)
- `src/desk/stocks/activity.py`, `analysis.py`, `collect.py`, `signals.py`, `hub.py`, `lists.py`, `runner.py`, `web.py`, `static/research.js`
- `src/desk/arbitrage/catalog.py`, `comparables.py`, `matching.py`, `repo.py`, `runner.py`, `views.py`, `web.py`, `static/app.js`, `static/style.css`, `static/setup-guides.json`
- `tests/unit/test_stocks_activity.py`, `tests/unit/test_arbitrage_catalog.py`, `tests/arbitrage_capacity.py`

## Appendix — full user-supplied Arbitrage rebuild assignment

The following assignment is included so this document is useful without the original attachment. It records the requested target; it does not imply all items are implemented.

---

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


## Export follow-up checkpoint

After requesting this Markdown, the user explicitly asked to resume the ongoing build. Source/navigation work and
review continued. A new runtime permission request returned `network:null` (no grant). An additive cloud-domain
draft was confirmed saved with `requires_publish:true`; it has not changed the active policy. Browser/real-source
acceptance and remote publishing remain blocked/unconfirmed. Do not claim the user's laptop has received this code.
