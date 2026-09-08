# Market Structure Shift methodology — Phase 9

## Scope and operational definition

This repository defines MSS as **displacement-confirmed opposing structure-break
evidence**, not a trade signal, probability, score, entry, or permanent regime
override. This is a deterministic operational convention, not a claim that it is
the only ICT/SMC interpretation.

The engine consumes existing **Phase 8 `PDSnapshot` frames**. It never reruns or
changes swing, trend, BOS/CHoCH, liquidity/sweep, displacement, FVG, OB, or PD
calculations. All previous objects and IDs are retained unchanged.

## 1. Exact bullish and bearish requirements

For current completed observation t and previous observation t−1:

| Requirement | Bullish MSS | Bearish MSS |
| --- | --- | --- |
| Previously established control | Phase 3 trend through t−1 is ready and bearish | Phase 3 trend through t−1 is ready and bullish |
| Known before the break bar opens | Previous structure context and broken level were available by open(t) | Same |
| Existing displacement on t | Bullish Phase 5 `DisplacementEvent` | Bearish Phase 5 `DisplacementEvent` |
| Existing opposing break on t | Bullish Phase 3 CHoCH, with `trend_before=bearish` | Bearish Phase 3 CHoCH, with `trend_before=bullish` |
| Exact broken level | Previously confirmed high in the prior bearish structure | Previously confirmed low in the prior bullish structure |

All requirements are mandatory. The recorded CHoCH must reference the exact
`Swing` retained by the previous frame's latest confirmed high/low `SwingEvidence`.
Its previous/current closes must agree with the actual observations. The level's
confirmation index is strictly earlier than t, and its true availability is no
later than open(t). No new confirmation on t can replace the broken level used.

The Phase 3 event already verifies a strict closing-price crossing:

```text
bullish: close[t-1] <= confirmed_high.price < close[t]
bearish: close[t-1] >= confirmed_low.price > close[t]
```

Phase 9 **consumes that event**, rather than implementing a second break detector.
A wick-only excursion, exact closing touch, missing displacement, mismatched
body/break direction, or missing established prior control yields no MSS.
A continuation **BOS alone—even with displacement—is not an MSS**: under the
existing Phase 3 convention, the opposing break required here is a CHoCH.

“Previously established” means the prior Phase 3 snapshot has two confirmed highs
and two confirmed lows (`ready=True`) and a directional label. Warm-up and
mixed/equal/ranging structure are insufficient. A trend first established by
confirmations on t cannot retroactively establish prior control for a break on t.
PD-range validity is not a substitute structure detector: `INSUFFICIENT_CONTEXT`
in PD does not by itself invalidate an otherwise ready directional Phase 3 trend.

## 2. What an MSS does not change

An MSS marks evidence of a transition **toward** opposing control. It does not
force Phase 3's separate swing-derived trend to reverse or alter any prior label.
That trend may still retain its earlier direction until further confirmed swings
arrive. This preserves the existing CHoCH/confirmation semantics.

Each original structure level has the Phase 3 one-time close-crossing behavior.
Phase 9 adds no duplicate-level opportunity, cooldown, count quota, or mandatory
number of shifts. Multiple and consecutive MSS events are possible when the
actual prior-control and newly confirmed-level conditions qualify; continuing
beyond the same consumed level cannot manufacture another event.

## 3. Publication and availability timing

- The broken swing's pivot time locates the old extremum, not when it became known.
- Its confirmation-candle opening timestamp is also an identifier, not a claim
  that confirmation was available before that candle closed.
- The previous structure context and the exact broken level must be known **by
  the current candle open**. This conservative rule avoids guessing intrabar order.
- Current displacement and CHoCH become observable from the completed candle.
- MSS `available_at` is the current upstream observation's real assumed/supplied
  availability, when all required evidence is present. `timestamp` identifies the
  current candle open and is never used as early publication time.

Default historical source availability is bar close, inherited from prior layers.
Explicit arrival delays are preserved without rounding away microseconds. If the
latest prior context arrived during the break bar, no MSS is published for that
bar, even if an older context might have suggested the same direction. There is
no backward search that invents a still-valid pre-bar state.

There is no provisional MSS, future-confirmation window, end-of-series flush, or
later FVG/OB enrichment of an old MSS. No wall-clock scheduling or trading-entry
interpretation is implemented.

## 4. Immutable evidence relationships

The relationships are descriptive facts, **not additional detection filters**.
MSS remains possible without a sweep, pool relation, FVG, or OB. Exact existing
snapshots are retained; no mutable `latest` pointer or fabricated producer ID is
introduced.

### Structure and broken level

`MSSEvidence` contains the original previous/current `StructureContext`, actual
current `StructureEvent`, and exact prior `SwingEvidence` for the broken level.
Because Phase 3 structure events have no standalone provenance ID, their references
identify the immutable containing structure context; the actual event object is
retained alongside it. No synthetic BOS/CHoCH event ID is manufactured.

`prior_control_events` retains matching continuation BOS events, if any, in the
immediately previous structure snapshot. It is not a new search for an arbitrary
old BOS. The required confirmation remains the current opposing CHoCH.

### Liquidity pools

- `broken_level_pools`: actual pool-update versions published on t whose members
  include the exact broken swing. These may be terminal pool snapshots; they are
  not falsely presented as pre-break active versions.
- `swept_pools`: original pre-sweep target pool snapshots embedded in the selected
  existing preceding sweeps.
- `related_pool_references`: deterministic deduplicated exact evidence-version
  references, not mutable pool entity IDs.

If no such pool record is present, none is synthesized. Structure confirmation
uses the original swing level, not a newly inferred liquidity pool.

### Sweeps

Copy the current displacement's `preceding_sweeps` and references unchanged.
Those were selected causally by Phase 5. Same-t sweeps, future sweeps, and later
context cannot be attached as preceding events. No sweep detector or additional
lookback/selection policy runs in Phase 9.

### FVG context: concurrent, not caused by the current displacement

`concurrent_fvgs` retains **same-direction FVG events first published on the MSS
candle t**, if present. This is an explicit **co-publication relationship**, not a
claim that the current displacement generated those FVGs: their C2 is t−1, and
any existing associated displacement remains exactly the FVG's original reference.
An FVG is retained whether or not its C2 had displacement, as allowed by the
already-configured Phase 6 producer.

A matching FVG whose C2 is the MSS displacement at t would require future C3=t+1.
It is never added later, and MSS is not delayed to obtain it. Opposing-direction
concurrent gaps remain in the original upstream frame but are not selected into
this relationship group.

### Order Blocks

`displacement_order_blocks` retains only OBs published on t that reference the
**exact current MSS displacement ID** and match its direction. No OB is fabricated
when a candidate or structural/zone condition failed.

An OB requiring a next-candle FVG is not yet available for the current displacement.
Such later publication cannot enrich the earlier MSS. An OB published on t for
another, older displacement is not falsely attached to the current shift.

### Premium/Discount

The current immutable `PDSnapshot`, its exact reference, and its categorical close
classification are retained, including `OUTSIDE_RANGE` or `INSUFFICIENT_CONTEXT`.
There is no PD eligibility gate or invented favorable classification.
Existing current `PDArrayContext` records for selected pool/displacement/FVG/OB
subjects are linked unchanged. No prior PD object or ID is altered, and no new PD
annotation is injected into the protected Phase 8 engine.

## 5. Models and API

`src/smcsignal/analysis/mss/` separates strict configuration, evidence matching,
immutable models, provenance/identity construction, and stream/batch orchestration.

- `MSSDirection`: immutable bullish/bearish vocabulary, not an order side.
- `MSSEvidence`: prior/current PD frames, known control, exact break/level,
  displacement, all selected relationships, and `EvidenceProvenance`.
- `MSSEvent`: qualified evidence, its own provenance, deterministic `event_id`,
  symbol/timeframe/units/direction, detection index, opening identifier, actual
  availability, and methodology version.
- `MSSSnapshot`: original current/previous PD frames, zero/one qualified evidence
  and event deltas, strict settings, and its own provenance.

Each of the three record models satisfies `ProvenancedEvidence`. The direction
enum itself is a label; its use is sourced by the containing evidence/event.

```python
from smcsignal.analysis import MSSAnalyzer, MSSConfig, analyze_mss

mss = MSSAnalyzer(MSSConfig())
for pd_frame in pd_frames:
    result = mss.update(pd_frame)

# Alternative with fresh/precomputed input, not an additional upstream pipeline:
results = analyze_mss(pd_frames, MSSConfig())
```

Each update consumes one original Phase 8 frame. The batch helper is exactly the
same update loop. `latest`, `processed_count`, source identity, price unit, and
configuration artifact are read-only views. No prior engine is instantiated or
rerun by the MSS analyzer.

## 6. Strict configuration, not tunable signal policy

```toml
[mss]
enable_displacement_requirement = true
enable_structure_requirement = true
```

The table requires exactly these two boolean keys. In **strict `mss-v1`, both must
remain true**. Setting either false or supplying an untyped value is an explicit
configuration error, not a silently weakened or incomplete MSS definition.
These fields declare required invariants; Phase 9 does not provide an experimental
“structure-free” or “displacement-free” mode. A different concept would need a
separately approved definition/version.

There are no MSS size thresholds, score/quality parameters, probabilities,
optimization knobs, source fallbacks, delay settings, or signal-count targets.
Existing Phase 3/5 configuration stays owned by those already-running producers.
Changing upstream settings midstream is rejected rather than retrospectively
reinterpreting accepted evidence.

## 7. Provenance, precision, and deterministic identities

The approved canonical codec, source-frame schema, `EvidenceReference`, and
provenance factory are reused unchanged. New artifacts bind `mss-v1`, mandatory
requirement flags, and inherited price units. The exact artifact bytes hash to
recorded configuration IDs. Producer version is 1; future rule changes must be
versioned before reusing identities.

Evidence explicitly references previous/current PD snapshots, previous/current
structure contexts, the broken confirmed swing, displacement, and selected existing
relationships. Source candle references identify the broken pivot, its confirming
candle, prior observation, and current observation in unique chronological order.
The evidence uses the **current already-consumed input-prefix hash**, never a
whole-file/future-row hash. Event provenance references that exact qualified
evidence; the parent MSS frame references both without a cycle.

No price/ATR/structure calculation is duplicated. Decimal values and exact
comparisons come from the original observations and validated events, never binary
float approximations. `evidence_json` preserves all nested raw facts and provenance.
Source/configuration/evidence archives remain caller-owned; these hashes identify
artifacts but do not authenticate source truth or create a storage service.

## 8. Causality and replay guarantees

With the same valid canonical history, source/configuration identity, fixed replay
origin, and availability annotations:

- Every prefix equals the corresponding full-series prefix, including event IDs,
  relationships, original object snapshots, and provenance hashes.
- Replacing or appending future candles cannot alter already-observable MSS events.
- Batch, streaming, and arbitrary chunked replay produce identical results.

All input/configuration/continuity checks and evidence construction finish before
state is committed. Failed input leaves the MSS index and latest snapshot unchanged.
The consumer does not rewind a separately advanced upstream engine. Input starts
at zero with consecutive unique observed indices, consistent series/settings,
actual source-history links, and nondecreasing availability. Invalid or conflicting
frames are rejected, not sorted, filled, or relabeled.

Tests cover bullish/bearish and multiple/consecutive positive cases, actual sweep/
FVG/OB/PD relationships, false/equal/wick breaks, continuation BOS, missing/wrong
displacement, ambiguous prior structure, late arrivals, independent graph/ID checks,
immutable records, provider integration, every prefix, and future-price shocks.
These are offline software tests, not backtesting or performance analysis.

## 9. Hand-computed synthetic examples

`config/mss.example.toml` uses 28 **synthetic** observations, compact three-candle
fractals, unchanged default ATR(14)/displacement thresholds, and strict MSS flags:

| MSS index | Prior control | MSS direction | Existing CHoCH level | Displacement close |
| --- | --- | --- | --- | --- |
| 22 | Bullish | Bearish | Confirmed low 13 | 11 |
| 27 | Bearish | Bullish | Confirmed high 15 | 30 |

Both have already-existing concurrent same-direction FVG context and matching
current-displacement OB context. Their concurrent FVGs use the prior candle as C2;
no future gap is claimed. Other tests demonstrate shifts both with and without
preceding sweeps, and consecutive bullish/bearish MSS at indices 21/22 when the
actual prior confirmed structure changes between those observations.

All prices and indices are hand-audited test data, not exchange observations,
entries, eligibility, remaining liquidity, or expected returns.

## 10. Limitations and stop boundary

This method deliberately consumes Phase 3's latest confirmed-level/CHoCH convention;
it does not introduce protected-level selection, internal/external structure
hierarchies, intrabar ordering, or a new persistent control state. It may omit a
shift when the latest prior context arrived during the bar, even if an older
state might have been usable. There is no later confirmation/enrichment window.

Optional relationships have explicit narrow roles, not causal or trading claims.
Concurrent FVG context is not a product of the current displacement. Missing OB or
PD context does not imply a trade should occur or that requirements can be relaxed.
Core state retains only its latest input/output, but nested prior-layer evidence
and output/JSON inherit existing history/storage costs. No production-throughput
or bounded-total-memory guarantee is made.

No signals, entries, stops, targets, sizing, risk management, setup scoring,
probability estimates, performance/monthly reporting, Telegram, halal filtering,
MTF execution, AI optimization, strategy ranking, Breaker/Mitigation Blocks, OTE,
trading, or later-phase functionality is implemented. Existing future quality
policy remains documentation only: scale 0–100, future default publication
threshold 75, valid zero-signal outcomes, no quotas; scoring is deferred to a separately approved later phase.
Phase 10 now consumes these unchanged outputs for
[Breaker formation](breaker-block-methodology.md).
**Stop after Phase 11. Phase 12 — OTE requires explicit approval.**
