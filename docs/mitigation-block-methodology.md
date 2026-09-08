# Mitigation Block methodology — Phase 11

## Scope and operational definition

This repository implements a **deterministic operational SMC Mitigation definition**,
not a claim that it is the only ICT/SMC interpretation. A `MitigationBlock` is
immutable first-interaction evidence that a previously identified Order Block's
original zone was later overlapped by a completed candle. It is not a trade, entry,
retest fill, remaining-liquidity claim, probability, quality grade, or performance
prediction.

The engine consumes existing **Phase 10 `BreakerSnapshot` frames**. It does not
recreate market data, structure/BOS/CHoCH, pools, sweeps, displacement, FVG, OB,
PD, MSS, or Breakers. Original records, IDs, candidate prices, and availability
times are unchanged.

## 1. Strict `mitigation-v1` definition

All conditions below are required on the source OB's **first valid post-publication
interior overlap**:

| Requirement | Bullish Mitigation | Bearish Mitigation |
| --- | --- | --- |
| Original source | Existing bullish OB | Existing bearish OB |
| Source availability | OB published earlier and known by the interaction bar's open | Same |
| Interaction candle | Completed; never an unclosed row | Same |
| Geometry | Positive interior overlap with the original OB zone | Same |
| Breaker policy | No confirmed Breaker for that OB has been published yet | Same |
| Emission policy | First qualifying interaction only | Same |

Direction follows the **source OB**, not an opposing conversion:

```text
bullish OB → bullish mitigation
bearish OB → bearish mitigation
```

This is stronger than “any later candle near an OB is a mitigation.” The original
OB need not have been created with this consumer's preferred strategy settings:
its actual Phase 7 settings/evidence are preserved, not redetected under different
rules.

## 2. Exact interaction geometry

Default `interaction_basis = "range_intersection"` uses **Decimal** comparisons
only. Let the original OB zone be the closed interval `[L, U]` and the completed
candle range be `[low, high]`. A valid interaction requires the candle range to
intersect the **open** interval `(L, U)`:

```text
high > L  and  low < U
```

Therefore:

| Candle vs zone | Result |
| --- | --- |
| Completely outside (`high < L` or `low > U`) | No mitigation |
| Exact endpoint touch with zero interior overlap (`high == L` or `low == U`) | No mitigation |
| Positive wick overlap into `(L, U)` | Valid |
| Body overlap with `(L, U)` | Valid |
| Full traversal (`low < L` and `high > U`) | Valid |
| Opening and/or closing strictly inside `(L, U)` | Valid |
| Doji strictly inside `(L, U)` | Valid (overlap size may be zero) |

There is no epsilon, tick-size guess, or rounding before comparisons. Ambient
Decimal precision/rounding cannot change the classification. Overlap bounds are
stored as:

```text
overlap_lower = max(low, L)
overlap_upper = min(high, U)
overlap_size  = max(0, overlap_upper - overlap_lower)
```

Body overlap uses `[min(open, close), max(open, close)]` with the same interior
test. Full traversal, open-inside, and close-inside flags are descriptive geometry,
not extra gates.

Wick-only overlap is valid. A close through the far OB boundary is **not** itself
the mitigation definition; if that same candle also confirms a Breaker, the
`ignore_after_breaker` rule below applies.

## 3. Source OB bookkeeping and first interaction only

Register each newly published Phase 7 `OrderBlockEvent` from the existing input
stream by its **exact immutable event ID**, in original publication order. Preserve
its candidate candle, formation confirmation, settings, zone, and provenance.
Newly published OBs are registered **after** prior-OB checks; they cannot mitigate
on their own publication bar.

The private ledger remembers only unmitigated source OBs, previously seen IDs, and
IDs that have already become confirmed Breakers. It is not a live zone manager:
there are no public active zones, remaining-size updates, entries, or trading
actions.

Each source has at most one first-interaction emission in strict v1:

- If the completed candle has interior overlap, the OB was known by that bar's
  open, and Breaker policy allows it, emit `MitigationEvidence` and a confirmed
  `MitigationBlock`, then remove that source from future first-interaction
  consideration.
- Later candles remaining inside or re-entering the zone cannot emit another
  mitigation for the same original OB ID.
- Completely outside or exact endpoint-touch candles do **not** consume the first
  opportunity.

No lookback expiry or arbitrary source-count cap silently drops older eligible OBs.
Repeated publication of the same/conflicting original OB ID is an input error,
not an overwrite. Distinct OB IDs with identical prices remain distinct sources.

### Pre-publication interaction

Phase 7 can defer OB publication for FVG confirmation. Overlaps that occur before
the OB is known at the interaction bar open are not mitigations and are not
backdated. A later post-publication overlap remains eligible. No search of
pre-publication prices is used to invent an earlier actionable interaction.

## 4. Breaker relationship

Confirmed Phase 10 `BreakerBlock` records are consumed, not recomputed.

When `ignore_after_breaker=true` (mandatory in strict v1):

1. Newly confirmed Breakers on the current candle retire their source OB from
   first-mitigation eligibility **before** overlap checks.
2. A later return into a converted zone cannot create a first mitigation.
3. A mitigation already published on an earlier candle remains **immutable**.
   A later Breaker of the same original OB does not rewrite, delete, or upgrade
   that record.

Rejected first-violation Breaker evidence is **not** a Breaker. Those OBs remain
eligible for a later true interior-overlap mitigation unless they later convert.

Same-candle full traversal that also confirms a Breaker is treated as conversion,
not as a first mitigation, because the Breaker is already published on that close.

## 5. Multiple candidates and zone definition

**All** independently qualifying prior OBs are assessed in original publication
order. There is no nearest-only, strongest, ranked, or quota-based selection.
Overlapping, nested, and identical zones are not merged or cancelled. Different
source OB IDs yield distinct Mitigation IDs, even when zones coincide.

The mitigation zone is **exactly the original OB zone**. Both original boundaries
and the interaction overlap are stored explicitly. If Phase 7 created a body-based
OB, that exact body zone is preserved; it is not silently rebuilt from full wicks.

## 6. Time and publication contract

A confirmed `MitigationBlock` stores separately:

1. Source OB candidate index/opening timestamp and candidate availability.
2. Source OB confirmation index/opening timestamp and original OB availability.
3. Interaction candle open (`interaction_timestamp`).
4. Interaction candle close (`interaction_closed_at`) and interaction availability.
5. Mitigation publication time (`available_at`).

As in prior models, **opening timestamps identify candles**; they do not mean a
close-based result was known at the open. Actual `available_at` values preserve
upstream completion/publication delays, including microseconds. The original OB
must be known by the interaction candle open; the mitigation cannot be published
before that interaction candle is complete.

No wall-clock scheduling, entry time, retest fill, or intrabar path is inferred.
A source arriving during an overlapping candle is not retroactively available
before that candle began.

## 7. Models, provenance, and API

- `MitigationDirection`: immutable bullish/bearish vocabulary, matching the source OB.
- `MitigationEvidence`: exact original OB, previous/current Breaker frames, raw
  interaction and previous observations, direction, original zone, overlap/body
  geometry flags, and its own provenance.
- `MitigationBlock`: qualified evidence, original zone, overlap span, explicit
  time coordinates, deterministic event ID, configuration/method version and
  provenance.
- `MitigationSnapshot`: original input frame, previous frame, all first-interaction
  evidence deltas, matching Mitigation events, configuration and provenance.

Records are frozen/slotted and satisfy the existing `ProvenancedEvidence` contract.
The direction enum is a label; its meaning and provenance are carried by the records.

```python
from smcsignal.analysis import (
    MitigationBlockAnalyzer,
    MitigationBlockConfig,
    analyze_mitigation_blocks,
)

mitigation = MitigationBlockAnalyzer(MitigationBlockConfig())
for breaker_frame in breaker_frames:
    result = mitigation.update(breaker_frame)

# Alternative with a fresh/precomputed input iterable:
results = analyze_mitigation_blocks(breaker_frames, MitigationBlockConfig())
```

The batch helper is the same update loop, not a separate retroactive pass. The
consumer retains exact upstream objects and uses the existing canonical codec,
provenance factory, and current consumed-prefix hash. No future-file hash or random
ID is introduced. New configuration artifacts bind `mitigation-v1`, mandatory
rules, inherited units, all-source publication ordering, and first-interaction-only
policy. Producer version is 1; rule/encoding changes must be versioned before
reusing IDs.

Evidence references the exact source OB and previous/current Breaker frames.
A confirmed block references its exact qualified evidence; the parent frame
references both without cycles. `evidence_json` retains full nested raw facts and
exact Decimal strings. Source, configuration and evidence archives remain caller
responsibilities.

## 8. Causality and failure handling

The engine consumes chronological existing Phase 10 frames starting at observed
index zero. Series, settings, units, prior-context links and availability must
remain consistent. Missing/duplicate/conflicting input is rejected, not repaired.

Before committing local state, an update validates input, retires newly converted
Breaker sources, evaluates all previously registered sources, registers new OBs,
and constructs all immutable evidence/IDs. A failure leaves the local interaction
ledger, seen IDs, latest output and index unchanged. The consumer does not rewind
an independently advanced upstream engine.

For identical historical inputs, settings, source identity, replay origin and
availability annotations:

- Every prefix equals the corresponding longer-run prefix, including events, IDs
  and provenance hashes.
- Changing future prices or later FVG/sweep/PD/OB/MSS/Breaker evidence cannot
  change an already-published mitigation.
- Batch, streaming and arbitrary chunked replay are identical.

Tests include nonempty bullish/bearish mitigations, first vs repeated interactions,
pre-publication exclusion, exact boundary/wick/body/traversal/inside geometry,
multiple/identical/nested sources, mitigation-before-Breaker immutability,
post-Breaker rejection, full graph/ID checks, and provider integration.
These are software consistency tests, not a backtesting or performance engine.

## 9. Strict defaults and synthetic example

```toml
[mitigation_blocks]
interaction_basis = "range_intersection"
first_interaction_only = true
ignore_after_breaker = true
```

Exactly these three keys are required. Both booleans must remain true in strict v1;
false/untyped values are rejected rather than allowing repeated events or
post-Breaker first mitigations. The interaction enum accepts only
`range_intersection`. There are no score/probability, optimization, signal,
retest, lifecycle or alternative-geometry parameters.

`config/mitigation-block.example.toml` supplies 25 explicitly **synthetic**
observations, compact three-candle fractals, and unchanged default ATR(14)
displacement thresholds:

| Source candidate | OB publication | Source OB | Original zone | Mitigation |
| --- | --- | --- | --- | --- |
| 18 | 20 | Bullish | [13,15] | 21 |
| 22 | 23 | Bearish | [16,23] | 24 |

The bullish source later confirms a bearish Breaker at 23. That conversion does
not rewrite the already-published mitigation at 21. These indices/prices are
hand-audited test facts, not exchange observations, entries, remaining
institutional orders, or expected returns.

## 10. Limitations and stop boundary

The definition is conservative and operational: one first interior overlap per
source, no multi-bar confirmation wait, no remaining-size tracking, and no attempt
to recover a post-Breaker first mitigation. It inherits the exact existing OB and
Breaker methodologies rather than asserting universal ICT/SMC semantics.

Private eligibility/seen-ID bookkeeping grows with unbroken/history OB counts;
there is no silent expiry/cap. Rich nested evidence and retained JSON inherit
earlier history/storage costs. This is not a live zone manager and carries no
bounded-total-memory or production-throughput guarantee. Source truth, vendor
revisions, availability annotations and shifted-history limitations remain in force.

No entries, stops, targets, risk, position sizing, trade management, performance
tracking, signals, scoring, probabilities, Telegram, halal filtering, scraping,
credentials, live trading, futures, leverage, backtesting, monthly statistics,
OTE, new multi-timeframe processing, AI optimization, or strategy ranking is
introduced. Future quality policy remains documentation only: scale 0–100, default
publication threshold 75, zero signals valid, and no quotas.
**Stop after Phase 11. Phase 12 — OTE requires explicit approval.**
