# Breaker Block methodology — Phase 10

## Scope and operational definition

This repository implements a **deterministic operational SMC Breaker definition**,
not a claim that it is the only ICT/SMC interpretation. A `BreakerBlock` is immutable
formation evidence for a previously identified OB's opposite-direction structural
zone. It is not a trade, entry, retest, remaining-liquidity claim, probability,
quality grade, or performance prediction.

The engine consumes existing **Phase 9 `MSSSnapshot` frames**. It does not recreate
market data, structure/BOS/CHoCH, pools, sweeps, displacement, FVG, OB, PD, or MSS.
Original records, IDs, candidate prices, and availability times are unchanged.

## 1. Strict `breaker-v1` definition

All conditions below are required on the source OB's **first observed strict
closing violation**:

| Requirement | Bullish Breaker | Bearish Breaker |
| --- | --- | --- |
| Original source | Existing bearish OB | Existing bullish OB |
| Source availability | OB published earlier and known by the violation bar's open | Same |
| Previous close | At or below original upper boundary | At or above original lower boundary |
| Current completed close | **Strictly above** original upper boundary | **Strictly below** original lower boundary |
| Existing current displacement | Bullish | Bearish |
| Existing current MSS | Bullish, referencing that exact displacement | Bearish, referencing that exact displacement |

The existing MSS carries its actual prior-control and CHoCH confirmation. A separate
MSS/BOS/CHoCH/displacement detector is not run. A structure break without the required
displacement/MSS, or a move in the wrong direction, cannot create a Breaker.

This is stronger than “any broken OB becomes a Breaker.” An original OB need not
have been created with this consumer's preferred strategy settings: its actual
Phase 7 settings/evidence are preserved, not redetected under different rules.

## 2. Source OB and first-violation bookkeeping

Register each newly published Phase 7 `OrderBlockEvent` from the existing input
stream by its **exact immutable event ID**, in original publication order. Preserve
its candidate candle, formation confirmation, settings, zone, and provenance.
Newly published OBs are registered **after** prior-OB checks; they cannot be treated
as known before their own publication bar.

The private formation ledger remembers only unassessed source OBs and previously
seen IDs. It is not a live zone manager: there are no public active zones, price
updates to zone geometry, retests, mitigation states, or trading actions.

Each source has one first-closing-violation assessment:

- If all strict conditions hold on that candle, emit `BreakerEvidence` and a
  confirmed `BreakerBlock`.
- If confirmation is missing or ineligible, emit immutable **rejected
  `BreakerEvidence`** with the reason and no `BreakerBlock`.
- In both cases remove that source from future formation consideration. A later
  matching MSS never upgrades that rejected observation or revives the source.
- Staying beyond the boundary or repeatedly crossing it cannot emit another
  Breaker for the same original OB ID.

No lookback expiry or arbitrary source-count cap silently drops older eligible OBs.
Repeated publication of the same/conflicting original OB ID is an input error,
not an overwrite. Distinct OB IDs with identical prices remain distinct sources.

### OB already beyond its far boundary when first published

Phase 7 can defer OB publication for FVG confirmation. If a newly published OB is
already beyond its opposing boundary at that publication close, Phase 10 records
that observed violation as **rejected: source not known at bar open**. It is never
backdated into a pre-existing source and is not later reconsidered. No search of
pre-publication prices is used to invent an earlier actionable invalidation.

## 3. Exact invalidation and gap boundaries

Only the completed **close** is used for invalidation. Wick-only excursions do
not invalidate or consume a source. A close exactly at the far boundary does not
invalidate; a later strictly-through close remains eligible for assessment.
There is no epsilon, tick-size guess, or rounding before comparisons.

Gap-through bars can qualify if the previous close was on the non-violated side,
the completed close is strictly beyond the boundary, and the existing matching
displacement and MSS both qualify. No exact intrabar crossing/touch path is inferred.
A gap alone with inadequate actual body/range does not supply displacement.

The operational choice is **same-first-violation-candle confirmation only**. There
is no multi-bar confirmation wait, retrospective classification, or end-of-batch
flush. First invalidation and formation confirmation therefore share a candle in
confirmed strict-v1 records, although their coordinates and source evidence are
stored separately. Later-confirmation variants would require separate approval.

## 4. Rejected first violations are evidence, not Breakers

`BreakerEvidence` records actual first closing violations, including ones that
failed to form a Breaker. Its `qualified` flag means all stated formation rules
hold, **not** a grade or probability. Only qualified evidence can construct a
`BreakerBlock`.

Rejection precedence is explicit:

1. `source_not_known_at_open`: original OB was not an earlier, pre-bar-known source.
2. `previous_close_already_beyond`: a proper close-through starting position is absent.
3. `missing_displacement`: no existing displacement on this candle.
4. `wrong_displacement_direction`: body/displacement direction does not oppose the OB.
5. `missing_mss`: no existing MSS on this candle.
6. `wrong_mss_direction`: MSS direction does not match the opposing conversion.

The exact MSS/displacement linkage must agree with the current observation. Conflicting
or future evidence is an input/model error, not an inferred confirmation. Some
inconsistent-direction combinations are already impossible under the earlier
strict models; the consumer still validates them defensively.

A rejected evidence record is not an invalidation/lifecycle update of the original
OB. The original OB is never mutated and still describes its historical formation.

## 5. Multiple candidates and zone definition

**All** independently qualifying prior OBs are assessed in original publication
order. There is no nearest-only, strongest, ranked, or quota-based selection.
The nearest eligible source is not excluded, but neither are independent older
sources. Multiple Breakers may be published on one candle.

Overlapping, nested, and identical zones are not merged or cancelled. Different
source OB IDs yield distinct Breaker IDs, even when zones coincide. Newly created
opposing OBs on the same candle do not become pre-bar sources for that candle.

The Breaker zone is **exactly the original OB zone**:

```text
breaker_lower = original_ob.zone_lower_boundary
breaker_upper = original_ob.zone_upper_boundary
breaker_size  = original_ob.zone_size
```

Both original and breaker boundaries are stored explicitly and remain equal.
If Phase 7 created a body-based OB, that exact body zone is preserved; it is not
silently rebuilt from full wicks. No alternate Breaker zone is implemented.

## 6. Confirmation and contextual relationships

### Displacement, MSS, and structure

The same-candle original `DisplacementEvent` and `MSSEvent` are retained with exact
references. The MSS must confirm that exact displacement and match the Breaker
direction. Its original CHoCH and structure context are not replaced or redetected.
The current existing structure event tuple/context reference is retained; Phase 3
structure events have no invented standalone IDs.

### Sweeps

Copy the confirming displacement's original `preceding_sweeps` and reference group.
Phase 5 already enforces its causal pre-open selection. No new sweep detector or
selection window is added. Same-candle/future sweeps are not retroactively preceding
context, and later sweeps never enrich an already-published Breaker.

### FVGs

Copy the current MSS's original `concurrent_fvgs` and references. These are **already
observable co-publication context**, not FVGs attributed to the current displacement's
future C3. Their own C2/association remains unchanged. FVG is not a formation gate.

There is no required-FVG or delayed-FVG mode in strict breaker-v1. Future FVGs never
modify the record. Unknown options attempting to enable such a mode are rejected.

### Premium/Discount

Retain the current original Phase 8 snapshot reference and classification, including
`OUTSIDE_RANGE` or `INSUFFICIENT_CONTEXT`. It is the PD context available at the
Breaker publication cutoff, not a new classification of the old candidate using a
future range. PD is not a qualification gate. Future dealing ranges cannot rewrite
this stored classification.

The original MSS and nested source objects also preserve their full liquidity,
OB, and other existing provenance. No previous object ID is changed.

## 7. Time and publication contract

A confirmed `BreakerBlock` stores separately:

- Original OB ID, candidate index/opening timestamp, OB confirmation index/opening
  timestamp, and original OB actual availability.
- First invalidating observation/index/opening timestamp and its actual availability.
- Existing displacement index/opening timestamp/availability.
- Existing MSS confirmation index/opening timestamp/availability and original
  structure context/events in its evidence.
- Final breaker confirmation index/opening timestamp and `available_at`.

As in prior models, **opening timestamps identify candles**; they do not mean a
close-based result was known at the open. Actual `available_at` values preserve
upstream completion/publication delays, including microseconds. The original OB
must be known by the invalidation candle open; all current mandatory confirmation
must be known by the Breaker's publication instant.

No wall-clock scheduling, entry time, retest time, or intrabar fill is inferred.
A source arriving during a violation candle is not retroactively available before
that candle began.

## 8. Models, provenance, and API

- `BreakerDirection`: immutable bullish/bearish vocabulary, opposite the original OB.
- `BreakerEvidence`: exact original OB, previous/current MSS frames, raw invalidating
  and previous observations, direction, qualified/rejection outcome, and original
  displacement/MSS/structure/sweep/FVG/PD references with its own provenance.
- `BreakerBlock`: qualified evidence, identical original/breaker zones, explicit time
  coordinates, deterministic event ID, configuration/method version and provenance.
- `BreakerSnapshot`: original input frame, previous frame, all first-violation
  evidence deltas, the qualifying Breaker subset, configuration and provenance.

Records are frozen/slotted and satisfy the existing `ProvenancedEvidence` contract.
The direction enum is a label; its meaning and provenance are carried by the records.

```python
from smcsignal.analysis import BreakerBlockAnalyzer, BreakerBlockConfig, analyze_breaker_blocks

breaker = BreakerBlockAnalyzer(BreakerBlockConfig())
for mss_frame in mss_frames:
    result = breaker.update(mss_frame)

# Alternative with a fresh/precomputed input iterable:
results = analyze_breaker_blocks(mss_frames, BreakerBlockConfig())
```

The batch helper is the same update loop, not a separate retroactive pass. The
consumer retains exact upstream objects and uses the existing canonical codec,
provenance factory, and current consumed-prefix hash. No future-file hash or random
ID is introduced. New configuration artifacts bind `breaker-v1`, mandatory rules,
inherited units, all-source publication ordering, and first-violation-only policy.
Producer version is 1; rule/encoding changes must be versioned before reusing IDs.

Evidence references the exact source OB, previous/current observations via existing
frames, and available confirmation/context records. A confirmed block references
its exact qualified evidence; the parent frame references both without cycles.
`evidence_json` retains full nested raw facts and exact Decimal strings. Source,
configuration and evidence archives remain caller responsibilities.

## 9. Causality and failure handling

The engine consumes chronological existing Phase 9 frames starting at observed
index zero. Series, settings, units, prior-context links and availability must
remain consistent. Missing/duplicate/conflicting input is rejected, not repaired.

Before committing local state, an update validates input, evaluates all previously
registered sources, records first violations, registers new OBs, and constructs
all immutable evidence/IDs. A failure leaves the local formation ledger, seen IDs,
latest output and index unchanged. The consumer does not rewind an independently
advanced upstream engine.

For identical historical inputs, settings, source identity, replay origin and
availability annotations:

- Every prefix equals the corresponding longer-run prefix, including rejected
  first violations, confirmed events, IDs and provenance hashes.
- Changing future prices or later FVG/sweep/PD/OB/MSS evidence cannot change an
  already-published Breaker or upgrade a past rejection.
- Batch, streaming and arbitrary chunked replay are identical.

Tests include nonempty bullish/bearish and consecutive/multiple conversions,
wick/equality/one-tick boundaries, exact source-zone preservation, source-born-broken
rejection, missing/false confirmation, full graph/ID checks, and provider integration.
These are software consistency tests, not a backtesting or performance engine.

## 10. Strict defaults and synthetic example

```toml
[breaker_blocks]
require_displacement = true
require_mss = true
invalidation_basis = "close_through_far_boundary"
zone_basis = "original_order_block"
```

Exactly these four keys are required. Both booleans must remain true in strict v1;
false/untyped values are rejected rather than allowing incomplete formations. The
two rule enums accept only the documented values. There are no score/probability,
optimization, signal, retest, lifecycle or alternative-zone parameters.

`config/breaker-block.example.toml` supplies 28 explicitly **synthetic** observations,
compact three-candle fractals, and unchanged default ATR(14)/displacement thresholds:

| Source candidate | OB publication | Source OB | Original/preserved zone | Breaker publication |
| --- | --- | --- | --- | --- |
| 18 | 20 | Bullish | [13,15] | Bearish at 22 |
| 21 | 22 | Bearish | [16,18] | Bullish at 27 |

These indices/prices are hand-audited test facts, not exchange observations,
entries, remaining institutional orders, or expected returns.

## 11. Limitations and stop boundary

The definition is conservative and operational: one first closing violation per
source, same-candle displacement/MSS confirmation, no longer confirmation wait,
and no attempt to recover a rejected source later. It inherits the exact existing
OB and MSS methodologies rather than asserting universal ICT/SMC semantics.

Private formation eligibility/seen-ID bookkeeping grows with unbroken/history OB
counts; there is no silent expiry/cap. Rich nested evidence and retained JSON inherit
earlier history/storage costs. This is not a live zone manager and carries no
bounded-total-memory or production-throughput guarantee. Source truth, vendor
revisions, availability annotations and shifted-history limitations remain in force.

No entries, retests, mitigation, stops, targets, risk, position sizing, trade
management, performance tracking, signals, scoring, probabilities, Telegram,
halal filtering, scraping, credentials, live trading, futures, leverage, backtesting,
monthly statistics, OTE, new multi-timeframe processing, AI optimization, or strategy
ranking is introduced. Future quality policy remains documentation only: scale
0–100, default publication threshold 75, zero signals valid, and no quotas.
Phase 11 now consumes these unchanged outputs for
[Mitigation first interaction](mitigation-block-methodology.md).
**Stop after Phase 11. Phase 12 — OTE requires explicit approval.**
