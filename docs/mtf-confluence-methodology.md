# Multi-timeframe confluence methodology — Phase 13

## Scope and inputs

Phase 13 answers one causal question: **which already-published higher-timeframe
evidence was knowable when a lower-timeframe observation became available?**

It is an analysis/evidence layer. It does not resample OHLCV, rerun Phases 1–12,
create trade direction, entries, stops, targets, risk rules, position size,
scores, or performance claims.

`MTFAnalyzer.update` consumes an existing Phase 12 **`OTESnapshot`** as the
primary (LTF) frame. Nested PD, OB, FVG, displacement, liquidity, and structure
objects remain the original instances. Higher-timeframe input is a mapping of
**already-generated** `OTESnapshot` sequences, one sequence per configured HTF.
Those HTF sequences are produced by running the existing pipeline independently
on each timeframe's own candles. MTF-v1 does not aggregate 15m bars into 1h/4h.

MSS, Breaker, and Mitigation are parallel PD consumers and are **not nested**
inside an `OTESnapshot`. They are therefore not joined in mtf-v1. Structure,
liquidity/sweep, displacement, FVG, Order Block, Premium/Discount, and OTE
facts that already exist on an eligible HTF OTE frame are referenced in place.

## 1. Timeframe relationships

Only `SUPPORTED_TIMEFRAMES` with a **fixed UTC duration** may participate.
`1M` is rejected: a calendar month is not a fixed integer-multiple interval.

A higher timeframe must be a **strictly longer integer multiple** of the
primary. Examples: 15m→1h, 15m→4h, 1h→4h, 1d→1w. Rejected examples: 15m→15m,
1h→15m, 3d→1w, 1w→3d, duplicates, or listing the primary among the HTFs.

Configured HTFs stay independent. They are never merged into one synthetic
timeframe. Output relation order follows `higher_timeframes`.

## 2. Availability policy — completed candle

Default and only policy:

```text
htf_evidence.available_at <= primary_candle.opened_at
```

Equality is eligible. The cutoff is the **primary open**, not the primary close.
An incomplete HTF candle's future close is never used.

A 4h candle 08:00–12:00 has `available_at` at 12:00 (or later if arrival is
delayed). A 15m observation that opens at 09:00 cannot use it. The 15m bar that
opens at 12:00 may use it if the 4h frame is actually available at that instant.

HTF frames may be supplied in a full replay buffer. Frames whose
`available_at` is still in the future relative to the current LTF open stay in
the buffer unused. They must not appear on the published LTF snapshot, even as
rejected IDs, because listing a future ID would leak lookahead.

Delayed HTF arrival is respected: if a 1h close at 09:00 is annotated available
at 09:05, the 15m 09:00 open cannot use it; a later LTF open may.

Local candle indices are never compared across timeframes.

## 3. Directional labels — unweighted and independent

For each HTF, mtf-v1 copies the Phase 3 trend on the **latest eligible HTF OTE
snapshot**:

| Latest eligible HTF frame | Relation label |
| --- | --- |
| None | `INSUFFICIENT_CONTEXT` (`no_completed_htf_candle`) |
| Trend not ready | `INSUFFICIENT_CONTEXT` (`htf_structure_not_ready`) |
| Ready bullish | `BULLISH` |
| Ready bearish | `BEARISH` |
| Ready ranging | `NEUTRAL` |

A single HTF relation is never `MIXED`. PD and OTE classifications on that
latest frame are copied as location context, not as a second vote.

Overall snapshot confluence is unweighted agreement among non-insufficient HTF
labels:

- no known HTF label → `INSUFFICIENT_CONTEXT`
- all known labels identical → that label
- any disagreement, including bullish vs ranging → `MIXED`

If 1h is bullish and 4h is still insufficient, the overall label is `BULLISH`
and both relation states remain visible. There is no score, weight, confidence,
or ranking.

## 4. Evidence references

Each relation retains:

- `latest` — the latest eligible HTF `OTESnapshot`, or absent
- publication deltas from every eligible HTF candle (swings, pool updates,
  sweeps, displacement, FVG, OB, PD arrays, newly confirmed dealing range/OTE
  zone)
- current-state references from the latest frame (OTE snapshot/observation/zone,
  PD snapshot/range/equilibrium, structure context)

Original evidence IDs are preserved. Ordering is deterministic: configured HTF
order, then `available_at`, then evidence kind rank, then evidence ID.
Duplicates collapse by ID.

Symbol isolation is mandatory. Venue/provider/dataset_id may differ by
timeframe. Mixing `BTCUSDT` with `ETHUSDT` is an error.

## 5. Immutable models, provenance, and API

```python
from smcsignal.analysis import MTFAnalyzer, MTFConfig, analyze_mtf

mtf = MTFAnalyzer(MTFConfig(), higher={"1h": hour_frames, "4h": four_hour_frames})
for ltf_frame in primary_ote_frames:
    result = mtf.update(ltf_frame)

# Alternative for a fresh/precomputed iterable:
results = analyze_mtf(primary_ote_frames, {"1h": hour_frames, "4h": four_hour_frames})
```

Default configuration:

```toml
[mtf]
enabled = true
primary_timeframe = "15m"
higher_timeframes = ["1h", "4h"]
availability_policy = "completed_candle"
```

`enabled` must be true. Unknown keys, including score, weight, or signal
settings, are rejected.

Records are frozen/slotted. `MTFSnapshot` is published on the **primary**
series, reuses the primary consumed-prefix hash, and depends on the primary OTE
frame plus each eligible latest HTF snapshot. Future HTF rows are not hashed
into past identities. Producer version is 1.

## 6. Streaming, replay, and no-lookahead contract

Primary input must start at index zero and remain consecutive and chronological.
Each HTF sequence has the same local-series contract on its own timeframe.

HTF history is materialized at analyzer construction. Eligibility is evaluated
per LTF open. Failed primary updates do not advance the primary index or HTF
cursors.

For identical fixed-origin primary history, HTF histories, configurations, and
availability annotations:

- Every primary prefix equals the corresponding full-series prefix, including
  labels, relation latest IDs, evidence IDs, and provenance hashes.
- Changing or appending future LTF or still-ineligible HTF candles cannot change
  prior snapshots.
- Batch, streaming, and arbitrary chunks produce identical results.

Tests are software consistency checks, not a performance backtest.

## 7. Hand-computed synthetic example

`config/mtf.example.toml` uses **synthetic** 15m candles from 08:00 through 12:00
UTC on 2024-01-01, plus independent 1h history from 00:00 and one 4h candle
08:00–12:00. Prices are not resampled across timeframes. Fractals are
three-candle windows.

| 15m open | Latest eligible 1h | Latest eligible 4h | 1h label | 4h label | Overall |
| --- | --- | --- | --- | --- | --- |
| 08:00–08:45 | 07:00 1h (not ready) | none | INSUFFICIENT_CONTEXT | INSUFFICIENT_CONTEXT | INSUFFICIENT_CONTEXT |
| 09:00 | 08:00 1h (ready bullish) | none | BULLISH | INSUFFICIENT_CONTEXT | BULLISH |
| 12:00 | 11:00 1h | 08:00 4h (not ready) | BULLISH | INSUFFICIENT_CONTEXT | BULLISH |

The 4h candle is unknown to every 15m bar that opens before 12:00. These
indices are hand-audited test facts, not exchange observations, entries, or
expected returns.

## 8. Known limitations and stop boundary

- Consumes Phase 12 OTE frames per timeframe. Does not join MSS/Breaker/Mitigation.
- Does not resample, align partial HTF candles, or invent 1M multiples.
- Direction is Phase 3 trend on the latest eligible HTF snapshot, not a blend of
  PD/OTE/liquidity votes and not a score.
- Core state holds HTF cursors plus accumulated eligible evidence references;
  retained snapshots and JSON inherit nested upstream history. There is no
  bounded-total-memory or production-throughput guarantee.
- Existing exact arithmetic resource bounds, source-truth and arrival-time
  assumptions, fixed starting history, and caller-owned archives remain in force.

No Setup Quality Score, 75+ threshold, signal generation, BUY/SELL, probability,
profitability claims, entries, stops, targets, risk/reward, position sizing, trade
management, Telegram, halal filter, scraping, live trading, credentials, futures,
leverage, backtesting, monthly statistics, AI optimization, or strategy ranking
is implemented.
**Stop after Phase 13. Phase 14 — Setup Quality Scoring requires explicit approval.**
