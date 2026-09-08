# Architecture — Phase 7

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

## Methodology and phase boundary

- [Market structure, swing confirmation, BOS/CHoCH definitions](market-structure-methodology.md)
- [Trend classification and readiness](trend-methodology.md)
- [No-look-ahead argument, tests, and limitations](no-look-ahead.md)

Phase 7 implements none of:
breaker/ mitigation blocks, premium/discount, OTE, session strategy, a signal
engine, BUY/SELL signals, charts, Telegram, a halal filter, or scoring.
There are also no orders, authenticated account access, or trading-performance
claims. “Halal” remains a design goal, not certification.

Stop after Phase 7. Phase 8 — Premium/Discount / PD Arrays requires explicit approval.
