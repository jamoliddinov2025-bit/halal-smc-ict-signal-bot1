# Premium / Discount methodology — Phase 8

## Scope and inputs

Phase 8 evaluates deterministic **local-series** Premium/Discount (PD) context
using the existing confirmed swing structure. It is an analysis/evidence layer,
not a trade direction, entry, risk rule, position size, score, or performance claim.

`PDAnalyzer.update` consumes an existing Phase 7 **`OrderBlockSnapshot`**. Its nested
frames already contain Phase 3 structure, Phase 4 swing/pool/sweep evidence, Phase 5
displacement, and Phase 6 FVGs. This layer does not rerun or modify any of those
engines, their models, or their existing evidence IDs.

Each `PDSnapshot` evaluates the current **closed candle's close**, retains the exact
original upstream frame, and exposes one of:

- `PREMIUM`
- `DISCOUNT`
- `EQUILIBRIUM`
- `OUTSIDE_RANGE`
- `INSUFFICIENT_CONTEXT`

## 1. Deterministic dealing-range selection

Maintain the **most recently confirmed swing low and most recently confirmed
swing high**, independently, from the upstream `SwingEvidence` publications.
Their raw `Swing` records agree with the latest endpoints in the existing Phase 3
trend snapshot. No extrema, pivot, trend, or confirmation detector is duplicated.

A valid opposing pair requires:

1. Both endpoints are confirmed and available by the current evaluation cutoff.
2. Their pivot candle indices are distinct.
3. The high price is **strictly greater than** the low price.

The earlier/later **pivot order**, not a newly inferred trend, sets orientation:

| Pivot order | Range direction | Boundaries |
| --- | --- | --- |
| Low, then high | Bullish | Low swing price → high swing price |
| High, then low | Bearish | High swing price → low swing price |

Stored `lower_boundary` and `upper_boundary` are always the low and high prices,
respectively. Bearish orientation does not reverse the numerical boundaries or
turn the low half into premium. A Phase 3 ranging trend can still have a valid
ordered opposing pair; trend direction is not an extra filter.

### Most-recent means no silent fallback

This is latest-high/latest-low pairing, not protected-swing selection, widest-range
fitting, or searching backward for a more convenient older pair. A new confirmed
endpoint replaces its same-kind predecessor. There is no range fitting using
future candles, no automatic price extension, and no gap/session/age filter.

An outside candle can confirm both a high and a low on the **same pivot candle**.
Their publication order cannot establish an intrabar low→high or high→low path;
such a newest pair yields `INSUFFICIENT_CONTEXT` with `range_status=same_pivot`.

If the newest high is equal to or below the newest low, do not swap their prices,
clamp the range, or keep an older range. The result is `INSUFFICIENT_CONTEXT` with
`range_status=nonpositive_span`. Missing either endpoint yields `missing_swings`.
This distinguishes insufficient usable evidence from an actual sideways market.

Between new confirmations, the **same immutable range and equilibrium objects**
are reused. A new pivot at an old price is still new evidence, with a new range ID.
Earlier range versions and previously returned classifications never change.

## 2. Confirmation and observation timing

Indices are local to a fixed replay starting at zero. Swing pivot timestamps
identify historical candle opens. They are **not** confirmation/availability times.
The existing `SwingEvidence` retains the full closed confirmation window and its
actual assumed/supplied availability instant.

At closed observation t, all swing confirmations already published by the upstream
frame at t may participate. Therefore a newly confirmed pair can be used **at this
close**, not at its earlier pivot time and not at the current candle's open.
This is not a pre-bar range-only rule: all inputs used must be available by the
current closed-candle evaluation cutoff.

`DealingRange` is first published on its latest endpoint's confirmation observation.
It retains both original swing-evidence objects, their pivot/confirmation references,
current structure context, price units, orientation, bounds, size, confirmation
index/opening identifier, and actual `available_at` instant. Its provenance prefix
ends at that range-creation cutoff, not at a later evaluation candle.

Upstream default availability is the exclusive bar close, a historical replay
assumption. Supplied arrival delays are preserved. All usual source-truth,
normalization, vendor-revision, and completed-candle assumptions still apply.

## 3. Exact equilibrium and configurable band

For low L, high H, and positive span S=H−L:

```text
midpoint = L + (H - L) × 0.5
half_width = S × equilibrium_half_width_fraction
equilibrium_lower = midpoint - half_width
equilibrium_upper = midpoint + half_width
```

This is exactly `(L + H) / 2`, using the repository's existing exact Decimal sum,
product, and difference helpers. No binary floats, rounded ratios, epsilon, or
price quantization are used. Existing 4096-required-digit operation/exponent bounds
apply; unsupported arithmetic raises an explicit error without mutating PD state.

Configuration:

```toml
[premium_discount]
equilibrium_half_width_fraction = "0"
```

The sole setting is a quoted finite Decimal from **0 through 0.5 inclusive**.
It is the **half-width as a fraction of the full range**, not the total band width
and not an absolute quote-price distance. For example, 0.05 means a band from
45% through 55% of the range. Default zero means the exact midpoint only. At 0.5,
the equilibrium band covers the entire range; outside prices remain outside.

The fraction is a physical geometric tolerance, not a quality/probability value.
Direct Python construction uses `PDConfig(Decimal(...))`. Unknown keys, including
HTF, trade, or score settings, are rejected.

`Equilibrium` is immutable and references the exact `DealingRange`. Its midpoint
and band bounds are preserved with their own provenance. Changing only the band
setting changes equilibrium/PD IDs, not upstream IDs or the range-selection ID.

## 4. Price classification and exact boundaries

Evaluate in this order:

1. Missing/ambiguous/nonpositive selected context → `INSUFFICIENT_CONTEXT`.
2. `price < L` or `price > H` → `OUTSIDE_RANGE`.
3. `equilibrium_lower <= price <= equilibrium_upper` → `EQUILIBRIUM`.
4. `price < equilibrium_lower` → `DISCOUNT`.
5. Otherwise → `PREMIUM`.

Thus range boundaries are **inside**, and equilibrium-band boundaries are
**inclusive**. Premium/discount are strictly above/below the equilibrium band.
Outside detection has priority over any premium/discount interpretation.
The same geometry applies to bullish and bearish ranges; it is not a buy/sell rule.

Each candle snapshot classifies its **close**, not its open, extreme wick, or whole
OHLC range. The full original candle is retained for other future interpretations.

## 5. PD array relationships without rewriting history

`PDArrayContext` is a separate frozen annotation that retains the **exact original
subject object and `EvidenceReference`**. The original object's fields, provenance,
and ID are unchanged. A sidecar has its own PD ID, configuration identity,
evaluation candle/cutoff, and range/equilibrium references.

Only **newly published upstream deltas at the current candle** are annotated, in
deterministic order: pool updates, sweeps, displacement events, FVG events, OB events.
There is no new active-array registry, lifecycle, strategy, or retrospective pass
that reclassifies every historical object whenever the range changes.

| Subject | Primary evaluated price | Preserved interval |
| --- | --- | --- |
| LiquidityPool snapshot | Fixed `reference_price` anchor | Original lower/upper pool band |
| SweepEvent | Actual `extreme_price` | Point at the extreme, not reclaim close |
| DisplacementEvent | Actual confirming candle close | Point at that close |
| FVGEvent | Exact midpoint of original gap bounds | Full original gap interval |
| OrderBlockEvent | Exact midpoint of original OB zone | Full original zone interval |

Every annotation includes `classification` of its representative price **and**
`lower_classification` / `upper_classification` for its endpoints. A zone midpoint
in discount does **not** claim that the entire zone is in discount or inside the
range: mixed or outside endpoint labels and raw bounds make that visible.

The context is **publication-time**, using the range known at the current close.
For example, an OB with candidate index 18 and publication index 21 receives a PD
annotation evaluated at 21, not a retroactively assigned candidate-time PD label.
This is equally important for sweeps and delayed FVG/OB evidence.

Pool revisions use their exact immutable evidence-version IDs, not just the stable
pool entity ID. A newly published version can receive different PD context while
an earlier sidecar remains unchanged. If no range is usable, all relevant labels
are explicitly `INSUFFICIENT_CONTEXT`; no midpoint or classification is fabricated.

## 6. Immutable models, provenance, and future HTF interface

- `DealingRange`: confirmed opposing endpoints, orientation, exact geometry and
  confirmation provenance.
- `Equilibrium`: exact midpoint/band and its range/configuration provenance.
- `PDClassification`: immutable typed enum containing the five required labels.
- `PDSnapshot`: original Phase 7 frame, latest endpoint evidence, range status,
  range/equilibrium, close classification, array sidecars, and provenance.
- `PDArrayContext`: immutable publication-time overlay on an unchanged source object.
- `PDContextReference`: exact range and equilibrium references, with source series,
  timeframe, and real availability. This is the **architecture-only HTF hook**.

A future consumer can reference a higher-timeframe range through these same typed,
versioned references. The reference pair validates matching source identity and
causal range/equilibrium availability, without comparing indices across timeframes.
**Phase 8 does not select, fetch, align, aggregate, join, or execute HTF logic.**
The current PD evaluator rejects foreign-series ranges; one analyzer evaluates
one source series. No dormant HTF execution switch is provided.

All provenance uses the approved canonical codec/factory. New ranges reference
the exact low/high swing evidence, their pivot and confirming candle references,
and existing structure context. Equilibrium references its exact range. Sidecars
reference their original subject and current PD context. Each PD frame references
the current upstream frame, endpoint evidence, context, and new annotations.
The dependency graph is acyclic and resolvable from retained outputs.

The current upstream **consumed-prefix hash** is reused; no future-file hash,
wall-clock/random ID, or duplicate source detector is introduced. Separate immutable
configuration artifacts bind local range-selection rules versus equilibrium/PD
settings and inherited price units. `configuration_artifact` and
`range_configuration_artifact` expose those exact bytes after the first successful
input. Future rule/encoding changes must version the methodology before reusing IDs.

`evidence_json` preserves complete nested raw records and exact Decimal strings.
Consumers own source, configuration, and evidence archives; no new storage service
or registry is implemented. Hashes are identity/provenance, not source authenticity.

## 7. Streaming, replay, and no-lookahead contract

```python
from smcsignal.analysis import PDAnalyzer, PDConfig, analyze_pd

pd = PDAnalyzer(PDConfig())
for order_block_frame in order_block_frames:
    result = pd.update(order_block_frame)

# Alternative for a fresh/precomputed iterable:
results = analyze_pd(order_block_frames, PDConfig())
```

Input must start at index zero and remain consecutive, chronological, unique,
nonoverlapping, and nondecreasing in availability. Series, units, preceding context
links, and upstream configurations must remain consistent. The existing upstream
engines are consumed once; no sorting, imputation, revision, or re-detection occurs.

Local range/equilibrium/endpoint state is committed only after validation, exact
arithmetic, model construction, and provenance complete successfully. Failed input
does not alter the last output or advance the index. The consumer does not rewind
an independently advanced upstream engine.

For identical fixed-origin history, configurations, and availability annotations:

- Every prefix equals the corresponding full-series prefix, including all
  classifications, range/sidecar IDs, dependencies, and provenance hashes.
- Changing/appending future candles cannot change prior ranges or annotations.
- Batch, streaming, and arbitrary chunks produce identical results.

An unconfirmed pivot can be confirmed or rejected by later candles, but nothing is
published from it before its actual confirmation. Same-candle confirmed inputs are
allowed only after that current candle is complete. Data changes before the cutoff,
source revisions, or shifted latest-N starts are different inputs, not equivalent
replays. Tests are software consistency checks, not a performance backtest.

## 8. Hand-computed synthetic example

`config/premium-discount.example.toml` uses seven **synthetic** observations with
three-candle strict fractals and default zero-width equilibrium:

| Index | Latest usable range | Orientation | Midpoint | Close | Classification |
| --- | --- | --- | --- | --- | --- |
| 0–2 | Missing low/high pair | None | None | Varies | INSUFFICIENT_CONTEXT |
| 3 | [11,16], high pivot 1 / low pivot 2 | Bearish | 13.5 | 15 | PREMIUM |
| 4 | [11,16], low pivot 2 / high pivot 3 | Bullish | 13.5 | 12 | DISCOUNT |
| 5 | [11,16], high pivot 3 / low pivot 4 | Bearish | 13.5 | 13.5 | EQUILIBRIUM |
| 6 | [11,14.5], low pivot 4 / high pivot 5 | Bullish | 12.75 | 10 | OUTSIDE_RANGE |

Phase 5's ATR warm-up does not prevent a confirmed Phase 3 range from being used.
Additional tests cover all five array types, including real existing sweep/FVG/OB
fixtures and old-candidate/new-publication timestamps. No example claims actual
exchange observations, resting orders, asset eligibility, or trading returns.

## 9. Known limitations and stop boundary

- Local most-recent opposing pair only, not protected/dealing-range optimization,
  swing hierarchies, HTF selection, or trend-dependent geometry.
- Ambiguous/nonpositive newest pairs intentionally remove usable current context;
  no silent fallback to an older range.
- No automatic time-gap/session filter, lifecycle, historical array reassessment,
  or claim that a midpoint label applies uniformly to a whole price zone.
- Core state holds only current endpoints/context, but original rich upstream
  frames and retained output/JSON have inherited history/storage costs. There is
  no bounded-total-memory or production-throughput guarantee.
- Existing exact arithmetic resource bounds, source-truth and arrival-time
  assumptions, fixed starting history, and caller-owned archives remain in force.

No signal scoring, trade entries, risk management, position sizing, Telegram,
backtesting statistics, monthly performance, halal filtering, strategy ranking,
AI optimization, or multi-timeframe execution logic is implemented. The existing
future quality policy (0–100, default publication threshold 75, zero signals valid,
no quotas) remains documentation only; scoring remains deferred to Phase 11.
**Stop after Phase 8. Phase 9 requires explicit approval.**
