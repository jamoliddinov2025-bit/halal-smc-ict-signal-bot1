# Setup quality score methodology — Phase 15

## Scope and inputs

Phase 15 answers one quality question: **given already-published Phase 1–14
facts on this closed candle, what integer 0–100 score does sqs-v1 assign, and
does that total meet the configured publish threshold?**

It consumes an existing Phase 14 **`HalalSnapshot`**. Nested MTF, OTE, PD, OB,
FVG, displacement, liquidity, and structure objects remain the original
instances. Upstream evidence IDs are never rewritten. Earlier analyzers are
not rerun. Future candles are not read. Component weights are frozen
methodology constants; they are not fitted from historical performance and
are not TOML-tunable.

`threshold_passed` is a quality flag, **not** a BUY/SELL signal, rank, entry,
stop, target, or risk parameter. Zero passing scores is a valid outcome.

## 1. Hard eligibility gate

| Classification | Score |
| --- | --- |
| `HALAL` | May receive a positive total from nested facts. |
| `HARAM` | Total **0**; every component is 0. |
| `UNKNOWN` | Total **0**; every component is 0. `UNKNOWN` is never treated as `HALAL`. |

Ineligible assets never set `threshold_passed`, even when
`publish_threshold = 0`.

## 2. Frozen integer weights (sum 100)

| Component | Weight | Source on the consumed frame |
| --- | --- | --- |
| Halal | 10 | `HalalSnapshot.classification` |
| MTF | 15 | nested `MTFSnapshot.direction` |
| MSS | 10 | not nested on this graph; always missing in sqs-v1 |
| Displacement | 12 | current-candle `DisplacementSnapshot.events` |
| Liquidity sweep | 10 | current-candle `LiquiditySnapshot.sweeps` |
| FVG | 8 | current-candle `FVGSnapshot.events` |
| Order block | 10 | current-candle `OrderBlockSnapshot.events` |
| Breaker | 5 | not nested on this graph; always missing in sqs-v1 |
| Mitigation | 5 | not nested on this graph; always missing in sqs-v1 |
| Premium/Discount | 8 | nested `PDSnapshot.classification` |
| OTE | 7 | nested `OTESnapshot.classification` |

MSS, Breaker, and Mitigation remain parallel PD consumers. sqs-v1 does not
join them. Missing evidence contributes **0 points**, never a hard failure.
The nested-only maximum is therefore 80. A default threshold of 75 still
requires substantial same-candle confluence among the nested facts.

Partial awards are integers, not probabilities:

| Label | Points |
| --- | --- |
| MTF `BULLISH` / `BEARISH` | 15 |
| MTF `NEUTRAL` | 8 |
| MTF `MIXED` | 5 |
| MTF `INSUFFICIENT_CONTEXT` | 0 |
| PD `DISCOUNT` | 8 |
| PD `EQUILIBRIUM` | 5 |
| PD `PREMIUM` | 3 |
| PD `OUTSIDE_RANGE` | 1 |
| PD `INSUFFICIENT_CONTEXT` | 0 |
| OTE `INSIDE_OTE` | 7 |
| OTE `BELOW_OTE` / `ABOVE_OTE` | 3 |
| OTE `INSUFFICIENT_CONTEXT` | 0 |

PD discount is scored above premium only as location context for this
repository's declared long-only scope. It is not an entry.

Current-candle displacement, sweep, FVG, and order-block publications award
their full weight when present and 0 when absent.

## 3. Threshold

```toml
[setup_quality]
publish_threshold = 75
```

The table must contain **exactly** `publish_threshold`: an integer from 0 to
100. Direct `SetupQualityConfig()` defaults to 75. Unknown keys, including
weights, scores, signals, probabilities, and ranks, are rejected.

```text
threshold_passed = eligible AND total >= publish_threshold
```

A total of 75 meets a threshold of 75. Totals below the threshold fail. The
flag does not publish trades.

## 4. Immutable models, provenance, and API

```python
from smcsignal.analysis import SetupQualityAnalyzer, SetupQualityConfig, analyze_setup_quality

engine = SetupQualityAnalyzer(SetupQualityConfig())
for halal_frame in halal_frames:
    result = engine.update(halal_frame)

# Alternative for a fresh/precomputed iterable:
results = analyze_setup_quality(halal_frames)
```

- `ScoreBreakdown` — integer points and one reason per component; `total` is
  their sum in 0–100
- `SetupQualityScore` — breakdown, eligibility, total, and `threshold_passed`
- `ScoreSnapshot` — original Halal frame, reused score, frame provenance

Records are frozen/slotted. The score reuses the current Halal consumed-prefix
hash, depends on the Halal frame, and includes the current source candle.
The snapshot depends on the Halal frame plus the score. Producer version is 1.
Changing only the threshold changes SQS IDs, not upstream Halal/MTF IDs.

## 5. Streaming, replay, and no-lookahead contract

Input must start at index zero and remain consecutive, chronological, unique,
and nondecreasing in availability. Series identity cannot change mid-stream.
Local state commits only after validation, model construction, and provenance
succeed. Failed input does not alter the last output or advance the index.

Scoring does not read future candles or prices. Appending or replacing later
Halal frames cannot change prior totals, flags, or identities.

For identical fixed-origin Halal history and configuration:

- Every prefix equals the corresponding full-series prefix, including totals,
  flags, score IDs, and provenance hashes.
- Batch, streaming, and arbitrary chunks produce identical results.

Tests are software consistency checks, not a performance backtest.

## 6. Hand-computed synthetic example

`config/setup-quality.example.toml` reuses the Phase 13/14 synthetic 15m/1h/4h
history. Default allow-list `BTCUSDT` results:

| Index | Nested facts used | Total | `threshold_passed` at 75 |
| --- | --- | --- | --- |
| 0 | HALAL only; MTF/PD/OTE insufficient; no events | 10 | false |
| 4 | HALAL 10 + directional MTF 15 | 25 | false |

The same history classified `UNKNOWN` (`ADAUSDT`) or `HARAM` (deny-listed
`XYZUSDT`) scores 0 at every index, including candles whose nested MTF label
is already `BULLISH`. These are integer awards against already-published
facts, not exchange observations, entries, or expected returns.

## 7. Known limitations and stop boundary

- sqs-v1 reads the nested Halal→MTF→OTE graph. Parallel PD consumers are not
  joined; their weights stay at 0 until a later approved join exists.
- Current-candle event tuples are presence checks, not historical inventories
  or zone-lifecycle state.
- Weights are not optimized, machine-learned, or caller-tunable.
- Core state holds the latest snapshot; retained outputs inherit nested
  upstream history. There is no bounded-total-memory guarantee.
- Existing source-truth, arrival-time, fixed-history, and caller-owned archive
  assumptions remain in force.

No signal generation, BUY/SELL, ranking, probability, win-rate, profitability
claims, entries, stops, targets, risk/reward, position sizing, trade
management, Telegram, live trading, credentials, futures, leverage,
backtesting, monthly statistics, AI optimization, portfolio management, or
strategy ranking is implemented.
**Stop after Phase 15. Phase 16 — Signal Engine requires explicit approval.**
