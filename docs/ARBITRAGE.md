# KESTREL Arbitrage — starting the separate app

Run these commands in your normal terminal. They switch to the tested feature branch and gracefully restart
Arbitrage to load the new code. They preserve your runtime data and do not stop Stocks.

```bash
cd ~/kestrel
git fetch origin
git switch codex/arbitrage-v1
git pull --ff-only origin codex/arbitrage-v1
uv sync --frozen
uv run desk arbitrage dashboard --restart --open
```

If Git reports local changes and refuses to switch, preserve them; do not reset or overwrite them. The application
is also available at `http://127.0.0.1:8768`. To run it visibly in a terminal instead:

```bash
cd ~/kestrel
uv run desk arbitrage dashboard --open
```

An already responding Arbitrage dashboard is reused. Its collector starts automatically unless you previously
paused it. The new desk has independent data, credentials and locks. Its icon opens a separate browser app window.

## First use in the app

1. **Discover** opens first. Its ranked shortlist separates modeled dollar profit, ROI and observed source-price
   drops. Fresh community/editorial deal mentions appear in a separate discovery inbox after source access succeeds.
   You do not have to fill in every setting to browse or begin research.
2. Open **Settings** to see the actual state of each automatic feed/monitor. Public feed candidates need
   no API key; their real-network acceptance is pending. A blocked or broken source shows its error and retains
   already recorded work. No sample products are substituted for a failed source.
3. **Settings → Data connections & setup guides** explains each source, free-access conditions and limits.
   Optional eBay active-listing discovery requires your approved Production app credentials. Save them privately
   in the app; blank fields preserve saved credentials. Do not press Ctrl+C to save credentials.
4. **Check this deal** opens the original published lead. Follow it to the merchant, then enter the actual product
   URL and price you observe. This creates a research item with unknown stock; it does not auto-approve a purchase.
   Save and dismiss keep the inbox manageable without deleting a researched item.
5. The product research page carries that same item into **Compare destination**. Enter supported resale/base and
   downside prices, fees as a percentage, fulfillment, return allowances, overhead/labor value, hands-on time and
   cash-cycle estimates. Unknown costs may remain blank; they block calculated profit and purchasing readiness.
   Use **Edit assumptions** to continue the same comparison rather than creating duplicates.
6. Review exact model/GTIN, variant, pack and condition, seller eligibility and sourcing documents. The research
   page shows specific remaining checks beside each destination. Research sold-listing links are external evidence
   gathering, not an imported confirmed-sales feed.
7. Record a fresh regional delivery/price/quantity check before relying on stock. It expires for qualification
   after five minutes. Failed source access also invalidates an earlier stock check.
8. **Where to sell** compares your covered alternatives. A best checked destination appears only when required
   checks pass. Main-page numeric profit ranks require current source observations and positive downside economics;
   other products remain visible as research, without a numeric profit rank.
9. Set your actual separate business capital/reserve in **Settings** when you want a capital plan.
   Capital includes inventory at cost; this is independent of all $500 trading reference accounts. **Capital planner**
   applies cash, quantity and concentration constraints. A high unit margin does not authorize exceeding them.
10. Record owned stock and actual net sales/refunds in **Inventory**. Returned units stay quarantined until
    restocked. Asking prices, attention and feed mentions never become completed-sales quantities.

The separate application-menu icon remains available. Install it once with
`uv run desk open --desk arbitrage --install`. The explicit `--restart` above gracefully replaces an older
Arbitrage dashboard after an update, without stopping Stocks/Crypto or deleting a lock file. Ordinary opens reuse
an existing dashboard and therefore cannot hot-load changed Python code.

FBA means Amazon stores and fulfills approved inventory for fees. WFS is Walmart's corresponding fulfillment
service. Seller approval, fees and source documentation are separate from developer API access. Amazon/Walmart
seller APIs and premium history are not yet connected; the app explains this rather than requesting unused keys.

## What can be empty, and why

The working build ships without sample business observations. Public feed endpoint candidates and automatic retailer access must
succeed, and eBay needs approved production access. Confirmed market sales history is not included in Browse;
own dated records or a separately authorized licensed source are required. Heat can remain unknown when its
cohort or history is insufficient. Momentum and sell-through are explicitly deferred pending adequate data.
Collection, research and record entry remain usable while some inputs are missing. No 24-hour wait blocks the UI.

Source parsing is tested with synthetic documents but this cloud session could not access the actual retailer
pages through its policy. Production eBay tests also need credentials kept on your laptop. Do not mistake unit
tests, synthetic screenshots or stored URLs for a verified live feed or business profit.

## Controls and diagnostics

Pause/resume collection on **Settings**; it persists across app restarts. Settings saves do not require a
terminal shutdown. Closing a browser window need not stop its detached dashboard. A terminal-launched dashboard
stops when that terminal process is interrupted. The laptop must remain powered and connected to collect; sleeping
does not create observations for the missed period. Tailscale alone does not provide another always-on computer.

For a bounded local report without provider calls, credentials or database migrations:

```bash
cd ~/kestrel
uv run desk arbitrage diagnose
```

Runtime database: `~/desk-data/arbitrage/db/arbitrage.db` by default. Make a consistent SQLite backup before manual
repair; do not delete locks, databases or private files to clear a worker error. No purchase, reservation, listing,
repricing or broker order action exists in this desk.

Detailed architecture, math and limits: [implementation](../research/arbitrage/IMPLEMENTATION.md).
Data access evidence: [feasibility](../research/arbitrage/FEASIBILITY.md).
Outstanding acceptance and continuation: [handoff](../research/arbitrage/HANDOFF.md).


## Current development correction (2026-10-22)

Source connection setup, URLs, saved searches, private credentials and setup guides belong in **Settings**.
**Sources** browses collected products by actual retailer/marketplace host, with server-side filtering and pagination.
The sourcing shortlist defaults to compact rows and supports cards. Buy/landed costs appear alongside explicitly
classified comparable low, average, high and median prices; missing evidence stays unknown.

The new public catalog candidates and optional exact-identity eBay resale research are implemented against
synthetic payloads. Real endpoint access/compatibility and the new browser workflows are not yet accepted.
Read `research/arbitrage/REBUILD_CHECKPOINT.md` and `validation/rebuild-checks.json` before treating this revision as
production-ready. Historical browser screenshots/results in `validation/` describe the earlier baseline.
