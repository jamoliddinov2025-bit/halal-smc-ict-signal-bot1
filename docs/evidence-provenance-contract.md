# Evidence provenance contract — architecture addendum

## Status and scope

This contract was approved in commit `edda6c0`. The shared records and structural
protocol in `src/smcsignal/analysis/provenance.py` remain unchanged. After explicit
Phase 4 approval, actual `LiquidityPool` and `SweepEvent` producers now compose
this contract. Their implemented payload mapping, lifecycle choices, canonical
artifacts, and tests are documented in the
[Phase 4 methodology](liquidity-sweep-methodology.md). Phase 5 now also adds
`ATRReference`, `DisplacementEvent`, and frame records using these same contracts;
see [displacement methodology](displacement-methodology.md).

No scoring, weights, normalized quality values, confidence ratings, ranking,
signal publishing, or active publication-threshold configuration is implemented.
Scoring remains excluded through Phase 10 and deferred to separate future approval. Existing Phase 3 swings, trend,
BOS/CHoCH definitions, results, and data-provider behavior remain unchanged.

## 1. Composition, not feature/scoring coupling

Every liquidity/sweep record must expose an immutable
`provenance: EvidenceProvenance` property, satisfying `ProvenancedEvidence`, and
carry a separate typed payload of **raw observed facts**. Other future factor
records can use the same contract without changing detector APIs into evaluators.

Implemented shared records:

| Record | Required meaning |
| --- | --- |
| `SeriesProvenance` | Symbol, timeframe, venue, provider, and stable logical dataset identity. Include the replay-origin convention in that identity so local candle indices are unambiguous. |
| `CandleReference` | Exact series, local candle index, UTC opening instant, and an explicit **exclusive closed-bar boundary**. |
| `EvidenceReference` | Exact immutable evidence snapshot ID, its series, and its real knowable `available_at` instant. Never a mutable entity's `latest` pointer. |
| `EvidenceProvenance` | Snapshot ID, schema version, series, producer/version, configuration hash, input-prefix hash, availability instant, immutable source-candle tuple, and dependency references. |
| `ProvenancedEvidence` | Structural typing contract requiring provenance, with no evaluation/calculation method. |

Producer configuration and input references use lowercase SHA-256 digests. They
are **caller supplied**: this module neither computes nor stores a dataset or
configuration archive. Producers must retain retrievable canonical source/config
artifacts, not just unrecoverable hashes. Source IDs must not contain credentials.

`source_candles` may be empty for a non-candle source, such as a future externally
reviewed document; its source artifact and knowable time still need provenance.
Absence of evidence means absence/unknown, never an inferred approval or a default
quality value.

## 2. Causality, timeframes, and stable identity

- Candle opening time is not availability. Phase 3 `Swing.confirmed_timestamp`
  identifies the confirming candle's **open**; it must not be copied into
  `EvidenceProvenance.available_at` as though confirmation were already knowable.
- A producer supplies the confirming candle's actual closed-bar boundary from
  known interval metadata, not by reading the next candle. For an upstream
  inclusive final-millisecond close time, the exclusive boundary is later.
- Evidence is available no earlier than all required source candles have closed,
  all confirmation conditions are observable, and all dependencies are known.
  Arrival/publication latency must also be respected when relevant.
- Shared records reject unclosed-at-cutoff source candles and dependencies whose
  declared availability is later than the record's cutoff. UTC normalization
  preserves microseconds rather than rounding delayed evidence into the past.
- Source candle references belong to one declared series and are chronological
  and unique. Cross-timeframe/instrument context is linked through explicit
  evidence dependencies. **Do not compare local indices across timeframes**;
  compare actual UTC availability.
- `input_prefix_hash` must cover only the normalized input prefix available at
  that record's cutoff, including the fixed starting-history/cleaning convention.
  Do not hash a full file containing future rows and attach that hash to past
  evidence. Appending future data must not rename old snapshots.
- IDs identify immutable versions. Keep a stable feature/entity ID in the raw
  payload and a distinct evidence snapshot ID for each later lifecycle version.
  Do not use random run IDs or current wall-clock time in deterministic identity;
  separate operational run logging from analytical identity.
- Dependencies are immutable snapshot references. Direct self-references and
  duplicate references are rejected. Registry resolution, artifact authenticity,
  transitive-cycle checks, and validation that a supplied hash really represents
  the claimed prefix remain producer/integration responsibilities.

These are metadata validation rules, not a claim that hand-authored references
prove unseen source data. Future producer tests must verify causal derivation and
prefix-stable identities as well as the existing analytical prefix invariance.

## 3. Required liquidity payload

The Phase 4 liquidity object must retain at least:

| Field group | Required raw facts / references |
| --- | --- |
| Identity | Stable pool/entity ID plus the shared immutable provenance snapshot. |
| Meaning | Explicit buy-side/sell-side designation and detector-defined pool kind; these are not trade directions or quality grades. |
| Geometry | Reference price, lower/upper bounds, and price units. Preserve exact decimal precision; do not retain only a rounded bucket. |
| Membership | Exact source `Swing` identities/records, pivot coordinates, confirmation coordinates, and corresponding source candle references. |
| Observations | Ordered, deduplicated touch/member references; any stored touch count must be derivable from that list. Preserve the observation window and the settings that decided membership. |
| Formation and availability | First contributing observation, first confirmed/knowable pool state, latest contributing observation, and the current snapshot cutoff. |
| Lifecycle | Detector-defined candidate/active/consumed/invalidated state, transition evidence, and previous snapshot references when revised. Unknown transitions stay absent. |
| Context | References to already available trend/structure evidence when used; do not recompute those facts from a later market state. |

Phase 4 now implements singleton pools, equal-level membership from two confirmed
swings, fixed-anchor geometric tolerance, and first-breach retirement without
expiry. Settings are retained as raw configuration provenance, not points or
quality classifications; see the implemented methodology for exact definitions.

## 4. Required sweep payload

The Phase 4 sweep object must retain at least:

| Field group | Required raw facts / references |
| --- | --- |
| Identity | Stable sweep/entity ID plus its own immutable provenance snapshot. |
| Target | Reference to the exact liquidity snapshot used in the decision, including side and bounds. Never look up an enlarged or relabeled pool from a later time. |
| Breach | Candle references and observation-window boundaries for the breach, relevant source OHLCV, and actual extreme price/candle coordinates. |
| Return / reclaim | Return-close price and corresponding candle reference, if observed. Missing/unconfirmed return evidence remains absent rather than a fabricated success. |
| Confirmation | Candle(s) and actual availability instant at which the detector's approved conditions became knowable. Preserve each multi-candle confirmation dependency. |
| Raw measurements | Enough source prices/ranges/volumes, units, and window references to reproduce any later depth, distance, duration, body/wick, or relative-context assessment. No normalized rating or strength grade. |
| Lifecycle | Pending/confirmed/invalidated states only as defined by the future detector; later transitions create new evidence snapshots, never rewrite historical ones. |
| Structural context | References to the applicable historical `TrendState`, BOS/CHoCH events, and other available evidence, using shared dependency references. |

Phase 4 implements strict same-candle full-band breach/reclaim rules and confirmed
SweepEvent objects only; pending/multi-candle sweep states are not produced.
No sweep-quality assessment is implemented.
A pool formed/enlarged after a breach must not retrospectively justify that past
breach as though the later pool had already been known.

## 5. Future factor compatibility — facts, not contributions yet

Potential future contributors must retain their own producer/settings/source and
availability information through the same composition contract:

| Future factor | Evidence to preserve, not an implemented calculation |
| --- | --- |
| HTF trend alignment | Higher-timeframe identity, confirmed trend/swing snapshot, and its true availability relative to the candidate's timeframe. |
| BOS / CHoCH quality | Exact structure event, broken confirmed swing, previous/current closes, prior trend, and relevant closed-candle context. |
| Liquidity sweep quality | Immutable target pool snapshot and the breach/reclaim/confirmation facts above. |
| Displacement quality | Source candles, raw body/range/volume inputs, and the historical reference window/settings, if used. |
| FVG quality | Formation candle references, original bounds, availability, and versioned lifecycle facts known at the cutoff. |
| Order Block quality | Origin candle/structure references, bounds, detector settings, and versioned state. |
| Premium / Discount location | Exact historical range/endpoints and the price/availability at which location is assessed. |
| Risk-to-reward quality | Proposed price inputs and their provenance/assumptions when that later component exists; no hypothetical ratio is created now. |
| Halal eligibility confirmation | Authoritative source/review reference, policy version, publication/known-at time, and any separate effective date. Unknown is not approval, and a current review must not be backdated into historical evidence. |

Do not replace raw facts with a component's points or a generic `quality` field.
Preserving the facts allows later methodology changes without rerunning an
untraceable or hindsight-contaminated detector. Mandatory safety/eligibility
prerequisites, once defined, cannot be overridden by a favorable aggregate result.

## 6. Deferred signal policy — documentation only

The eventual signal engine must:

- Calculate a **Setup Quality Score on a 0–100 scale** in a later approved phase,
  **not through Phase 10; deferred to separate future approval**.
- Publish only valid setups **above** a configurable threshold; the required
  future default is **75**. No active setting or comparison is added now.
- Prioritize quality over quantity. **Zero signals is a valid result** when no
  qualifying setup exists, not an error to be repaired with fallback signals.
- Never use minimum/desired signal counts, daily quotas, activity targets, or
  missed-target compensation to influence generation, candidate acceptance,
  thresholds, filters, weights, or parameter tuning. Do not relax standards to
  manufacture activity. Future observability counts must not become generator inputs.

No component weights, score values, placeholder totals, normalization rules,
missing-factor substitutions, thresholds in executable configuration, or publish
logic are created by this architectural change.

## 7. Producer acceptance requirements

Liquidity/sweep producer tests must establish that:

1. Both record types compose `EvidenceProvenance` and expose their typed raw facts.
2. Dependencies and source candles were available at each record's cutoff,
   including higher-timeframe confirmation boundaries and data-arrival delays.
3. Source swings/pools are exact versioned references, not mutable latest state.
4. Stored measurements, counts, and time coordinates are reconstructible from
   archived raw facts/configuration; they are not proxy quality values.
5. Later touches, invalidations, reclaims, or added future data do not mutate or
   rename historical evidence. New states use new immutable snapshot identities.
6. The required liquidity/sweep fields above survive any future serialization.
7. There is still no scoring or signal publishing through Phase 10 and no count-target
   mechanism influencing analytical outputs.

Shared-contract tests retain their original coverage. `tests/liquidity/` now also
verifies actual producers, immutable membership/lifecycle versions, raw JSON
payloads, configuration artifacts, independently framed prefix digests, complete
resolvable dependency graphs, and prefix/future/replay invariance. A persistent
evidence artifact registry, scoring, and signal publishing are not implemented.

## Phase 5 displacement producers

The frame-native displacement layer reuses the original observations, series,
current-prefix hash, canonical codec, and provenance factory. Its event references
prior ATR, current structure context, and optional prior sweeps. Current ATR is
published separately for the next candidate. Raw OHLC, true ranges and exact total,
reference period/candle, body/range/close metrics, configuration version, and actual
availability are retained; no raw facts are replaced by quality/probability values.

Producer tests now also cover independent ATR reconstruction, full dependency
graph resolution, exact boundary comparisons, deterministic identity, arrival-time
association, and all-prefix/future/replay invariance for actual displacement events.

## Phase 6 FVG creation producers

`FVGEvent` and `FVGSnapshot` now compose the same unchanged provenance contract.
They retain all three original Phase 5 frames and exact source-candle references,
strict gap boundaries/size, C2's actual displacement and inherited prior-sweep
group, source/configuration identity, and C3's consumed-input-prefix hash.
Creation becomes knowable only after C3 closes. No mutable lifecycle, future
interaction, scoring value, signal, or trade is attached. See
[FVG methodology](fvg-methodology.md) for the implemented causal relationship rules.

## Phase 7 Order Block formation producers

Order Block records retain the exact candidate's original facts and full bounded
selection window, mandatory displacement event, matching same-candle structure
event/context when used, inherited sweep group, and optional exact next-candle FVG.
Candidate timestamps are not publication times. Default availability follows
confirmed displacement/structure; requiring FVG explicitly delays availability to
C3 without changing the candidate. The current publication prefix, existing codec,
and shared provenance contracts are reused. See
[Order Block methodology](order-block-methodology.md) for the operational definition.
No lifecycle, entry, quality score, or probability estimate is added.

## Phase 8 PD context and non-mutating relationships

Confirmed `DealingRange` and `Equilibrium` evidence now supply local geometric
context. `PDSnapshot` classifies each close; `PDArrayContext` annotates new upstream
evidence versions at publication time, retaining the original object and ID.
Range, equilibrium, source subject, cutoff, configuration and prefix dependencies
are explicit. `PDContextReference` is a timeframe-bearing data hook for future HTF
consumers, not an HTF join or execution engine. See
[PD methodology](premium-discount-methodology.md). No scores, entries, portfolio
risk, ranking, or optimization are produced.

## Phase 9 MSS evidence

`MSSEvidence`, `MSSEvent`, and `MSSSnapshot` retain their own approved provenance
records. MSS direction is immutable vocabulary, not an order side. The actual prior
control context, original confirmed level, opposing CHoCH, matching displacement,
and available relationship objects are linked without changing their IDs.
MSS uses the current consumed prefix and a fully known publication cutoff, never a
later confirmation or revised upstream state. See [MSS methodology](mss-methodology.md).
No scoring, risk, entries, optimization, or reporting engine is added.

## Phase 10 original-OB Breaker formations

`BreakerEvidence` retains the exact original OB and first closing violation,
including rejection reasons when confirmation is absent. `BreakerBlock` exists
only for a previously known source with matching current displacement and MSS;
original and breaker zone boundaries remain identical. Original candidate/OB,
invalidation, displacement/MSS and publication times remain distinct. Existing
sweep, concurrent-FVG and PD references are copied without later enrichment.
The current input prefix, configuration artifacts and canonical provenance factory
are reused. See [Breaker methodology](breaker-block-methodology.md).
No trading state, retests, mitigation, scoring, or performance tracking is added.
