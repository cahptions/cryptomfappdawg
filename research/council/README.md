# KESTREL supplemental evidence council — research handoff

**Conclusion:** research two advisors: **C1_BRC, benchmark-residual continuation**, and **C2_ECDR,
execution-confirmed dislocation reversal**. BRC separates market exposure from a continuation signal; ECDR
requires an adverse move plus an observed pre-event/execution-recovery sequence. ECDR is especially
experimental for memecoins. Neither has a demonstrated KESTREL edge or production authority.

Both remain **SHADOW / influence NONE**. No collector, credentials, database, execution setting, risk rule,
wallet or LIVE authorization changed in this research pass. Claude's app work can finish independently;
re-audit its final revision before implementing this design. The audited snapshot is `32344daf517c58f88f87e6a1eb21ba86f4fcb035`.

## Read in this order

| File | Contents |
|---|---|
| [STRATEGIES.md](STRATEGIES.md) | Exactly two full specifications: mechanisms, stock/crypto/meme profiles, equations, signals, costs, data gaps, examples, exits, falsification and pseudocode |
| [COUNCIL.md](COUNCIL.md) | Structured advisory, causal input hashes, agreement/conflict policy and immutable primary boundary |
| [validation_plan.md](validation_plan.md) | Point-in-time walk-forward design, paired ablations, dependence/multiple-testing controls, promotion/demotion and economic hurdles |
| [implementation_plan.md](implementation_plan.md) | Future repository files, meaningful tests, minimal version, CPU/RAM/API/disk budgets and acceptance sequence |
| [specifications.json](specifications.json) | Machine-readable proposed defaults; **not application configuration** |
| [advisory.schema.json](advisory.schema.json) | Research-only JSON output contract; financial semantic checks are also required |
| [repo_audit.md](repo_audit.md) / [design_review.md](design_review.md) | Existing overlap, verified operational blockers, data availability and design corrections |
| [equity_evidence.md](equity_evidence.md) | Five primary equity/multi-asset sources, exact reading scope and contrary evidence |
| [crypto_evidence.md](crypto_evidence.md) | Two primary crypto/AMM papers and five official data/protocol documents, including current source-use restrictions |
| [validation_evidence.md](validation_evidence.md) | Two primary real-time counterevidence papers and the limits of the available methodological sources |
| [sources/](sources/) | Commit-pinned URLs where obtained, bibliographic metadata, SHA256 hashes, read scopes, blocked sources and rejected mislabeled documents |
| [verify_design.py](verify_design.py) | Offline design arithmetic/consistency checks; **no backtest or production advisor implementation** |
| [HANDOFF.md](HANDOFF.md) | Completed work, unknowns and next-session instructions |

## Compact evidence assessment

The full tables provide title/authors/year, publication versus working-version distinctions, exact pages,
sections/equations/tables, universes, dates, costs and limitations. These abbreviated findings retain their limits:

| Source | Relevant read locator | What it contributes; what it does not establish |
|---|---|---|
| Moskowitz/Ooi/Pedersen, *Time series momentum* (2012), [DOI](https://doi.org/10.1016/j.jfineco.2011.11.003) | §§2–5, Eqs1/5, pp230–244, Table5 | Monthly liquid-futures continuation and overlap of momentum variants; no intraday memecoin proof |
| Daniel/Moskowitz, *Momentum crashes* (2016), [DOI](https://doi.org/10.1016/j.jfineco.2015.12.002) | §4 pp232–233, §6/AppendixA pp242–243 | State-dependent long/short crash risk; short-loser mechanism and excluded costs restrict long-only transfer |
| Moreira/Muir, *Volatility-Managed Portfolios* (2017), [DOI](https://doi.org/10.1111/jofi.12513) | Eq1, §II.B/TableIV pp1623–1625, TableV | Risk management hypothesis and explicit selected cost tests; no permission for low-vol leverage or blanket cheap fills |
| de Groot/Huij/Zhou, *Another look at trading costs and short-term reversal profits* (accepted2011/published2012), [DOI](https://doi.org/10.1016/j.jbankfin.2011.07.015) | §§4/6, PDFpp16–28 | Liquidity/universe/turnover restrictions and costs can erase gross reversal; authors also challenge a simple inventory explanation |
| Cheng/Hameed/Subrahmanyam/Titman, *Short-Term Reversals: The Effects of Past Returns and Institutional Exits* (2017), [DOI](https://doi.org/10.1017/S0022109016000958) | Introduction, sample/method discussion, conclusion | Conditional monthly equity reversal/participation evidence; no simple proof that buying forced-sale losers drives profits |
| Tadi/Kortchemski, *Evaluation of Dynamic Cointegration-Based Pairs Trading Strategy in the Cryptocurrency Market* (2021), [primary copy](https://github.com/jackywong1228/CTA-Quant-Papers/blob/0c30ae8c0cdb2cc671f4a19a277633462f21cc2b/73_Cointegration_Pairs_Trading_Crypto_Tadi2021.pdf) | §§3–7, TableI pp6–22 | A narrow derivatives/reversion study with material rebate contributions; not long-only AMM replication |
| Adams/Zinsmeister/Robinson, *Uniswap v2 Core* (2020), [official copy](https://github.com/Uniswap/docs/blob/1c7597d73be0e9a3a4e44aa75b8fb212b40ea136/archive/static/whitepaper.pdf) | §§2–3, Eqs1–3/9–11 | Routing/reserve/fee mechanics and price-versus-fill distinction; no alpha experiment |
| Cederburg/O'Doherty/Wang/Yan, *On the performance of volatility-managed portfolios* (2020) | §§3–4, Table6, pp98–107; [review](validation_evidence.md) | In-sample benefits often fail to improve real-time combinations; uncertainty and estimation instability matter |
| Liu/Tang/Zhou, *Volatility-Managed Portfolio: Does It Really Work?* (December2018 working version) | §3 pp9–11; [review](validation_evidence.md) | Real-time scaling/drawdown and significance limitations; do not silently cite this read copy as the published2019 version |

All five equity papers were read substantively **in part**, not cover-to-cover. The two crypto/AMM PDFs and
several official documents were read in full; the long Binance REST document was read in relevant part.
The two validation papers were read in the substantive sections recorded in their review. Extracted text
and captions were used; no unread figure was treated as a chart-pattern result.

Canonical NBER/arXiv/author endpoints were blocked by the inherited proxy. Legitimate public GitHub copies
were used where verified; no proxy bypass or paid access was attempted. Two misleading filenames were rejected
after checking their actual title pages. Liu/Tsyvinski crypto factor papers, Makarov/Schoar, crypto wash-trading
papers, Nagel, PBO and DSR remain unread leads; no quantitative finding is attributed to them here.

## What is possible now

Freeze specs and membership; normalize the already fetched SOL raw series with genuine receipt times;
report coverage and abstentions; perform offline arithmetic/provenance tests. This minimum version adds
zero provider calls. It is an implementation plan, not code connected to the application.

What still blocks advisor-ready signals: verified native candle interval semantics, long enough aligned
benchmark/token history, stable causal fits, and (for ECDR) genuine pre-event quote/reserve/depth panels.
Stocks have no implemented data/broker/corporate-action adapter. Binance archive terms retrieved in this pass
permit limited personal research but restrict production/live proprietary use; free download is not a free
production license. A licensed accessible dataset remains a separate prerequisite for those profiles.

The source audit also reproduced a primary-engine limitation: H7's required age-adjusted-volume feature is
MISSING/UNVERIFIED in the ordinary builder path and propagates a veto into every trade strategy. H1 also lacks
a verified migration-occurrence input. These are handoff findings, not changes or permission to weaken gates.
An all-NO-TRADE primary has no measurable incremental filtering sample; freeze a separately resolved/versioned
primary baseline before claiming council improvement.

## What has not been done

No market dataset was backtested for these two specifications, no effect size or win probability was measured,
and no profitability or readiness conclusion was reached. Arithmetic examples and schema checks do not supply
forward evidence. The $100,000 learning cushion cannot validate the real $100 replica, and neither can authorize
LIVE, a larger position or altered guardian behavior. Paper/test calendar floors remain necessary but insufficient.

Verification completed: [verification.json](verification.json) records successful example arithmetic, all three
profile warm-up counts, the16+14request budget, exactly two advisor IDs, source-manifest integrity fields, and
JSON Schema checks rejecting LIVE/influence/third-advisor/probability output variants. Ruff passed for the
standalone verifier. These checks validate the design artifacts; **zero strategy trades were tested**.
