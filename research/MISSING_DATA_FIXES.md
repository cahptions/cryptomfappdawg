# SPY and Arbitrage missing-data repair — 2026-10-22

The operator's screenshots showed the previous interface: SPY had stored bars but an empty company-analysis
screen, and Arbitrage had hundreds of deal mentions but only the single seeded product. Those are different
inputs. A working feed did not establish a working product-import pipeline.

SPY now gets an ETF market profile even when its listing classification is absent. The profile calculates
session returns, complete-window volatility, moving averages, volume and coverage from existing stored bars.
Company P/E, earnings and insider panels are omitted for ETFs. Trend/risk observations are descriptive, without
an invented committee score or profitability probability. Longer lookbacks stay missing until enough real sessions
exist. Pointer/keyboard/touch price inspection remains tested. SPY and QQQ chains are included at the start of
every scheduled configured options pass, before long optional backfills; held contracts remain included. No real
Schwab credentials were available here, so actual laptop chain retrieval still depends on its connection.

Arbitrage now seeds eight fixed Adafruit category-microdata monitors and a US Arduino catalog. These import
actual published prices, currencies, product URLs, photos and stock declarations in batches, without per-product
forms or an eBay key. New catalog checkpoints drain on a shorter bounded startup schedule; category monitors
refresh every 15 minutes. Existing individual/global Pause and reviewed product identity are preserved. An old
sitemap truncation dropped 665 URLs after its first 5,000; the larger bounded queue now retains them.

Actual normal collector acceptance into isolated cloud data:

- First pass: **1,328 distinct products priced in USD**.
- After three passes: **2,526 distinct priced products** (2,409 USD; 117 EUR), plus 5,655 pending merchant URLs.
- This was not the synthetic 10,000-product capacity test. See `arbitrage/validation/live-catalog-results.json`.
- The run exposed a Unicode product-handle rejection that blocked the US catalog's second page. Encoding the
  permitted Unicode handle fixed it; a fresh actual page-2 request imported 108 offers and advanced to page 3.
  See `arbitrage/validation/live-page2-results.json`. The original run retains that pre-fix error for provenance.
- One oversized category remains explicitly unavailable at the 2 MB response bound. It does not halt other sources.
- The actual real-data dashboard and real CDN photos were checked in Chromium; no image interception or generated
  product data. See `arbitrage/validation/live-browser-results.json` and the two `live-*.png` screenshots.

The header now reports the full product catalog, rather than the 50-row page size. Checked-route counts are
labeled as page-specific. Available-at-last-check offers receive priority over out-of-stock archive entries;
stock is still dated and must be refreshed before reliance. Settings exposes workflow version `bulk-catalog-v3`.

These products are acquisition research. Completed-sale history, all-in resale costs, shipping/tax, seller
eligibility and actual regional availability are not implied. Missing economics stay missing. The developer
account/paid-data budget was not changed and no purchase, listing or brokerage order was placed.

Validation: **1,244 offline tests passed**, five opt-in live-provider tests excluded (139.89 seconds); Ruff and strict
Mypy passed across 232 source files. Current desktop/mobile browser checks passed for Stocks ETF/chart/navigation
and Arbitrage forms/discovery. The separate real-data browser check passed with actual CDN image downloads.
The synthetic 10,000-product capacity check measured roughly 0.05 seconds per catalog page and 35.5 MiB peak
process RSS; these are cloud measurements, not Dell power-loss or business-performance evidence.

After the branch is published, update once and restart both apps in the background:

```bash
cd ~/kestrel &&
git fetch origin codex/arbitrage-v1:refs/remotes/origin/codex/arbitrage-v1 &&
git switch codex/arbitrage-v1 &&
git pull --ff-only origin codex/arbitrage-v1 &&
uv sync --frozen &&
uv run desk open --desk stocks --restart &&
uv run desk open --desk arbitrage --restart
```

`desk open --restart` uses the existing exact-user/exact-dashboard kernel-lock restart helper. It does not require
a separate persistent terminal and never uses a broad process-name kill. If Git reports conflicting local work,
preserve it and resolve the conflict; do not reset data or force a pull.
