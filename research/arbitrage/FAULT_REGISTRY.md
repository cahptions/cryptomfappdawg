# Arbitrage reliability registry

All evidence below is synthetic/offline unless explicitly labeled otherwise. It does not validate live offers,
provider entitlements, exact delivery stock, actual profit or the operator laptop's power-loss behavior.

| Fault / invariant | Detection and behavior | Evidence |
| --- | --- | --- |
| Unknown fee, shipping, tax, seller approval, demand or timing | Explicit blocker; no qualified basket | `test_arbitrage.py` economic-gate parameter cases |
| Stale, future, zero-quantity or unavailable source evidence | Verification/age/quantity gate; no fabricated price or stock | Economic tests, later-source-failure test |
| Product/variant change | Prior destination approval invalidated; incompatible offer excluded | Identity mutation test; GTIN check-digit tests |
| Repeated and changed inventory IDs | Identical retry is a no-op; changed reuse rejected | Inventory idempotency tests |
| Over-selling, reserved stock, returned units, late event | Validate prospective lot state inside transaction; reject invalid event | Conservation/refund/quarantine/write-off tests |
| Mixed currencies | No mixed-currency profit arithmetic; explicit inventory currency; Settings cannot relabel costs | Currency/accounting test |
| Comparison explosion / catalog turnover | 5,000-pair guard before computation; pause routes to recover; evict only untouched unreferenced API cache | Bound/recovery and protected-cache tests |
| Monetary accounting | Independently checked receipts = costs + profit; basket cash conservation | $52/$29/$11/$67.78 fixture and inventory conservation tests |
| Private file permissions, symlink, partial credential edit | Refuse unsafe read; atomic owner-only save; preserve blank fields | `test_arbitrage_ebay.py` private-file tests |
| Credential or token echo | Status contains booleans only; failure body suppressed; tokens memory-only | Synthetic secret marker checks and browser saved-field check |
| 401/403, 429, 5xx, redirect | No redirect following or provider-body echo; named failure; persistent global eBay backoff/quota | Synthetic status cases and restart-persistent budget tests |
| Malformed/missing/ambiguous listing/page data | Refuse invalid batch/product; not a successful empty feed | JSON-LD ambiguity, auction exclusion and eBay structure tests |
| Robots prohibition, arbitrary host/path, oversized response | Fixed reviewed endpoints; deny URL fetch; 2 MB cap | Source allowlist, robots and response-size tests |
| Invalid/huge request, CSRF, other host/origin | 422/413/403 before a successful local write; 500 KB import bound | Local API boundary tests |
| Disk full/storage unavailable | Sanitized 503, no claimed save; collector storage guard | Injected SQLite error; physical disk exhaustion not performed |
| Duplicate collector/start, cancellation | Kernel lock; own task cancelled/released; no orphan child; no broad kill | Lock/lifecycle tests and actual temporary-server shutdown |
| Sparse, future or stale popularity evidence | UNKNOWN; no attention-to-sales conversion | Heat-cohort tests |
| Cross-desk effects | Independent home/database/credentials/locks, optional new icon | Isolation test, launcher regressions, full offline suite |
| Actual forms, guide links, charts, mobile width | Desktop + 390px browser checks, no uncaught JavaScript errors | `validation/browser-results.json`, synthetic screenshots |

Not yet physically tested: home-Wi-Fi reconnect, real provider quota/entitlements, actual webpage markup,
full disk exhaustion, forced power failure, retailer race-to-sold-out, true seller payout/refund reconciliation,
and prospective business performance. Synthetic tests do not substitute for these observations.
