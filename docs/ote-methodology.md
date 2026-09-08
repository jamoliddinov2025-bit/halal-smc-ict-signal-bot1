# Optimal Trade Entry methodology — Phase 12

## Scope and inputs

Phase 12 evaluates deterministic **local-series** Optimal Trade Entry (OTE) location
using an existing Phase 8 dealing range. It is an analysis/evidence layer, not a
trade direction, entry, stop, target, risk rule, position size, score, or
performance claim.

`OTEAnalyzer.update` consumes an existing Phase 8 **`PDSnapshot`**. Nested frames
already contain Phase 3 structure and the confirmed `DealingRange`. This layer does
not rerun or modify swings, trend, BOS/CHoCH, liquidity, sweeps, displacement, FVG,
Order Blocks, Premium/Discount, MSS, Breakers, or Mitigation, nor their existing
evidence IDs.

OTE is **location/context only**. Each `OTESnapshot` retains the original upstream
frame and exposes:

- `OTEZone` — Fibonacci-style retracement interval of the current causal range
- `OTEObservation` — classification of the completed candle's **close**
- one of `INSIDE_OTE`, `BELOW_OTE`, `ABOVE_OTE`, or `INSUFFICIENT_CONTEXT`

## 1. Source range

The only range source is the current Phase 8 `DealingRange` on the consumed
snapshot. Phase 8 already selected the latest confirmed opposing swing pair and
oriented it from pivot order (low-then-high is bullish; high-then-low is bearish).
OTE does not detect pivots, extend prices, fit a more convenient older pair, or
search a lookback.

If the current snapshot has no valid dealing range (`missing_swings`, `same_pivot`,
or `nonpositive_span`), the result is `INSUFFICIENT_CONTEXT`. There is **no silent
fallback** to an older range.

Phase 8 publishes **one current causal range** per snapshot. OTE-v1 therefore
evaluates at most one OTE zone per candle: the zone derived from that range, and
only if the range was known before the classified candle opened. If a later Phase 8
implementation exposed multiple concurrently valid ranges, each would receive an
independent `OTEZone` in original source publication order; those zones would not
be merged, ranked, or reduced to a nearest-only result. Historical observations
remain bound to the range that was causal when they were published.

## 2. Confirmation and observation timing

The range and its anchors must have been known **before** the candle being
classified:

```text
range.confirmation_index < evaluation.index
and range.available_at <= evaluation.opened_at
```

A dealing range is first published on its latest endpoint's confirmation close.
That confirmation candle **cannot** be classified against the new zone. The next
completed candle may use it, provided the actual `available_at` instant is not
later than that candle's open.

This is stricter than Phase 8 close-time PD classification. An OTE observation
cannot use a dealing range that became available only because of the candle itself
or a future candle. Delayed arrival annotations are preserved. Opening timestamps
identify candles; they do not mean a close-based result was known at the open.

Between new Phase 8 confirmations, the **same immutable range and OTE zone objects**
are reused. A later range creates a new zone with a new ID. Earlier observations
never change.

## 3. Exact retracement geometry

For a confirmed range with low L, high H, and positive span S = H − L, using the
configured ratios `lower_retracement` r_low and `upper_retracement` r_high:

```text
bullish 62% price = H - r_low  * (H - L)
bullish 79% price = H - r_high * (H - L)
bearish 62% price = L + r_low  * (H - L)
bearish 79% price = L + r_high * (H - L)
```

The stored interval is then ordered so `lower_boundary <= upper_boundary`. For the
default bullish case the 0.79 price is the lower bound and the 0.62 price is the
upper bound. Arithmetic uses the repository's existing exact Decimal sum, product,
and difference helpers. No binary floats, rounded Fibonacci substitution, epsilon,
or price quantization are used.

Default configuration:

```toml
[ote]
lower_retracement = "0.62"
upper_retracement = "0.79"
boundary_policy = "inclusive"
price_basis = "close"
```

Both retracements are quoted finite Decimals satisfying
`0 < lower_retracement < upper_retracement < 1`. The engine uses those exact
values. It does **not** silently replace 0.62/0.79 with 0.618/0.786. An explicit
0.618/0.786 configuration is a different, caller-chosen geometry.

`boundary_policy` accepts only `inclusive`. `price_basis` accepts only `close`.
Unknown keys, including score, entry, or multi-timeframe settings, are rejected.
Direct Python construction uses `OTEConfig()` / `OTEConfig(Decimal(...), Decimal(...))`
with the exported `BoundaryPolicy` and `PriceBasis` enums.

Existing 4096-required-digit operation/exponent bounds apply; unsupported arithmetic
raises an explicit error without mutating OTE state.

## 4. Price classification

Evaluate the completed candle's **close** in this order:

1. No current Phase 8 range, or the current range was not known before this bar
   opened → `INSUFFICIENT_CONTEXT`.
2. `lower_boundary <= close <= upper_boundary` → `INSIDE_OTE`.
3. `close < lower_boundary` → `BELOW_OTE`.
4. `close > upper_boundary` → `ABOVE_OTE`.

Exact 62% and 79% prices are inside the zone. Wicks, opens, and midpoints are not
the evaluation basis. A wick that overlaps the zone does not classify a close that
finished below or above it. Closes outside the dealing range are still
`BELOW_OTE` or `ABOVE_OTE` relative to the retracement interval; OTE does not add
an `OUTSIDE_RANGE` label.

The same geometry applies to bullish and bearish ranges. Direction is a location
orientation copied from the dealing range, not a buy/sell instruction.

The OTE zone is deterministic range evidence. The observation is a later
classification of a completed candle against that already-known zone.

## 5. Immutable models, provenance, and API

- `OTEDirection`: bullish/bearish vocabulary matching the source dealing range.
- `OTEClassification`: the four required labels.
- `OTEZone`: exact range identity, retracement prices, ordered bounds, confirmation
  coordinates, and provenance. Created when the Phase 8 range is created.
- `OTEObservation`: evaluated close, optional known zone, classification, and
  provenance. `zone` is absent precisely when the label is `INSUFFICIENT_CONTEXT`.
- `OTESnapshot`: original PD frame, current causal zone (if any), observation,
  close classification, and provenance.

```python
from smcsignal.analysis import OTEAnalyzer, OTEConfig, analyze_ote

ote = OTEAnalyzer(OTEConfig())
for pd_frame in pd_frames:
    result = ote.update(pd_frame)

# Alternative for a fresh/precomputed iterable:
results = analyze_ote(pd_frames, OTEConfig())
```

Records are frozen/slotted and satisfy the existing `ProvenancedEvidence` contract.
The consumer retains exact upstream objects and uses the existing canonical codec,
provenance factory, and current consumed-prefix hash. No future-file hash or random
ID is introduced. Configuration artifacts bind `ote-v1`, inherited price units,
current-range selection, and before-open observation timing. Producer version is 1;
rule/encoding changes must be versioned before reusing IDs.

A zone references its exact `DealingRange`. An observation references its zone only
when that zone was known at the bar open. The parent frame references the upstream
PD snapshot, current zone, and observation without cycles. `evidence_json` retains
full nested raw facts and exact Decimal strings. Source, configuration, and
evidence archives remain caller responsibilities.

Changing only the retracement settings changes OTE IDs, not upstream PD or range
IDs.

## 6. Streaming, replay, and no-lookahead contract

Input must start at index zero and remain consecutive, chronological, unique,
nonoverlapping, and nondecreasing in availability. Series, units, preceding context
links, and upstream configurations must remain consistent. The existing upstream
engines are consumed once; no sorting, imputation, revision, or re-detection occurs.

Local zone state is committed only after validation, exact arithmetic, model
construction, and provenance complete successfully. Failed input does not alter the
last output or advance the index. The consumer does not rewind an independently
advanced upstream engine.

For identical fixed-origin history, configurations, and availability annotations:

- Every prefix equals the corresponding full-series prefix, including all
  classifications, zone/observation IDs, dependencies, and provenance hashes.
- Changing or appending future candles cannot change prior zones or observations.
- Batch, streaming, and arbitrary chunks produce identical results.

Tests are software consistency checks, not a performance backtest.

## 7. Hand-computed synthetic example

`config/ote.example.toml` uses ten **synthetic** observations with three-candle
strict fractals and default 0.62/0.79 inclusive close classification:

| Index | Latest usable range | Orientation | OTE interval | Close | Classification |
| --- | --- | --- | --- | --- | --- |
| 0–3 | Missing opposing pair | None | None | Varies | INSUFFICIENT_CONTEXT |
| 4 | [9,41] published now | Bullish | [15.72, 21.16] | 25 | INSUFFICIENT_CONTEXT |
| 5 | [9,41] known before open | Bullish | [15.72, 21.16] | 18 | INSIDE_OTE |
| 6 | same | Bullish | [15.72, 21.16] | 12 | BELOW_OTE |
| 7 | same | Bullish | [15.72, 21.16] | 25 | ABOVE_OTE |
| 8 | same | Bullish | [15.72, 21.16] | 15.72 | INSIDE_OTE |
| 9 | same | Bullish | [15.72, 21.16] | 21.16 | INSIDE_OTE |

Span 32: `41 - 0.62×32 = 21.16` and `41 - 0.79×32 = 15.72`. Exact 62%/79% closes
are inside. The candle that confirms the high cannot use that future range.
These indices/prices are hand-audited test facts, not exchange observations,
entries, or expected returns.

## 8. Known limitations and stop boundary

- Current Phase 8 opposing pair only, not protected-swing OTE, Fibonacci
  extensions, 50%/equilibrium overlap rules, or HTF retracement maps.
- Ambiguous/nonpositive newest pairs and not-yet-known ranges intentionally
  remove usable current context; no silent fallback to an older range.
- Close-only, inclusive 0.62–0.79 geometry. No wick fill, body overlap, or
  multi-candle OTE confirmation wait.
- Core state holds only the current zone, but original rich upstream frames and
  retained output/JSON have inherited history/storage costs. There is no
  bounded-total-memory or production-throughput guarantee.
- Existing exact arithmetic resource bounds, source-truth and arrival-time
  assumptions, fixed starting history, and caller-owned archives remain in force.

No Setup Quality Score, 75+ threshold, signal generation, BUY/SELL, probability,
profitability claims, entries, stops, targets, risk/reward, position sizing, trade
management, Telegram, halal filter, scraping, live trading, credentials, futures,
leverage, backtesting, monthly statistics, multi-timeframe confluence, AI
optimization, or strategy ranking is implemented. Future quality policy remains
documentation only.
**Stop after Phase 12 for this layer. Phase 13 — Multi-Timeframe Confluence is a
separate consumer of existing OTE frames.**
