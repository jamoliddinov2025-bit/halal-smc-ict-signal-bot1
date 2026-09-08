# Signal engine methodology — Phase 17

## Purpose and scope

Phase 17 answers one publication question: **given already-published Phase 16
eligibility on this closed candle, may a spot signal be published, and if so
what kind?**

It consumes an existing Phase 16 **`EligibilitySnapshot`**. Nested Halal, SQS,
MTF, OTE, PD, OB, FVG, displacement, liquidity, and structure objects remain the
original instances. Upstream evidence IDs are never rewritten. Earlier analyzers
are not rerun. Future candles are not read. Direction is copied from Phase 16
bias; this engine does not invent a second directional algorithm.

SQS is an integer **quality score**, not a probability, confidence, expected
value, or win-rate prediction. Crossing `publish_threshold` is a quality gate,
not a forecast.

`BUY_SIGNAL` is a spot publication record, **not** an order, entry, stop, target,
size, or Telegram message. Zero published buys is a valid outcome. Historical
publications never change.

## Spot-only behavior

| Phase 16 facts | Phase 17 status | Direction |
| --- | --- | --- |
| HALAL + ELIGIBLE + `LONG_BIAS` + threshold passed + evidence present + new setup | `BUY_SIGNAL` | `LONG` |
| HALAL + ELIGIBLE + `SHORT_BIAS` + threshold passed | `BEARISH_AVOID` | `NONE` |
| HALAL + ELIGIBLE + `NEUTRAL` | `NO_SIGNAL` | `NONE` |
| HARAM or UNKNOWN | `NO_SIGNAL` | `NONE` |
| not ELIGIBLE or threshold failed | `NO_SIGNAL` | `NONE` |
| would-be BUY but required evidence missing | `NO_SIGNAL` | `NONE` |
| would-be BUY but setup already published | `NO_SIGNAL` | `NONE` |

There is no `SELL` status and no `SHORT` direction. Eligible bearish bias is
`BEARISH_AVOID`: a warning to stay out, never a short trade.

## Eligibility gates

A `BUY_SIGNAL` requires **all** of:

1. Asset classification `HALAL`
2. Upstream SQS `threshold_passed`
3. Phase 16 status `ELIGIBLE`
4. Phase 16 bias `LONG_BIAS`
5. Causally available evidence references
6. Setup identity not already used for a `BUY_SIGNAL` in this replay

`publish_threshold` on `[signal_engine]` must equal the consumed SQS threshold.
The engine does not recompute SQS.

## Configuration

```toml
[signal_engine]
enabled = true
publish_threshold = 75
spot_only = true
duplicate_policy = "one_per_setup"
```

The table must contain **exactly** those four keys. `enabled` and `spot_only`
must be true. `duplicate_policy` accepts only `one_per_setup`. Direct
`SignalEngineConfig()` uses the same defaults. Unknown keys, including entry,
stop, target, Telegram, and ranking options, are rejected.

## Setup identity and one-per-setup

Identity is a content hash of facts that actually exist on the consumed frame:

- symbol and primary timeframe
- Phase 16 bias (the directional component)
- MTF HTF evidence IDs, if present
- OTE `zone_id`, if a zone exists
- current-candle OB / FVG / displacement event IDs, if present

Missing optional features are **omitted**, never invented. Per-candle snapshot
IDs are excluded so repeats of the same structural setup can collapse. MSS,
Breaker, and Mitigation IDs are omitted until a nested join exists on the
consumed graph.

Under `one_per_setup`, the streaming analyzer publishes at most one `BUY_SIGNAL`
per identity. Later candles with the same identity become `NO_SIGNAL` with
reason `duplicate_setup`. Different identities remain independent.
`build_signal_snapshot` without `seen_setups` is the one-frame mapping and does
not collapse duplicates.

## Deterministic IDs, provenance, and API

```python
from smcsignal.analysis import (
    SignalEngineAnalyzer,
    SignalEngineConfig,
    analyze_signal_engine,
)

engine = SignalEngineAnalyzer(SignalEngineConfig())
for eligibility_frame in eligibility_frames:
    result = engine.update(eligibility_frame)

results = analyze_signal_engine(eligibility_frames)
```

- `Signal` — status, direction, copied Halal/eligibility/SQS facts, setup
  identity, primary candle, and publication times
- `SignalCandidate` — unique reason codes, already-available evidence
  references, and `signal_id`
- `SignalSnapshot` — original eligibility frame, reused candidate, frame
  provenance

Records are frozen/slotted. Candidate and snapshot reuse the current eligibility
consumed-prefix hash, depend on that frame, and include the current source
candle. Producer version is 1. Changing only the SQS threshold changes signal
IDs, not Halal or eligibility object identities of unchanged upstream frames.

## Causal / no-lookahead rules

Input must start at index zero and remain consecutive, chronological, unique,
and nondecreasing in availability. Series identity cannot change mid-stream.
Local state, including published setup identities, commits only after
validation, model construction, and provenance succeed. Failed input does not
alter the last output, published-set, or index.

The engine does not read future candles or prices. Evidence references must
satisfy `available_at <= published_at`. Publication is not before the primary
candle close. Appending or replacing later eligibility frames cannot change
prior status, identity, reasons, or IDs.

For identical fixed-origin eligibility history and configuration:

- Every prefix equals the corresponding full-series prefix, including status,
  setup identity, signal IDs, and provenance hashes.
- Batch, streaming, and arbitrary chunks produce identical published sets.

Tests are software consistency checks, not a performance backtest.

## Explainability reason codes

| Reason | When it is used |
| --- | --- |
| `halal_asset` | BUY or BEARISH_AVOID, asset is HALAL |
| `sqs_threshold_passed` | BUY or BEARISH_AVOID, threshold flag is true |
| `long_bias` | BUY, Phase 16 bias is LONG_BIAS |
| `mtf_alignment` | BUY, nested MTF direction is already BULLISH |
| `bullish_mss` | BUY, only if a nested bullish MSS fact is present (not on this graph) |
| `bullish_displacement` | BUY, a current-candle bullish displacement event exists |
| `discount_context` | BUY, nested PD classification is DISCOUNT |
| `ote_context` | BUY, nested OTE classification is INSIDE_OTE |
| `not_halal` | HARAM or UNKNOWN |
| `sqs_below_threshold` | HALAL but SQS flag is false |
| `neutral_bias` | eligible HALAL with NEUTRAL bias |
| `bearish_spot_avoid` | eligible HALAL SHORT_BIAS; never a short |
| `missing_required_evidence` | mapping would be BUY but no evidence references remain |
| `duplicate_setup` | mapping would be BUY but this setup already published |

Reasons never claim missing confluence. Optional BUY reasons are appended only
when the nested fact is already present.

## Hand-computed synthetic example

`config/signal-engine.example.toml` reuses the Phase 13–16 synthetic 15m/1h/4h
history. Default allow-list `BTCUSDT` and `publish_threshold = 75`:

| Index | Nested facts used | Status |
| --- | --- | --- |
| 0 | HALAL; score 10; NEUTRAL | `NO_SIGNAL` (`sqs_below_threshold`) |
| 4 | HALAL; score 25; LONG_BIAS | `NO_SIGNAL` (`sqs_below_threshold`) |

Lowering only the SQS threshold to 10, with a matching engine threshold, makes
index 4 `BUY_SIGNAL` (`LONG`). Later candles that share that setup identity are
`NO_SIGNAL` / `duplicate_setup`. HARAM (`XYZUSDT` deny list) and UNKNOWN
(`ADAUSDT`) stay `NO_SIGNAL` / `not_halal` at every index.

These are publication records against already-published facts, not exchange
orders, entries, or expected returns.

## Known limitations and stop boundary

- signal-engine-v1 reads the nested Halal→MTF→OTE graph via the eligibility
  frame. Parallel MSS/Breaker/Mitigation consumers are not joined; those IDs
  are omitted from setup identity until a later approved join exists.
- Current-candle event tuples are presence checks, not historical inventories
  or zone-lifecycle state.
- Duplicate collapsing applies to `BUY_SIGNAL` only. `BEARISH_AVOID` is not
  collapsed.
- Core state holds the latest snapshot and published BUY identities; retained
  outputs inherit nested upstream history. There is no bounded-total-memory
  guarantee.
- Existing source-truth, arrival-time, fixed-history, and caller-owned archive
  assumptions remain in force.

No live trading, futures, leverage, short selling, stop loss, take profit,
entry price, position sizing, portfolio management, order execution, exchange
trading APIs, Telegram, chart rendering, backtesting, analytics, ML,
optimization, or self-improvement is implemented.
**Stop after Phase 17. Phase 18 requires explicit approval.**
