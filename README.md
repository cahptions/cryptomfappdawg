# Solana Quant Desk (working brand: KESTREL)

A locally operated research and trading desk for Solana memecoins that discovers opportunities, rejects unsafe
tokens, paper trades under realistic execution costs, measures whether any strategy has a statistically defensible
net edge — and stays in PAPER mode indefinitely if it doesn't.

> **Status: M0 (planning) complete — awaiting approval. There is no runnable code yet.**
> Nothing here trades real money. LIVE trading will require a separate, explicit approval after months of evidence.

## Start here
| Document | What it is |
|---|---|
| [PLAN.md](PLAN.md) | Architecture, strategy hypotheses, risk controls, costs, milestones, **questions for the operator (§24)** |
| [RESEARCH.md](RESEARCH.md) | Verified facts about every data source and API, with evidence tags and source URLs |
| [ACCEPTANCE.md](ACCEPTANCE.md) | Objective tests each milestone must pass |
| [PROGRESS.md](PROGRESS.md) | Current status and the exact next step (read first when resuming) |
| [research/provider_registry.yaml](research/provider_registry.yaml) | Machine-readable registry of providers, limits and verified fields |
| [research/evidence/](research/evidence/) | Sanitized raw API responses captured during research |
| [docs/design/kestrel-concept.html](docs/design/kestrel-concept.html) | Static visual concept of the dashboard (sample data only — download and open in a browser) |

## Important
- Profitability is **not** promised. A likely and acceptable outcome is "no defensible edge — remain in PAPER".
- Never paste a seed phrase or private key into any chat, web form, or file in this repository.
