"""KESTREL's forecast of where a stock ends up, split into a direction part and a volatility part
(docs/STOCKS_METHOD.md → Direction and volatility).

* ``panel``: the daily stock panel (split-adjusted closes, dollar volume, point-in-time side data) and its features.
* ``direction``: quantiles of each stock's standardized log return over 1–63 sessions (gradient-boosted quantile
  trees, weekly walk-forward fits, live per horizon only where they beat the unconditional distribution).
* ``vol_surprise``: how far realized volatility will differ from what options imply, beyond the HAR-IV forecast.
* ``structure``: the two combined into a distribution of the stock at expiry, and any option structure valued under
  it against its realistic fill, with the edge split into its direction and volatility parts.
* ``store`` keeps the fits; ``job`` runs the weekly refit (in the Learner's training subprocess).
"""
