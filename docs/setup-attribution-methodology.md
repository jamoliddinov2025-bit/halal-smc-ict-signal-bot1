# Setup attribution methodology — Phase 19b

## Purpose and scope

Phase 19b answers one descriptive question: **which confluence facts were
already published on the frame that produced each `BUY_SIGNAL`?** It consumes
existing **`SignalSnapshot`** frames only and projects the nested facts onto a
closed label taxonomy. Attribution is **outcome-independent**: it never reads
Phase 18 outcome records, future candles, or any data the stream has not
published. Labels are factual observations, not strategy names, ratings, or
predictions.

## The closed label taxonomy

Twelve labels, in canonical order:

| Label | Nested fact observed |
| --- | --- |
| `bullish_displacement` | a bullish `DisplacementEvent` on the signal frame |
| `sweep_associated` | the displacement event carries preceding sweeps |
| `fvg_bullish` / `fvg_bearish` | an `FVGEvent` in that direction |
| `order_block_bullish` / `order_block_bearish` | an `OrderBlockEvent` in that direction |
| `inside_ote` | `OTEClassification.INSIDE_OTE` |
| `pd_discount` / `pd_premium` / `pd_equilibrium` | the matching `PDClassification` |
| `mtf_bullish` / `mtf_mixed` | the matching `MTFDirection` |

Missing or `INSUFFICIENT_CONTEXT` facts produce no label; nothing is invented.
**MSS, Breaker, and Mitigation facts are not reachable on the nested signal
graph** (they are published by Phases 9–11 but not embedded in the Phase 15–17
frame chain consumed here), so attribution-v1 has no labels for them — this is
documented, not an omission to fix. The taxonomy is closed: adding a label
requires a methodology version bump.

## Labels, profiles, and combination keys

- `labels_for(frame)` is a **pure function** of the frame: every label derives
  from a named nested fact on that exact frame.
- A `SetupAttribution` profile exists **exactly on `BUY_SIGNAL` frames**.
  Other frames carry none; bearish facts are observable as labels but never
  become BUY profiles.
- `combination_key` joins the label values with `+` in canonical order (for
  example `bullish_displacement+sweep_associated+mtf_bullish`). It is a
  descriptive reporting bucket for Phase 19c grouping, not a strategy name.
- The engine context is copied **verbatim**: `signal_id`, `setup_identity`,
  `symbol`, `timeframe`, the signal `CandleReference`, `published_at` at the
  signal cutoff, and the full SQS `ScoreBreakdown` (with
  `score_total == breakdown.total` enforced).

## Deterministic identity and provenance

- `attribution_id = "setup-attribution:" + sha256({methodology, signal_id,
  labels, enabled})` — stable and independent of future candles.
- Two producers: `attribution-record` (one per BUY profile) and
  `attribution-frame` (per consumed frame; dependencies are the upstream
  signal frame plus the record when a profile exists). Both reuse the current
  consumed prefix hash and the existing canonical codec.
- The configuration artifact declares the fact-only role explicitly:
  `outcome_dependence: false`, `future_data: false`,
  `strategy_invention: false`, `mss_breaker_mitigation: "not_reachable_in_v1"`,
  `execution: false`.

## Configuration

```toml
[setup_attribution]
enabled = true
```

The table must contain exactly that key and it must be true. There are no
thresholds, weights, or outcome settings — attribution is a fixed projection.

## Causal / no-look-ahead rules

Input must be `SignalSnapshot` frames from one signal-engine configuration,
from index zero, consecutive, chronological, and non-rewinding. Each
`BUY_SIGNAL` is attributed at most once (duplicate signal IDs are rejected
with state unchanged). Commit happens only after every check and provenance
construction succeeds.

For identical input history and configuration: every prefix equals the
corresponding full-series prefix; batch, streaming, and chunked replays are
identical; and **appending different future candles never changes a past
profile** — labels are a pure function of already-published nested facts, and
an import-graph test keeps the package free of any outcome-tracking read.

## API

```python
from smcsignal.analysis import (
    SetupAttributionAnalyzer,
    SetupAttributionConfig,
    analyze_setup_attribution,
)

engine = SetupAttributionAnalyzer()
for signal_frame in signal_frames:
    snapshot = engine.update(signal_frame)

snapshots = analyze_setup_attribution(signal_frames)
profiles = [s.attribution for s in snapshots if s.attribution is not None]
```

## Hand-computed synthetic example

`config/setup-attribution.example.toml` reuses the synthetic Phase 13–17
history. At SQS threshold 10 the four BUYs (indices 4, 8, 12, 16) each carry
exactly `(mtf_bullish)` with score 25. The sweep fixture adds
`bullish_displacement+sweep_associated+mtf_bullish` at the displacement
candle; the impulse fixture adds order-block, FVG, and premium/discount
labels; the OTE fixture produces `fvg_bearish+inside_ote+pd_discount+mtf_bullish`;
the mirrored fixture produces `order_block_bearish` without any BUY profile;
and the mixed-HTF fixture produces `mtf_mixed`. Across these fixtures every
one of the twelve labels is observed at least once.

## Known limitations and stop boundary

- Attribution is replay-local; there is no persistence.
- Unattributable confluence (MSS, Breaker, Mitigation) stays unlabeled until
  a future phase makes those facts reachable on the consumed graph — that
  change requires explicit approval and a methodology bump.
- Profiles never feed back into signal decisions; decision modules cannot
  import this package (enforced by import-graph tests).

No live trading, orders, execution, Telegram, optimization, or
self-modification is implemented.

**Stop after Phase 19. Phase 20 requires explicit approval.**
