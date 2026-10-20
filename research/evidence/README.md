# Research evidence

Sanitized raw responses captured during M0 with **read-only, unauthenticated** requests on 2026-10-18 between
21:53 and 22:06 UTC from the research container (shared cloud egress IP). Cookies were stripped; only rate-limit and
cache headers were kept (`response_headers_ratelimit_cache.json`). No API keys were used or stored.

These files back the `[MEASURED]` claims in `RESEARCH.md` and will seed the M1 test fixtures.

**Untrusted content warning:** token names, symbols, descriptions and links inside these files are
attacker-controlled data from the public internet. They must never be rendered as HTML or followed automatically.
Some are deliberately kept as prompt-injection / XSS test material.

| File | Request |
|---|---|
| `geckoterminal_new_pools_p1.json` | `GET api.geckoterminal.com/api/v2/networks/solana/new_pools?page=1` |
| `geckoterminal_trending_pools_1h.json` | `GET …/networks/solana/trending_pools?duration=1h&include=base_token` |
| `geckoterminal_search_usdc.json` | `GET …/search/pools?query=USDC&network=solana` |
| `dexscreener_tokens_v1_10mints.json` | `GET api.dexscreener.com/tokens/v1/solana/<10 fresh mints>` (9 pairs returned) |
| `dexscreener_token_profiles_latest.json` | `GET api.dexscreener.com/token-profiles/latest/v1` |
| `solana_public_rpc_getHealth.json` / `_getSlot.json` | public RPC |
| `solana_public_rpc_getAccountInfo_token2022_mint.json` | `getAccountInfo(<pump.fun mint>, jsonParsed)` |
| `solana_public_rpc_getTokenLargestAccounts_429.json` | `getTokenLargestAccounts` → HTTP 429 |
| `solana_public_ws_logsSubscribe_pump_sample.json` | one `logsNotification` for the pump.fun program |
| `jupiter_lite_v1_quote_sol_usdc.json` | `GET lite-api.jup.ag/swap/v1/quote` 0.1 SOL→USDC |
| `jupiter_lite_v1_quote_bonding_curve.json` | 0.05 SOL→bonding-curve token (route via Pump.fun) |
| `jupiter_v2_order_keyless_sol_usdc.json` | `GET api.jup.ag/swap/v2/order` keyless, no taker |
| `jupiter_v2_order_memecoin_roundtrips.json` | buy+sell `/order` pairs for 3 memecoins × 2 sizes |
| `jupiter_tokens_v2_search_one_mint.json` | `GET lite-api.jup.ag/tokens/v2/search?query=<mint>` |
| `jupiter_tokens_v2_recent.json` | `GET lite-api.jup.ag/tokens/v2/recent` |
| `jupiter_tokens_v2_toptrending_1h.json` | `GET lite-api.jup.ag/tokens/v2/toptrending/1h?limit=20` |
| `jupiter_ultra_v1_shield.json` | `GET lite-api.jup.ag/ultra/v1/shield?mints=<mint>` |
| `jupiter_v2_shield_429.json` | `GET api.jup.ag/swap/v2/shield` → 429 (keyless budget exhausted) |
| `response_headers_ratelimit_cache.json` | rate-limit/cache headers from several of the above |

`2026-10-20/council_ohlcv_interval_semantics.json` — four keyless GeckoTerminal requests (SOL/USDC reference pool)
made by `research/council/probe_ohlcv_semantics.py`: OHLCV bar time is the interval START (revision, trade-volume and
aggregation checks agree; see RESEARCH.md).

Robinhood desk (2026-10-20, docs/ROBINHOOD.md):

| File | What it is |
|---|---|
| `2026-10-20/robinhood_crypto_trading_api_openapi.json` | The Crypto Trading API spec embedded in docs.robinhood.com (paths, schemas, auth, rate limits) |
| `2026-10-20/robinhood_crypto_fee_schedule_20260622.txt` | Text of Robinhood Crypto's fee schedule PDF (exchange routing tiers; market-maker routing: no commission) |
| `2026-10-20/coinbase_candles_probe.json` | Two keyless Coinbase candle requests (`research/robinhood/probe_coinbase.py`): newest first, newest bar still forming |
| `2026-10-20/rh_backtest_v1_1095d.json`, `rh_backtest_v1_365d.json` | First history simulation of the frozen v1 strategies on 20 starter coins, 2% round trip assumed |
