# Architecture — Phase 14

## Layers and explicit I/O

```text
TOML [market_data] -> DataProvider -> canonical OHLCVBatch
                      /       \
                  local CSV   public Binance Spot klines
                                      |
                           explicit fetch only

TOML [analysis] + completed, chronological OHLCV candles
                                      |
                       MarketStructureAnalyzer.update
                                      |
             AnalysisSnapshot(confirmed_swings, trend, events)
```

The Phase 2 data provider interface, schema validation, cleaning reports, and
source behavior remain unchanged. Phase 3 operates on their canonical immutable
`OHLCV` records; it performs no fetching, sorting, resampling, or source selection.
Package imports, analyzer construction, and the CLI do not initiate I/O or analysis.

## Market data package

`src/smcsignal/data/` retains the provider contract, validated settings, provider
factory, OHLCV models, normalization, CSV replay, Binance public transport, and
error types. See the [market data methodology](market-data-methodology.md).
Runtime dependencies remain Python's standard library only.

## New analysis package

| Module | Responsibility |
| --- | --- |
| `analysis/config.py` | Frozen odd-window configuration and explicit `[analysis]` TOML loader |
| `analysis/models.py` | Typed enums and immutable Swing, TrendState, StructureEvent, AnalysisSnapshot |
| `analysis/swings.py` | Bounded trailing window; publish strict symmetric pivots only upon confirmation |
| `analysis/trend.py` | Strict HH/HL or LH/LL classification from available two-high/two-low evidence |
| `analysis/structure.py` | Prior-level close crossings, BOS/CHoCH classification, consumption, and replay orchestration |
| `analysis/errors.py` | Configuration and analysis-input failures |
| `analysis/__init__.py` | Explicit public API exports |

`MarketStructureAnalyzer.update(candle)` is the primary stateful API. The
`analyze(candles, config)` batch helper performs exactly the same sequential
updates and returns one snapshot per candle. `SwingDetector` / `detect_swings`
also expose confirmation-only detection without a structure engine.

## Per-candle flow

1. Validate the new candle's type and strictly increasing timestamp before any
   state mutation. Calculate fractal confirmations from the trailing closed window.
2. Evaluate this candle's close against levels and trend available through the
   **previous** candle; consume crossings and emit eligible BOS/CHoCH events.
3. Publish this candle's new confirmations; replace active levels and update
   the two most recent highs/lows.
4. Classify the current swing-derived trend and return a frozen snapshot.

Calculating new confirmations in step 1 does not activate them early in step 2.
A CHoCH does not force the separate swing-derived trend to reverse. A ranging-state
cross consumes its level but has no BOS/CHoCH label. These are explicit project
conventions, not claims of a universal SMC definition.

The stream retains O(fractal_length) observations plus a bounded set of swing
references and the latest snapshot. Earlier returned snapshots contain immutable
records/tuples, not views into mutable deques. The batch helper additionally
retains O(number of candles) output snapshots.

## Time, provenance, and series boundaries

Indices are local to a replay, starting at zero. All timestamps identify candle
opening times, while results are available only after the relevant candle closes.
`Swing` separates pivot time from confirmation time; `StructureEvent` retains its
broken level, prior/current closes, and prior trend for auditability.

Use one analyzer per symbol/timeframe/configuration and feed each completed candle
once. Configuration is immutable. Start a fresh analyzer when changing the series
or settings. There is no symbol metadata inside six-column OHLCV, no automatic
series mixing detection, no persistence, and no rolling-window restart equivalence.

## Testing and packaging

The original data tests are retained. `tests/analysis/` adds swing, trend, BOS,
CHoCH, configuration/model, replay, and explicit no-look-ahead tests. Synthetic
fixtures have hand-computed event outcomes. Socket access remains blocked in the
pytest process; provider integration uses injected responses rather than live I/O.

The source distribution includes explicit example configurations, docs, tests,
and tiny labeled CSV fixtures. The wheel includes both runtime subpackages and
the typing marker, not repository-local configuration/tests. Build outputs and
large/downloaded datasets remain Git-ignored.

## Phase 4 liquidity and sweep layer

`analysis/liquidity/` implements the actual producers on top of the unchanged
Phase 3 `MarketStructureAnalyzer` and the approved `analysis/provenance.py` contract:

| Module | Responsibility |
| --- | --- |
| `config.py` | Exact fixed-anchor tolerance and explicit price units; strict TOML loader |
| `time.py` | Exclusive UTC bar closure without reading the next row |
| `evidence.py` | Canonical artifacts and deterministic prefix-only evidence IDs |
| `models.py` | Frozen LiquidityPool, SweepEvent, raw observations, swing evidence, context, and frame deltas |
| `analyzer.py` | Existing-pool breach checks, one-time retirement, grouping, and batch/stream replay |
| `__init__.py` | Phase 4 public exports |

The update pipeline is: validate input/availability and fingerprint the consumed
observation; obtain the existing structure result and wrap confirmations/context;
check **prior** active pools for breaches; emit sweeps/terminal pool versions;
then activate this candle's newly confirmed pool members. Calculated new swings
cannot be used early to justify a sweep of a not-yet-known pool.

`LiquiditySnapshot` publishes pool-state deltas, not a mutable latest-state map.
`active_pools` is a read-only current view. Sweeps reference the exact old ACTIVE
pool; a terminal pool version can reference that sweep without a circular link.
Source/configuration artifacts and evidence output archives remain caller-owned;
there is no database or network I/O in this layer.

Phase 4 does not share the Phase 3 detector's fixed memory bound: active pools and
members persist until breach, and repeated full-member versions/serialization can
grow quadratically. No silent expiry, eviction cap, or signal-count quota is used.

See [liquidity/sweep methodology](liquidity-sweep-methodology.md) for complete
rules and limits, and [evidence provenance](evidence-provenance-contract.md) for the
composition contract. The future Setup Quality Score policy (0–100, configurable
threshold default 75, quality over quantity, valid zero-signal outcomes, no count
targets) remains documentation only. Scoring is not implemented in Phase 4.

## Phase 5 displacement layer: consume frames, do not duplicate producers

```text
canonical OHLCV -> existing LiquidityAnalyzer.update
                   (existing structure + liquidity/sweep + provenance)
                              |
                        LiquiditySnapshot
                              |
                  DisplacementAnalyzer.update(frame)
                              |
          DisplacementSnapshot(metrics, prior ATR, next ATR, events)
```

The original frame instance is retained. No second market-data, structure, pool,
sweep, closure, canonical-hash, or provenance implementation is introduced.

| New module in `analysis/displacement/` | Responsibility |
| --- | --- |
| `config.py` | Frozen explicit displacement thresholds and strict TOML loading |
| `calculation.py` | Exact finite-decimal arithmetic, true range, and descriptive ratios |
| `models.py` | Immutable ATRReference, DisplacementMetrics, DisplacementEvent, and frame records |
| `evidence.py` | New producer/configuration artifacts using the existing canonical codec and provenance factory |
| `analyzer.py` | Prior-ATR classification, optional known-prior sweep association, and batch/stream replay |
| `__init__.py` | Public API for current and future consumers |

ATR is a rolling SMA of prior true ranges, not Wilder smoothing. At t, the engine
classifies from the ATR reference through t−1, then publishes ATR through t for
the next candle. Sweep context is optional, direction-neutral, and restricted to
prior eligible cohorts known before the candidate opened. New state is committed
only after all input, arithmetic, evidence, and model checks succeed.

The current input-prefix hash is inherited directly from the upstream frame. An
event depends on prior ATR, current structure context, and optional prior sweeps,
not current ATR; the enclosing frame can reference both ATRs without cycles.
Separate configuration artifacts make ATR evidence independent of unrelated
body/close/context threshold changes.

Future FVG, raid/displacement, Order Block, location, multi-timeframe, and quality
components can consume typed raw records and `provenance.as_reference()` without
turning this detector into a strategy/scoring API. FVG is now a separate Phase 6 consumer; the other components remain deferred.
See [Phase 5 methodology](displacement-methodology.md) for exact
conditions, defaults, input/availability assumptions, and memory/numeric limits.

## Phase 6 FVG consumer

`analysis/fvg/` consumes existing `DisplacementSnapshot` frames exactly once,
retaining only the trailing one/two/three input frames for FVG creation. It does
not duplicate the existing pipeline, exact Decimal arithmetic, canonical codec,
source-prefix hashing, or provenance contracts.

| Module | Responsibility |
| --- | --- |
| `config.py` | Frozen absolute minimum gap and optional matching-displacement requirement |
| `calculation.py` | Strict outer-wick geometry and coherent three-frame validation |
| `models.py` | Immutable FVGEvent/FVGSnapshot with original source frames and explicit C1/C2/C3 observations |
| `evidence.py` | FVG configuration/identity artifacts through the existing provenance factory |
| `analyzer.py` | Fixed-origin streaming/batch creation; state committed only after successful validation |
| `__init__.py` | Public Phase 6 API |

The C2 displacement event and its prior-sweep group are linked as originally
published, never recomputed or selected from C3/future data. Creation occurs after
C3 closes; equality is not a gap. A configured minimum is an inclusive absolute
price distance, default zero with strictly positive geometry still required.
Optional displacement filtering requires an existing same-direction C2 event.

FVG records represent formations only: no OPEN/fill/invalidation lifecycle,
active-zone registry, mitigation, entry, or other trading behavior is introduced.
All distinct qualifying windows are retained, including nested/opposing zones.
See [FVG methodology](fvg-methodology.md) for complete definitions and limits.

## Phase 7 Order Block formation consumer

`analysis/order_blocks/` consumes existing `FVGSnapshot` frames, preserving the
original Phase 5 displacement and Phase 3 structure objects rather than rerunning
any detector. Its modules separate configuration, factual selection/calculation,
immutable models, evidence/identity integration, stream orchestration, and exports.

The default chain is: own-candle candidate facts → matching displacement with a
strict close beyond the selected zone → matching BOS/CHoCH on that displacement
candle → published OB. Optional `require_fvg` freezes the d-time search history and
waits only for the exact same-direction C2=d FVG at d+1 before publishing. A failed
wait creates no OB; it never starts lifecycle tracking or changes an old record.

An event retains the full bounded candidate window, selected raw candle, exact
zone, displacement, original structure event/context reference, inherited sweep
group, optional FVG, and separate candidate/confirmation/availability timestamps.
The current upstream prefix and approved canonical/provenance machinery are reused.
No standalone structure IDs, score values, entries, or mutable latest-zone references
are fabricated. See [Order Block methodology](order-block-methodology.md).

## Phase 8 Premium/Discount consumer

`analysis/premium_discount/` consumes existing `OrderBlockSnapshot` frames without
rerunning or modifying the Phase 1–7 engines. It selects the latest already-confirmed
low/high evidence, derives chronological range orientation, and classifies each
closed candle's close using exact Decimal midpoint/band geometry.

Its modules separate configuration, geometric calculations/classification enums,
immutable models, provenance/sidecar construction, frame orchestration, and public
exports. The required `DealingRange`, `Equilibrium`, `PDClassification`, and
`PDSnapshot` models preserve actual endpoint evidence and causal availability.

`PDArrayContext` annotates newly published pool versions, sweeps, displacement,
FVGs, and OBs at their publication cutoff without changing their original objects
or IDs. It retains representative and endpoint classifications. Later ranges never
rewrite older annotations. Current same-candle confirmations can participate only
after they are actually available at the close.

`PDContextReference` carries exact range/equilibrium IDs, timeframe/source identity,
and availability for future HTF consumers. The current evaluator is local-only:
no range resampling, HTF join, or multi-timeframe execution is implemented.
See [Premium/Discount methodology](premium-discount-methodology.md) for full rules.

## Phase 9 strict MSS consumer

`analysis/mss/` consumes existing `PDSnapshot` frames. It does not construct a new
trend, break, sweep, displacement, FVG, OB, or PD detector. Prior ready directional
structure and the exact prior confirmed level must have been known by the current
bar open. Current matching displacement and the existing opposing CHoCH then
produce one immutable `MSSEvidence` / `MSSEvent`; `MSSSnapshot` retains the original
previous/current frames and publication provenance.

Configuration declares both mandatory structure/displacement requirements; false
is rejected rather than enabling incomplete MSS. No size, score, optimization,
lookback, cooldown, or signal-threshold parameter is added.

The event is a shift-warning fact, not a forced upstream trend reversal. Relationships
retain actual broken-level pool updates, pre-sweep target pools, original preceding
sweeps, current same-direction concurrent FVGs, current OBs for the exact displacement,
and current PD classifications/sidecars. Concurrent gaps are not misattributed to
the current displacement's future C3. Nothing is later backfilled into an old MSS.

The configuration/identity factory reuses the existing canonical codec and current
consumed-prefix hash. Required confirmation data is already available when the MSS
is published; the dependency graph remains acyclic. See
[MSS methodology](mss-methodology.md) for exact criteria, time semantics, relation
roles, frozen model fields, tests, and limits.

## Phase 10 Breaker Block formation consumer

`analysis/breaker_blocks/` consumes existing `MSSSnapshot` frames and exact published
OB objects. It keeps private first-violation eligibility/seen-ID bookkeeping, not
a live zone manager. No earlier detector, price model, or provenance implementation
is changed or rerun.

The original OB must be known before the violation bar opens. Only a first strict
opposing close-through, supported by matching current displacement and MSS, creates
a `BreakerBlock`. Other first violations retain immutable rejected `BreakerEvidence`
with reasons, and cannot later be upgraded. All qualifying source IDs are processed
in original publication order; identical, nested and overlapping zones are not merged.
Original and breaker zone boundaries remain equal.

`BreakerEvidence`, `BreakerBlock`, and `BreakerSnapshot` retain the original OB,
actual observations, same-candle confirmation, source sweep/FVG/PD context, exact
references, and separate origin/invalidation/publication times. State commits only
after all checks and construction succeed. The source-prefix hash and canonical
factory are reused. See [Breaker methodology](breaker-block-methodology.md) for the
precise strict-v1 contract, configuration, causal rules, and limitations.

## Phase 11 Mitigation Block first-interaction consumer

`analysis/mitigation_blocks/` consumes existing `BreakerSnapshot` frames and exact
published OB objects. It keeps private first-interaction eligibility/seen-ID/Breaker
bookkeeping, not a live zone manager. No earlier detector, price model, or provenance
implementation is changed or rerun.

The original OB must be known before the interaction bar opens. Only the first
strict interior range overlap with the original OB zone creates a `MitigationBlock`.
Completely outside candles and exact endpoint touches do not qualify. All qualifying
source IDs are processed in original publication order; identical, nested and
overlapping zones are not merged. Original zone boundaries and IDs remain equal.

If a confirmed Breaker already exists for that OB, later overlaps cannot create a
first mitigation. Any mitigation already published remains immutable.

`MitigationEvidence`, `MitigationBlock`, and `MitigationSnapshot` retain the original
OB, actual observations, interaction geometry, exact references, and separate
origin/interaction/publication times. State commits only after all checks and
construction succeed. The source-prefix hash and canonical factory are reused. See
[Mitigation methodology](mitigation-block-methodology.md) for the precise
strict-v1 contract, configuration, causal rules, and limitations.

## Phase 12 Optimal Trade Entry consumer

`analysis/ote/` consumes existing `PDSnapshot` frames and the current Phase 8
`DealingRange`. It does not rerun or modify earlier detectors. The zone is exact
Decimal 0.62–0.79 retracement geometry of that range. A completed close is
classified only when the range was known before the bar opened. Missing or
not-yet-known ranges yield `INSUFFICIENT_CONTEXT` without falling back to an older
pair.

`OTEZone`, `OTEObservation`, and `OTESnapshot` retain the original range object and
ID, ordered bounds, close classification, and separate zone-creation versus
observation times. State commits only after all checks and construction succeed.
The source-prefix hash and canonical factory are reused. See
[OTE methodology](ote-methodology.md) for geometry, timing, multiple-range policy,
and limitations.

## Phase 13 multi-timeframe confluence consumer

`analysis/mtf/` consumes existing `OTESnapshot` frames for one primary timeframe
and independently generated OTE sequences for each configured higher timeframe.
It does not resample OHLCV or rerun Phases 1–12. HTF evidence is eligible only
when `available_at <= primary_candle.opened_at`. Incomplete HTF candles are
excluded. Multiple HTFs stay independent; label disagreement is `MIXED` without
a score.

`MTFRelation`, `MTFEvidenceReference`, and `MTFSnapshot` retain original HTF
objects and IDs. State commits only after validation and provenance succeed.
The primary consumed-prefix hash is reused. See
[MTF methodology](mtf-confluence-methodology.md) for eligibility, labels, and
limits.

## Phase 14 halal asset filter

`analysis/halal_filter/` consumes existing `MTFSnapshot` frames. It does not
rerun or modify Phases 1–13. Classification is a frozen allow-list or deny-list
registry lookup on the series symbol. There is no internet fetch, screening API,
or autonomous religious decision.

`AssetClassification`, `HalalDecision`, and `HalalSnapshot` retain the original
MTF object and ID. Only `HALAL` is eligible for future signal phases.
`UNKNOWN` is never silently treated as `HALAL`. Deny-list mode never emits
`HALAL`. State commits only after validation and provenance succeed. The current
consumed-prefix hash is reused. See
[halal filter methodology](halal-filter-methodology.md).

## Methodology and phase boundary

- [Market structure, swing confirmation, BOS/CHoCH definitions](market-structure-methodology.md)
- [Trend classification and readiness](trend-methodology.md)
- [No-look-ahead argument, tests, and limitations](no-look-ahead.md)

Phase 14 implements none of:
session strategy, a signal
engine, BUY/SELL signals, charts, Telegram, or scoring.
There are also no orders, authenticated account access, or trading-performance
claims. The filter is a caller-supplied registry, not Sharia certification.

Stop after Phase 14. Phase 15 — Setup Quality Scoring requires explicit approval.
