# Equity and multi-asset evidence for the KESTREL council

Research date: 2026-10-19. Research and design only; no collector, trading configuration, runtime database, or LIVE authorization was changed.

## Access and reading scope

Canonical NBER and arXiv requests were denied by the inherited network proxy with HTTP 403. Public GitHub-hosted copies were fetched through the normal permitted connection, then their PDF title pages and bibliographic details were checked. These are ordinary document copies, not relay/proxy services. Canonical DOI links remain the preferred citations. The mirror is identified separately so another researcher can reproduce what was read.

The five sources below were **read in substantive part**, including the relevant methods, empirical results, and limitations; they are not described as cover-to-cover readings. The exact inspected sections/pages are stated. Mathematical figures were inspected through extracted text/captions; no visual chart-pattern inference was made from unread figures. Internet appendices and external cited papers were not implicitly treated as read.

One purported TSMOM PDF was rejected: `jackywong1228/CTA-Quant-Papers/23_Time_Series_Momentum_Moskowitz2012.pdf` actually contains Cúrdia and Woodford's *The Central-Bank Balance Sheet as an Instrument of Monetary Policy*, NBER Working Paper 16208 (2010). Its filename is not evidence. It contributes no claims here.

## Evidence table

| ID | Primary source; actual reading | Data, method, and finding | Practical limit and council implication |
|---|---|---|---|
| EQ1 | Tobias J. Moskowitz, Yao Hua Ooi, Lasse Heje Pedersen (2012), *Time series momentum*, JFE 104, 228–250. [DOI](https://doi.org/10.1016/j.jfineco.2011.11.003). Read abstract/introduction, §2, §3, §4 and §5 empirical discussion; particularly pp. 230–240 and Table 5/correlation discussion pp. 241–244. | 58 liquid commodity, currency, developed equity-index, and government-bond futures/forwards. Underlying history reaches 1965–2009; principal pooled results and strategy charts use January 1985–December 2009. §2.4 Eq 1 uses lagged exponentially weighted daily squared returns, approximately 60-day center of mass; §3 studies multiple monthly formation/holding periods. §4 Eq 5: sign of trailing 12-month excess return, inverse ex-ante volatility, one-month holding/rebalancing. Return continuation at 1–12 months with later partial reversal. Table 5 finds substantial overlap of time-series and cross-sectional momentum rather than independent votes. | Figure 2 explicitly reports **gross** Sharpe ratios. The study is not a quote-to-fill, fee-inclusive memecoin backtest. p. 230 specifically warns that high-frequency equity findings can be contaminated by stale-price/microstructure effects. Monthly liquid-futures findings cannot establish an intraday new-token edge. A second trend indicator may duplicate KESTREL's existing momentum information. |
| EQ2 | Kent Daniel, Tobias J. Moskowitz (2016), *Momentum crashes*, JFE 122, 221–247. [DOI](https://doi.org/10.1016/j.jfineco.2015.12.002). Read pp. 221–224 (§1 and §2.1–2.2), pp. 232–233 (Table 5 and §4), pp. 242–243 (§6 and Appendix A.1). | US CRSP common-stock deciles, monthly reconstitution, main sample 1927–2013; stocks ranked over months t−12 through t−2, skipping the latest month. Cross-sectional winner-minus-loser momentum can suffer when markets rebound after drawdowns and elevated volatility; the losing short leg is central to the mechanism. §4 Eq 6 weights a momentum portfolio by estimated conditional mean/variance. The authors compare in-sample and implementable out-of-sample versions and examine additional equity markets/asset classes. | Appendix A.1 p. 243 explicitly excludes transaction costs from calculated returns. §4 p. 233 notes the dynamic version uses more leverage and would incur higher transaction costs. Most importantly, a spot-only token BUY has no short-loser leg. §6 footnote 16 p. 242 treats application to time-series momentum as future research. This is support for testing state-dependent risks, **not** proof that high volatility universally predicts negative long-token returns. |
| EQ3 | Alan Moreira, Tyler Muir (2017), *Volatility-Managed Portfolios*, Journal of Finance 72(4), 1611–1644. [DOI](https://doi.org/10.1111/jofi.12513). Read pp. 1611–1619 (§I.A–D, Eqs 1–3, Table I), pp. 1623–1629 (§II.A–D, Tables IV–V), and p. 1641 conclusion. | Scale monthly factor excess returns by inverse previous-month realized variance: Eq 1, `f_managed,t+1 = c / variance_hat,t × f_t+1`; Eq 2 builds realized variance from prior daily returns. Equity factors and currency carry; sample dates differ by factor (Table I). Authors find risk-adjusted benefits for many factors because volatility varies more than expected compensation, and test lower-risk exposure in recessions. Table IV includes explicit assumed costs for market timing. | This is a **monthly portfolio-risk** study, not an intraday token-entry signal. `c` is chosen using full-sample unconditional risk for presentation; a deployable implementation cannot use future-sample risk normalization. Table IV costs apply to the market portfolio, and p. 1625 explicitly declines to report every factor's costs because reliable implementation costs are unavailable. Inverse-variance leverage can be extreme (Table V baseline 99th-percentile weight 6.39); bounded spot risk cannot inherit it. Cederburg et al. (2020), independently read by the validation researcher, provides essential contrary real-time evidence. |
| EQ4 | Wilma de Groot, Joop Huij, Weili Zhou, *Another look at trading costs and short-term reversal profits*, accepted July 2011 manuscript of the 2012 JBF article. [DOI](https://doi.org/10.1016/j.jbankfin.2011.07.015). Read manuscript pp. 1–7,12–15,17–19,23–29 (PDF pages 3–9,14–17,19–21,25–31). | US 1,500 largest Citigroup BMI stocks 1990–2009, European 1,000 largest 1995–2009; microcaps intentionally excluded. §4.1 ranks five-day returns, skips one day, forms daily equal-weighted long-loser/short-winner quintiles. Broad-universe gross reversal returns disappear after modeled costs. Excluding smaller expensive stocks and keeping holdings until they cross a wider rank threshold lowers turnover and improves modeled net returns. | Cost models are Keim–Madhavan and proprietary Nomura estimates, not observed KESTREL executions; Nomura omits unfilled-trade opportunity cost (Appendix B). §6 explicitly disputes a simple liquidity-inventory explanation and does **not** identify behavioral overreaction causally. Evidence supports testing execution-aware selection and hysteresis, not buying every sharp token drop. |
| EQ5 | Si Cheng, Allaudeen Hameed, Avanidhar Subrahmanyam, Sheridan Titman (2017), *Short-Term Reversals: The Effects of Past Returns and Institutional Exits*, JFQA 52(1),143–173. [DOI](https://doi.org/10.1017/S0022109016000958). Read title/abstract and pp. 146–148 (§II.A), p. 169 (§V), with selected introduction. | CRSP common stocks 1980–2011 excluding month-end prices below $5. Three-month prior return state and quarterly changes in active institutional participation condition next-month loser-minus-winner reversal returns. Authors interpret reduced active participation as diminished liquidity provision and document stronger reversals among prior-quarter losers. | Monthly stock portfolio findings use institutional ownership/transaction data absent from free Solana feeds. Conclusion explicitly says profits do **not** arise primarily from long loser positions and cannot be fully explained by prolonged institutional fire sales. These inspected results are not a quote-to-fill net-cost test. Transfer to a long-only AMM bounce remains experimental. |

Numerical results above describe the cited study only. No KESTREL profitability, incremental council benefit, parameter validity, or performance estimate follows from them.

## EQ1: mechanism and transfer test

Accessible mirror: [verified published PDF](https://raw.githubusercontent.com/xhlgogo/Quantitative-Investment-Trading-system/master/paper/10.Time%20Series%20Momentum.pdf).

The principal hypothesis is slow incorporation of information and/or sustained trading/hedging demand. The paper studies hedger/speculator positions as well as prices; its behavioral and rational interpretations are hypotheses, not uniquely identified causes.

Relevant reproducible definitions:

- The annualized variance in Eq 1 uses only lagged daily returns with exponential weighting. Its calendar multiplier 261 belongs to that data construction, not automatically to 24/7 crypto.
- Eq 5 takes the sign of the past 12-month excess return and scales a position inversely to ex-ante volatility. The 40% target used for an individual contract is a research scaling convention, not an operator risk recommendation.
- §4.3 pp. 237–239 describes losses when trends reverse, including the end of the 2008–2009 crisis. The ability to short declining contracts is part of the reported behavior; cash-only spot long positions do not reproduce it.
- §4.4 p. 239 uses a June 2010 cross-sectional volume snapshot as an illiquidity proxy rather than a historical point-in-time volume series. That limitation makes the liquidity result especially unsuitable as a ready-made memecoin-liquidity rule.
- §4.5 and Table 5 show shared structure across momentum variants. Two advisors that both read the same short-term return path must not be advertised as independent evidence merely because one uses EMA and the other a price ROC.

**Falsifiable KESTREL adaptation:** after security and cost controls, does a longer and genuinely observed return state add predictive or rejection value conditional on the existing momentum trigger? Estimate incremental after-cost outcomes and conditional error correlation. If the added state merely duplicates the primary trigger, reject the advisor even if its standalone historical chart looks attractive. A minutes-to-hours variant is a new hypothesis with new parameters and new evidence requirements.

## EQ2: state-dependent momentum risk and counterinterpretation

Accessible mirror: [verified open-access published PDF](https://raw.githubusercontent.com/xhlgogo/Quantitative-Investment-Trading-system/master/paper/4.Momentum%20Crashes.pdf).

The stock portfolio is long past winners and short past losers, with a one-month gap before investment to reduce contamination by short-term reversal. The authors use CRSP common shares, formation-date data requirements, delistings, and value-weighted portfolios. These controls matter when adapting a study to a market where unavailable historical dead tokens can create extreme survivorship bias.

Its crash mechanism is not simply “a large red candle.” After a market decline, previous losers can have strongly positive exposure to a sharp rebound; a momentum portfolio that shorts them loses. A long-only investor buying a loser during that rebound may be on the **opposite** side of the paper's crash mechanism. Accordingly:

1. Do not equate a panic-state indicator with a calibrated probability of a long-token loss.
2. Do not use rebound-state disagreement as an automatic veto without direct out-of-sample evidence.
3. Do not import dynamic weights that can reverse sign, use leverage, or assume a short leg.
4. Treat high-volatility/drawdown/rebound interaction as a research covariate whose incremental effect must be measured in the actual long-only universe.

**Falsifiable KESTREL adaptation:** do existing entries have worse net expectancy or worse exit tails in predeclared shock/rebound states, after matching exposure, liquidity, age, and primary strategy? If not, a risk-state advisor has no basis to reduce those entries. Its opposite prediction must remain visible rather than retrospectively relabeled.

## EQ3: volatility and exposure rather than a free source of alpha

Accessible mirror: [verified published PDF](https://raw.githubusercontent.com/coolgan/Volatility-Managed-Portfolios-Sometimes-it-Works/main/reference/%E3%80%90Moreira%2C%202017%2C%20JF%E3%80%91Volatility-managed%20Portfolios.pdf).

The mechanism is a time-varying trade-off between forecast risk and expected return. A recent volatility increase need not bring a proportional expected-return increase. This motivates evaluating risk states, but it does not supply a direction signal or justify increasing exposure whenever recent volatility is low.

Table I uses 1926–2015 for market/SMB/HML/momentum,1963–2015 for RMW/CMA,1967–2015 for ROE/investment,1983–2015 for currency carry, and 1929–2012 for betting-against-beta. These are established portfolios. The units, calendars, diversification, and trading infrastructure differ radically from a single young Solana pool.

Table IV §II.B considers monthly turnover and costs of 1 bp,10 bp, and 14 bp, plus break-even values; it also tests inverse volatility, forecast variance, and constrained weights to reduce turnover. These cost assumptions are explicit tests in that setting. They do **not** license imposing a blanket 10 bp cost on Jupiter routes. A $100 operator account faces fixed transaction costs, quote failures, adversarial execution, and unavailable exits that must be estimated separately.

§II.C/Table V demonstrates the practical problem of uncapped inverse-variance weights: extremely low estimated variance can imply large leverage. On sparse token data, stale prices can also make measured variance deceptively low. Therefore an advisor must ABSTAIN on inadequate observations and may not interpret a zero realized variance as permission to enlarge a trade.

**Falsifiable KESTREL adaptation:** a fixed, bounded volatility/shock-state feature may improve filtering or risk forecasts after conditioning on the existing strategy. It starts with zero influence. No automatic position increase, no volatility-based leverage, and no claim of Sharpe improvement before the council's actual incremental forward evaluation. The contrary Cederburg results make real-time normalization and expanding-window estimation central acceptance conditions.

## EQ4: reversal after trading costs, with an important contrary interpretation

Accessible mirror: [verified accepted manuscript](https://raw.githubusercontent.com/kuzeysahin-ai/quant-paper-pipeline.2/main/papers/paper-02-groot-huij-zhou-2012/source.pdf). The PDF is the July 2011 accepted manuscript, not the later typeset publication; page references below use its printed manuscript pagination.

§2 p. 6 specifies the universe and excludes microcaps. US data are 1990–2009, European data 1995–2009; these are large-stock data with dividends, market capitalization, and volumes from FactSet. §4.1 p. 14 constructs cross-sectional quintiles from five trading days of returns, skips one day specifically to reduce bid–ask bounce, and rebalances daily. This is a long–short portfolio strategy at a weekly signal horizon, not a single-coin 15-minute BUY rule.

For the broad US 1,500 universe, the gross reversal strategy's large turnover consumes its gross profits under both modeled cost estimates (§4.1 pp. 14–15). The largest 500/100 universes and a wider rebalance band fare better. §4.2 pp. 17–19 holds an existing long loser until it reaches the upper half of the prior-week-return ranking instead of replacing it immediately on leaving the bottom quintile; the reverse applies to shorts. The reported net 30–50 bp per week belongs to those specific large-stock configurations and modeled costs. It is not a candidate expected return for KESTREL.

Useful economic lesson: an apparent signal need not justify a new transaction; incremental expected improvement must exceed the costs of replacing the holding. A predeclared hysteresis rule can reduce churning, but its opportunity cost must be tested too. Merely reducing the polling frequency or ignoring a deteriorating exit is not equivalent to the paper's rule.

The Appendix B cost model includes spread, trade size versus volume, and volatility, calibrated periodically on institutional trades. It explicitly excludes opportunity costs of unfilled trades. It has no Solana network-fee, MEV, dynamic pool-migration, or vanished-route model. Furthermore §6 pp. 24–26 argues that the authors' large-stock and time-stability findings contradict a simple liquidity-inventory explanation. They mention behavioral overreaction as not inconsistent with their findings but explicitly state that their study provides no direct evidence for that explanation. Do not cite this as causal proof of a liquidity-shock rebound.

**Falsifiable KESTREL adaptation:** can strict liquidity/executable-cost qualification and fixed entry/exit hysteresis preserve incremental net return or rejection value relative to the same residual-shock baseline? Compare all matched-risk variants with historical failures and dead pools included. If transaction costs erase the rebound, reject the strategy regardless of attractive gross charts.

## EQ5: conditional reversal and liquidity-provider participation

Accessible mirror: [verified published paper](https://raw.githubusercontent.com/Pieismath/bw_hackathon/main/papers/pdfs/cheng_hameed_subrahmanyam_titman_2017_short_term_reversals.pdf).

The paper's chronology in Figure 1/§II.A pp. 147–148 separates prior-quarter state (months t−3..t−1), a one-month formation signal(t), and the following-month return(t+1). That ordering cannot be compressed to current 5-minute candle and immediate recovery without defining and validating a new experiment.

The proposed mechanism is a fluctuation in liquidity provision: active institutions participate less in prior losers, and changes in participation are associated with stronger short-horizon reversals. Data include Thomson Reuters 13F holdings and institutional transaction information; an anonymous token holder list is not an equivalent measure of liquidity-provider supply. Reported security volumes may include both liquidity demand and supply and do not identify either by themselves.

The conclusion p. 169 explicitly limits a simple forced-sale story: loser positions do not drive most contrarian profits, and abnormal turnover does not fully support prolonged institutional fire sales as an explanation. A pool's persistent liquidity withdrawal may instead signal permanent impairment. Recovery of a reserve-derived depth measure and recovery of a quote are observations that can qualify a hypothetical temporary dislocation; neither proves that the original event was non-informational or that a rebound will follow.

**Falsifiable KESTREL adaptation:** for a prespecified class of observable negative residual shocks, does verifiable recovery of executable depth separate temporary price pressure from continued declines, after security/ownership changes and costs? The stock paper motivates the question; it supplies no validated token threshold, AMM estimator, expected holding period, or probability. Missing reserve history, unresolved pool semantics, fresh mint/supply changes, or absent exit quotes require ABSTAIN. The first version remains explicitly experimental and advisory-only.

## Stock-to-crypto transfer checklist

These are engineering hypotheses and necessary controls, not results established for KESTREL by the papers:

| Issue | Equity/futures study setting | Required crypto treatment |
|---|---|---|
| Calendar | Daily closes, monthly portfolios, exchange sessions; instrument-specific futures histories | Explicit UTC decision boundaries and continuously traded bars; no equity annualization multiplier copied blindly; preserve downtime/gaps |
| Identity and survival | CRSP identifiers, common-share filters, delistings; futures contracts/rolls | Mint and pool identities, historical discovery time, migration links, dead/drained pools retained; no current-token-list historical universe |
| Supply events | Splits/dividends and corporate-action returns handled in stock data | Token supply/decimals/authority changes and quote-token depegs recorded; never equate split adjustment with a manipulable token-price history |
| Liquidity and price | Established centralized venues and liquid futures; reported closes | Routed executable BUY/SELL amounts, pool depth, price impact, quote age, route availability and quote provenance; chart close cannot serve as a fill |
| Adversarial activity | Market microstructure and stale-price issues remain relevant | Wash volume, bots, concentrated ownership, liquidity removal and MEV can dominate the signal; aggregate reported volume alone cannot establish demand |
| Positions | Borrowing, futures margin, leveraged long/short research | Initial council remains long-only spot advisory, no borrow/funding assumptions; funding must enter costs if derivatives are ever independently researched |
| Costs | Gross results or selected modeled market costs | Per-route fees/spread/impact plus fixed fees, latency, failed transactions and exits; avoid counting components already included in quoted output twice |

## Research boundary and unresolved evidence

Exactly two advisors are selected and specified in the parent council report. These five source studies are inputs to that choice, not five additional deployable strategies.

Additional short-term equity reversal/liquidity-provision research was searched, including Stefan Nagel (2012), *Evaporating Liquidity*, RFS 25(7),2005–2039, [DOI](https://doi.org/10.1093/rfs/hhs066). No verified substantive primary copy of Nagel was obtained in this equity research pass. Secondary repository descriptions were not promoted into primary-source findings. EQ4/EQ5 above were actually inspected and provide conditional reversal/cost evidence; no inspected source establishes the proposed intraday AMM strategy's expected return or parameters. That adaptation remains experimental.

The downloadable PDFs were retained only in `/tmp/council-sources`, outside production runtime state. Their URLs, sizes, hashes, bibliographic metadata, and read scopes are recorded in `sources/equity_manifest.json`; copyrighted papers are not added to the repository.
