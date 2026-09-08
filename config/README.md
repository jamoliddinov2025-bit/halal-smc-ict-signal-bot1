# Configuration — market data and analysis through Phase 4

Three explicit loaders consume separate tables:

- `smcsignal.data.load_data_config(path)` reads only `[market_data]`.
- `smcsignal.analysis.load_analysis_config(path)` reads only `[analysis]`.
- `smcsignal.analysis.load_liquidity_config(path)` reads only `[liquidity]`.

Loading settings does not fetch data or run analysis. The CLI remains informational.

## Included examples

- `example.toml`: original five-candle **synthetic** offline data example with the
  default five-candle fractal. This tiny monotonic sample has insufficient swing
  evidence for a ready trend; no results are fabricated to overcome warm-up.
- `binance-public.example.toml`: unauthenticated Binance Spot source plus default
  analysis settings; network access requires an explicit provider fetch.
- `analysis.example.toml`: fourteen **synthetic** candles and a three-candle fractal
  for the hand-computed BOS/CHoCH demonstration.

- `liquidity.example.toml`: twelve synthetic candles with hand-computed Phase 4
  equal-high and equal-low sweeps.

## Market data settings (unchanged)

| Key | Contract |
| --- | --- |
| `symbol` | Required exchange symbol, e.g. `BTCUSDT`; 2–30 ASCII alphanumeric characters, normalized to uppercase. |
| `timeframe` | Required, case-sensitive supported interval; `1m` means minute, `1M` means month. |
| `data_source` | Required: `csv` or `binance_public`; no fallback or automatic selection. |
| `history_limit` | Required integer from 1 to 1000; return up to this many candles, oldest-first within the latest validated window. |
| `csv_path` | Required only for CSV and forbidden for Binance. Relative TOML paths resolve against the TOML directory. |
| `missing_value_policy` | `error` (default) or explicit whole-row `drop`; never fill/fabricate prices. |
| `timeout_seconds` | Finite number greater than zero and at most 60; default 10. HTTP I/O only. |

Unknown market-data settings, including credentials, are rejected. Direct
`MarketDataConfig` construction resolves relative CSV paths against the current
working directory at construction. Files are not opened by provider construction.

## Fractal settings (Phase 3, unchanged)

```toml
[analysis]
fractal_length = 5
```

The table must contain **exactly** `fractal_length`: an odd integer from 3 to 1001.
It is the total window width, not the count on each flank. Five means two left
candles + pivot + two right candles, with a two-candle confirmation delay.

`AnalysisConfig()` defaults to 5 for direct Python use; the TOML loader requires
an explicit table/key so a missing/typoed setting cannot silently choose another
methodology. The upper bound limits scan/buffer size, not the amount of available
source history. Two highs and two lows may require substantially more candles
than a single window; `TrendState.ready` distinguishes insufficient evidence.

Settings are frozen during a stream. Use a new analyzer for a different length,
symbol/timeframe, or replay starting point. There is no wick-break toggle, event
lookback fitting, future-data setting, or excluded-feature configuration in Phase 3.

## Liquidity settings (Phase 4)

```toml
[liquidity]
price_unit = "USDT"
equal_tolerance_bps = "0"
```

The table requires exactly these two keys. `price_unit` is an explicit nonempty
label, not inferred asset eligibility. Tolerance is a quoted decimal string from
0 through 1000 basis points with at most eight meaningful fractional places.
Direct `LiquidityConfig("USDT")` defaults to exact equality (zero tolerance).
This is geometric price tolerance, not a quality value or a publication threshold.

The first confirmed swing anchors the band permanently; matching members do not
recenter it. Fractal settings still come from `[analysis]`, unchanged. Source
series identity and fixed dataset/replay origin are supplied explicitly through
`SeriesProvenance`; they are not inferred by opening/hashing a whole future CSV.

No scoring table, active quality threshold, signal-count target, multi-bar reclaim
window, pool expiry, or unapproved feature setting is available. Unknown keys are
errors. See [Phase 4 methodology](../docs/liquidity-sweep-methodology.md).

## Metadata, secrets, and artifacts

`[project]`, `[scope]`, and `[safety]` in the original example describe intent only;
they are not a runtime trading-policy or halal-screening engine. No authenticated
exchange access, orders, margin, leverage, or short selling can be enabled.

Never add API keys, tokens, passwords, or account data. Public klines need no keys.
Local `config/local*.toml` / `config/secrets*.toml` and downloaded root `data/` files
are ignored; ignore rules are not secret management. `MANIFEST.in` explicitly
includes only the example configurations and README, not arbitrary local files.

See [market data methodology](../docs/market-data-methodology.md),
[market structure definitions](../docs/market-structure-methodology.md), and
[no-look-ahead guarantees](../docs/no-look-ahead.md).
