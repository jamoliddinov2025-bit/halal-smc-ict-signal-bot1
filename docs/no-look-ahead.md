# No-look-ahead guarantees — Phases 3 through 14

## Contract

At observation `t`, only completed canonical OHLCV candles `0..t` may influence
outputs. All runs compared below start from the **same initial history and
configuration**, with identical, valid, completed prefix candles.

For `analyze`, there is one immutable snapshot per processed candle:

```python
full = analyze(candles, config)
for n in range(len(candles) + 1):
    assert analyze(candles[:n], config) == full[:n]
```

Replacing or appending any valid future candles at indices `>= n` must not change
`full[:n]`. This includes all historical trend directions/evidence, structure
events/levels, and swing-confirmation metadata—not just the latest direction.

## Why a symmetric fractal is still causal

For `fractal_length = 2r+1`, a pivot at `p` needs right-side candles up to `p+r`.
It is **not published at p**. The detector emits it only while processing the
closed candle `t=p+r`, using the completed trailing window `[t-2r, t]`.

Therefore a Swing carries two distinct coordinates:

- `pivot_index` / `pivot_timestamp`: where the extremum occurred.
- `confirmed_index` / `confirmed_timestamp`: the confirming candle's index and
  opening-time identifier; the Swing is available only after that candle closes.

A future candle may confirm or disqualify a previously *pending* pivot. It cannot
change an already published result. No provisional pivot, centered retrospective
label at `p`, end-of-series flush, or synthetic right-side padding is emitted.

For the flat `detect_swings` API, compare prefixes by **confirmation**, not pivot:

```python
full_swings = detect_swings(candles, config)
assert detect_swings(candles[:n], config) == tuple(s for s in full_swings if s.confirmed_index < n)
```

Filtering by `pivot_index < n` would wrongly import confirmations from the future.

## State-transition argument

Let `S[t-1]` be the analyzer's private state after candle `t-1`. Processing `t`
reads only `S[t-1]` and `candle[t]`:

1. Validate chronological order/type and advance the bounded trailing window.
2. Compute candidate confirmations using only that window, ending at `t`.
3. Check close-to-close crossings using the prior close, active levels, and
   trend through `t-1`. No level confirmed on `t` is used in a break on `t`.
4. Activate the newly confirmed swings; retain only the latest two of each kind.
5. Derive the new trend and publish the immutable output for `t`.

No step receives future data, a final-series statistic, or the final series
length. Identical initial state and prefix candles therefore give identical state
and output at every prefix step, by induction. The batch helper is literally the
same stream-update loop, not a separate vectorized retrospective algorithm.

Invalid duplicate/out-of-order/noncanonical stream updates are rejected before
state mutation. They cannot advance the index or edit the last snapshot. Swings,
trend states, events, and snapshot collections are frozen, so keeping earlier
returned objects does not retain a mutable view into the engine's queues.

## Tests that exercise the guarantee

`tests/analysis/test_no_lookahead.py` includes:

- Every prefix of seeded, varied OHLCV series, for total window lengths 3, 5, 7,
  and 9, compared against corresponding full-series snapshots.
- Replacement of *all* future prices at every cutoff with alternating extreme
  valid prices; previously available results must remain identical.
- Appended future candles, including checks on the original unconfirmed tail.
- Flat swing-prefix comparisons using confirmation indices.
- A pivot that future candles can either confirm or invalidate, without changing
  the shared historical prefix.
- Retained snapshots compared to independent prefix runs and deep copies after
  the stream processes future data.
- An independent closed-prefix extrema oracle and confirmation-delay metadata.
- A hand-computed sequence with actual bullish/bearish BOS and CHoCH events, so
  passing the causal tests is not merely an artifact of always returning no events.

`test_replay.py` also compares individual updates, arbitrary chunk boundaries,
single-pass iterables, independent analyzer instances, CSV replay, and mocked
Binance normalization. `test_bos.py` checks that same-candle new confirmations or
trend labels do not retroactively change event classification.

## Phase 4 evidence and producer guarantees

The shared [evidence provenance contract](evidence-provenance-contract.md) adds
explicit closed-bar boundaries and real `available_at` instants. Unlike the
Phase 3 candle-opening identifiers, those availability instants are appropriate
for cross-timeframe dependency checks. A producer must not substitute a
Swing's confirming-candle opening timestamp for the instant it became knowable.
Source/configuration fingerprints and immutable snapshot IDs must be derived
from the known prefix only. Phase 4 producers now populate these records; no scoring is added.

For `analyze_liquidity`, every fixed-prefix replay must equal the corresponding
full-series prefix, including provenance IDs/hashes, pool-member revisions,
retirements, contexts, and sweep payloads. The same state-transition argument
applies: each update reads only prior state and the current available observation.
Newly confirmed pools are not activated before prior-pool breach checks, and a
sweep target must have been known by the bar open. Pending pivots and failed
breaches are never later relabeled as historical sweeps.

`tests/liquidity/` verifies every prefix of seeded and hand-computed series, future
replacement/appending, immutable retained objects, independent prefix hashing,
resolvable dependency graphs, delayed arrivals, and batch/stream/chunk/CSV/mocked
Binance equivalence. The [Phase 4 methodology](liquidity-sweep-methodology.md)
defines the exact rules and historical-availability assumptions. Use identical
explicit availability annotations when comparing delayed-arrival stream replays.

## Phase 5 displacement

`DisplacementAnalyzer` consumes the original Phase 4 frames. At candidate t it
uses only the available ATR ending at t−1, raw current closed-candle measurements,
and the optional latest prior sweep cohort known by the candidate open. The
current candle is added to ATR only for the next candidate. Current-candle sweeps
are cached only after classification and cannot become preceding context on t.

Every-prefix/future-replacement/append tests compare full frames, ATR evidence,
event IDs, and provenance hashes. Batch and chunk helpers are the same update
loop. Data hashes and canonical encoding come from the existing upstream contract,
not from a file containing future rows or a recomputed independent source pipeline.
Only a successful update commits local state. See
[displacement methodology](displacement-methodology.md) for the exact warm-up,
arrival-time, numeric-boundary, source-trust, and fixed-history assumptions.

## Phase 6 FVG creation

`FVGAnalyzer` reads only existing Phase 5 frames. It evaluates C1=t−2, C2=t−1,
C3=t after C3's observation is available. Strict positive outer-wick geometry and
an exact configured absolute minimum determine formation. No candidate is emitted
on C1/C2, and no future fill can alter a created record.

Associations refer to C2's already-published displacement and preceding-sweep
group. C3 displacement, C2/C3 same-candle sweeps, and future evidence are not
substituted or backfilled. C3's already-known input-prefix hash is reused.

Phase 6 tests compare every prefix, changed/appended future suffixes, batch/stream/
chunk replay, immutable records, deterministic IDs/hashes, full dependency graphs,
and provider integration. The [FVG methodology](fvg-methodology.md) specifies
source/availability assumptions, equality/minimum boundaries, and creation-only
scope. Local state changes only after a complete successful update.

## Phase 7 Order Block formation

`OrderBlockAnalyzer` consumes existing Phase 6 frames. Candidate classification and
zone facts come only from that candle's original Phase 5 observation. Selection
uses a bounded window before the matching displacement, with pre-open candidate
availability. The OB label is **not** assigned at the earlier candidate time.

Default publication follows matching displacement, strict zone departure, and
same-displacement-candle BOS/CHoCH. Required FVG mode freezes the d-time candidate
history and waits only for the exact matching C2=d FVG at d+1. If it is missing,
there is no OB. Future structure cannot rescue absent structure on d, and future
sweeps/FVGs never enrich an already observable default-mode record.

Tests explicitly distinguish the older candidate coordinate from the later
publication cutoff, and compare every prefix, changed/appended future suffixes,
batch/stream/chunk replay, immutable evidence, configuration identity, and source
hashes. All state changes occur after a successful update; failed input does not
advance a private pending confirmation. See
[Order Block methodology](order-block-methodology.md) for exact causal rules.

## Phase 8 Premium/Discount

PD consumes already-confirmed swing evidence in the existing Phase 7 stream. A
range can first be used at its latest endpoint's actual confirmation cutoff. At
closed candle t, only endpoints and context known by t participate; a same-pivot
or nonpositive newest pair yields insufficient context rather than a fitted older
range. Exact midpoint/band arithmetic uses existing Decimal helpers.

Every close is classified, and newly published upstream arrays receive immutable
publication-time sidecars. Original objects and IDs are preserved. A later range
may change later classifications, but never an earlier range, frame, or sidecar.
Tests compare all prefixes, future-price replacement/appending, batch/stream/chunk
replay, stable IDs/hashes, complete provenance graphs, and original serialized
objects. HTF references are data-only hooks; no cross-timeframe joining occurs.
See [PD methodology](premium-discount-methodology.md) for exact rules and limits.

## Phase 9 MSS

The MSS consumer reads prior/current PD frames, not future input. It requires
ready prior directional Phase 3 control and the exact broken confirmed swing to
be available by the break candle's open. Current matching displacement and the
existing opposing CHoCH provide the closed-candle confirmation. New current
confirmations cannot retrospectively establish prior control or replace the level.

Actual source objects and references are retained. Sweep context comes from the
existing displacement; concurrent FVG context has its original C2 identity; OBs
must match the exact current displacement; PD is context rather than a signal
gate. No future C3 or delayed OB enriches an old MSS. There is no forced rewrite
of prior structure labels. Event/evidence/frame IDs and prefix hashes remain
stable across every prefix, future-price shocks, and batch/stream/chunk replay.
See [MSS methodology](mss-methodology.md) for the exact operational definition.

## Phase 10 Breaker formation

The Breaker consumer registers original published OB IDs after processing older
sources for the current closed candle. A source is assessed once on its first
strict opposing closing violation. It must have been known before the bar opens,
and matching current displacement/MSS must exist before publication. A missing
confirmation creates immutable rejected evidence, not a candidate to relabel later.

All independent qualifying sources are retained, with original zone boundaries
and IDs. Same-candle/future sweeps, future FVGs, later PD ranges, new OBs and future
MSS evidence cannot enrich an already-published Breaker. All formation bookkeeping
is committed only after successful validation; failed frames cannot consume an
eligible source. Prefix, future-suffix, batch/stream/chunk, identity, and original
object immutability tests cover both confirmed and rejected first violations.
See [Breaker methodology](breaker-block-methodology.md) for exact rules and limits.

## Phase 11 Mitigation first interaction

The Mitigation consumer registers original published OB IDs after processing older
sources for the current closed candle. A source is assessed once on its first
interior range overlap. It must have been known before the bar opens. Confirmed
Breakers retire remaining first-mitigation eligibility before overlap checks.
Outside candles and exact endpoint touches do not consume the first opportunity.

All independent qualifying sources are retained, with original zone boundaries
and IDs. Future prices, later FVG/sweep/PD/OB/MSS/Breaker evidence cannot enrich
an already-published mitigation. All interaction bookkeeping is committed only
after successful validation; failed frames cannot consume an eligible source.
Prefix, future-suffix, batch/stream/chunk, identity, and original object
immutability tests cover first interactions and post-Breaker rejections.
See [Mitigation methodology](mitigation-block-methodology.md) for exact rules
and limits.

## Phase 12 Optimal Trade Entry

The OTE consumer reads the current Phase 8 dealing range from existing PD frames.
A zone is created when that range is published. A close is classified against it
only if the range was known before the classified candle opened. The confirmation
candle of a new range cannot use that future zone. Missing or not-yet-known ranges
yield insufficient context rather than an older-range fallback.

Exact Decimal 0.62/0.79 geometry uses existing helpers. Later ranges create new
zone IDs and never rewrite earlier observations. Prefix, future-suffix,
batch/stream/chunk, identity, and original object immutability tests cover all
four classification labels. See [OTE methodology](ote-methodology.md) for exact
rules and limits.

## Phase 13 multi-timeframe confluence

The MTF consumer reads existing primary OTE frames and a buffer of already-generated
HTF OTE frames. At primary open t, only HTF evidence with `available_at <= t` may
appear on the published snapshot. Future HTF candles sitting in the input buffer
are ignored and must not be referenced, even as rejected IDs. A later HTF close
may affect later LTF contexts; it cannot rewrite published ones.

Prefix, future-suffix, batch/stream/chunk, identity, and original-object
immutability tests cover incomplete 4h candles versus 15m 09:00 opens, delayed HTF
arrival, and independent 1h/4h labels. See
[MTF methodology](mtf-confluence-methodology.md) for the exact cutoff.

## Phase 14 halal asset filter

The filter reads only the configured registry and the current series symbol from
an existing MTF frame. It does not inspect future candles, prices, or HTF
context to upgrade UNKNOWN into HALAL. The registry decision is bound on the
first observation and reused. Snapshot identities reuse the current MTF
consumed-prefix hash, so appending later frames cannot rename earlier ones.

Prefix, future-suffix, batch/stream/chunk, identity, and original-object
immutability tests cover allow-list HALAL, unlisted UNKNOWN, deny-list HARAM,
and case normalization. See
[halal filter methodology](halal-filter-methodology.md).

## What this guarantee does not mean

- Candle opening timestamps are identifiers, **not** claims that a close-based
  result was knowable at the candle open. Feed completed candles only.
- It does not make unclosed live rows or manually falsified confirmation metadata
  safe. CSV closure/provenance remain caller assumptions; Binance's data-layer
  closed-candle cutoff relies on an appropriately synchronized host clock.
- Changing earlier candles, configuration, or the starting history changes the
  problem. Vendor revisions, dropped rows, gaps, or latest-N window shifts can
  legitimately change a fresh replay. This is not a promise of equal outputs
  across unequal input histories or persisted incremental state.
- Upstream normalization may reject malformed files before any replay; the
  prefix guarantee compares successful analysis of valid canonical sequences.
- No wall-clock scheduling, intrabar execution ordering, same-close fills,
  backtest profitability, trading recommendations, or asset eligibility follows
  from these guarantees.

All tests are offline. Phase 14 adds registry enforcement only. No
OB/FVG lifecycle, session strategy, signals, risk management,
position sizing, Telegram, backtesting, execution, or scoring is implemented.
