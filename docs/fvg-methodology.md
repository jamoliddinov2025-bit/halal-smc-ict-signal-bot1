# Fair Value Gap methodology — Phase 6

## Scope

Phase 6 identifies deterministic **three-candle FVG creation evidence**, not
entries, orders, resting liquidity, probabilities, or a strategy. “ICT-style” here
names the specified outer-wick non-overlap pattern; terminology varies. An FVG
record does not prove that no trades occurred inside its interval—C2 may have
traded through it—and does not imply a profitable or currently unfilled setup.

The engine consumes existing Phase 5 `DisplacementSnapshot` frames. It does not
fetch/normalize prices, recreate candle closures, rerun structure, liquidity,
sweep, or displacement detection, or duplicate provenance/precision machinery.

## 1. Exact three-candle definition

At current observed index t:

- **C1** = candle t−2.
- **C2** = middle/candidate candle t−1.
- **C3** = candle t, which must be completed and available.

| Direction | Strict qualification | Lower boundary | Upper boundary |
| --- | --- | --- | --- |
| Bullish | `Low(C3) > High(C1)` | `High(C1)` | `Low(C3)` |
| Bearish | `High(C3) < Low(C1)` | `High(C3)` | `Low(C1)` |

```text
gap_size = upper_boundary - lower_boundary
```

The reported interval is `[lower_boundary, upper_boundary]`. These endpoints are
raw observed prices; interval notation specifies geometry, **not a fill rule**.
Equality between outer wick boundaries always produces **no FVG**, even when the
configured minimum is zero. Overlap produces no FVG; there is no epsilon, rounding,
centered-price bucket, or inferred intrabar path.

No middle-candle body/direction, wick dominance, or bridge-through-zone requirement
is added to this definition by default. A doji, long-wick, or opposite-color C2 can
therefore participate. Optional displacement filtering is described below.

With fewer than three completed observations, the engine emits no creation event.
The first possible event is index **2**. Gaps in wall-clock coverage remain gaps;
these are three consecutive **observed** candles, not synthesized time slots. No
session or exchange-grid continuity strategy is added.

## 2. Minimum gap and default configuration

```toml
[fvg]
min_gap_size = "0"
require_displacement = false
```

`FVGConfig()` has these same defaults. The TOML table requires exactly both keys;
`min_gap_size` is a quoted finite nonnegative decimal string and
`require_displacement` is a strict boolean. Direct Python configuration uses
`Decimal`. Unknown settings—including ATR filters, lifecycle switches, scores,
publication thresholds, or strategy flags—are rejected.

The minimum is an **absolute price distance in the upstream Phase 5 `price_unit`**.
It is not basis points, ATR units, a quality value, or a universal exchange tick.
Qualification requires both:

1. The strict positive geometry above.
2. `gap_size >= min_gap_size` (**inclusive** configured minimum).

Default zero means strict-positive geometry only. This avoids inventing a universal
absolute tick/noise floor for different instruments. A one-tick positive gap can
qualify; callers should explicitly configure an instrument-appropriate minimum
when they want additional size filtering. The default is not a claim that every
positive gap is significant or trade-worthy. There is **no ATR-relative filter**
in Phase 6, and no ATR or displacement warm-up is required in the default mode.

The analyzer binds price units from its first successful upstream frame. Its
`price_unit`, `series`, and `configuration_artifact` are `None` before that bind;
no currency is guessed and no duplicate price-unit setting is introduced.

## 3. Displacement relationship

C2 is an existing Phase 5 frame, which already has zero or one displacement event.
Phase 6 records that **actual C2 event**, if present:

- `associated_displacement`: the original immutable event object.
- `displacement_reference`: its exact versioned evidence reference.
- `displacement_aligned`: True/False relative to the FVG direction, or None when
  no C2 displacement exists. This is a factual relation, not a quality rating.

With default `require_displacement=false`, no displacement is required. Bullish
or bearish C2 displacement can be recorded, including an opposing-direction event;
it is never relabeled to match the gap.

With `require_displacement=true`, C2 must have an **existing matching-direction**
displacement event. Missing or opposing C2 displacement rejects creation. Phase 5
criteria and ATR are not recalculated or changed. With default ATR warm-up, the
first possible displacement-associated FVG is index 16: displacement on C2 at 15,
followed by completed C3 at 16.

The C2 event must already be available by C3's actual emission time. It may have
arrived during C3, provided it is known by that assessment; it is not backdated to
C3's open. Equal availability timestamps are allowed for sequentially processed
batched arrivals: C2 was processed first and has a strictly earlier candle index.
C3's own displacement and future displacement are **never substituted for C2**.
No historical displacement record is modified.

## 4. Sweep relationship: reuse C2's published context

`preceding_sweeps` is copied **intact from the C2 Phase 5 frame**, even when C2 did
not itself qualify as displacement. Its exact `preceding_sweep_references` are
preserved. This can represent no preceding sweep, buy-side, sell-side, or several
pools/both sides in the same prior cohort.

There is **no new sweep scan, cache, lookback rule, or direction filter** in FVG.
The existing Phase 5 rules apply: by default, the latest eligible prior sweep
candle within 20 observed bars, known by **C2's open**, with all its events retained.
The actual lookback is the frozen setting in that C2 frame, not a hard-coded FVG
value. Zero lookback disables that upstream association.

Consequently:

- C1 or earlier sweeps may be context if Phase 5 already selected them for C2.
- A same-C2 sweep is not retrospectively attached just because it becomes eligible
  for C3's separate displacement assessment.
- C3/detection-candle sweeps and future sweeps are not attached.
- A sweep arriving during C2 but before C3 is not backfilled into C2's context.
- The FVG engine does not reselect using C3's newer `preceding_sweeps` group.

This is a conservative relationship to the **middle candidate**, not a claim that
there were no other sweeps anywhere before gap observation. The full original
frames remain available for independently defined future analysis.

## 5. Creation, observation, and lifecycle

**Formation geometry** involves C1/C2/C3. **Creation/detection** is at index t only,
never on C1 or C2. `timestamp` identifies C3's candle **open**, matching existing
models; `available_at` is the true assumed/supplied instant after C3 closes and its
evidence is available. An opening identifier is not a claim of opening-time knowledge.

**Future interaction is not implemented in this phase.** There is no mutable or
implied OPEN/PARTIALLY_FILLED/FILLED/INVALIDATED status, active-gap registry,
first-touch detection, mitigation, expiration, gap-through rule, wick/close fill
rule, or entry assumption. C3 forms a boundary by definition; its participation
is not treated as a same-candle fill. A later price touch/cross never alters or
renames an already emitted formation record.

Consecutive, overlapping, nested, repeated-price, and opposing windows produce
independent events when they meet the definition. They are not merged, ranked,
cancelled, or suppressed by later prices. There is at most one event per window
(the two strict directions cannot both hold for canonical OHLCV). There is no
output-count target, signal quota, or activity fallback.

## 6. Models and public API

```python
from smcsignal.analysis import FVGAnalyzer, FVGConfig, analyze_fvg

engine = FVGAnalyzer(FVGConfig())
for displacement_frame in displacement_frames:
    result = engine.update(displacement_frame)

# Alternative for the same precomputed frames, not a second upstream pipeline:
results = analyze_fvg(displacement_frames, FVGConfig())
```

The two snippets illustrate alternatives; use fresh/single-pass input as appropriate.
`FVGSnapshot` contains the settings, last one/two/three original Phase 5 frames,
zero/one creation-event deltas, and provenance. `upstream` is the exact original
current input frame. `latest` and `processed_count` expose read-only stream state.

`FVGEvent` is a frozen, slotted record with:

- Symbol, timeframe, inherited price unit, direction, stable `event_id`.
- Creation/detection index, C3 opening timestamp, actual availability instant.
- Original three-frame window and explicit `c1`, `c2`, `c3` `ObservedCandle`
  objects, each retaining full OHLCV and `CandleReference` metadata.
- Exact lower/upper boundaries and positive gap size.
- Original associated C2 displacement, exact reference, and factual alignment.
- Original C2 preceding-sweep group and exact references.
- Frozen configuration, `threshold_version = fvg-v1`, and `EvidenceProvenance`.

No probability, score, trade direction, stop, target, or order data is invented.
Later consumers can use these typed facts and `provenance.as_reference()` without
changing the existing producer contracts. Phase 7 now consumes these unchanged FVG records for
[Order Block formation](order-block-methodology.md); strategies remain deferred.

## 7. Precision, identity, and provenance

The engine reuses the existing Phase 5 **exact Decimal difference** helper and its
4096-required-digit operation limit. Threshold comparisons use unrounded Decimal
values. No binary floats or normalized/display ratios are used for qualification.
Unsupported arithmetic spans raise an explicit input error; they are not rounded
into a different gap size or silently ignored.

The existing canonical JSON codec and provenance factory are reused. Each event
binds source identity, producer/version, exact configuration artifact/hash, all
three Phase 5 frame identities, geometry, source candle references, and related
evidence. All three candle dependencies are explicit. Associated displacement and
sweep references are explicit additional dependencies, never mutable `latest` links.

The **C3 upstream input-prefix hash is reused**; no full-file/future-row hash or new
upstream detector is introduced. The configuration artifact includes `fvg-v1`,
settings, inherited units, and the reused arithmetic limit. Artifact bytes become
available on the analyzer after the first successful frame; their SHA-256 hash is
recorded. Event/frame producer version is 1. Rule or identity-format changes must
be versioned before reusing identities.

`evidence_json` preserves complete nested raw evidence, Decimal strings, and UTC
microsecond availability. A parent FVG frame can reference its event without
cycles; the event depends only on already-produced upstream evidence. Retained
outputs resolve the complete dependency graph. Persistent archives and source
truth/normalization reports remain caller responsibilities.

## 8. No-lookahead, replay, and input failures

For identical canonical history, source identity, upstream/FVG settings, price
units, and availability annotations:

- Every prefix produces exactly the corresponding full-series prefix, including
  event IDs, thresholds, associations, raw evidence, and provenance hashes.
- Replacing or appending future prices cannot change previously created records.
- Batch, individual updates, and arbitrary chunk boundaries are identical.

Each update validates a current immutable Phase 5 frame, forms at most a trailing
three-frame window, and reads no future input. All checks, arithmetic, and evidence
construction finish **before** local state is committed. Failed updates do not
advance its index, latest output, configuration bind, or window.

Streams start at zero with consecutive unique observed indices. Source/price
units, upstream settings, prior-context links, visible liquidity configuration,
nonoverlapping chronological candles, and nondecreasing availability must agree.
Missing, duplicate, reordered, conflicting, or noncanonical upstream input is an
error, not repaired. Phase 1–5 validation is not bypassed or weakened. The consumer
does not rewind a separately advanced upstream engine on a rejected frame.

Tests cover nonempty hand-computed formations, exact threshold/one-tick/equality
cases, precision, relationships, future shocks, all-prefix identity, immutable
retained records, resolvable dependency graphs, and CSV/mocked-Binance integration.
All tests are offline; no trading-performance backtest is introduced.

## 9. Example and limitations

`config/fvg.example.toml` contains all prior-layer defaults and the two FVG defaults,
using twenty explicitly **synthetic** candles:

| Creation index | Direction | Interval | Size | C2 displacement |
| --- | --- | --- | --- | --- |
| 16 | Bullish | [101, 104] | 3 | Bullish at 15 |
| 19 | Bearish | [98, 106] | 8 | Bearish at 18 |

Index 17 is an equality/no-gap boundary example. These are software-verification
facts, not live prices, entries, remaining liquidity, or expected returns.

Limitations: geometry-only by default; no universal tick/noise threshold; no
middle-candle bridge rule; no lifecycle/fill tracking; no session/time-gap filtering;
no strategy, multi-timeframe alignment, or performance guarantee. The FVG core
retains only three frames, but those frames contain rich historical upstream
metadata. Total pipeline/output/JSON storage inherits the earlier layers' history
costs; no bounded-total-memory or production-throughput claim is made.

Source revisions, changing replay origin/settings, dishonest availability metadata,
and shifted latest-N histories are not equivalent-prefix inputs. These assumptions
are unchanged from the [no-lookahead guarantees](no-look-ahead.md).

Phase 6 implements no signals, Setup Quality Score, probability estimates, active
publication threshold, Telegram, halal filtering, CryptoIslam scraping, orders,
credentials, live trading, futures, leverage, backtesting, blocks, premium/discount,
OTE, session strategy, or multi-timeframe signal engine. The future quality policy
remains documentation only: scale 0–100, default threshold 75, zero signals valid,
and no signal quotas.
Phase 7 adds [Order Block formation](order-block-methodology.md) without changing
any FVG definition above.
**Stop after Phase 7. Phase 8 — Premium/Discount / PD Arrays requires explicit approval.**
