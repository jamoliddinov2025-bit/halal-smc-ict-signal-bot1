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
