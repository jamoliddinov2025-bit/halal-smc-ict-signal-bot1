# Order Block methodology — Phase 7

## Scope and operational definition

This repository uses a **deterministic operational definition for reproducibility**,
not a claim that this is the only accepted SMC/ICT interpretation. An Order Block
(OB) here is **confirmed formation evidence**, not an actual institutional order,
entry, probability, quality grade, trade, or claim of remaining liquidity.

The default is deliberately stricter than “the last opposite candle before a move”:

- **Bullish OB:** the selected previously known bearish candle precedes an existing
  bullish Phase 5 displacement; that displacement closes strictly **above** the
  selected zone and has a matching bullish **BOS or CHoCH on its own candle**.
- **Bearish OB:** the selected previously known bullish candle precedes an existing
  bearish Phase 5 displacement; that displacement closes strictly **below** the
  selected zone and has a matching bearish **BOS or CHoCH on its own candle**.

Direction is inherited from the actual confirming displacement. A displacement
is always mandatory. Ordinary price movement, a structure break without qualifying
displacement, an opposing displacement, or an absent candidate cannot create an OB.

Phase 7 consumes existing `FVGSnapshot` frames. It does not rerun market-data,
structure, liquidity/sweep, displacement, FVG, Decimal, or provenance engines.

## 1. Candidate facts, selection, and lookback

For displacement candle d, inspect the previous **at most 10 observed candles**
by default, indices `max(0, d-L)..d-1`, where L is `max_candidate_lookback`.
The displacement candle itself is never a candidate.

A candidate's factual eligibility uses only its own already-closed observation:

1. Its exact candle classification is opposite to the displacement direction
   (unless the explicit neutral-doji override below applies).
2. Its configured zone has strictly positive size.
3. Its actual `available_at` is **no later than the displacement candle's open**.

Classification and zone values are inherited from the original Phase 5 metrics/
OHLC observation, not inferred from later returns, extrema, touches, or outcomes.
The later displacement determines which prior eligible candle is selected; this
**does not mean it was knowable as an OB at the old candidate timestamp**.

### Selection policies

- **`nearest` (default):** choose the most recent eligible prior candle.
- **`earliest`:** choose the earliest eligible candle within the same bounded window.

There is **one selected candidate per displacement**, not all candidates and not
a merged cluster. The raw complete search window is retained in an emitted event
so selection is independently reproducible. Consecutive opposite candles do not
become an implicit multi-candle zone. Same-direction candles are ineligible.

Lookback is inclusive: distance `d - candidate_index == L` is allowed; L+1 is not.
It counts observed candles, not wall-clock slots. Existing data gaps are not filled.
If no candidate qualifies, emit no OB.

Selection happens **before** the zone-departure test. If the nearest selected
candidate fails departure, the default does **not** search for a more favorable
older zone. Earlier selection requires the explicit `earliest` setting.

## 2. Candle classification and dojis

Use the existing exact Phase 5 candle direction:

| Own-candle fact | Classification |
| --- | --- |
| `close > open` | Bullish |
| `close < open` | Bearish |
| `close == open` | Doji / neutral |

Dojis are rejected by default (`allow_doji=false`). With `allow_doji=true`, a neutral
doji can be eligible for either OB direction **only if its selected zone is nonzero**.
It remains labeled `doji`, never silently relabeled bullish or bearish. The chosen
nearest/earliest policy applies among all eligible candles, including these dojis.

With a body zone, a doji has zero zone size and is therefore rejected even with the
override. A zero-range point candle is always rejected. Any strictly nonzero body,
however small, remains directional; there is no floating epsilon or unconfigured
“tiny body” judgment.

## 3. Zone definition and strict confirming departure

`zone_basis` explicitly determines the boundaries for either direction:

| Basis | Lower boundary | Upper boundary | Default |
| --- | --- | --- | --- |
| `full_range` | Candidate low | Candidate high | Yes |
| `body` | `min(open, close)` | `max(open, close)` | No |

`zone_size = upper - lower`. The already-computed exact Phase 5 range/body sizes
are reused; no new rounded price calculation or mixed body/wick convention is
introduced. Boundaries remain the original candidate's values forever.

After selection, require a matching displacement's completed close strictly outside
that zone: **above upper for bullish, below lower for bearish**. Equality does not
confirm an OB. This prevents assigning a remote opposite candle whose zone the
confirming close has not passed. No fallback candidate search follows failure.

This is a close-location requirement, not an inferred intrabar crossing path.
A gap open can still qualify if the existing displacement criteria and completed
close satisfy the rules. Gap-only movement cannot substitute for the actual body/
range requirements already enforced by Phase 5. No entry or fill is inferred.

## 4. Displacement and structural confirmation

An OB retains the exact existing `DisplacementEvent`, its evidence reference, and
its original Phase 5 frame. No second displacement detector or changed threshold
is involved. Its direction defines the OB direction.

`structure_requirement` applies **on the displacement candle d only**:

| Setting | Required existing matching-direction event on d |
| --- | --- |
| `bos_or_choch` (default) | BOS or CHoCH |
| `bos` | BOS |
| `choch` | CHoCH |
| `displacement_only` | No structure event required |

Use the actual event in `displacement_frame.liquidity.context.snapshot.events`.
It must match the displacement direction. Earlier unrelated events or later
confirmation-candle breaks are not substituted. In displacement-only mode, an
existing matching event on d is still retained as context if present; an opposing
event is not mislabeled as matching confirmation.

A Phase 3 `StructureEvent` does not have its own standalone provenance ID. The OB
therefore retains the exact event object **and its existing `StructureContext`
reference**, which identifies the immutable containing snapshot. No synthetic BOS/
CHoCH detector or fabricated standalone structure ID is created.

If required structure is absent on d, there is no formation awaiting a future
structure break. Even when FVG confirmation is enabled, a BOS/CHoCH first appearing
on C3 cannot retrospectively rescue the missing structure on d.

## 5. Sweep relationship

Copy `displacement.preceding_sweeps` and its exact reference group **unchanged**.
The original Phase 5 policy already determines which earlier sweep cohort was
known before displacement opened. No sweep is required, and there is no new
sweep lookback, selection, direction filter, or detector in Phase 7.

This can preserve prior sell-side, buy-side, both-side/multiple-pool, or no sweep
context. A sweep on the displacement candle is not retrospectively preceding
context. Future sweeps, including new context available at a delayed FVG publication,
never replace the displacement's original group. No intrabar order is inferred.

## 6. Optional FVG confirmation and bounded delay

### Default: `require_fvg=false`

Publish the OB after the displacement/required same-candle structure evidence is
available at d. `associated_fvg` and `fvg_reference` are **None**. A current FVG
belonging to some earlier displacement is not attached. A matching FVG formed on
d+1 never edits, enriches, or renames the already-published OB.

### Explicit: `require_fvg=true`

All candidate, displacement, departure, and structure requirements must already
hold on d. Preserve that immutable candidate-search history and displacement frame
privately for **one next observed candle only**. This is not a published OB.

At d+1, require an actual Phase 6 FVG that:

- was created on d+1, with C2 at d;
- has the same direction as the displacement/OB;
- references the **exact displacement event ID** that confirmed the candidate;
- is available at the current publication cutoff.

Only then publish an OB at d+1, using the candidate selection fixed from the d-time
history. Lookback remains relative to d, not d+1. C3's candle classification or
later prices cannot become a new candidate. Revalidation uses the same frozen
historical inputs, never a fresh search window at publication.

If the next observed candle provides no matching FVG, the private wait expires
without an OB. There is no longer wait, independent gap detector, end-of-batch
flush, or later backfill. Phase 6's configured minimum/displacement filter is
respected; Phase 7 does not reconstruct gaps that Phase 6 did not emit.

This bounded confirmation choice is not mitigation, invalidation, or lifecycle
tracking of an already-created block. Candidate/zone interactions while waiting
are not interpreted as fills or trading outcomes.

## 7. Candidate time versus confirmation and availability

The immutable event keeps these coordinates separately:

| Field family | Meaning |
| --- | --- |
| `candidate_index`, `candidate_timestamp`, `candidate_available_at` | Original candle's observed index, opening identifier, and actual own-candle availability |
| `displacement_index`, `displacement_timestamp`, `displacement_available_at` | Actual matching displacement candle and its knowable instant |
| `structure_confirmation_*`, `structure_available_at` | Matching structure event on the displacement candle, if present/required |
| `fvg_confirmation_*`, `fvg_available_at` | Required exact next-candle FVG, if that mode is enabled |
| `confirmation_index`, `confirmation_timestamp`, `available_at` | Final publication candle and actual instant after **all required evidence** is known |

Timestamp fields ending in `timestamp` are candle **opening identifiers**, as in
prior models; the corresponding `available_at` fields are true assumed/supplied
knowledge instants. Do not treat any opening identifier as early availability.

Without required FVG, final confirmation index is d. With required FVG, it is d+1.
The candidate index is always strictly earlier than d. Actual source-arrival delays
are preserved. Inputs remain completed chronological frames; the engine reads no
wall-clock “now” and invents no trading-entry timing.

### Abstract example

Suppose the nearest qualifying bearish candidate is candle 18, bullish displacement
and BOS occur at 20, and a matching C2=20 FVG is observed at 21:

- At 18: bearish candle facts exist, **no bullish OB is yet observable**.
- At 20: default mode publishes candidate 18's OB; FVG-required mode publishes none.
- At 21: FVG-required mode publishes an OB for the **same candidate 18**. Default
  mode's earlier OB is unchanged and still has no associated FVG.

If the FVG never appears at 21, FVG-required mode emits nothing, even if unrelated
future evidence later looks favorable. This is a valid analysis outcome.

## 8. Immutable models and provenance

`OrderBlockEvent` and `OrderBlockSnapshot` follow the approved frozen/slotted,
`ProvenancedEvidence`-compatible pattern. An event contains:

- Source symbol/timeframe/price units, OB direction, deterministic event ID.
- Full bounded candidate search window, exact selected candidate frame/OHLC,
  factual candle classification, candidate distance, zone basis/bounds/size.
- Original displacement event/reference and original structural context/event.
- Original sweep group/references and optional exact FVG event/reference.
- Separate candidate, displacement, structure, FVG, and final availability metadata.
- Frozen configuration, `threshold_version=order-block-v1`, and provenance.

Provenance reuses the existing factory/canonical codec and the **publication
frame's consumed-input-prefix hash**. It explicitly references the raw search
frames (including the selected candidate), displacement frame and event, existing
structure context when used, inherited sweeps, optional FVG, and current publication
frame. Source references include the bounded search observations, displacement,
and required C3 when applicable, in chronological unique order.

The current OB frame may reference its emitted event; the event never refers back
to the OB frame, so the dependency graph remains acyclic. The configuration artifact
binds methodology version, all rules, inherited price unit, and existing arithmetic
limit. It becomes available after the first successful input, without guessing
units or creating another upstream pipeline.

`evidence_json` preserves all nested immutable raw facts. Source/configuration/
output archives remain caller-owned. There are no random IDs, whole-file future
hashes, mutable `latest` feature references, probability estimates, or quality values.

## 9. API, state, and no-lookahead guarantees

`OrderBlockAnalyzer(config).update(fvg_frame)` consumes an existing `FVGSnapshot`.
`analyze_order_blocks(frames, config)` runs the same sequential updates. Neither
method creates or reruns any Phase 1–6 detector. Input/output frames retain the
original upstream objects.

Input must start at zero with consecutive unique observed indices, one series/
configuration/unit identity, consistent prior-context links, nonoverlapping candles,
and nondecreasing availability. Missing, duplicate, conflicting, reordered, or
untyped input is rejected, not repaired. Existing upstream validation is preserved.
All checks and evidence/model construction complete before local state changes.
A rejected frame leaves the local history, pending confirmation, and latest output
unchanged; this consumer does not rewind a separately advanced upstream engine.

For the same canonical history, settings, price units, origin, and availability:

- Every prefix equals the corresponding prefix of a longer run, including pending
  silence, observable OBs, original candidate facts, IDs, and provenance hashes.
- Changing/appending candles after publication cannot change an observable OB.
- Batch, individual updates, and arbitrary chunks produce identical outputs.
- In FVG-required mode, future C3 can determine whether an **as-yet-unpublished**
  formation becomes observable, but cannot change any earlier published output.

Tests cover nonempty hand-computed BOS/CHoCH formations, both publication modes,
all-prefix/future-shock equivalence, immutable retained evidence, exact boundaries,
complete dependency resolution, and CSV/mocked-public-Binance integration.
They do not evaluate returns or implement a backtesting engine.

## 10. Configuration and example

```toml
[order_blocks]
max_candidate_lookback = 10
candidate_selection = "nearest"
zone_basis = "full_range"
allow_doji = false
structure_requirement = "bos_or_choch"
require_fvg = false
```

The loader requires exactly these six keys. Lookback is an integer 1–1000;
selection is `nearest`/`earliest`; zone is `full_range`/`body`; structure is
`bos_or_choch`/`bos`/`choch`/`displacement_only`; the two flags are strict booleans.
Direct construction uses the typed enums. There is no `all` candidate mode,
score/threshold/quantity target, lifecycle switch, or alternative confirmation delay.

`config/order-block.example.toml` uses 24 explicitly synthetic candles, compact
three-candle fractals, unchanged default displacement thresholds with ATR(14),
and default OB settings. Expected outcomes:

| Candidate | Direction | Zone | Displacement/structure | Default publication | If FVG required |
| --- | --- | --- | --- | --- | --- |
| 18 | Bullish | [13,15] | Displacement + BOS at 20 | 20 | 21 |
| 21 | Bearish | [19,23] | Displacement + CHoCH at 22 | 22 | 23 |

These are abstract software-verification prices/indices, not real exchange prices,
asset eligibility, entries, remaining liquidity, or trading performance evidence.

## 11. Precision, limitations, and phase boundary

All classification, boundary, and departure comparisons use existing exact Decimal
values. Zone size is the original exact Phase 5 range/body size. There is no binary
float comparison, rounding before qualification, or tiny-body epsilon. Earlier
numeric-resource bounds, source correctness, availability assumptions, and revised/
shifted-history limitations remain applicable.

Limitations: one selected candidate per displacement; fixed same-displacement-bar
structure confirmation; pre-open candidate availability; optional exact one-bar FVG
confirmation, not arbitrary later confirmation; no evaluation of intervening zone
touches. The method does not claim universal SMC/ICT acceptance or optimal parameters.
Identical, overlapping, nested, and reused-candidate zones remain independent
formation records when confirmed by different displacements, with distinct IDs.

The engine retains a bounded prior-frame window and at most one private FVG wait,
but these are rich upstream frames. Output/JSON size and prior-layer pool/history
costs remain significant; no bounded-total-memory or production-throughput guarantee
is claimed. No zone lifecycle, mitigation, breaker conversion, invalidation, entry,
stop loss, take profit, or trade management exists in Phase 7.

Future Setup Quality Score/probability work remains deferred to Phase 11. The
existing future policy stays documentation only: scale 0–100, future configurable
publication threshold default 75, zero signals valid, and no signal quotas.
No scores, probabilities, BUY/SELL signals, Telegram, halal filtering, CryptoIslam
scraping, orders, credentials, live trading, futures, leverage, backtesting,
performance analytics, Breaker/Mitigation Blocks, Premium/Discount, OTE, session
strategy, or multi-timeframe signal engine is introduced.
Phase 8 now adds separate [PD context sidecars](premium-discount-methodology.md)
without changing these earlier formation rules or evidence IDs.
Phase 9 now consumes these unchanged outputs in a separate
[MSS evidence engine](mss-methodology.md).
**Stop after Phase 9. Phase 10 requires explicit approval.**
