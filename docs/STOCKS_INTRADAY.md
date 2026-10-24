# 1D stock and ETF charts

Every stock/ETF research chart has a 1D option alongside the existing 1M/3M/6M/1Y/5Y/Max ranges. It uses actual
Schwab one-minute OHLC and share volume, with line and candle views. Hover, touch and keyboard inspection show
the minute's time, OHLC and volume and update the headline price. All intraday time labels use America/New_York.

The existing saved Schwab authorization supplies this data. No new API account is needed. Open the stock page
and choose 1D; its protected history-request action registers interest and the bounded chart refresh fetches
minute candles independently of bulk history collection. Live access still depends on Schwab authorization and
entitlements. Settings contains the existing connection and access-test controls.

The request uses pricehistory with periodType=day, period=5, frequencyType=minute, frequency=1 and extended hours
off. The lookback finds the latest actual regular session before market open, on weekends and after holidays.
Only that session is shown in 1D. The session date and last candle time are visible. Extended-hours prices may
appear in a separately timestamped stock quote; the 1D chart itself is the regular session. The latest minute
may still be forming. Missing minute observations remain gaps, and no daily bar is relabelled as minute data.

Minute timestamps are preserved in a separate compressed chart cache added by stocks_market migration 0005.
An empty, invalid or failed response preserves the previous chart and shows the error. A missing cache shows a
loading/connection state; the daily ranges remain usable. A corrupt minute cache does not erase daily candles
or a saved quote. Previous-close comparisons use the matching session's quote or the preceding trading day's
Schwab daily candle; if neither is available the headline explicitly uses session open as its reference.

Active stock pages refresh independently. Each pass fetches at most two minute histories, two daily histories
and one bounded quote batch through the existing provider limiter. Minute attempts are paced to 30 seconds
(60 seconds after failure); inactive pages expire after two minutes. Data refresh does not reset a chart being
inspected. Minute observations never mark a bulk price session complete, change research scores or fill a
PAPER trade. UI protocol 2 keeps older running servers from silently presenting an unsupported chart.

Verification covers parsing and bounds, daylight saving time, weekends/before-open selection, gaps, zero volume,
empty/malformed/future responses, cache preservation, pacing, and bulk/PAPER separation. Desktop and phone
browser flows check SPY/QQQ minute line/candle views, Eastern time ticks including the midpoint, hover/keyboard
OHLC/headline behavior, daily-range switching and the missing-intraday state. Fixtures are synthetic; operator
credentials and live provider data were not available in this workspace.

Release verification: 1,322 offline tests passed (5 opt-in live tests deselected); Ruff, mypy (49 Stocks files),
JavaScript syntax and diff checks passed. Desktop/phone workflows passed for 1D including touch, existing
stock navigation, 5Y/Max charts, calculator/Evidence actions and old-server update recovery.
