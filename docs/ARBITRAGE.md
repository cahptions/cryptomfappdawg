# KESTREL Arbitrage — starting the separate app

Arbitrage ships in the same checkout as Stocks; `kestrel update` keeps it current. There is no branch to switch to.
On the computer you want it on, install its menu icon (lime green) and open it once:

```bash
cd ~/kestrel
uv run desk open --desk arbitrage --install
```

After that, open **KESTREL Arbitrage** from the application menu. After an update, load the new code with
`uv run desk open --desk arbitrage --restart`; it preserves your runtime data and does not stop Stocks. The
application is also available at `http://127.0.0.1:8768`. To run it visibly in a terminal instead:

```bash
cd ~/kestrel
uv run desk arbitrage dashboard --open
```

An already responding Arbitrage dashboard is reused. Its collector starts automatically unless you previously
paused it. The new desk has independent data, credentials and locks. Its icon opens a separate browser app window.

## What it does

KESTREL Arbitrage **finds deals by itself** and ranks them by profit after every fee, and it answers one question fast in
a store: **is this worth buying to resell, and how many?**

### The finders (automatic)

| Finder | What it looks for | How often |
|---|---|---|
| eBay listings under their sold prices | For each hunt in Settings (30 LEGO sets, sneaker style codes, sealed Pokémon products), the cheapest Buy It Now listings from US sellers whose total is under the same item's 90-day sold median. Box-only, lots, parts, customs and the like are skipped. | each hunt every 12 hours |
| Gold & silver listings | `metal:` hunts (10k/14k scrap, rings, chains, sterling): listings whose title states weight and karat, priced under what a scrap buyer pays for the melt. Plated, filled and "tone" items are skipped. | every 12 hours |
| Deal posts | Slickdeals (front page, LEGO, sneakers, Pokémon) and DealNews posts naming a LEGO set number or sneaker style code, against that item's sold prices. | every 30 minutes |
| Rising Pokémon cards | Cards in the 14 newest sets whose last week of sales averages 15%+ over their 30-day average (Cardmarket sale averages; US value is TCGplayer's market price) → Buy & Hold. | daily |

The status of each finder (last run, what it found, or why it found nothing) is at the top of **Deals**, with
**🔎 Search now** to run whatever is due without waiting. A full pass of all hunts takes about an hour because eBay is
read slowly. Found items stay in Deals while they're seen again (3 days), with **Buy on eBay ↗** (or the deal post)
and **Not interested**. A BUY needs at least 5 sales behind the price (or a marketplace's own recent-sales price);
fewer is at most a MAYBE.

### Checking something yourself

- **The check bar** (always at the top): pick what you're checking — Anything (scan or type a barcode, or the item's
  name), LEGO (set number), Pokémon or Magic (card name), Sneakers (style code), or Gold & silver — type the price you
  see and how many are on the shelf, and press Check. 📷 scans a barcode where the browser supports it (Chrome on
  Android; the page must be opened as `localhost` or over HTTPS).
- **The card it gives back**: what you pay (with your sales tax), what it sells for (the median buyer total of the
  last 90 days of sales), what you keep after eBay's fees and your shipping label, the profit in dollars and percent,
  how fast it sells, a five-check confidence meter (enough sales, same item, steady price, sells fast, fresh prices),
  and a verdict: **BUY** (at least 30% and $10 profit with 3+ checks; both thresholds are in Settings), **MAYBE** or
  **SKIP**, with how many to buy — what sells within a month, never more than the shelf has or a quarter of your
  bankroll.
- **Deals**: every item you've checked, BUY first, then by profit share. Filter by clearance, LEGO, cards, sneakers.
- **Buy & Hold**: items whose sold prices are rising at least 10% a year (from 90 days of dated sales, or a month of
  a card's market prices), steepest first. ☆ Watch an item and its sold prices refresh daily in the background.
- **Gold & silver**: karat or silver purity, grams and tag price, against melt value at the live spot price and what
  a scrap buyer pays (80% of melt by default). The page lists the most to pay per gram for each purity.

## Where prices come from

| What | Source | Notes |
|---|---|---|
| eBay sold prices | eBay's own sold-results page, read from your computer | Your choice, knowing eBay's rules and robots.txt disallow automated reading. One search every 20 seconds at most, an honest identity (`KESTRELArbitrage/2.0`), reused for 12 hours. If eBay refuses or shows a robot check, reading pauses 6 hours; nothing tries to get around it. |
| eBay sold prices, by hand | Paste | On eBay's sold results (the card's **eBay sold ↗** link): select all, copy, paste into the card. Same result, always available. |
| Pokémon / Magic cards | TCGplayer market price (from its recent sales) via the Pokémon TCG API (TCGdex when it fails) and Scryfall | Free, no keys. |
| Gold & silver spot | gold-api.com | Free, no key; read at most every 30 minutes. |
| Barcode names | UPCitemdb free trial (100 a day) | Only the name; eBay matches the barcode itself. An unknown barcode is named from its sales. |

eBay fees used (US, editable later): 13.6% of the buyer's total including the sales tax eBay collects, plus $0.40 an
order ($0.30 at $10 or less); trading cards 13.25%; jewelry 15%; athletic shoes $150+ 8% with no order fee.
Amazon and Walmart publish no sold prices; Amazon's price history (Keepa) is paid and isn't used.

To see whether your computer can read eBay's sold pages: `kestrel ebay-check` (one request; it prints how many sales
it read, or why not). If it reads none on a page eBay did answer, it saves the page and prints a line to send to
Claude so the reader can be adjusted.

## Controls and diagnostics

- `kestrel arbitrage` opens the app, restarted on the latest code; `kestrel update` also stops an Arbitrage app that
  is running so the icon starts the updated one.
- `kestrel ebay-check [words]` tests eBay's sold pages from this computer (one request).
- **Settings** in the app: your sales tax, the sales tax eBay charges buyers, the BUY thresholds, the scrap payout
  share and an optional bankroll. It also shows whether eBay reading is paused and whether the background refresh is
  running.
- `uv run desk arbitrage diagnose` reads local status without any network request.

Runtime database: `~/desk-data/arbitrage/db/arbitrage.db` by default (`hunt_*` tables hold items, every price check
and every set of sold prices; sold prices are append-only). The earlier beta's catalog tables are kept but its
hobby-store collector no longer starts. No purchase, reservation, listing or repricing action exists in this desk.

History of the beta: [implementation](../research/arbitrage/IMPLEMENTATION.md),
[feasibility](../research/arbitrage/FEASIBILITY.md), [handoff](../research/arbitrage/HANDOFF.md).
