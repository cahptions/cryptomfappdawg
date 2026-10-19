# RESEARCH.md — Verified Infrastructure Facts (M0)

**Research window:** 2026-10-18, 21:50–22:10 UTC
**Researcher environment:** Claude Code cloud container (Linux, shared cloud egress IP behind a proxy).
**Not** the operator's laptop — see §0.2 for why some measurements must be repeated at home.

Every claim below carries an evidence tag:

| Tag | Meaning |
|---|---|
| **[MEASURED]** | Observed in a live, read-only request during M0. Raw response saved under `research/evidence/2026-10-18/`. |
| **[DOCUMENTED]** | Stated in the provider's official documentation or official machine-readable spec (URL given). |
| **[SECONDARY]** | Third-party article/blog. Treated as a hint only, never as a design input. |
| **[ASSUMPTION]** | Reasonable default not yet verified. Each has a milestone where it must be verified. |
| **[TO VERIFY @Mx]** | Explicitly open; the named milestone must resolve it before depending on it. |

---

## 0. Headline findings (read this first)

1. **Execution cost is the dominant obstacle.** Jupiter quote-implied round-trip loss on live memecoins with $22k–$59k
   pool liquidity was **2.2%–4.1%** for 0.1–1 SOL tickets [MEASURED]. That is *before* network fees, priority fees,
   latency drift beyond the ~3.5 s between quotes, MEV, and failed transactions. Any strategy must beat roughly a
   **3–5% per-trade hurdle** just to break even. This shapes every design decision.
2. **Sub-second sniping is not feasible with this infrastructure.** GeckoTerminal caches all data for 60 s
   [DOCUMENTED]; DexScreener sends `cache-control: max-age=30` [MEASURED]; public RPC adds ~150–400 ms per call
   [MEASURED]. The plan targets **minutes-to-hours** holding periods instead.
3. **GeckoTerminal's free limit is ~10 calls/min and erratic** [DOCUMENTED + MEASURED] — far below the 30/min many
   tutorials quote. It cannot be the polling workhorse.
4. **DexScreener allows 300 req/min with 30 addresses per call** on pair/token endpoints [DOCUMENTED] — this is the
   refresh workhorse. It **omits the `liquidity` field entirely** for bonding-curve pairs [MEASURED] (absent ≠ zero).
5. **New pump.fun mints are Token-2022** with `metadataPointer` + `tokenMetadata` extensions [MEASURED]. A naive
   "reject Token-2022" rule would reject nearly the whole universe; we need an **extension allow-list**.
6. **Public Solana RPC refused `getTokenLargestAccounts` 4/4 times with HTTP 429** [MEASURED, from a shared cloud IP].
   Holder-concentration analysis therefore needs a keyed RPC (free tier is sufficient — see §3.3) or must be
   re-measured from the operator's home IP.
7. **Jupiter Tokens v2 exposes rich, unaudited "provider claims"**: dev wallet, `devMints` (we observed devs with
   **1,852** and **13,477** prior mints), `topHoldersPercentage`, distinct trader counts, and "organic" volume. On one
   active token only ~2% of 5-minute volume was labelled organic [MEASURED]. Definitions are undocumented → use as
   features with provenance, never as ground truth.
8. **The raw launch rate is ~2 tokens/second** [MEASURED via Jupiter `/tokens/v2/recent` + pump.fun log stream].
   Subscribing to the full pump.fun log firehose costs **~140–234 msgs/s, ~217 KiB/s ≈ 19 GB/day** download
   [MEASURED] — not justified for a laptop that cannot win the earliest window anyway.
9. **Token-account rent is now 0.00149 SOL (165 B) / 0.00151 SOL (170 B Token-2022)** [MEASURED] — lower than the
   ~0.00204 SOL figure in older tutorials. Refundable when the account is closed.

### 0.1 Reference prices at research time
- SOL ≈ **$101.8** (0.1 SOL → 10.181166 USDC via Jupiter, 21:54 UTC) [MEASURED]. Used only for order-of-magnitude
  cost estimates in PLAN.md; the system will always use live prices.

### 0.2 Why some measurements must be repeated on the operator's laptop
The research container shares an egress IP with other workloads. IP-based limits (public RPC, GeckoTerminal,
Jupiter keyless) may be **stricter here than at the operator's home**. M1 includes a `desk doctor` command that
re-measures latency and rate-limit behaviour from the real machine (Linux Mint 22.3, Wi-Fi RTL8821CE). Until then,
the M0 numbers are treated as **pessimistic but plausible**.

---

## 1. GeckoTerminal (public API v2)

**Base URL:** `https://api.geckoterminal.com/api/v2` [DOCUMENTED]
**Spec:** `https://api.geckoterminal.com/docs/v2/swagger.json` (title "GeckoTerminal API V2", version `v2-beta`) [DOCUMENTED]
**Auth:** none for the public API [MEASURED].

### 1.1 Limits and freshness
| Item | Value | Evidence |
|---|---|---|
| Rate limit | "approximately **10 calls/minute**, which may fluctuate based on network traffic" | [DOCUMENTED] swagger `info.description` |
| Measured behaviour | 16 distinct uncached requests at ~1 per 1.7 s → **7×429, 9×200**; 429s carried `retry-after: 0` (useless) | [MEASURED] |
| Data freshness | "All endpoints listed below are cached for **1 minute**"; updates "as fast as 2–3 seconds" after confirmation | [DOCUMENTED] |
| HTTP caching | `cache-control: max-age=30, public, must-revalidate, s-maxage=60`; Cloudflare `cf-cache-status: HIT`, `age: 48` observed | [MEASURED] `response_headers_ratelimit_cache.json` |
| API status | Beta; "subject to frequent changes"; pin version via `Accept: application/json;version=20230203` | [DOCUMENTED] |
| Paid upgrade | CoinGecko paid plans expose these as `/onchain` endpoints with higher limits; price **not verified** (pricing page returned 403 to our fetcher) | [DOCUMENTED] link; price [TO VERIFY if ever needed] |

**Design consequence:** budget ≤ 8 calls/min globally, adaptive backoff on 429 (ignore `retry-after: 0`, use own
exponential schedule), never poll an endpoint faster than its 60 s cache.

### 1.2 Endpoints relevant to Solana (from spec) [DOCUMENTED]
| Endpoint | Use | Notes |
|---|---|---|
| `GET /networks/solana/new_pools?page=N` | Discovery of pool creations incl. migrations | 20 pools/page [MEASURED], page max 10 |
| `GET /networks/solana/trending_pools?duration=5m\|1h\|6h\|24h` | Discovery of active pools | page max 10 |
| `GET /networks/solana/pools?sort=h24_volume_usd_desc\|h24_tx_count_desc` | Top pools | no sort by creation time |
| `GET /networks/solana/dexes/{dex}/pools` | Top pools per DEX | same sort options |
| `GET /networks/solana/pools/multi/{addresses}` | Batch refresh | **up to 30 addresses** |
| `GET /networks/solana/tokens/multi/{addresses}` | Batch token data | up to 30 addresses |
| `GET /networks/solana/pools/{pool}/ohlcv/{day\|hour\|minute}` | Historical bars | aggregate minute: 1/5/15; hour: 1/4/12; day: 1; `limit` ≤ 1000; `before_timestamp` |
| `GET /networks/solana/pools/{pool}/trades` | Last 24 h trades | free tier: no cursor/per_page (Pro only) |
| `GET /networks/solana/tokens/{addr}/info` | Token metadata | |
| `GET /simple/networks/solana/token_price/{addresses}` | Price batch | up to 30 |

### 1.3 Verified response fields (`new_pools`, `trending_pools`) [MEASURED]
Evidence: `geckoterminal_new_pools_p1.json`, `geckoterminal_trending_pools_1h.json`.
- `attributes.address`, `name`, `pool_created_at` (ISO-8601 UTC, e.g. `2026-10-18T21:52:51Z`)
- `base_token_price_usd`, `quote_token_price_usd` (decimal **strings**, up to ~70 significant digits → parse as `Decimal`)
- `fdv_usd` (string), `market_cap_usd` (**often `null`**)
- `reserve_in_usd` (**can be `null`** and can be `"0.0"` for an active pool) → `null` = MISSING, `"0.0"` = suspicious, flag
- `price_change_percentage.{m5,m15,m30,h1,h6,h24}` (strings)
- `transactions.{m5,m15,m30,h1,h6,h24}.{buys,sells,buyers,sellers}` — **`buyers`/`sellers` are distinct-address counts**
  (e.g. 73 buys from 67 buyers) — useful for "transactions ≠ traders"
- `volume_usd.{m5,...,h24}` (strings)
- `relationships.base_token.data.id` = `solana_<mint>`, `quote_token`, `dex.data.id` (e.g. `pump-fun`, `pumpswap`,
  `meteora-dbc`, `meteora-damm-v2`, `raydium`, `raydium-clmm`, `meteora`, `stonkfun`)
- For a pool younger than the window, all windows (m5…h24) hold the same values — windows are "since creation", not
  a full window. **Features must use pool age to normalise.**

### 1.4 Observed universe characteristics [MEASURED]
- Page 1 of `new_pools` (20 pools) spanned only **~40 seconds** of creations (21:52:18 → 21:52:51) — i.e. ~30 pools/min,
  overwhelmingly `pump-fun` bonding curves.
- A **new pool is often not a new token**: `CATE / SOL` appeared as a new `pumpswap` pool and a new `meteora-damm-v2`
  pool while the token's top pool dated from 2026-07-26. Token and pool identity must be modelled separately.
- Trending (1 h) list contained tokens aged 1 hour to >1 year, liquidity $22k–$22M — a plausible "survivor" universe.

---

## 2. DexScreener

**Base URL:** `https://api.dexscreener.com` [MEASURED]
**Spec:** `https://openapi.gitbook.com/o/F3EJQhITOlKtqJoq3nND/spec/dex-screener-api.yaml` (linked from
`https://docs.dexscreener.com/api/reference`) [DOCUMENTED]
**Auth:** none [MEASURED].

### 2.1 Endpoints and limits [DOCUMENTED]
| Endpoint | Limit | Notes |
|---|---|---|
| `GET /latest/dex/pairs/{chainId}/{pairId}` | **300 req/min** | one or multiple pairs |
| `GET /tokens/v1/{chainId}/{tokenAddresses}` | **300 req/min** | **up to 30** comma-separated token addresses |
| `GET /token-pairs/v1/{chainId}/{tokenAddress}` | 300 req/min | all pools of a token |
| `GET /latest/dex/search?q=` | 300 req/min | |
| `GET /token-profiles/latest/v1` | 60 req/min | **paid** profiles → promotion signal, biased source |
| `GET /token-boosts/latest/v1`, `/token-boosts/top/v1` | 60 req/min | **paid** boosts → promotion signal |
| `GET /orders/v1/{chainId}/{tokenAddress}` | 60 req/min | paid-order check |
| `GET /community-takeovers/latest/v1`, `/ads/latest/v1`, `/metas/...` | 60 req/min | |

There is **no "new pairs" endpoint** → DexScreener cannot be a neutral discovery source; it is the refresh engine.

### 2.2 Verified pair fields [MEASURED]
Evidence: `dexscreener_tokens_v1_10mints.json` (10 fresh mints requested → **9 pairs returned**; one not yet indexed).
- `chainId`, `dexId` (`pumpfun`, `meteoradbc`, … — **different spellings from GeckoTerminal** → need a DEX-ID mapping table)
- `pairAddress`, `baseToken{address,name,symbol}`, `quoteToken{...}`
- `priceNative`, `priceUsd` (strings)
- `txns.{m5,h1,h6,h24}.{buys,sells}` — **no distinct-trader counts**
- `volume.{m5,h1,h6,h24}`, `priceChange.{m5,h1,h6,h24}` (numbers)
- `fdv`, `marketCap`, `pairCreatedAt` (epoch **milliseconds**)
- `liquidity{usd,base,quote}` — **absent entirely for bonding-curve pairs** (key missing, not 0)
- `labels`, `info`, `boosts` — optional
- Caching: `cache-control: public, max-age=30` [MEASURED]

---

## 3. Solana RPC

### 3.1 Public mainnet endpoint
`https://api.mainnet-beta.solana.com` (docs also list `https://api.mainnet.solana.com`).

Documented limits [DOCUMENTED — https://solana.com/docs/references/clusters]:
- 100 requests / 10 s per IP; **40 requests / 10 s per IP for a single RPC method**; 40 concurrent connections per IP;
  100 MB data / 30 s.
- "The public RPC endpoints are **not intended for production applications**."

Measured [MEASURED]:
| Call | Result | Latency |
|---|---|---|
| `getHealth` | `"ok"` | 157 ms |
| `getSlot` | 458591147 | 394 ms |
| `getAccountInfo` (mint, `jsonParsed`) | full parsed Token-2022 mint incl. extensions | 252 ms |
| `getTokenSupply` | ok | 219 ms |
| `getTokenLargestAccounts` | **HTTP 429 "Too many requests for a specific RPC call" — 4 of 4 attempts**, spaced 6 s, first attempt included | ~160–410 ms |
| `getMinimumBalanceForRentExemption(165/170/182)` | 1,488,440 / 1,513,840 / 1,574,800 lamports | — |
| `getAccountInfo` on pool accounts | owner program IDs (see §5) | — |

`apiVersion` reported: `4.3.0` [MEASURED].

### 3.2 Public WebSocket [MEASURED]
- `wss://api.mainnet-beta.solana.com` accepted `logsSubscribe({"mentions":[<pump.fun program>]}, confirmed)`.
- 40 s sample: **9,392 notifications (~234/s)**, 16 messages containing `Instruction: Create*`, 1 containing `Instruction: Migrate`.
- 20 s bandwidth sample: **~140 msg/s, ~217 KiB/s → ≈19 GB/day**.
- Log lines do not directly contain the new mint; decoding needs the Anchor event payload (`Program data:` base64) or a
  follow-up `getTransaction` (rate-limited). [TO VERIFY @M2 only if a streaming detector is adopted]
- Sample message: `solana_public_ws_logsSubscribe_pump_sample.json`.

### 3.3 Keyed RPC providers (free tiers)
| Provider | Free tier | Evidence |
|---|---|---|
| **Helius** | $0: **1M credits/month, 10 RPC req/s, 2 DAS req/s**; standard RPC call = 1 credit, `getProgramAccounts`/archival/DAS = 10 credits; LaserStream WSS (standard) only; no `transactionSubscribe`. Developer $49/mo: 10M credits, 50 RPS | [DOCUMENTED] https://www.helius.dev/pricing |
| Others (QuickNode, Alchemy, Triton, Chainstack…) | Not researched in depth in M0 | [TO VERIFY @M1 if Helius unsuitable] |

Credit cost of `getTokenLargestAccounts` on Helius: **not stated** on the pricing page [TO VERIFY @M1].
**Recommendation:** the RPC adapter is provider-agnostic (URL + optional key); default to a free keyed endpoint.

---

## 4. Jupiter

**Developer docs:** `https://developers.jup.ag/docs/` (old `dev.jup.ag` redirects with 301) [MEASURED]

### 4.1 Swap API v2 — current [DOCUMENTED — https://developers.jup.ag/docs/swap]
- **Single entry point** `https://api.jup.ag/swap/v2`; API key via `x-api-key` header from the Portal
  (`developers.jup.ag/portal`).
- **Meta-Aggregator path:** `GET /swap/v2/order` → quote + assembled transaction (when `taker` given) →
  sign → `POST /swap/v2/execute` (Jupiter-managed landing). Routers compete: **Metis, JupiterZ (RFQ), DFlow, OKX**.
  Charges a Jupiter platform fee.
- **Router path:** `GET /swap/v2/build` → raw instructions (Metis only) → own transaction → own RPC or `tx.jup.ag`.
- `/order` parameters [DOCUMENTED — api-reference/swap/v2/order]: `inputMint`, `outputMint`, `amount` (base units),
  `taker`, `receiver`, `swapMode` (**ExactIn only**), `slippageBps` (auto if unset), `priorityFeeLamports`,
  `jitoTipLamports`, `broadcastFeeType`, `excludeRouters`, `excludeDexes`, `payer`, `maxSupportedTransactionVersion`.
- `/order` response fields include: `inAmount`, `outAmount`, `otherAmountThreshold`, `slippageBps`, **`priceImpact`
  (percentage points)**, `routePlan`, `feeBps`, `feeMint`, `platformFee`, `signatureFeeLamports`,
  `prioritizationFeeLamports`, `rentFeeLamports` (+ payers), `router`, `quoteId`, `expireAt`, `transaction`
  (null without `taker`), `lastValidBlockHeight`, `gasless`, `requestId`, `totalTime`, `errorCode`, `errorMessage`.
- `/execute` request/response schema, status values, and requestId validity window are **not documented on the
  pages fetched** [TO VERIFY @M8 — blocking for LIVE, not for PAPER].

### 4.2 Rate limits [DOCUMENTED — https://developers.jup.ag/docs/portal/rate-limits]
| Tier | RPS | RPM | Key |
|---|---|---|---|
| Keyless | 0.5 | 30 | none |
| **Free** | 1 | **60** | required |
| Developer | 10 | 600 | required |
| Launch | 50 | 3,000 | required |
| Pro | 150 | 9,000 | required |
- 60 s **sliding window**; Swap, Price and Token requests **share one bucket**; `/swap/v2/execute` has a separate
  bucket (20 RPS keyless / 50 free / 100 paid). Limits are per account, not per key.
- Headers `x-ratelimit-remaining` (signed; can go negative), `x-ratelimit-current`, `x-ratelimit-reset` (unix s) on
  200/429 responses — **observed on both lite-api and api.jup.ag** [MEASURED].
- Measured: `lite-api.jup.ag` reported a 5-request window (remaining 4 after 1 call); `api.jup.ag/swap/v2/order` keyless
  reported 20 remaining after 1 call; after ~13 calls in ~1 min, `api.jup.ag` returned
  **429 `[API Gateway] Too many requests`** [MEASURED].
- Plan prices: not on the rate-limit page [TO VERIFY only if a paid tier is ever proposed].

### 4.3 Measured quotes [MEASURED]
SOL→USDC 0.1 SOL:
- `lite-api.jup.ag/swap/v1/quote`: 10.181166 USDC, route Raydium CLMM, `priceImpactPct "0"`, 125 ms.
- `api.jup.ag/swap/v2/order` (keyless, no taker): 10.179259 USDC, **router `jupiterz` (RFQ), `feeBps: 2`**, `gasless: true`,
  `transaction: null`, 479 ms total.

Memecoin round-trips via `/swap/v2/order` (buy SOL→token, then ~3.5 s later sell the exact `outAmount` token→SOL)
— evidence `jupiter_v2_order_memecoin_roundtrips.json`:

| Token (pool liq. at the time) | Ticket | Router | Jupiter fee | Buy impact | Sell impact | **Quote round-trip loss** | Buy latency |
|---|---|---|---|---|---|---|---|
| godspeed ($59k, age 1 h) | 0.1 SOL | metis | 10 bps | 3.19 | 0.45 | **3.63%** | 517 ms |
| godspeed | 1.0 SOL | metis | 10 bps | 1.55 | 1.04 | **2.21%** | 245 ms |
| OPEQ ($22k) | 0.1 SOL | metis | 10 bps | 1.99 | 0.87 | **2.84%** | 230 ms |
| OPEQ | 1.0 SOL | metis | 10 bps | 3.20 | 0.93 | **4.10%** | 275 ms |
| DOGESTER ($37k) | 0.1 SOL | metis | 10 bps | 1.17 | 1.41 | **2.57%** | 423 ms |
| DOGESTER | 1.0 SOL | metis | 10 bps | 1.64 | 1.85 | **3.46%** | 271 ms |

Caveats: (a) the two legs are ~3.5 s apart, so the loss includes real price drift on very volatile tokens (godspeed
traded $2.4M/h) — that drift is *real* execution risk, not noise to remove; (b) impact values are absolute values of
the API's signed field; (c) quotes are not fills. **Jupiter platform fee on memecoins: 10 bps per side**; pool/venue fees
are embedded in `outAmount`, so the system will **measure costs from quotes rather than hard-coding venue fee
schedules** (secondary sources disagree on PumpSwap fees: 0.25%–0.30%+ [SECONDARY]).

Bonding-curve token (age seconds, ~$3.3k curve liquidity), 0.05 SOL buy via lite v1: route
Whirlpool → Raydium CLMM → **Pump.fun**, `priceImpactPct 0.029` (≈2.9%) [MEASURED] — Jupiter can route bonding-curve
tokens, but they are extremely thin.

### 4.4 Tokens API v2 [MEASURED + DOCUMENTED — https://developers.jup.ag/docs/tokens]
- `GET lite-api.jup.ag/tokens/v2/search?query=<mint>` → fields observed: `id`, `name`, `symbol`, `decimals`, **`dev`**,
  `circSupply`, `totalSupply`, `tokenProgram`, `launchpad` (`pump.fun`, `met-dbc`, …), **`graduatedPool`, `graduatedAt`**,
  `holderCount`, `fdv`, `mcap`, `usdPrice`, `priceBlockId`, `liquidity`, `stats5m/1h/6h/24h` with `priceChange`,
  `holderChange`, `liquidityChange`, `volumeChange`, `buyVolume`, `sellVolume`, **`buyOrganicVolume`, `sellOrganicVolume`**,
  `numBuys`, `numSells`, **`numTraders`**, `numOrganicBuyers`, `numNetBuyers`, `firstPool{id,createdAt}`,
  **`audit{mintAuthorityDisabled, freezeAuthorityDisabled, topHoldersPercentage, devMigrations, devMints}`**,
  `organicScore` (0–100), `organicScoreLabel`, `tags`, `createdAt`, `updatedAt`.
- `GET /tokens/v2/recent` → **30 newest tokens with first pool**, which spanned only ~15 s at 22:01 UTC.
- `GET /tokens/v2/toptrending/1h?limit=20` → 20 tokens (also `toporganicscore`, `toptraded` categories per docs).
- Documented: organic score "measures how genuine a token's trading activity is", relative, normalised; `audit.isSus`
  present only when flagged. **Not documented:** what `topHoldersPercentage` covers (top 10? excludes pools?), how
  `devMints`/`devMigrations`/`numTraders` are computed [TO VERIFY @M2 against on-chain data].
- Example observation on `godspeed` (5 m window): 915 traders, 49 organic buyers; organic volume ≈ 2% of total.

### 4.5 Shield (token warnings)
- `GET lite-api.jup.ag/ultra/v1/shield?mints=<mint>` → `{"warnings":{mint:[{type:"NOT_VERIFIED",severity:"info"},
  {type:"NEW_LISTING",...}]}}` [MEASURED].
- `GET api.jup.ag/swap/v2/shield` → 429 at time of test (keyless budget exhausted), existence **unconfirmed**
  [TO VERIFY @M2].
- `lite-api.jup.ag` is still live, but the current docs centre on `api.jup.ag` with keys. Treat `lite-api` as a
  **deprecated-risk fallback** only [ASSUMPTION].

---

## 5. On-chain program identities [MEASURED via `getAccountInfo(pool).owner`]

Resolved from live pool accounts listed by GeckoTerminal, not from memory:

| GeckoTerminal DEX id | Program ID (owner) | Pool account size |
|---|---|---|
| `pump-fun` (bonding curve) | `6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P` | 166 B |
| `pumpswap` (post-migration AMM) | `pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA` | 301 B |
| `meteora-dbc` (dynamic bonding curve) | `dbcij3LWUppWqq96dh6gJWwBifmcGfLSB5D4DuSMaqN` | 424 B |
| `meteora-damm-v2` | `cpamdpZCGKUy5JxQXB4dcpGPiikHawvSWAd6mEn1sGG` | 1,112 B |
| `stonkfun` | `LanMV9sAd7wArD4vJFi2qDdfnVhFxYSUg6eADduJ3uj` (believed to be Raydium LaunchLab [ASSUMPTION]) | 429 B |
| Token-2022 program | `TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb` | — |
| Wrapped SOL mint | `So11111111111111111111111111111111111111112` | — |
| USDC mint | `EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v` (verified via GeckoTerminal search after a memory-based mint was wrong) | — |

Lesson recorded: **an address typed from memory was wrong during M0.** All program/mint constants in code must come
from verified on-chain lookups or official docs, with a test asserting each constant's on-chain owner/type.

### 5.1 Token-2022 mint example [MEASURED]
`$15` mint (pump.fun, minutes old): program `spl-token-2022`, decimals 6, supply 1,000,000,000,000,000 raw
(1e9 UI), `mintAuthority: null`, `freezeAuthority: null`, extensions: `metadataPointer` (authority null, points to
itself), `tokenMetadata` (updateAuthority null, `uri` on a third-party host). Evidence:
`solana_public_rpc_getAccountInfo_token2022_mint.json`.

### 5.2 Fees and costs [MEASURED / DOCUMENTED]
- Rent-exempt minimum: 165 B token account **1,488,440 lamports (0.001488 SOL)**; 170 B (Token-2022 with ImmutableOwner)
  1,513,840; 182 B 1,574,800 [MEASURED]. Refundable on account close.
- Jupiter platform fee: 10 bps/side on memecoins, 2 bps on SOL→USDC (RFQ) [MEASURED].
- Pump.fun bonding-curve fee "1%" and PumpSwap 0.25–0.30% [SECONDARY, not used]; embedded in quotes anyway.
- Base network fee 5,000 lamports/signature [ASSUMPTION — standard, TO VERIFY @M5 via `/order` fee fields with a taker].
- Priority fee: chosen automatically by Jupiter `/order` unless overridden [DOCUMENTED]; magnitude
  [TO VERIFY @M5 by requesting `/order` with a read-only `taker` public key].

---

## 6. Anthropic API — NOT USED (operator decision, 2026-10-18)

> The operator declined a paid AI API, so the desk has no AI judge and no LLM dependency (PLAN.md §15). This section
> is kept only as a record of what was researched; nothing below is used by the design.

Source: bundled Claude API reference skill (model table cached 2026-10-06). Would need re-verification with the
Models API if an AI judge were ever re-proposed.

| Model | ID | Input $/MTok | Output $/MTok |
|---|---|---|---|
| Claude Haiku 5.5 | `claude-haiku-5-5` | $0.10 (≤100K-token prompts) | $0.50 |
| Claude Sonnet 5.5 | `claude-sonnet-5-5` | $2.00 | $10.00 |
| Claude Opus 5.5 | `claude-opus-5-5` | $4.00 | $20.00 |

- Python SDK: `anthropic` 1.x (built on `httpx2`); structured outputs via `client.messages.parse(...)` with a pydantic
  model, or `output_config.format`; strict tool schemas via `strict: true`. Forced `tool_choice` is rejected on some
  current models → use structured outputs. [DOCUMENTED in skill reference]
- Batches: 50% discount, asynchronous (not useful for live decisions, useful for research re-scoring).
- Prompt caching: reads ~0.1× input price; minimum cacheable prefix is model-dependent (may exceed our short prompts).
- Haiku 5.5: thinking on by default; can be disabled only at effort ≤ `high`; default effort `medium` → set `low`
  explicitly for cost.
- **Claude Pro subscription ≠ API access.** The bot needs a separate API key with billing; without one, the judge stays
  disabled and the system runs deterministic-only (spec requirement).

---

## 7. Telegram Bot API
Not probed in M0 (requires a bot token). Well-established free API; the plan uses only `sendMessage` and long-polling
`getUpdates` for `/status` and `/stop`, restricted to an allow-listed chat ID. [TO VERIFY @M7]

---

## 8. Library versions available on PyPI / npm (2026-10-18) [MEASURED via `pip index versions` / `npm view`]

Python: `pydantic 2.14.1`, `pydantic-settings 2.16.0`, `httpx 0.28.1`, `anthropic 1.15.0` (not used), `solders 0.29.0`,
`solana 0.41.0`, `fastapi 0.146.0`, `uvicorn 0.54.0`, `structlog 26.1.0`, `sqlalchemy 2.1.5`, `alembic 1.20.0`,
`pytest 9.1.1`, `pytest-asyncio 1.4.0`, `respx 0.23.1`, `hypothesis 6.170.1`, `numpy 2.5.4`, `pandas 3.0.6`,
`scipy 1.18.1`, `scikit-learn 1.9.1`, `statsmodels 0.15.0`, `arch 8.0.0`, `websockets 17.2`, `orjson 3.13.0`,
`tenacity 9.2.1`, `cryptography 50.0.2`, `base58 2.1.1`, `uv 0.13.1`.

npm: `react 19.3.1`, `vite 8.3.4`, `typescript 7.0.2`, `tailwindcss 4.3.3`, `@tanstack/react-table 9.3.1`,
`@tanstack/react-query 5.104.4`, `lightweight-charts 5.2.1` (**Apache-2.0**), `echarts 6.1.0`, `lucide-react 1.59.0`,
`@radix-ui/react-dialog 1.3.1`.

Exact pins are chosen at M1 (latest stable minus any version younger than ~2 weeks) and locked with hashes (`uv.lock`,
`package-lock.json`).

---

## 9. Operator machine (supplied by operator, 2026-10-18)

Dell 15 DC15255 laptop · AMD Ryzen 7 7730U (8C/16T, Zen 3) · **16 GB RAM** (≈15 GB available; **2 GB swap file 83% used**
at report time) · 1 TB NVMe (~290 GB free on `/`) · **Wi-Fi only** (Realtek RTL8821CE, `rtw88_8821ce` driver) ·
Linux Mint 22.3 (Ubuntu 24.04 base → system **Python 3.12**) · systemd 255 · Docker installed with ~6 running containers ·
**Tailscale installed** · Cinnamon/X11, 1920×1080 15.6" display · suspend mode `s2idle` · battery-powered laptop.

Implications are handled in PLAN.md §2 and §15.

---

## 10. Alternatives considered

| Need | Chosen | Alternatives | Why not (now) |
|---|---|---|---|
| Neutral discovery | GT `new_pools` + Jupiter `/tokens/v2/recent` + trending lists | pump.fun log firehose (WS) | 19 GB/day; earliest window unwinnable anyway |
| Migration detection | GT `new_pools` (pumpswap pools) + Jupiter `graduatedAt` | `programSubscribe(pAMM, dataSize=301)` | Promising and cheap *if* pool accounts rarely change; [TO VERIFY @M2] |
| Third-party launch feeds | — | PumpPortal-style WebSocket data feeds | Third-party, unverifiable, ToS unknown; spec discourages third-party trading tools. Revisit only with approval |
| Refresh workhorse | DexScreener `tokens/v1` (30/call, 300/min) | GT multi (30/call, ~10/min) | GT budget too small |
| Holder data | Keyed RPC `getTokenLargestAccounts` + Jupiter `audit` as cross-check | Helius DAS / paid indexers (Birdeye, Bitquery) | Paid; marginal benefit unproven |
| Historical OHLCV | GT OHLCV (≤1000 bars) + own snapshots | Birdeye / CoinGecko Pro | Paid |
| Execution | Jupiter v2 `/order` + `/execute` (managed landing) | Router `/build` + own RPC/Jito | More control, much more to build and get wrong; managed landing compensates for home Wi-Fi latency |
| Social data | none | X API | Expensive; scraping prohibited by spec |

---

## 11. Open questions carried forward

| ID | Question | Resolve by |
|---|---|---|
| ~~R-01~~ | **Resolved 2026-10-19:** public RPC `getTokenLargestAccounts` is 429 from the operator's home IP as well → keyed RPC required for M2; GT/DS/Jupiter keyless all fine from home (148–401 ms). With the free Helius RPC it returns 20 accounts in 85 ms | — |
| R-02 | Helius credit cost of `getTokenLargestAccounts`; WS credit accounting | M1 |
| R-03 | Definitions of Jupiter `topHoldersPercentage`, `devMints`, `numTraders`, organic metrics | M2 (compare with on-chain) |
| R-04 | Max mints per Jupiter `tokens/v2/search` call (comma-separated lists work: 3 verified) | M2 |
| R-05 | `/swap/v2/execute` contract, status codes, requestId validity | M8 |
| R-06 | `/order` with read-only `taker`: are fee/priority/rent fields populated without funds? | M5 |
| R-07 | Cheap migration detector via `programSubscribe` on PumpSwap | M2 (optional) |
| R-08 | LP lock/burn verifiability per venue (PumpSwap, Meteora DAMM v2, Raydium) | M2 |
| ~~R-09~~ | ~~Anthropic model list & prices~~ — closed: AI judge removed by operator decision | — |
| R-10 | Whether systemd sleep/lid inhibitors are honoured by Cinnamon's power manager on the operator's laptop | M7 |

---

## 12. M1 verification log (2026-10-18, 22:15–23:10 UTC)

New facts established while building and testing M1. Evidence files are prefixed `m1_` in
`research/evidence/2026-10-18/`; the code that relies on each fact has a test.

| Fact | Tag | Consequence |
|---|---|---|
| DexScreener `/tokens/v1` returns **one pair per token** (its main pair): 10 mints → 9 pairs (1 not yet indexed), 5 → 5, 1 → 1 | [MEASURED] | Good for refreshing a token's main pool; use `/token-pairs/v1` when all pools of a token are needed (M2) |
| DexScreener AMM pairs (PumpSwap, Meteora) **do** report `liquidity.{usd,base,quote}`; only bonding-curve pairs omit it | [MEASURED] `m1_dexscreener_tokens_v1_amm_pairs.json` | "Liquidity missing" is a bonding-curve signal, not a data outage |
| GeckoTerminal `pools/multi` adds `locked_liquidity_percentage` (e.g. `"100.0"` on a PumpSwap migration pool), `pool_fee_percentage` (nullable), `pool_name`, and `base_token_price_native_currency`; duplicate addresses in a request are de-duplicated | [MEASURED] `m1_geckoterminal_pools_multi.json` | LP-lock evidence for R-08 — stored as a **provider claim**, to be checked on-chain in M2 |
| Jupiter `graduatedPool` for a token equals GeckoTerminal's PumpSwap pool address for the same token | [MEASURED] | Cross-source confirmation of migration pools |
| GeckoTerminal OHLCV: `data.attributes.ohlcv_list[] = [ts_seconds, o, h, l, c, volume_usd]` as JSON floats, plus `meta.base` / `meta.quote` | [MEASURED] `m1_geckoterminal_ohlcv_5m.json` | Parsed as `Decimal`; bar timestamp semantics (start vs end) still [TO VERIFY @M3] |
| Jupiter Tokens v2 `search` accepts **comma-separated mints** (3 → 3 tokens) | [MEASURED] `m1_jupiter_tokens_v2_search_3mints.json` | Batched claim lookups; maximum per call still unknown (R-04 partly resolved) |
| Jupiter timestamps carry up to **9 fractional digits** (`…11.75830942Z`) | [MEASURED] | Parser truncates to microseconds explicitly (works on Python 3.11 and 3.12) |
| Classic SPL Token program `TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA` is the owner of the USDC and wSOL mints | [MEASURED] (live test) | Program constant verified on-chain, not from memory |
| USDC mint has **both mint and freeze authority set** | [MEASURED] `m1_solana_rpc_getAccountInfo_usdc_spl_mint.json` | Real "authority present" fixture for M2 rejection tests |
| Venue program IDs (pump.fun curve, PumpSwap, Meteora DBC/DAMM v2) re-verified against live pools | [MEASURED] (live test M1-19) | `providers/venues.py` constants confirmed |
| Public RPC `getTokenLargestAccounts` still 429 (desk doctor, ~22:55 UTC) | [MEASURED] | R-01 remains open until measured from the operator's home IP |
| Local clock vs latest confirmed block time: +1.3 s | [MEASURED] (container) | Freshness checks can rely on block time as a cross-check |
| SOL ≈ $103.7 (0.1 SOL → 10.3743 USDC via Metis, 2 bps fee) | [MEASURED] doctor ~22:55 UTC | Reference only |
| Jupiter Tokens v2 is served on the keyed host `api.jup.ag/tokens/v2/...` (200 keyless as well; 41 ms with the operator's free key) | [MEASURED] 2026-10-19 | Keyed path verified; `lite-api` only used when no key is configured |
| Helius free RPC: `getTokenLargestAccounts` works (20 accounts, 85 ms); `getSlot` 155 ms from the operator's laptop | [MEASURED] 2026-10-19 (laptop doctor) | Holder concentration feasible for M2 |
| DexScreener reports `liquidity.usd = 0` **explicitly** for drained PumpSwap pools (reserves e.g. 34 tokens / 0.0000217 SOL) while still showing 24 h volume; 22 of ~269 tracked tokens in one hour | [MEASURED] `2026-10-19/m1_dexscreener_tokens_v1_drained_pumpswap_pools.json` | Explicit zero = rug/drain signal (flagged), distinct from "not reported" (bonding curves); reserves stored for M2 |

---

## Sources
- GeckoTerminal API spec: https://api.geckoterminal.com/docs/v2/swagger.json
- DexScreener API reference: https://docs.dexscreener.com/api/reference · spec: https://openapi.gitbook.com/o/F3EJQhITOlKtqJoq3nND/spec/dex-screener-api.yaml
- Solana clusters & public RPC limits: https://solana.com/docs/references/clusters
- Helius pricing: https://www.helius.dev/pricing
- Jupiter Swap API: https://developers.jup.ag/docs/swap · `/order` reference: https://developers.jup.ag/docs/api-reference/swap/v2/order
- Jupiter rate limits: https://developers.jup.ag/docs/portal/rate-limits
- Jupiter Tokens API: https://developers.jup.ag/docs/tokens
- Secondary (not used for design): The Block on PumpSwap fees https://www.theblock.co/post/354038/pumpswap-revenue-tokens ; Hummingbot Jupiter config https://hummingbot.org/exchanges/gateway/jupiter/
