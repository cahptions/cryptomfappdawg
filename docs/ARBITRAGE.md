# KESTREL Arbitrage — starting the separate app

Run these commands in your normal terminal. They switch to the new tested feature branch and install/open the
Arbitrage application-menu icon. They do not replace your runtime data or require stopping Stocks.

```bash
cd ~/kestrel
git fetch origin
git switch codex/arbitrage-v1
git pull --ff-only origin codex/arbitrage-v1
uv sync --frozen
uv run desk open --desk arbitrage --install
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

1. Open **Business settings**. Enter your actual separate business capital, reserve, region and risk limits.
   Capital includes inventory at cost. Inventory currency cannot be changed after its first event. This is not the Stocks/Crypto $500 reference account.
2. Read **Data connections & setup guides** on that same page. Every provider has links, steps, costs/access
   caveats and implementation status. No paid subscription is needed or authorized by the build.
3. The included narrow retailer adapter needs no API key. Inspect **Sources & system** for its first request,
   actual error or observation. Access denial does not mean stock is available or that the retailer is down.
4. For broader active listings, follow the **eBay Browse API** guide. Use approved **Production** app credentials,
   not your eBay login or Sandbox keys. Confirm permitted research/retention use and no added data charge before
   enabling it. Save in the private connection form. You do not stop the terminal or press Ctrl+C to save.
   Blank credential fields preserve saved values; presence is shown separately from a successful source request.
5. Enabling eBay adds three starter searches. The collector uses them automatically when collection is enabled.
   Add/pause individual keyword searches on **Sources & system**. Search results are active asking-price listings,
   not proof of completed sales, exact SKU equivalence or deliverability.
6. Open a product dossier. Review brand/model or valid GTIN, variant, pack count and condition. Record a real
   regional delivery/price/quantity check. This attestation expires for qualification after five minutes.
7. Add a selling-route scenario: destination, fulfillment, conservative prices, all fees/costs, evidence-supported
   quantity and cash-recovery timing. Confirm seller/category approval and acceptable sourcing documents.
8. **Where to sell** compares those scenarios. **Capital planner** chooses a bounded conservative basket only
   when both evidence and capital constraints pass. **Top opportunities** contains up to 100 qualified products with reviewed routes;
   category selections show up to ten. Neither view is filled with made-up rows.
9. Pause unused selling routes on **Where to sell** if the comparison-limit message appears. This preserves
   their inputs and lets the remaining bounded calculation resume.
10. Record owned stock and actual net sale/refund outcomes in **Inventory**. Returns remain quarantined until
   restocked. Add dated own-sales/attention evidence separately; price observations never become sales counts.

FBA means Amazon stores and fulfills approved inventory for fees. WFS is Walmart's corresponding fulfillment
service. Seller approval, fees and source documentation are separate from developer API access. Amazon/Walmart
seller APIs and premium history are not yet connected; the app explains this rather than requesting unused keys.

## What can be empty, and why

The working build intentionally ships without sample business observations. Automatic retailer access must
succeed, and eBay needs approved production access. Confirmed market sales history is not included in Browse;
own dated records or a separately authorized licensed source are required. Heat can remain unknown when its
cohort or history is insufficient. Momentum and sell-through are explicitly deferred pending adequate data.
Collection, research and record entry remain usable while some inputs are missing. No 24-hour wait blocks the UI.

Source parsing is tested with synthetic documents but this cloud session could not access the actual retailer
pages through its policy. Production eBay tests also need credentials kept on your laptop. Do not mistake unit
tests, synthetic screenshots or stored URLs for a verified live feed or business profit.

## Controls and diagnostics

Pause/resume collection on **Sources & system**; it persists across app restarts. Settings saves do not require a
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
