# Liquidity and sweep methodology — Phase 4

These are explicit project conventions, not universal SMC definitions. “Liquidity”
here means a **price-structure proxy**, not observed stop orders, actual resting
liquidity, an order book, or a trade recommendation. No scoring is implemented.

## 1. Inputs, time, and APIs

`LiquidityAnalyzer(series=..., config=..., analysis_config=...)` processes one
canonical `OHLCV` candle at a time through `update(candle, available_at=...)`.
`analyze_liquidity(...)` is the same sequential loop, with default availability.
One frozen `LiquiditySnapshot` is published per observed candle, containing:

- `context`: this candle's actual Phase 3 `AnalysisSnapshot`, raw observation,
  and provenance; Phase 3 trend/BOS/CHoCH semantics are unchanged.
- `confirmed_swings`: newly confirmed `SwingEvidence` records with full raw windows.
- `pool_updates`: new, revised, or terminal pool snapshots, **not all active pools**.
- `sweeps`: confirmed events first available on this candle, not pending candidates.

`active_pools`, `latest`, and `processed_count` expose read-only current state.
To reconstruct active state, upsert ACTIVE deltas by `pool_id`, and remove SWEPT
or INVALIDATED deltas. Earlier returned objects remain unchanged.

### Closed-bar and availability conventions

Use one declared `SeriesProvenance` per symbol/timeframe/provider/dataset and fixed
replay origin. Indices start at zero. No fetching, sorting, deduplication, source
switching, gap filling, or market data revisions happen inside this analyzer.

- `CandleReference.opened_at` is the canonical candle-opening identifier.
- `closed_at` is the **exclusive** end derived from the declared UTC timeframe,
  not inferred from the next input row. Fixed intervals add their duration;
  `1M` advances to the next calendar month and requires a UTC month-start opening.
  Other fixed-interval exchange-grid alignment is a source/caller assumption.
- Default `available_at = closed_at` is an **immediate historical availability
  assumption**, not a claim about exchange/feed publication latency. For known
  delays, supply the true availability instant to `update` and replay those same
  annotations. Microseconds are preserved, never rounded into an earlier bar.
- Inputs must be chronological, nonoverlapping, completed by their declared
  availability time, and supplied in nondecreasing availability order. Gaps are
  allowed and count as observed bars, not synthetic slots.
- A sweep requires the **latest active pool version to have been known by the
  breach candle's OPEN**. This conservative pre-bar rule avoids guessing intrabar
  ordering. If the latest revision arrived during the bar, its breach invalidates
  that version without a sweep, even if an older version might have been eligible.

Invalid external inputs are rejected before advancing the detector, active state,
processed count, or incremental input hash. The library does not check wall-clock
“now”; completed/history/source assertions remain caller responsibilities.

## 2. Pool formation and equal levels

Reuse the approved strict, delayed fractals from `AnalysisConfig`:

- A confirmed **swing high** seeds an ACTIVE **buy-side** pool (`swing_high`).
- A confirmed **swing low** seeds an ACTIVE **sell-side** pool (`swing_low`).
- Two or more confirmed same-side members form `equal_highs` or `equal_lows`.
  A singleton is already a pool; two members change its kind, not its entity ID.
- Equal levels refer to **separate confirmed swings**, not arbitrary neighboring
  candle highs/lows. Adjacent plateau ties rejected by Phase 3 are not fabricated
  into new swings/pools. No tail flushing or backdating to a pivot is allowed.

### Fixed-anchor tolerance

`LiquidityConfig` requires an explicit `price_unit` and defaults to
`equal_tolerance_bps = Decimal("0")`. TOML requires a quoted decimal string.
The permitted range is 0–1000 basis points, at most eight meaningful fractional
places. This is raw geometric tolerance, **not a quality value**.

Let `P` be the first confirmed member's price and `b` the configured tolerance:

```text
lower_bound = P × (1 - b / 10000)
upper_bound = P × (1 + b / 10000)
```

The band is anchored permanently to `P`. New same-side swing prices **inside or
on either boundary** can join. The band is not recentered or widened by later
members. With the default zero tolerance, prices must be exactly equal.
Calculations use exact Decimal precision independent of the ambient context.

If more than one active band matches, assign the new swing to the **oldest-created
matching pool**, not the closest, strongest, or highest-rated pool. A swing joins
only one pool of its side. Bands can overlap; they are not merged transitively.
A new pool is created when none matches. Touch counts derive from ordered unique
confirmed members; raw wick contacts do not independently increment them.

This anchor rule is not pairwise-price equivalence: two members on opposite sides
of the anchor can differ by the entire band width. The raw member prices remain
available for any later methodology to assess without losing provenance.

## 3. Breaches and same-candle sweeps

At candle `t`, inspect **all previously active pools before adding confirmations
or members first known on t**. Retire each pool on its first strict band breach:

| Side | Strict breach | Required starting position | Required completed return |
| --- | --- | --- | --- |
| Buy-side | `high[t] > upper_bound` | `close[t-1] <= upper_bound` and `open[t] <= upper_bound` | `close[t] < lower_bound` |
| Sell-side | `low[t] < lower_bound` | `close[t-1] >= lower_bound` and `open[t] >= lower_bound` | `close[t] > upper_bound` |

When the pre-bar availability rule and all row conditions hold, publish a
`SweepEvent` and a new SWEPT pool snapshot. “Buy-side/sell-side” names the liquidity
being breached, **not an order direction**. Trend does not filter these events.
Historical trend and structure are retained as context only, without HTF alignment
or any new contextual feature being computed.

- A high/low exactly at the outer boundary is a touch, not a breach.
- A close exactly at the return boundary is not a full strict return.
- A close inside a nonzero-width band is insufficient: it must return through
  the **whole** band, not merely under its outer edge.
- Gap opens outside the band are not labeled sweeps, even if price later returns.
- A close beyond the level is a failed return, not a sweep.
- There is **no multi-candle reclaim window**, pending sweep output, or later
  relabeling of a failed breach. `SweepEvent` always means confirmed on this bar.

On a strict breach that fails the rules, publish an INVALIDATED pool version and
no sweep. Invalidation reasons are checked in this order:

1. `not_known_at_open`: latest active version was not available at the bar open.
2. `started_outside`: prior close or current open was beyond the outer boundary.
3. `not_reclaimed`: completed close did not strictly return through the band.

Both terminal states remove the pool from active consideration. No subsequent
crossing of the same entity produces a duplicate event. A later, genuinely new
confirmed swing can seed a different entity at the same price; that is not a
resurrection or reuse of the old pool's members.

Multiple distinct pools can be swept on one candle, including both sides. Output
order is deterministic pool-creation order, **not an inferred intrabar path or a
ranking**. Each target entity is reported at most once. There are no count quotas,
forced event counts, active-pool eviction caps, or automatic expiration rules.

## 4. Immutable payload and provenance mapping

The `EvidenceProvenance`, `EvidenceReference`, `SeriesProvenance`, and
`CandleReference` contract from commit `edda6c0` is reused **without changing it**.

| Payload | Preserved facts |
| --- | --- |
| `ObservedCandle` | Complete canonical OHLCV, indexed/open/closed reference, actual assumed/supplied availability. |
| `SwingEvidence` | Original Phase 3 Swing plus every raw observation in its symmetric confirmation window, with a distinct evidence ID. |
| `StructureContext` | Actual per-candle Phase 3 snapshot and provenance; references prior context and newly confirmed swing evidence. |
| `LiquidityPool` | Stable entity ID; distinct snapshot ID; side/kind/status; explicit fixed bounds/reference price; settings/units; raw members/count; formation/first-confirmation/latest-touch coordinates; context; prior version and terminal transition facts. |
| `SweepEvent` | Exact ACTIVE pre-breach pool snapshot; previous and breach OHLCV; actual extreme and reclaim close; true confirmation time; before/after structure context and versioned dependencies. |

Terminal pool snapshots link to their previous ACTIVE snapshot. SWEPT versions
also link to the emitted sweep. The sweep points only to the **old** pool, never
back to the terminal version, so the published dependency graph remains acyclic.
All references resolve within retained replay outputs; nothing points to a mutable
`latest` entity. No future touch, invalidation, or confirmation edits older objects.

`evidence_json(record)` retains the complete nested raw facts as JSON. Decimal
values use exact coefficient/exponent strings; UTC times retain microseconds.
It is serialization, not a persistent archive or a deserialization API. Consumers
must retain the source/configuration artifacts and output records they need.

## 5. Deterministic artifacts and IDs

No wall-clock calls, random IDs, final-series statistics, or whole-file hashes
participate in historical evidence identity. All producers use methodology version
`1`; changes to rules/encoding must version the methodology before reusing IDs.

- `configuration_artifact` contains the actual liquidity and fractal settings plus
  `liquidity-v1`. `structure_configuration_artifact` contains `structure-v1` and
  fractal settings. Their exact UTF-8 bytes hash to the recorded configuration IDs.
- Input hashing starts with canonical JSON of
  `{"schema":"observed-ohlcv-prefix-v1","series":<SeriesProvenance record>}`.
  For each available `ObservedCandle`, append its canonical JSON bytes prefixed by
  an unsigned eight-byte big-endian byte length to the incremental SHA-256 state.
  Only the consumed prefix contributes, including explicit availability metadata.
- Canonical JSON sorts keys, uses compact separators and ASCII escapes. Numerically
  equal Decimal spellings share a normalized coefficient/exponent representation
  without context-sensitive rounding. This does not quantize prices.
- Versioned evidence IDs bind producer, source series, configuration hash, current
  prefix hash, availability, raw identity key, source references, and dependencies.
  Pool entity IDs derive from the first member and liquidity configuration.

Use a stable dataset namespace with a fixed replay origin, normalization policy,
and vendor revision convention; **not the digest of a file containing future rows**.
The input hash covers canonical supplied observations, not a claim of source
truth or authenticity. Archive normalization reports separately when needed.

## 6. Hand-computed example

`config/liquidity.example.toml` uses twelve explicitly **synthetic** candles,
`fractal_length=3`, and exact equality. The expected chronology is:

| Observation index | Newly available outcome |
| --- | --- |
| 2 | Buy-side pool at 16, pivot 1 confirmed |
| 3 | Sell-side pool at 11, pivot 2 confirmed |
| 4 | Equal highs at 16: pivots 1 and 3 |
| 5 | Pool at 11 invalidated: strict breach without return |
| 6 | Sell-side pool at 9, pivot 5 confirmed |
| 8 | Equal lows at 9: pivots 5 and 7 |
| 9 | **Buy-side sweep** of EQH 16: high 17, close 12 |
| 10 | **Sell-side sweep** of EQL 9: low 8, close 12; a separate new high pool at 17 is confirmed |
| 11 | New low pool at 8 confirmed |

Both sweep targets are immutable earlier snapshots. The newly confirmed high at
10 cannot replace or retrospectively justify the sweep target used on 10.
The fixture is software-test data, not market performance evidence.

## 7. Guarantees and limits

For the same valid closed-candle prefix, availability annotations, starting
history, and configuration, full-series and prefix snapshots are identical,
including IDs, hashes, contexts, members, lifecycle deltas, and sweep facts.
Extreme future replacement/appending cannot repaint the published past.
Batch, stream, chunk, CSV, and mocked Binance replay tests exercise this contract.

Unlike the bounded Phase 3 detector, Phase 4 retains unbroken pools and their raw
members. There is **no fixed memory bound independent of history**. Each update
scans active pools; member revisions copy/validate full member tuples. Long replay
outputs and full JSON serialization can grow quadratically with repeated revisions.
This implementation makes no production-scale throughput claim. Archive large
outputs outside Git; later optimizations must preserve these semantics and IDs.

The guarantees do not equate shifted latest-N windows, revised historical inputs,
changed settings, falsified source/arrival metadata, or real execution fills.
No live source availability or actual resting liquidity is asserted by the tests.

Phase 5 adds a separate [displacement consumer](displacement-methodology.md)
without changing these liquidity/sweep rules. Phase 6 adds a separate
[FVG consumer](fvg-methodology.md), followed by Phase 7
[Order Block formation](order-block-methodology.md). OB/FVG lifecycle, premium/discount,
signal engines, charts, Telegram, halal filtering, and scoring remain absent.
The deferred Setup Quality Score policy remains unchanged in the
[evidence contract](evidence-provenance-contract.md): future threshold default 75,
quality over quantity, valid zero-signal outcomes, and no signal-count targets.
**Stop after Phase 7; Phase 8 — Premium/Discount / PD Arrays requires explicit approval.**
