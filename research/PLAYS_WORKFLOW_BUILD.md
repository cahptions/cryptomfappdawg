# Plays workflow continuation — 2026-10-27

Base: d6c86d8, published on claude/funny-rubin-gq99vj.

Complete every operator request, preserving the separate automatic Plays/PAPER/Learning records:

1. Click BUY/SELL badge → small adjacent confirmation → personal tracked PAPER selection with quantity.
   Clarified by operator: premarket selections enter on the first fresh regular-hours quote at/after 9:30 ET,
   NOT at 9:45. After-open confirmations cannot backdate fills. Better entry timing may replace this only with
   supported learning evidence; there is no validated adaptive entry policy yet. No brokerage orders.
2. Stable exact-play details and calculator handoff selecting every actual OCC leg, expiry, side, quantity;
   do not send the user to an unselected chain far down a stock page. Include historical P/L and scenarios.
3. Explain Delta/Gamma/Theta/Vega by name, units and per-contract dollars, including a plain guide.
4. Then enrich company profiles: what it does, sourced business overview, sector/industry/location/website,
   useful available financial/company context. Prefer authoritative existing SEC sources; no invented metadata.
5. Underlying quote beside ticker, and multiple meaningful probability meters (ITM vs profit vs realistic
   profit target). A long call has unbounded gain and a long put's absolute maximum requires stock $0;
   do not fabricate a meaningful probability of hitting those maxima. Label modeled targets and horizons.

Previous release verified 1,398 offline tests, Ruff, mypy236 and desktop/phone Plays/History/Performance.
Environment has no operator credentials; use disposable fixtures and do not claim live laptop acceptance.
Implemented in `play_selections.py`, `play_targets.py`, `company_profiles.py` and the Plays/research UI.
See `docs/STOCKS_PLAYS.md` and `PROGRESS.md` for behavior, verification and material limitations.
