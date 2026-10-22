# Arbitrage V1 build checkpoint — 2026-10-22

Branch: `codex/arbitrage-v1`, based on tested Stocks dashboard restart fix `e9dcc5a`.

The working software slice is implemented and tested. **Production data and business acceptance remain open.**

Completed:

1. Repository/interfaces/hardware constraints inspected; separate local data, credentials and locks selected.
2. Source access evidence and honest limitations recorded in `FEASIBILITY.md`; additive cloud domain draft saved.
3. Labeled mockup rendered and shown; implemented UI follows that layout without shipping fake business data.
4. Contracts, decimal economics, conservative integer capital planner, exact identity/GTIN checks, explicit
   currency guards, inventory accounting, descriptive comparable-cohort heat and prospective scenario archive.
5. Bounded robots-aware retailer adapter; optional eBay production Browse; owner-only in-app private credentials,
   automatic starter searches, persistent quota/global rate-limit backoff, pause/resume, safe cache turnover.
6. Nine page routes, actual forms, asking/reported-sales dot charts, source/route controls, API links/setup guides,
   separate app launcher, automatic bounded collector and bounded read-only diagnostic.
7. 1,192 offline tests passed (five live tests excluded), Ruff clean, strict Mypy clean on 225 source files,
   JavaScript syntax clean. Desktop/390px mobile browser workflows pass. Synthetic 500-product/5,000-observation/
   500-route snapshot: 0.0796 s, 46.2 MiB peak process RSS on cloud executor; no laptop or real-market claim.

Protected inventory, manual data and reviewed items cannot be evicted to make room for API cache. A 5,000-pair
comparison guard prevents oversized evaluation; unused routes can be paused without losing their inputs.

Still open: actual source/robots/markup verification through a permitted network; eBay production access kept
private on the laptop; approved completed-sale history; seller API fee/eligibility integrations; real financial
reconciliation/forward outcomes; richer interval-complete momentum/sell-through and seasonality; atomic bulk imports.
These are not simulated into success or labeled as connected.

Start with `docs/ARBITRAGE.md`. Detailed next steps and boundaries are in `HANDOFF.md`. Test evidence, scripts and
synthetic screenshots live in `validation/`. Machine-readable contracts are in `CONTRACTS.json` and the app API.


## Product correction v2

The operator rejected V1's usability and discovery coverage. `PRODUCT_CORRECTION.md` records the diagnosis,
mechanisms, UI changes and remaining source acceptance. A new discovery inbox, bounded public-feed candidate
adapters, independent profit/ROI/price-drop rankings, corrected source images, destination comparison/editing,
normal percentage inputs and safe Arbitrage-specific update restart are implemented. Neutral charcoal/white/blue
replaces the former green surfaces. Required stock, identity, eligibility, cost and capital gates remain in place.

Final checks are in `validation/checks.json`, with new flows in `browser-discovery-results.json`. Updated synthetic
showroom screenshots are views of the real frontend with clearly labeled intercepted test images, not production
market opportunities. Real source-to-resale acceptance remains OPEN; do not infer it from these checks.
