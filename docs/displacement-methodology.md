# Displacement methodology — Phase 5

## Scope and definition

Phase 5 classifies **one completed candle at a time** as bullish displacement,
bearish displacement, or no displacement using objective configured inequalities.
It is not a visual judgment, probability, quality grade, strategy, entry rule, or
BUY/SELL signal. No performance estimation, backtesting, or execution is included.

The detector consumes the existing **Phase 4 `LiquiditySnapshot` stream**, which
already contains validated observations, actual trend/structure context, pools,
sweeps, and provenance. It does not fetch, normalize, resample, or rerun those
layers. Their implementations and the approved provenance contract are unchanged.

## 1. API and integration

```python
from smcsignal.analysis import DisplacementAnalyzer, DisplacementConfig

displacement = DisplacementAnalyzer(
    DisplacementConfig(),
    price_unit=liquidity.config.price_unit,
)
for candle in closed_candles:
    upstream_frame = liquidity.update(candle)
    frame = displacement.update(upstream_frame)
```

`liquidity` is an existing `LiquidityAnalyzer`; `closed_candles` is its canonical
input sequence. For an already computed tuple/generator of Phase 4 frames, use
`analyze_displacement(frames, config, price_unit=...)`. This batch helper executes
exactly the same updates, without creating another upstream analyzer.

Each frozen `DisplacementSnapshot` retains:

- The original `liquidity` frame, not a copy or recomputed approximation.
- Current closed-candle `metrics`, including raw sizes and descriptive ratios.
- `atr_reference`: the ATR published through **t−1**, used to classify t.
- `atr_current`: ATR through **t**, published now for the **next** candle.
- The selected optional `preceding_sweeps`, without a direction-based filter.
- `events`: an immutable tuple containing zero or one new `DisplacementEvent`.
- Settings, declared price units, and its own provenance snapshot.

The stream must start at upstream index zero and be consecutive. Series,
structure settings, known liquidity settings, price units, preceding-context links,
and chronological availability are validated. A missing or repeated frame is an
error, not an invitation to sort, fill, or silently restart. Known data gaps within
a valid upstream series remain gaps; they are not missing *observed indices*.

## 2. ATR methodology: prior rolling SMA, not Wilder smoothing

For observed candle `i >= 1`, with previous **observed** close `C[i-1]`:

```text
TR[i] = max(H[i] - L[i], abs(H[i] - C[i-1]), abs(L[i] - C[i-1]))
ATR[k] = sum(TR[k-n+1 .. k]) / n
```

- Default period **n = 14**. This is a **simple moving average of true ranges**,
  not Wilder/RMA or EMA smoothing. No hidden alternative ATR method is selected.
- Candle zero supplies only the initial previous-close anchor. There is no
  invented `TR[0]`, zero padding, partial-period mean, or high/low-only seed.
- The first complete ATR uses `TR[1..n]` and is published after candle **n** is
  available. Its record retains all **n+1** raw observations, including the anchor.
- Candidate **t** uses the reference ending at **t−1**, specifically
  `TR[t-n .. t-1]`. **The current candidate is excluded** from its own reference.
- Therefore the first possible displacement is index **n+1**: with default n=14,
  index **15**, the sixteenth observed candle. Fifteen prior observations are
  required. Earlier frames still expose raw metrics, but have no displacement event.
- After classifying t, its true range enters `atr_current`, for use at t+1. This
  removes self-inflation/circularity from the comparison against a large candle.
- Gaps contribute through the previous observed close in TR. Missing time slots
  are not synthesized. Periods count observations, not fixed wall-clock durations.

`ATRReference` retains its period, raw observation window, individual true ranges,
exact total, displayed mean, start/end indices, reference candle, structure context,
and provenance. Its reference prefix hash ends at its own candle, not the candidate.

### Availability and insufficient/degenerate references

An ATR reference is available no earlier than its last observation and all its
inputs are known. For displacement, the prior reference must be available **by the
candidate's actual assessment time**. A delayed reference may arrive during the
candidate bar; classification is still after close and uses only prior-index data.
It is not claimed to have been known at the candidate open.

When history is insufficient, `atr_reference` is `None`. If the reference is zero,
there is no event and ATR-relative ratios are `None`; no division by zero, epsilon,
minimum placeholder ATR, or fabricated success occurs. A configured absolute
`atr_floor` can suppress small references: the exact ATR must be **strictly greater
than** that floor. The default floor is **zero**, preserving scale invariance for
positive near-zero prices rather than hard-coding an asset-dependent epsilon.

## 3. Candle metrics and exact qualification

For a completed candidate candle:

```text
body = abs(close - open)
range = high - low
close_location = (close - low) / range
body_atr = body / prior_ATR
range_atr = range / prior_ATR
```

`close > open` means bullish candle direction; `close < open` means bearish.
A doji (`close == open`) is nondirectional and never a displacement event.
Zero-range candles have no close-location ratio and never qualify.

With a complete usable prior ATR, **all** applicable conditions must pass:

| Requirement | Bullish | Bearish |
| --- | --- | --- |
| Direction | Close strictly above open | Close strictly below open |
| Body | `body >= min_body_atr × ATR_ref` | Same |
| Total high/low range | `range >= min_range_atr × ATR_ref` | Same |
| Close location | `close_location >= bullish_close_min` | `close_location <= bearish_close_max` |

Equality at each of these four **configured thresholds is accepted**; equality
at the absolute ATR floor is not. No trend, sweep, candle color sequence, cooldown,
quota, or undocumented body/range-dominance condition adds another filter.

Consequently, a long-wick candle with insufficient body or poor close location
fails, but a long-wick candle satisfying all declared criteria still qualifies.
Large body alone and large range alone are each insufficient. Consecutive
qualifying candles produce separate events; they are not merged into episodes.

### Gaps are not body size

Body and total range are strictly the current candle's OHLC distances. An opening
gap does not enlarge body or range. A gap-only candle with a small actual body
fails even if its true range is large. Gapped candles with genuinely sufficient
body/range/close location can qualify. The current gap enters ATR for **later**
candidates only. This deliberately differs from Phase 4's sweep gap policy.

### Numeric precision and boundaries

All source prices and finite differences/totals remain Decimal, with exact local
arithmetic independent of ambient precision, rounding, or traps. For comparisons,
no rounded ATR or close-location quotient is used:

```text
body × n >= min_body_atr × sum(TR)
range × n >= min_range_atr × sum(TR)
close - low >= bullish_close_min × range       # bullish
close - low <= bearish_close_max × range        # bearish
sum(TR) > atr_floor × n
```

Displayed ATR means and ratios are **50-significant-digit, round-half-even**
Decimals. They are descriptive physical measurements, not confidence or quality
values. A ratio can display exactly a threshold after rounding while the exact
cross-product correctly rejects a value infinitesimally below/above it. Raw OHLC,
TR totals, and period remain available to reproduce the exact decision.

Each exact arithmetic operation is limited to **4096 required digits**, including
span/carry allowance. Unsupported numeric spans or Decimal exponent overflow/
underflow raise `AnalysisInputError`, never silently change a threshold result.
Very small same-scale prices remain supported; a large exponent alone does not
force a huge fixed-point expansion. No binary floats are used for these decisions.

## 4. Optional relationship to liquidity sweeps

A sweep is **context, not a displacement prerequisite or a strategy rule**.
For candidate t, eligible sweeps must:

1. Come from an earlier accepted upstream candle, never t itself.
2. Fall within `1 <= t - sweep_index <= sweep_lookback_bars` observed bars.
3. Have been confirmed/available **by the candidate candle's open**.
4. Match the declared series and price units.

Default lookback is **20 observed bars**; zero disables association, not detection.
Choose the **latest eligible sweep candle** and retain **all** its sweep events in
the upstream's deterministic order. There is no arbitrary selection between
multiple pools/sides on that candle. The event exposes exact immutable references
and `SweepContext`: `none`, `after_buy_side`, `after_sell_side`, or `after_both`.

The choice is direction-neutral: bullish displacement after buy-side sweeps and
bearish displacement after sell-side sweeps are permitted. A strategy-specific
“raid then reversal” rule is explicitly deferred. A sweep can contextualize multiple
later displacement candles; there is no trade-entry consumption rule.

Same-candle sweeps remain in the retained Phase 4 frame but are **not preceding
context**. A sweep arriving during the candidate bar is not backdated to its open;
it may be eligible for a later bar. If a newer sweep is not yet eligible, the
latest older eligible cohort can be used. `none` means none in this configured
eligible window, not proof that no sweep occurred anywhere in market history.

## 5. Immutable event contract and provenance

`DisplacementEvent` satisfies the approved `ProvenancedEvidence` composition
contract. It contains or exposes:

- Explicit symbol, timeframe, price unit, and candle direction (not an order side).
- `start_index`, `end_index`, and `detection_index`, all equal to t for this
  single-candle methodology. There is no provisional start or later range rewrite.
- `timestamp`: the candle-opening identifier; `available_at`: the actual closed/
  supplied availability instant at which the event is knowable.
- The original `ObservedCandle` with full OHLCV and candle references.
- Exact body/range, descriptive ATR-relative sizes and close location, and the full
  sourced `ATRReference` ending at t−1.
- Frozen threshold settings and `threshold_version = displacement-v1`.
- Optional exact `preceding_sweep_references` and the original sweep snapshots.
- Actual historical structure context and an immutable provenance snapshot with
  a deterministic `event_id`, never a wall-clock/random ID.

The existing Phase 4 canonical JSON codec and provenance factory are reused.
The original upstream frame instance and its consumed-input-prefix hash are reused,
not re-hashed with a second upstream pipeline. New configuration artifacts bind
methodology, price units, settings, numeric precision, and resource-limit version.
`configuration_artifact` and `atr_configuration_artifact` expose the exact bytes
whose SHA-256 hashes appear in provenance. ATR identity does not unnecessarily
change when only displacement body/close/context thresholds change.

An event depends on current structure context, **prior ATR**, and the selected
prior sweeps. It does not depend on `atr_current`. The enclosing frame can reference
both ATRs and its event without cycles. IDs bind this evidence, the configuration,
raw metrics, and the already-known upstream prefix. A later candle never renames
an earlier event. Numerically equivalent Decimal spellings produce the same IDs.

`evidence_json` retains the full nested raw evidence and exact Decimal strings.
As before, source/configuration artifacts and evidence archives are caller-owned;
no database, archive service, transport, or credentials are implemented here.

## 6. No-look-ahead and replay contract

For identical valid upstream prefixes, configuration, price units, replay origin,
and availability annotations:

- Every-prefix results equal the corresponding full-series prefix, including
  metrics, events, ATR windows, dependencies, IDs, and provenance hashes.
- Replacing or appending future candles cannot alter historical outputs.
- Batch, individual streaming updates, and arbitrary chunk boundaries are identical.

Each update reads only prior local state and the current already-completed Phase 4
frame. It classifies using prior ATR/sweeps, calculates the next ATR, then commits
new state. A rejected input or arithmetic/model failure leaves this detector's
state unchanged. The detector does **not** own or rewind a separately advanced
upstream analyzer; callers must retain/replay valid upstream frames when retrying.

Tests include hand-computed nonempty events and actual sweep relationships, seeded
all-prefix/future-shock checks, independent reconstruction of true ranges, graph
resolution, identity/artifact checks, data-provider integration, and immutable
retained objects. Tests are offline, not a trading-performance backtest.

## 7. Defaults and configuration

| Setting | Default | Constraint / meaning |
| --- | --- | --- |
| `atr_period` | 14 | Integer 1–1000; number of prior true ranges |
| `min_body_atr` | 1.0 | Positive Decimal, at most 1000; inclusive multiplier |
| `min_range_atr` | 1.5 | Positive Decimal, at most 1000; inclusive multiplier |
| `bullish_close_min` | 0.70 | Inclusive lower close-location threshold |
| `bearish_close_max` | 0.30 | Inclusive upper close-location threshold |
| `atr_floor` | 0 | Nonnegative Decimal in declared price units; strict floor |
| `sweep_lookback_bars` | 20 | Integer 0–10000; observed-bar context window, 0 disables |

Close thresholds require `0 <= bearish_close_max <= bullish_close_min <= 1`.
TOML requires exactly these seven `[displacement]` keys; decimal quantities must
be quoted strings. Direct Python construction uses `Decimal` values. Price units
come explicitly from the existing liquidity configuration, not a duplicated
setting or inferred asset eligibility.

`config/displacement.example.toml` uses twenty **synthetic** candles and all default
thresholds. Fourteen prior true ranges equal 2. The expected events are:

| Index | Direction | Body | Range | Prior ATR | ATR end index | Sweep context |
| --- | --- | --- | --- | --- | --- | --- |
| 15 | Bullish | 3 | 5 | 2 | 14 | None |
| 16 | Bearish | 5 | 7 | 31/14 | 15 | None |

Indices 17–19 demonstrate doji, long-wick/small-body, and gap-only rejection.
These are hand-audited software examples, not real market observations or returns.

## 8. Limitations and phase boundary

- Single-candle displacement only; no multi-bar episode, FVG, block, session,
  OTE, entry/exit, or strategy interpretation is generated.
- SMA ATR is a chosen convention, not Wilder ATR. Thresholds are initial explicit
  defaults, not fitted, validated trading-performance parameters.
- Positive near-zero ATR can generate large raw relative ratios; configure an
  appropriate price-unit floor when source/tick precision requires one.
- Long wicks and gaps are governed only by the stated objective criteria; there
  is no additional body-dominance or visual “clean candle” filter.
- Sweep context is a bounded temporal association, not proof of causation or a
  required setup. No multi-timeframe alignment/signal engine is implemented.
- Core state keeps period+1 observations and recent sweep cohorts, but a cohort
  can contain many rich upstream events. Retained output/JSON size grows with
  history and ATR windows; Phase 4's unbounded pool/member history limits still
  apply. No production-throughput or bounded-total-memory claim is made.
- Source truth, vendor revisions, availability annotations, shifted rolling-history
  restarts, and archive persistence retain their existing documented limitations.

The future Setup Quality Score policy remains documentation only: scale 0–100,
future configurable publication threshold default 75, quality over quantity,
zero signals valid, and no signal quotas. **No scoring is implemented in Phase 5.**

Phase 5 itself added no FVG, Order Block, Breaker Block, Mitigation Block,
Premium/Discount, OTE, session strategy, multi-timeframe signal engine, BUY/SELL
signal, Telegram, chart, halal filter, backtesting, live trading, credentials,
or order execution.
Phase 6 now consumes these unchanged frames in a separate
[FVG creation engine](fvg-methodology.md); no displacement rule above is changed.
Phase 7 adds [Order Block formation](order-block-methodology.md), consuming the
exact displacement event without changing its definition.
**Stop after Phase 7. Phase 8 — Premium/Discount / PD Arrays requires explicit approval.**
