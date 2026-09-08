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
