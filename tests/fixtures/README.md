# Market data fixtures

`ohlcv.csv` contains five **synthetic** 15-minute candles for deterministic
software tests and the offline configuration example. Its declared BTCUSDT symbol
is only a test label: these are not Binance observations or trading evidence.
Do not use this fixture for performance evaluation or investment decisions.

`market_structure.csv` contains fourteen **synthetic** candles for the Phase 3
methodology example. With `fractal_length=3`, its hand-computed event indices are:
6 (bullish BOS), 8 (bearish CHoCH), 12 (bearish BOS), and 13 (bullish CHoCH).
The tiny prices and BTCUSDT label are for software verification only, not real
market observations, an asset-eligibility claim, or evidence of trading performance.

`liquidity.csv` contains twelve **synthetic** Phase 4 candles. With a three-candle
fractal and exact equality, a buy-side sweep of equal highs at 16 is confirmed at
index 9 (high 17, close 12); a sell-side sweep of equal lows at 9 is confirmed at
index 10 (low 8, close 12). The BTCUSDT label and tiny prices are for software
verification, not real market observations, asset eligibility, or trading results.

`displacement.csv` contains twenty **synthetic** Phase 5 candles. Default ATR(14)
uses prior true ranges only. The expected single-candle displacement events are
index 15 (bullish, body 3, range 5, prior ATR 2) and 16 (bearish, body 5, range 7,
prior ATR 31/14). There is no preceding sweep required. Later doji, small-body/long-wick,
and gap-only examples do not qualify. These are software checks, not historical
exchange observations, trading signals, eligibility evidence, or performance data.

`fvg.csv` contains twenty **synthetic** Phase 6 candles. Default settings create a
bullish FVG at index 16 with boundaries [101, 104] and a bearish FVG at index 19
with boundaries [98, 106]. Their actual C2 displacement events are at indices 15
and 18. This demonstrates formation evidence only, not fills, trade entries,
resting orders, market returns, eligibility, or live exchange availability.

`order_blocks.csv` contains twenty-four **synthetic** candles. With the example's
three-candle fractals, unchanged default displacement thresholds/ATR(14), and
default OB settings, candidate 18 is confirmed bullish by displacement/BOS at 20;
candidate 21 is confirmed bearish by displacement/CHoCH at 22. Requiring the exact
next-candle FVG delays publication to 21 and 23 respectively. This is formation
verification only, not exchange observations, entries, fills, or performance data.

`premium_discount.csv` contains seven **synthetic** observations. With three-candle
confirmed fractals, the last four snapshots classify closes as PREMIUM, DISCOUNT,
EQUILIBRIUM, and OUTSIDE_RANGE; the first three lack a confirmed opposing pair.
Ranges are [11,16] at indices 3–5 and [11,14.5] at 6. These are mathematical software
checks, not real prices, entries, scores, profitability, or asset eligibility.

`mss.csv` contains 28 **synthetic** Phase 9 observations. With the example's compact
three-candle fractals and unchanged default displacement thresholds/ATR(14), bearish
MSS at 22 breaks known low 13 with close 11; bullish MSS at 27 breaks known high 15
with close 30. The actual prior-control, CHoCH, displacement, and optional context
are retained. These are software-verification facts, not signals or performance.
