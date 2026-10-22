# Current rebuild handoff — 2026-10-22

Read `REBUILD_CHECKPOINT.md`, `validation/rebuild-checks.json`, `OPERATOR_REBUILD_REQUEST.md` and
`../STOCKS_ACTIVITY_FIXES.md` first. The current working pass changes source pagination, merchant queues,
exact comparable research, server catalog paging, price statistics, source navigation and cache invalidation.
Source setup is in Settings; Sources groups actual merchant products. Current Chromium desktop/mobile tests,
HTTP middleware/import tests and the full offline suite pass (1,229 passed; five live-provider tests excluded).
Current screenshots are isolated synthetic fixtures. Real source requests still receive proxy tunnel 403.
GitHub fetch and elevated local test execution now work; verify push status before issuing laptop update commands.

The earlier handoff below is historical baseline context; its screenshots/test results do not certify the new work.

---

# Arbitrage V1 continuation handoff — 2026-10-22

Branch `codex/arbitrage-v1`, based on Stocks graceful dashboard restart fix `e9dcc5a`. The older Stocks branch was
already published separately. Do not replace the operator's working data or credentials while switching code.

Implemented: independent local desk, nine working page routes, default automatic bounded collection, persistent
pause, exact-product metadata review/invalidation, GTIN validation, JSON-LD adapter, optional production eBay
Browse with private in-app credential entry and automatic starter searches, source toggles, asking/reported-sales
chart separation, explicit channel costs, stress/break-even math, cash/exposure-constrained integer greedy
baseline with bounded comparison fanout and explicit currency guards, top/category views, lot/event inventory including reservations/refunds/quarantine/write-off, raw
evidence and descriptive comparable-cohort ratings, prospective full-input scenario/rejection archive, local
diagnostic, optional separate launcher, and source/API setup links/guides in Settings.

No real price, stock, sale or profit was manufactured. No operator credentials, account or live collector were
accessed. No existing trading model or LIVE boundary was changed. All development fixtures are visibly synthetic.

## Acceptance status

Software/accounting/API fault and UI checks are recorded in `validation/`. The whole offline suite, Ruff and strict
Mypy were run. Browser evidence covers nine page routes, actual identity/availability forms, inventory receipt,
sale and refund, business capital edits, guide links, private credential save/empty-field behavior and starter /
custom searches. Desktop and 390px mobile layouts were checked without uncaught JavaScript errors or page overflow.
See `FAULT_REGISTRY.md` for exact coverage and physical tests that have not occurred.

**Live vertical-slice acceptance remains open.** Adafruit/product/robots live access was blocked by the current
cloud proxy; no proof of its real current markup compatibility exists. eBay production credentials/access remain
private and unavailable in this environment. Authentication/search HTTP fixtures are not a substitute for a real
production call. Confirmed completed-sale history, actual seller eligibility, fee-estimate integrations, cash
settlement and realized profitability are not validated.

## Immediate next work

1. Review/save and publish the already-saved additive cloud domain draft if cloud live verification is desired.
   The update tool returned `requires_publish=true`; saving did not apply the network policy. Alternatively,
   the operator can verify from the laptop's normal network through the app, keeping credentials local.
2. Verify one allowed real retailer observation and its exact source link/markup/robots behavior. If actual
   markup differs, fix the adapter from a permitted sanitized capture and add a meaningful regression fixture.
   Do not mark an API failure as empty-success, fake a live product, or bypass denial.
3. Follow the eBay guide in Settings; verify actual account production access/terms/quota/$0 data cost. Enable
   privately and verify a genuine request plus a failed/auth-expired request and rate-limit recovery. Record
   timestamps and access scope; do not paste credentials into chat or request an unused paid subscription.
4. Independently reconcile a real exact-product delivered purchase cost and a supported selling route. Validate
   one manual availability attestation and its expiry/source-failure veto. No purchase is authorized by the app.
5. Verify all-in fee scenarios against the operator's current account-specific schedules and actual comparable
   evidence. Amazon/Walmart seller APIs are **not implemented**; build only against accessible docs and granted
   entitlement. Add fee/eligibility integrations without introducing purchases or seller-account mutations.
6. Add a real owned lot, net sale, refund and payout records; reconcile with actual accounting. Then add atomic
   bulk CSV imports, correction/reversal references and richer settlement tracking with independent tests.
7. Expand approved source/categories only after access/variant/condition/geography/retention review. GTIN/MPN
   linking across listing identities needs explicit evidence, not title similarity. Broad scanning and auto
   retailer discovery remain limited; no assertion of finding every profitable item is justified.
8. Add interval-complete data coverage before momentum, market-wide demand or sell-through ratings. Evaluate
   chronological incremental outcomes with/without heat and alternative basket objectives at matched capital.
   Extend planning to supported seasonality only after sufficient dated observations; no dummy calendar forecast.
9. Measure the actual Dell backend/browser RAM, CPU, idle request usage, database growth, sleep/resume/Wi-Fi,
   forced shutdown and disk-pressure recovery. Cloud synthetic measurements are not laptop/power-loss evidence.

Preserve fee/access uncertainty and the distinction between reviewed scenarios and confirmed transactions.
Never edit released schema v1 DDL; add an explicit additive migration. Version and preregister any changed ranking
or optimizer before forward evaluation. Do not promote a heat score to a probability or sizing rule.

## Files to read next

`docs/ARBITRAGE.md`, `FEASIBILITY.md`, `IMPLEMENTATION.md`, `FAULT_REGISTRY.md`, `BUILD_STATUS.md`,
`validation/browser-results.json`, `validation/resources-synthetic.json`; then the corresponding `src/desk/arbitrage`
modules and `tests/unit/test_arbitrage*.py`. The consolidated `MASTER_PROMPT.md` remains the full target specification.

## Product-led usability correction — 2026-10-22

Operator feedback: the first release was too much an accounting console and too little a sourcing tool.
First correction adds image-led cards (optional eBay-owned thumbnails; older records remain compatible),
local title/category/availability filters, next-step cues, a find→compare→sell checklist, explicit source-search
links, and progressive disclosure for advanced capital limits. Source links are manual research, NOT integrations.
Google Shopping, Slickdeals, Walmart, Target, Amazon and eBay sold-search links do not create price, stock,
completed-sale or demand evidence in the app. No automatic sourcing breadth improvement is claimed.

The substantive unfinished requirement is still broader automatic discovery and exact-product cross-source
matching with dated costs, stock and resale evidence. Do not present this visual correction as completing it.
Cloud policy currently excludes retailer/eBay API hosts, and no production eBay credentials are available here.
Photo loading is browser-side HTTPS, no-referrer and lazy; no server image proxy or credentialed request.
Next: build supported structured imports/feeds and buyer-oriented destination comparison with account-specific
fees; verify genuine live vertical slices on permitted sources. Keep manual work visible and minimize it.


## Product/workflow correction v2 — 2026-10-22

The first card correction did not address discovery and decision workflow sufficiently. Read
`PRODUCT_CORRECTION.md` before continuing. Neutral charcoal/white/blue UI now replaces green surfaces; home ranks
by positive current stress-case dollars, independently by ROI, or by six-prior-day observed asking-price drops.
Current ranking cases retain their selected source/observation/currency. Research-only items have no numeric
profit rank. Product pages compare destination alternatives, show remaining checks, accept normal percentage
fees and preserve the selected product. Missing costs can be saved for continued research. Technical IDs are
generated for normal offer/search/comparison entry; per-form IDs remain stable on retries.

Two fixed public RSS/Atom feed candidates (Slickdeals/DealNews) seed automatically. They are NOT accepted live feeds
yet: current cloud policy excludes their hosts, provider documentation/terms/actual payloads have not been read.
The parser/fetch/schedule are tested with synthetic HTTP only. Do not claim successful production data access.
Feeds produce separate DealLead records, never price/stock/sales/qualified routes. Additive discovery schema
preserves the released v1 checksum and all inventory. Cache, time/age, dedup/save/dismiss, robots/access/size errors,
15-minute cadence, conditional GET and Retry-After are bounded. One feed failure does not block the other.
A lead can become a locally reviewed source offer via a short form; its stock/identity stay unverified.

Image CSP now permits only fixed approved source hosts for Arbitrage; existing desks retain their default policy.
Later source thumbnails enrich product display without changing reviewed identity. Browser tests now require
decoded images rather than only an img element. `browser_discovery.mjs` extends original workflow tests.
All showroom content/images are explicitly SYNTHETIC, with intercepted provider-host image bytes.

New update command `desk arbitrage dashboard --restart --open` uses the existing pidfd/kernel-lock identity checks
for this desk only. Test coverage includes a real temporary server, preservation of Pause and refusal to signal
an unrelated lock owner. No operator process was accessed or restarted.

Highest remaining priority: verify a permitted real-source → exact product → independently supported destination
sale/fee evidence slice. Broad automatic exact matching, confirmed-sales history and seller fee/eligibility APIs
remain unfinished. Search links, public-feed mentions and synthetic browser success are not those capabilities.
