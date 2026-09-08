# Signal eligibility methodology — Phase 16

## Scope and inputs

Phase 16 answers one gate question: **given already-published Phase 1–15 facts
on this closed candle, may a future signal engine consider this observation,
and what directional bias do those facts currently support?**

It consumes an existing Phase 15 **`ScoreSnapshot`**. Nested Halal, MTF, OTE,
PD, OB, FVG, displacement, liquidity, and structure objects remain the original
instances. Upstream evidence IDs are never rewritten. Earlier analyzers are
not rerun. Future candles are not read. Conflict policy is frozen as
`neutral`; it is not fitted from historical performance.

`ELIGIBLE` is a quality-and-registry gate, **not** a BUY/SELL signal, entry,
stop, target, rank, probability, or risk parameter. Zero eligible frames is a
valid outcome. Historical decisions never change.

## 1. Hard eligibility gate

| Condition | Result |
| --- | --- |
| Classification is `HALAL` **and** `threshold_passed` | `ELIGIBLE` |
| Classification is `HARAM` | `NOT_ELIGIBLE`, bias `NEUTRAL`, reason `gated_haram` |
| Classification is `UNKNOWN` | `NOT_ELIGIBLE`, bias `NEUTRAL`, reason `gated_unknown` |
| `HALAL` but score below `publish_threshold` | `NOT_ELIGIBLE`, reason `threshold_not_met` |

Non-HALAL assets never become eligible, even when `publish_threshold = 0`.
The integer score is copied from the upstream SQS frame; eligibility does not
recompute weights.

Evidence used in a decision must already have been published at that candle's
availability instant. Later nested facts cannot rewrite an earlier decision.

## 2. Directional bias from nested votes

Each consumed nested fact contributes at most one vote. Missing evidence
**abstains**; it is not a hard failure and does not force a direction.

| Source | LONG_BIAS | SHORT_BIAS | Abstain / mixed |
| --- | --- | --- | --- |
| MTF | `BULLISH` | `BEARISH` | `NEUTRAL`, `INSUFFICIENT_CONTEXT`; `MIXED` is conflict |
| Structure trend | ready `BULLISH` | ready `BEARISH` | unready or `RANGING` |
| Structure event | `BULLISH` BOS/CHoCH | `BEARISH` BOS/CHoCH | none on this candle |
| Order block | current-candle bullish event | current-candle bearish event | no current event |
| OTE | `INSIDE_OTE` bullish zone | `INSIDE_OTE` bearish zone | not inside, or no zone |
| MSS | — | — | always missing on this nested graph |
| Breaker | — | — | always missing on this nested graph |
| Mitigation | — | — | always missing on this nested graph |

MSS, Breaker, and Mitigation remain parallel PD consumers. eligibility-v1 does
not join them. Their votes are recorded as `mss_missing`, `breaker_missing`,
and `mitigation_missing`.

```text
if any vote is MIXED or both LONG_BIAS and SHORT_BIAS appear:
    bias = NEUTRAL   # conflicting_evidence; do not force a side
elif only LONG_BIAS votes:
    bias = LONG_BIAS
elif only SHORT_BIAS votes:
    bias = SHORT_BIAS
else:
    bias = NEUTRAL   # no_directional_evidence
```

Phase 17 maps `LONG_BIAS` / `SHORT_BIAS` onto spot publication statuses. Phase 16
itself does not emit BUY, SELL, entries, or stops.

## 3. Configuration

```toml
[signal_eligibility]
enabled = true
conflict_policy = "neutral"
```

The table must contain **exactly** those two keys. `enabled` must be true.
`conflict_policy` accepts only `neutral`. Direct `SignalEligibilityConfig()`
uses the same defaults. Unknown keys, including signal, entry, buy, sell,
Telegram, and ranking options, are rejected.

## 4. Immutable models, provenance, and API

```python
from smcsignal.analysis import (
    SignalEligibilityAnalyzer,
    SignalEligibilityConfig,
    analyze_signal_eligibility,
)

engine = SignalEligibilityAnalyzer(SignalEligibilityConfig())
for score_frame in score_frames:
    result = engine.update(score_frame)

# Alternative for a fresh/precomputed iterable:
results = analyze_signal_eligibility(score_frames)
```

- `SignalEligibility` — status, bias, copied Halal classification, copied
  integer score facts, and derived `eligible`
- `EligibilityDecision` — unique reason codes and already-available evidence
  references
- `EligibilitySnapshot` — original SQS frame, reused decision, frame provenance

Records are frozen/slotted. The decision reuses the current SQS consumed-prefix
hash, depends on the SQS frame, and includes the current source candle. The
snapshot depends on the SQS frame plus the decision. Producer version is 1.
Changing only the upstream SQS threshold changes eligibility IDs, not Halal or
MTF IDs.

## 5. Streaming, replay, and no-lookahead contract

Input must start at index zero and remain consecutive, chronological, unique,
and nondecreasing in availability. Series identity cannot change mid-stream.
Local state commits only after validation, model construction, and provenance
succeed. Failed input does not alter the last output or advance the index.

Eligibility does not read future candles or prices. Appending or replacing later
SQS frames cannot change prior status, bias, reasons, or identities.

For identical fixed-origin SQS history and configuration:

- Every prefix equals the corresponding full-series prefix, including status,
  bias, decision IDs, and provenance hashes.
- Batch, streaming, and arbitrary chunks produce identical results.

Tests are software consistency checks, not a performance backtest.

## 6. Hand-computed synthetic example

`config/signal-eligibility.example.toml` reuses the Phase 13/14/15 synthetic
15m/1h/4h history. Default allow-list `BTCUSDT` and `publish_threshold = 75`
results:

| Index | Nested facts used | Status | Bias |
| --- | --- | --- | --- |
| 0 | HALAL; score 10; MTF insufficient; no events | `NOT_ELIGIBLE` | `NEUTRAL` |
| 4 | HALAL; score 25; directional MTF long | `NOT_ELIGIBLE` | `LONG_BIAS` |

Lowering only the SQS threshold to 10 makes those same frames `ELIGIBLE`
without changing Halal/MTF identities and without emitting a trade. The same
history classified `UNKNOWN` (`ADAUSDT`) or `HARAM` (deny-listed `XYZUSDT`) is
`NOT_ELIGIBLE` / `NEUTRAL` at every index, including candles whose nested MTF
label is already `BULLISH`.

These are gate decisions against already-published facts, not exchange
observations, entries, or expected returns.

## 7. Known limitations and stop boundary

- eligibility-v1 reads the nested Halal→MTF→OTE graph via the SQS frame.
  Parallel PD consumers are not joined; their votes abstain until a later
  approved join exists.
- Current-candle event tuples are presence checks, not historical inventories
  or zone-lifecycle state.
- Conflict policy is not optimized, machine-learned, or caller-tunable.
- Core state holds the latest snapshot; retained outputs inherit nested
  upstream history. There is no bounded-total-memory guarantee.
- Existing source-truth, arrival-time, fixed-history, and caller-owned archive
  assumptions remain in force.

No BUY/SELL signals, ranking, probability, win-rate, profitability claims,
entries, stops, targets, risk/reward, position sizing, trade management,
Telegram, live trading, credentials, futures, leverage, backtesting, monthly
statistics, AI optimization, portfolio management, or strategy ranking is
implemented in this phase.
**Phase 17 consumes these eligibility decisions for spot publication only.**
