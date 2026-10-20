"""The Robinhood desk: liquid crypto on Robinhood, separate from the Solana memecoin desk.

Same discipline as the Solana desk: preregistered deterministic strategies, PAPER on the venue's own executable
prices before any money, recorded evidence against baselines, no model judge. Prices come from the Robinhood
Crypto Trading API (signed with a key generated on the laptop); history comes from Coinbase public candles because
Robinhood's API has no history endpoint. See docs/ROBINHOOD.md.
"""
