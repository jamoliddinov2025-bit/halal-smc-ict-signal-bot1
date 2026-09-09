# Configuration — market data and analysis through Phase 19

Twenty-two explicit loaders consume separate tables:

- `smcsignal.data.load_data_config(path)` reads only `[market_data]`.
- `smcsignal.analysis.load_analysis_config(path)` reads only `[analysis]`.
- `smcsignal.analysis.load_liquidity_config(path)` reads only `[liquidity]`.
- `smcsignal.analysis.load_displacement_config(path)` reads only `[displacement]`.
- `smcsignal.analysis.load_fvg_config(path)` reads only `[fvg]`.
- `smcsignal.analysis.load_order_block_config(path)` reads only `[order_blocks]`.
- `smcsignal.analysis.load_pd_config(path)` reads only `[premium_discount]`.
- `smcsignal.analysis.load_mss_config(path)` reads only `[mss]`.
- `smcsignal.analysis.load_breaker_block_config(path)` reads only `[breaker_blocks]`.
- `smcsignal.analysis.load_mitigation_block_config(path)` reads only `[mitigation_blocks]`.
- `smcsignal.analysis.load_ote_config(path)` reads only `[ote]`.
- `smcsignal.analysis.load_mtf_config(path)` reads only `[mtf]`.
- `smcsignal.analysis.load_halal_filter_config(path)` reads only `[halal_filter]`.
- `smcsignal.analysis.load_setup_quality_config(path)` reads only `[setup_quality]`.
- `smcsignal.analysis.load_signal_eligibility_config(path)` reads only `[signal_eligibility]`.
- `smcsignal.analysis.load_signal_engine_config(path)` reads only `[signal_engine]`.
- `smcsignal.analysis.load_outcome_tracking_config(path)` reads only `[outcome_tracking]`.
- `smcsignal.analysis.load_indicators_config(path)` reads only `[indicators]`.
- `smcsignal.analysis.load_setup_attribution_config(path)` reads only `[setup_attribution]`.
- `smcsignal.analysis.load_performance_config(path)` reads only `[performance]`.
- `smcsignal.analysis.load_review_config(path)` reads only `[review]`.
- `smcsignal.analysis.load_visualization_config(path)` reads only `[visualization]`.

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

- `order-block.example.toml`: 24 synthetic candles, compact three-candle fractals,
  default displacement ATR(14), and default confirmed OB formation.

- `premium-discount.example.toml`: seven synthetic candles illustrating confirmed
  bullish/bearish ranges and all five PD classifications.

- `mss.example.toml`: 28 synthetic observations with default displacement ATR(14)
  and strict bullish/bearish MSS confirmations and evidence relationships.

- `breaker-block.example.toml`: 28 synthetic observations with strict first-close
  conversions of actual earlier OBs using existing displacement/MSS evidence.

- `mitigation-block.example.toml`: 25 synthetic observations with first interior
  overlaps of actual earlier OBs, including a later Breaker that does not rewrite
  an already-published mitigation.

- `ote.example.toml`: ten synthetic candles illustrating a confirmed bullish
  dealing range and all four OTE close classifications, including exact 0.62/0.79
  boundaries.

- `mtf.example.toml`: synthetic 15m primary history from 08:00–12:00 with
  independent 1h/4h OTE frames demonstrating completed-candle HTF eligibility.

- `halal-filter.example.toml`: the Phase 13 synthetic MTF history plus the
  default allow-list registry (`BTCUSDT`, `ETHUSDT`, `BNBUSDT`, `SOLUSDT`).
- `setup-quality.example.toml`: the same synthetic MTF/halal history plus
  integer `publish_threshold = 75`.
- `signal-eligibility.example.toml`: the same synthetic history plus frozen
  eligibility-v1 `enabled = true` and `conflict_policy = "neutral"`.
- `indicators.example.toml`, `setup-attribution.example.toml`,
  `performance.example.toml`, `review.example.toml`, and
  `visualization.example.toml`: the same synthetic history plus the Phase 19
  `[indicators]`, `[setup_attribution]`, `[performance]`, `[review]`, and
  `[visualization]` tables.
- `outcome-tracking.example.toml`: the same synthetic history plus the
  fixed-horizon outcome table; zero outcomes at default threshold 75.
- `signal-engine.example.toml`: the same synthetic history plus frozen
  signal-engine-v1 `enabled = true`, `publish_threshold = 75`, `spot_only = true`,
  and `duplicate_policy = "one_per_setup"`.

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

## Order Block settings (Phase 7)

```toml
[order_blocks]
max_candidate_lookback = 10
candidate_selection = "nearest"
zone_basis = "full_range"
allow_doji = false
structure_requirement = "bos_or_choch"
require_fvg = false
```

Exactly six keys are required. Lookback is 1–1000 observed bars. Selection is
`nearest` or `earliest`; one candidate is selected, never an implicit cluster/all
mode. Zone is `full_range` or `body`. Doji override and FVG requirement are strict
booleans. Structure is `bos_or_choch`, `bos`, `choch`, or `displacement_only`.
Direct construction uses the exported typed enums and frozen `OrderBlockConfig`.

Displacement is always mandatory and must match direction and close strictly
beyond the chosen zone. Structure, when required, must be on the displacement's
own candle. Requiring FVG delays publication only to the next observed candle with
an exact matching C2 displacement reference; it never backfills or changes the
candidate selection at C3. No mitigation, lifecycle, score, quantity target, entry,
or execution switch exists. Price units are inherited from upstream frames.
See [Order Block methodology](../docs/order-block-methodology.md).

## Premium/Discount settings (Phase 8)

```toml
[premium_discount]
equilibrium_half_width_fraction = "0"
```

The table requires exactly this one key. It is a quoted finite Decimal in [0,0.5],
representing the half-width of the equilibrium band as a fraction of the range
span. Zero means only the exact midpoint; 0.05 means 45%–55% of the range.
Range and equilibrium boundaries are inclusive. Prices outside the selected range
are classified separately, never as a clipped premium/discount value.

`PDAnalyzer` consumes the existing Phase 7 stream and inherits its series/units.
There is no independent swing detector, older-range fallback, price-point fitting,
HTF execution flag, score threshold, or strategy setting. Sidecars preserve the
exact original evidence IDs. See
[Premium/Discount methodology](../docs/premium-discount-methodology.md).

## Market Structure Shift requirements (Phase 9)

```toml
[mss]
enable_displacement_requirement = true
enable_structure_requirement = true
```

The loader requires exactly these two boolean keys. **Both must remain true** in
strict `mss-v1`. False is an explicit configuration error: it does not silently
turn a move without displacement/structure into an MSS. These are invariant
requirement declarations, not optimization or scoring switches.

The existing prior directional structure and break level must be known before
the current candle opens. A same-candle existing CHoCH and matching displacement
confirm MSS at actual observation availability. Other existing evidence is
context only. No MSS price, score, probability, quota, entry, or output-threshold
settings are exposed. See [MSS methodology](../docs/mss-methodology.md).

## Breaker formation settings (Phase 10)

```toml
[breaker_blocks]
require_displacement = true
require_mss = true
invalidation_basis = "close_through_far_boundary"
zone_basis = "original_order_block"
```

Exactly four keys are required. Both requirement flags must remain true in strict
`breaker-v1`. The rule values accept only those shown; direct Python construction
uses the typed `InvalidationBasis` / `BreakerZoneBasis` enums. No alternate wick,
zone, score, probability, signal, retest, or confirmation-delay mode is provided.

An original OB must already be known by the first violating candle's open. The
close must strictly cross its opposing far boundary with matching same-candle
displacement and MSS. Rejected first violations are recorded, never retrospectively
upgraded. All independently qualifying source IDs are retained; there is no
nearest-only ranking or silent history expiry/cap. FVG and PD are existing context,
not new requirements or recomputed outputs. See
[Breaker methodology](../docs/breaker-block-methodology.md).

## Mitigation first-interaction settings (Phase 11)

```toml
[mitigation_blocks]
interaction_basis = "range_intersection"
first_interaction_only = true
ignore_after_breaker = true
```

Exactly three keys are required. Both booleans must remain true in strict
`mitigation-v1`. The interaction value accepts only `range_intersection`; direct
Python construction uses the typed `InteractionBasis` enum. No wick-only-as-invalid,
repeated-event, post-Breaker-first-mitigation, score, probability, signal, or
retest mode is provided.

An original OB must already be known by the interaction candle's open. The completed
candle range must intersect the open interval of the original zone. Exact endpoint
touches and total misses do not qualify. All independently qualifying source IDs
are retained; there is no nearest-only ranking or silent history expiry/cap. A
confirmed Breaker retires remaining first-mitigation eligibility; already-published
mitigations stay immutable. See
[Mitigation methodology](../docs/mitigation-block-methodology.md).

## Optimal Trade Entry settings (Phase 12)

```toml
[ote]
lower_retracement = "0.62"
upper_retracement = "0.79"
boundary_policy = "inclusive"
price_basis = "close"
```

Exactly four keys are required. Retracements are quoted finite Decimals satisfying
`0 < lower_retracement < upper_retracement < 1`. Direct Python construction uses
finite `Decimal` values; unquoted TOML numbers are rejected. The engine uses those
exact ratios and does not substitute 0.618/0.786. `boundary_policy` accepts only
`inclusive`. `price_basis` accepts only `close`.

`OTEAnalyzer` consumes existing Phase 8 frames and inherits series/units. A close
is classified only against the current dealing range if that range was known before
the bar opened. Missing or not-yet-known ranges yield `INSUFFICIENT_CONTEXT` with
no older-range fallback. No score, entry, stop, target, or multi-timeframe setting
is exposed. See [OTE methodology](../docs/ote-methodology.md).

## Multi-timeframe confluence settings (Phase 13)

```toml
[mtf]
enabled = true
primary_timeframe = "15m"
higher_timeframes = ["1h", "4h"]
availability_policy = "completed_candle"
```

Exactly four keys are required. `enabled` must be true. Higher timeframes must be
a nonempty array of unique supported fixed-duration intervals that are strictly
longer integer multiples of the primary. `1M` and non-multiples such as 3d/1w are
rejected. `availability_policy` accepts only `completed_candle`.

`MTFAnalyzer` consumes existing Phase 12 frames per timeframe. HTF evidence is
eligible only when `available_at <= primary open`. Multiple HTFs stay independent;
disagreement is `MIXED` with no score. Unknown keys, including weight or signal
settings, are rejected. See [MTF methodology](../docs/mtf-confluence-methodology.md).

## Halal asset filter settings (Phase 14)

```toml
[halal_filter]
mode = "allow_list"

allowed_assets = [
  "BTCUSDT",
  "ETHUSDT",
  "BNBUSDT",
  "SOLUSDT"
]
```

Allow-list tables require exactly `mode` and `allowed_assets`. Deny-list tables
require exactly `mode` and `denied_assets`. Listed allow-list assets are HALAL;
everything else is UNKNOWN. Listed deny-list assets are HARAM; everything else
is UNKNOWN. Deny-list mode never emits HALAL. Symbols use the market-data
contract and are normalized to uppercase. Unknown keys, including score, signal,
or scraping settings, are rejected.

`HalalFilterAnalyzer` consumes existing Phase 13 frames. The filter does not
fetch the internet or make autonomous religious decisions. UNKNOWN is never
silently treated as HALAL. See
[halal filter methodology](../docs/halal-filter-methodology.md).

## Setup quality settings (Phase 15)

```toml
[setup_quality]
publish_threshold = 75
```

The table requires exactly this one integer key in 0–100. Direct
`SetupQualityConfig()` defaults to 75. Component weights are frozen
methodology constants and are not TOML-tunable. Unknown keys, including
weights, scores, signals, probabilities, and ranks, are rejected.

`SetupQualityAnalyzer` consumes existing Phase 14 frames. HARAM and UNKNOWN
force total 0. Missing nested evidence contributes 0 points. `threshold_passed`
is a quality flag, not a BUY/SELL signal. See
[setup quality methodology](../docs/setup-quality-methodology.md).

## Signal eligibility settings (Phase 16)

```toml
[signal_eligibility]
enabled = true
conflict_policy = "neutral"
```

The table requires exactly these two keys. `enabled` must be true.
`conflict_policy` accepts only `neutral`. Direct `SignalEligibilityConfig()`
uses the same defaults. Unknown keys, including signal, entry, buy, sell,
Telegram, and ranking options, are rejected.

`SignalEligibilityAnalyzer` consumes existing Phase 15 frames. Eligibility
requires HALAL plus the upstream SQS threshold flag. Conflicting nested votes
stay `NEUTRAL`. Missing evidence abstains. `ELIGIBLE` is not a BUY/SELL signal.
See [signal eligibility methodology](../docs/signal-eligibility-methodology.md).

## Spot signal engine settings (Phase 17)

```toml
[signal_engine]
enabled = true
publish_threshold = 75
spot_only = true
duplicate_policy = "one_per_setup"
```

The table requires exactly these four keys. `enabled` and `spot_only` must remain
true. `publish_threshold` is an integer in 0–100 and must match the consumed SQS
threshold. `duplicate_policy` accepts only `one_per_setup`. Direct
`SignalEngineConfig()` uses the same defaults. Unknown keys, including entry,
stop, target, Telegram, and ranking options, are rejected.

`SignalEngineAnalyzer` consumes existing Phase 16 frames. Eligible `LONG_BIAS`
maps to `BUY_SIGNAL`. Eligible `SHORT_BIAS` maps to `BEARISH_AVOID` and never a
short trade. Missing HALAL, a failed threshold, or `NEUTRAL` maps to `NO_SIGNAL`.
Under `one_per_setup`, later BUY candidates that share a published setup identity
become `NO_SIGNAL` with reason `duplicate_setup`. See
[signal engine methodology](../docs/signal-engine-methodology.md).

## Outcome tracking settings (Phase 18)

```toml
[outcome_tracking]
enabled = true
horizon_bars = 10
```

The table requires exactly these two keys. `enabled` must remain true.
`horizon_bars` is an integer from 1 to 10 000 counting completed primary
candles after the signal candle. Direct `OutcomeTrackingConfig()` uses the same
defaults. Unknown keys, including entry, stop, target, fee, slippage, and
position-size options, are rejected; no trading knob exists.

`OutcomeTrackingAnalyzer` consumes existing Phase 17 frames. Only `BUY_SIGNAL`
publications open outcomes. The reference price is the signal candle close;
WIN/LOSS/FLAT uses the exact sign of the final close difference; MFE/MAE are
running evaluation-candle extremes. Open outcomes are never flushed at
end-of-series. See
[outcome tracking methodology](../docs/outcome-tracking-methodology.md).

## Indicators settings (Phase 19a)

```toml
[indicators]
enabled = true
ema_periods = [20, 50]
rsi_period = 14
volume_average_period = 20
```

The table requires exactly these four keys. `enabled` must remain true.
`ema_periods` is a nonempty strictly-increasing list of integers from 1 to
1000; `rsi_period` and `volume_average_period` are integers from 1 to 1000.
Indicators are context and visualization support only: they can never
generate, gate, or veto a signal, and no decision module reads them. ATR is
reused from Phase 5. Unknown keys, including thresholds or gates, are
rejected. See [indicators methodology](../docs/indicators-methodology.md).

## Setup attribution settings (Phase 19b)

```toml
[setup_attribution]
enabled = true
```

The table requires exactly this key. Attribution projects already-published
nested facts onto a closed twelve-label taxonomy; only `BUY_SIGNAL` frames
carry profiles, labels are outcome-independent, and combination keys join
labels in canonical order. There are no thresholds, weights, or outcome
settings. See
[setup attribution methodology](../docs/setup-attribution-methodology.md).

## Performance settings (Phase 19c)

```toml
[performance]
enabled = true
minimum_finalized_for_ranking = 10
```

The table requires exactly these two keys. `minimum_finalized_for_ranking`
is an integer from 1 to 1000 gating best/worst combination quotations; raw
counts are always shown regardless. Performance recomputes descriptive
statistics over published outcome records and attribution profiles; statuses
are copied, never reclassified. See
[performance methodology](../docs/performance-methodology.md).

## Review settings (Phase 19d)

```toml
[review]
enabled = true
minimum_finalized_for_comparison = 10
```

The table requires exactly these two keys. `minimum_finalized_for_comparison`
is an integer from 1 to 1000; month-over-month deltas are quoted only when
both months reach it, and sample sizes are always shown. Review renders one
performance report as plain text. See
[monthly review methodology](../docs/monthly-review-methodology.md).

## Visualization settings (Phase 19e)

```toml
[visualization]
enabled = true
svg_width = 800
svg_height = 400
text_rows = 24
```

The table requires exactly these four keys. `svg_width`/`svg_height` are
integers from 100 to 10000 and `text_rows` from 5 to 200. Drawings carry
semantic style tokens, never colors; output is deterministic SVG and plain
text only — no PNG, raster, or Telegram transport. Visualization consumes
published facts and never re-detects. See
[visualization methodology](../docs/visualization-methodology.md).

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
