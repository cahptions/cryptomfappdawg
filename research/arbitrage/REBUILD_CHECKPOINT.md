# Operator rebuild checkpoint — 2026-10-22

This is implemented development work, **not a completed live-business acceptance**. New real-source coverage in this
cloud session is zero. Earlier product screenshots and browser reports describe the previous baseline. The current
GUI has syntax/static checks and handler/projection tests; its actual browser acceptance remains blocked.

## Operator workflow delivered in this pass

Settings owns API credentials, setup links/guides, saved searches, source URLs, source toggles and diagnostics.
Sources browses collected products by their actual retailer/marketplace hostname, independent of the originating
feed or search. Filtering/search and pagination cover the catalog, rather than silently filtering the first page.

Home defaults to dense image/list rows with an optional card view. It puts buy/landed cost beside comparable low,
average, high and median, with currency, count, window and evidence type. Unknown costs remain unknown. Preferences,
query focus/selection and scroll are retained across refreshes. A separate fully labeled synthetic HTML layout
preview is in `validation/operator-preview.html`; it is not a production screenshot.

Public feed links enter a merchant queue only when they are supported canonical URLs. Adafruit sitemap traversal
and Arduino public-catalog candidates have persistent checkpoints, fixed allowed destinations, robots checks,
size bounds and explicit access/schema failures. Shopify-like prices require a published store currency; no USD
fallback is guessed. Real current endpoint compatibility/access has not been established here.

Optional eBay Browse search now requests up to 200 listings/page, advances durable offsets within its 10,000-result
window and never follows arbitrary `next` URLs. Batch observation writes are atomic; failed batches restore the
previous checkpoint. Exact declared GTIN/pack/condition matching can inspect bounded production item details and
create asking-price comparables. Missing identifiers/pack/condition stay unresolved, rather than title matching.
At least three exact current asking listings can prefill an unreviewed research route. Unknown fees, demand,
eligibility, delivery or operator identity review still prevent qualification. No purchases or seller mutations.

Catalog capacity is 50,000; normal responses contain 50 products. Protected/owned/researched records are preserved.
Current scenario indexing allows covered costed products outside the first alphabetic page to rank. Input changes
invalidate that derived index; cache validity cannot outlive the scenario or availability. Full capital planning
loads all configured selling-route products and current inventory commitments, independently of the visible page.
Unknown positive quantity cannot qualify. Economics gate hash specification is now `arb-economics-v2`.

## Explicit price evidence math

Cohorts match declared normalized GTIN, pack, condition, region and currency. Each listing URL contributes its
newest retained observation once; repeated polling does not become independent comparable count. Future records
are excluded. Over the labeled 30-day window: low=min, high=max, average=sum/n, median=ordinary sample median,
lower quartile=sorted sample at floor((n-1)/4). Extremes are displayed; the automatic draft uses median and lower
quartile, never the historical high or MSRP. These are item-price statistics excluding shipping/tax. They do not
infer an achievable sale or demand. Asking, reported sold, transaction-record imports, own net and reference
classes remain separate. Imported transaction records remain source claims; hidden accepted offers are unknown.

CSV import is atomic, bounded to 500 rows / request size, normalizes time/GTIN and refuses naive/future timestamps.
Product charts omit historical acquisition observations whose identity metadata differs from the current product.
Independent comparable plots use their own typed cohorts and label axes, dates and currency.

## Hardware and request bounds

No new runtime dependency, paid model, browser farm, GPU or remote server is required. Requests are serial.
Merchant URL enrichment takes at most ten products/cycle with 1,000 reserved page/robots attempts/day (at most
500 two-request enrichments before failures, excluding separate catalog/feed requests). New work and due refreshes
share bounded slots; paused sources do not drain queues. Catalog completion backs off six hours; repeated pages,
unsupported sitemap HTML, access denial and missing currency are failures, not successful empty inventory.
Optional eBay retains its local 2,000 HTTP-attempt/day ceiling, credential/free-access gate and persistent provider
backoff. That ceiling is not a claim about account quota, production entitlement or licensing.

Retail observations retain the existing 90-day / 100,000-row bounds; eBay active-listing observations and asking
comparables expire after 24 hours pending a fuller terms review. Own inventory events remain append-only. API
catalog metadata may outlive a price cache; the UI distinguishes catalog records from retained priced products.

`capacity-10000.json` is an actual isolated cloud synthetic test: 10,000 distinct priced fixture products and
queued URLs, first/middle/last pages and full-catalog search. It records exact updated timing, payload, RSS and disk
numbers. It proves neither real market coverage nor Dell/browser/power-loss performance.

## Validation and limits

Read `validation/rebuild-checks.json`: 104 targeted tests passed, five threaded/full-HTTP tests deselected. Targeted tests cover primary Stocks analysis/PAPER regressions and Arbitrage
pricing, accounting, queues, pagination, matching, expiry, source-failure veto, protected records and handler-level
CSV/projection behavior. Strict Mypy and Ruff pass. Syntax checks cover changed JS and browser scripts.

Browser/server commands requiring escalation could not execute because automatic approval review reported an
account usage limit, not an unsafe-action finding. A normal Chromium launch also failed on restricted socket
operations. Threaded/in-memory HTTP tests stalled and were bounded with timeouts; they are not passing HTTP tests.
A broader regression run timed out; its prior baseline full-suite result must not be reused for this revision.
No current desktop/mobile screenshots or decoded-image/browser-flow acceptance are claimed.

## Source and capability matrix

| Source | Implemented use | Access/evidence boundary |
|---|---|---|
| Adafruit public sitemap/Product JSON-LD | URL discovery and declared product offer enrichment | Real markup/robots/availability unverified here; no shipping/tax/physical stock certification |
| Arduino public Shopify-like catalog | Published variant prices, IDs/photos and explicit currency | Candidate endpoint, robots/access/schema unverified; pack/condition may remain unknown |
| Slickdeals / DealNews | Dated leads; supported direct merchant links can queue | Mentions are not priced offers, stock or sales; no affiliate redirect bypass |
| eBay Browse production | Paginated active offers; bounded exact-detail resale research | Requires private production credentials, account permission/terms/quota verification; asking prices only |
| Comparable CSV | Typed external/operator price records with exact identity checks | Imported claims, not independently certified transactions |
| Amazon / Walmart seller APIs | Setup/reference guides only | Fee estimates, seller eligibility and historical transactions are not integrated |
| Own inventory ledger | Lots, reservation, sale, refund/quarantine/write-off accounting | Full pending payout/settlement reconciliation is still unfinished |

Official reference links for subsequent access verification:
- https://www.adafruit.com/robots.txt
- https://store.arduino.cc/
- https://shopify.dev/docs/api/ajax
- https://developer.ebay.com/api-docs/buy/browse/overview.html
- https://developer.ebay.com/api-docs/buy/browse/resources/item_summary/methods/search
- https://developer-docs.amazon.com/sp-api/
- https://developer.walmart.com/

These links are references to inspect, not a claim that their current contents were read in this pass.

## Competitive research and remaining product work

Current official competitor docs/demos were inaccessible under the active cloud policy. SellerAmp, Tactical
Arbitrage, Keepa, RevSeller, BuyBotPro, InventoryLab, SmartScout and ScoutIQ remain the requested research set.
No current competitor capabilities, paid-subscription access, operator reviews or hands-on experience were
invented. Functional improvements above follow the operator requirements and repository audit; they are not
falsely attributed to examined competitor software. Access-enabled research remains outstanding.

Priority next work:
1. Run current isolated browser/HTTP tests and fix actual desktop/mobile/source-image/form defects found there.
2. Verify permitted real catalog -> exact product -> resale evidence -> realistic dated fees/eligibility slice.
   Check actual production eBay access locally without exposing credentials. Do not claim thousands of real offers.
3. Complete competitor review from accessible official material and measure which workflows reduce operator work.
4. Expand supported real sources and identity coverage using explicit permissions/data, rather than relaxing match
   quality or manufacturing pack/currency/history. Measure observed enrichment yield and failure reasons.
5. Add settlement/payout reconciliation, source/category forecast-vs-realized reporting, replenishment evidence,
   saved-alert/noise evaluation and richer stock/workload filters only against supported records.
6. Measure actual Dell browser/backend resource use, network outages, sleep/resume and power-loss recovery.

An additive cloud-domain draft has been saved with `requires_publish=true`. It preserves existing entries and adds
catalog/feed/research hosts, but is inactive until reviewed/saved in environment settings and the environment
published. Private laptop API credentials have not been accessed. Normal Git fetch also failed on network access;
verify remote publication before giving the operator pull commands for these changes.
