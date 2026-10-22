# Product correction: earning the operator's attention

The operator wants an arbitrage discovery and decision product, not a database-management interface. The first
release confused correct storage/accounting with useful sourcing. The subsequent card patch did not solve it.
This correction is based on the actual code audit, not a claim that an institutional team or hundreds of people
worked on the project.

## What failed

- The only automatic acquisition sources were generic eBay keyword searches and one electronics retailer URL.
  That is inadequate discovery coverage for a general consumer-goods arbitrage application.
- Every eBay listing remained an independent identity. No cross-retailer exact-product resolution or automatic
  destination price comparison had been implemented. Developer tokens alone cannot supply those features.
- Home had no substantive economics ranking. Most useful work required entering machine identifiers and long,
  undifferentiated forms. Route creation from a product did not select that product.
- The image change did not pass an actual-image acceptance test: the shared Content Security Policy blocked
  remote images, and already-known products did not gain later source photos.
- Green surfaces, repeated oversized empty panels and technical status text dominated the main task. A long list
  of unavailable integrations is not a successful onboarding workflow.

## Corrected jobs and decision units

The unit of comparison is **one exact product + one source offer + one destination/fulfillment method + quantity**.
Discover public leads → record/observe the actual source offer → establish exact identity → inspect resale evidence
→ compare costs/destinations → check stock/delivery → choose whether the capital commitment is worthwhile.
The first stages remain usable without falsely passing later stages.

Home now has a compact progress summary, ranked product shortlist and separate discovery inbox. Neutral charcoal,
white type and blue accents replace green surfaces. Product visuals, source/resale prices, dollars per unit,
downside dollars, capital efficiency, hands-on time and cash-cycle timing carry the decision. Green indicates
positive economics rather than serving as the default page background.

### Ranking v2

No unexplained composite score. Independent lists answer independent questions:

1. **Highest modeled dollars:** descending stress-case net contribution per unit, among stored current positive
   costed cases. Ties use stable product IDs. This favors worthwhile dollar profit rather than cheap high-ROI items.
2. **Return on capital:** descending base net profit / landed acquisition cost, selecting the best currently
   supported ROI route for the same item. It can disagree with the dollar-profit list.
3. **Observed price drops:** current source asking price versus median of equally weighted daily medians from
   at least six PRIOR UTC observation days within the stored 30-day window. Exact reviewed identity, source and
   currency must match. A crossed-out MSRP never substitutes for the baseline.

Current means observed within 15 minutes, not explicitly unavailable, with no later failed source check. Modeled
resale scenarios must also be unexpired. This is a V2 operational bound, not evidence that stock cannot change
within that interval. The stronger pre-purchase qualification still requires the existing five-minute regional
attestation and all eligibility/cost/evidence gates. Missing/negative/stale cases remain labeled research and have
no numeric current-profit rank. Rankings do not override qualification or capital concentration limits.

Dollar profit includes the explicitly supplied transaction, fulfillment, returns and overhead inputs. The operator
should include a value for labor in overhead; hands-on minutes are shown separately and are not silently monetized
at an invented hourly wage. An expensive or labor-intensive item is not excluded merely for being expensive;
its supported dollar contribution, capital commitment and work must justify it. No $500 trading reference amount
is assigned as Arbitrage business capital.

The chosen ranked route retains its exact observation ID, source URL and currency; the UI does not pair a cheap
source from one observation with a profit calculation made using another source. The destination comparison shows
all stored alternatives, a best qualified route when there is one, and specific missing checks otherwise.

## Acquisition layers: what each contributes

| Layer | Use | Current software | Remaining evidence/access |
|---|---|---|---|
| Public deal-community/editorial feeds | Broad merchant leads, promotion context and fast discovery | Bounded RSS/Atom readers for two fixed Slickdeals/DealNews endpoint candidates; automatic seeding, robots check, 15-minute cadence, validators, 304 caching, Retry-After, deduplication, save/dismiss/review | Real-network endpoint/terms/robots/markup acceptance not established here; feed posts are not current merchant prices or stock |
| Retailer product APIs and official product feeds | Exact variant, current source price, shipping and declared stock | Existing narrow Adafruit JSON-LD adapter; source-owned thumbnail extraction added | Need reviewed, authorized retailer-specific connectors and genuine vertical slices before claiming coverage |
| eBay production Browse | Active fixed-price listings and images | Existing private credential setup, bounded search and corrected photo rendering/cache | Operator production access remains private; active listings do not establish completed-sale prices |
| Marketplace sale/fee/eligibility sources | Realistic resale price, actual ability to sell, full destination costs | Reviewed local comparison workflow, sales evidence import and fee links | No market-wide confirmed-sales feed or Amazon/Walmart seller connector implemented; authorization/licensing/plan conditions matter |
| Own business records | Realized net profit, refunds, dead stock and capital recovery | Existing lot ledger and dated evidence | Actual outcomes and reconciliation still required; synthetic tests are not profits |
| External research links | Useful manual fallback and sale-comparable inspection | Targeted search launchers | Search links are never counted as integrated feeds or imported evidence |

Community tools are a worthwhile source of methods: combine feeds, watchlists, exact model/GTIN searches,
price-drop alerts and destination economics. Their existence does not establish access to every platform's
transaction data. More website names or proxies cannot substitute for exact products, permitted data and costs.
No private Discord server is joined or read without access/authorization. No CAPTCHA or access-control evasion,
paid proxies or new recurring services are introduced.

## What this correction does not finish

It does not make a verified whole-market bargain scanner. The next substantive acquisition work is an accepted
real-source vertical slice, exact-product cross-source matches with independent resale evidence, and authorized
fee/eligibility integrations. This is higher priority than another decorative indicator or a larger optimizer.
Source details and API guidance remain in Settings. A saved network configuration draft is not an applied policy.
The laptop can attempt the included feeds normally; the current cloud egress policy excludes these provider hosts.

## Resource and acceptance boundaries

No new runtime dependency. Serial lightweight HTTP; no browser farm or GPU. At most 100 rows from a 2 MB feed,
1,000 recent unprotected cached leads plus protected context with a hard 2,000-total bound; 15-minute feed cadence.
Re-fetch cannot refresh a lead's publication/first-seen age or undo save/dismiss. Deals older than 24 hours leave
Fresh; saved older context stays labeled. Lead data never enters inventory or a purchase basket automatically.

Browser acceptance includes decoded source-host images (synthetic interception, not real provider success), actual
rank switches, lead save/dismiss, lead→offer review, product preselection, partial-cost comparison, percentage input,
editing assumptions, preserved controls, keyboard-accessible forms, and desktop/mobile layouts. Fault tests cover
robots, malformed/oversized XML, HTML maintenance pages, entities, private/unsupported URLs, server limits,
source errors, timestamps, duplicate batches, stale rankings and independent dollar-profit/ROI ordering.
