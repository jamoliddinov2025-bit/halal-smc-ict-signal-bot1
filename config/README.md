# Configuration — market data and analysis through Phase 6

Five explicit loaders consume separate tables:

- `smcsignal.data.load_data_config(path)` reads only `[market_data]`.
- `smcsignal.analysis.load_analysis_config(path)` reads only `[analysis]`.
- `smcsignal.analysis.load_liquidity_config(path)` reads only `[liquidity]`.
- `smcsignal.analysis.load_displacement_config(path)` reads only `[displacement]`.
- `smcsignal.analysis.load_fvg_config(path)` reads only `[fvg]`.

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

- `displacement.example.toml`: twenty synthetic candles demonstrating default
  prior ATR(14) and bullish/bearish displacement.

- `fvg.example.toml`: twenty synthetic candles with hand-computed bullish/bearish
  FVG creation and actual middle-candle displacement references.

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

## Displacement settings (Phase 5)

```toml
[displacement]
atr_period = 14
min_body_atr = "1.0"
min_range_atr = "1.5"
bullish_close_min = "0.70"
bearish_close_max = "0.30"
atr_floor = "0"
sweep_lookback_bars = 20
```

The loader requires all seven keys and rejects unknown keys. Decimal quantities
are quoted strings; direct Python configuration requires finite `Decimal` values.
Period is an integer 1–1000, and sweep lookback is 0–10000 observed bars (zero
disables association). Multipliers are greater than zero and at most 1000. Close
thresholds satisfy `0 <= bearish <= bullish <= 1`. The absolute ATR floor is
nonnegative, in the price unit passed from the existing liquidity configuration.

Displacement uses a **prior** rolling SMA of true ranges; default first eligibility
is index 15. Body/range/close thresholds are inclusive, but ATR must exceed the
floor strictly. Sweep context is optional, not a signal prerequisite. These are
physical detection settings, not probabilities or Setup Quality Score values.

`DisplacementAnalyzer` consumes existing `LiquiditySnapshot` frames. Pass
`price_unit=liquidity.config.price_unit`; do not invent a second upstream engine or
infer asset eligibility from the unit label. See the complete
[displacement methodology](../docs/displacement-methodology.md).

No score, publication-threshold, quota, strategy, order, or credential settings are
added. Configuration is fixed per replay, with its exact canonical artifact hashed.

## FVG settings (Phase 6)

```toml
[fvg]
min_gap_size = "0"
require_displacement = false
```

Exactly both keys are required. The absolute minimum is a quoted finite nonnegative
Decimal in the Phase 5 frame's existing `price_unit`; units bind on the first valid
input. No duplicate unit declaration or universal asset tick size is inferred.
A gap must be **strictly positive** and at least the configured minimum. There is
no rounding before qualification and no ATR-relative gap filter.

`require_displacement=false` permits geometry without displacement and retains
actual C2 displacement even if opposing. True requires an already-produced
**matching-direction** C2 displacement event. It never reruns Phase 5 detection.
Sweep context is inherited from C2's existing frame and configuration, not selected
again using C3. No lifecycle, scores, signal threshold, quotas, or strategies are
configurable. See [FVG methodology](../docs/fvg-methodology.md).

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
