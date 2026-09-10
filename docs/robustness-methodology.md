# Robustness methodology — Phase 21 walk-forward validation

Validation research layer, not a trading system and not an optimizer.

**Stop after Phase 21. Phase 22 requires explicit approval.**

## Question answered

Do the `BUY_SIGNAL` facts that the unchanged pipeline publishes hold up across
sequential historical periods, multiple datasets and timeframes, and
descriptively different market regimes — and how much do they degrade between
each window's development (train) period and its validation (test) period?
Phase 21 answers exactly that question and nothing else. It is walk-forward
**validation**: it measures the existing strategy, never tunes it, never
selects parameters, and never proposes changes.

## Purpose and scope

`analysis/robustness/` is a consumer-only layer over the Phase 20 replay. It
plans chronological windows over declared datasets, replays each window as one
independent Phase 20 backtest, and composes descriptive statistics from the
existing Phase 18 outcome records and Phase 19 performance buckets. No signal
detection, MTF logic, outcome classification, or scoring is duplicated; no
Phase 1–20 module is modified; and no decision module may import this layer
(enforced by import-graph tests). The layer never runs live, never touches an
exchange, and has no third-party runtime dependency.

## Walk-forward methodology

For one dataset with `N` candles, window `k` starts at candle `k * step_bars`
and pairs a **development period** of `development_bars` candles with the
**validation period** of `validation_bars` candles that immediately follows
it. Windows advance by `step_bars`; a trailing stretch that cannot hold one
full window is dropped. The configuration requires `step_bars >=
validation_bars`, so validation periods never overlap; development periods may
overlap when the step is smaller than the development window. Chronological
order is enforced by construction: a window's periods are contiguous ranges of
dataset candle indexes.

Each window is one self-contained Phase 20 `replay_history` over its exact
candle slice (`candles[start:end]`) under the parent dataset identity. No
candle outside a window can influence that window's published facts, so no
leakage between windows is possible. Outcomes stay exactly as Phase 18
published them at the window's end: open outcomes are never flushed. A window
whose validation segment is shorter than the outcome horizon legitimately
reports open outcomes. Datasets shorter than one full window are rejected with
the missing-bar counts; a dataset with no publishable signals is a valid,
fully rendered zero-signal report.

## Cross-period, cross-asset, and cross-timeframe analysis

`run_robustness(datasets, backtest, robustness)` evaluates every dataset once
(sorted by series key for a deterministic report) and composes:

- **overall / by-symbol / by-timeframe / by-combination buckets** — the
  existing Phase 19c `bucket_for` statistics recomputed over **validation rows
  only**, so every out-of-sample signal is counted exactly once (validation
  segments never overlap);
- **by-period listings** — every validation segment positioned in its dataset,
  labeled with the UTC calendar month of its first candle via the existing
  Phase 19 `month_of` helper;
- **by-regime listings** — validation rows grouped by their causal regime
  annotation (below), including an explicit `unclassified` group for warmup
  candles.

Development rows appear only inside per-window results and degradation deltas.
Multiple datasets may differ in symbol or primary timeframe; each dataset
keeps its own MTF configuration through the unchanged Phase 20 replay, so the
existing MTF availability rules are preserved without duplication. Symbols are
never assumed halal: eligibility stays external and configurable through the
existing `[halal_filter]` registry.

## Market-regime classification

The regime layer is a research annotation only. At each closed candle it
computes, with exact `Decimal` arithmetic and only candles at or before that
candle:

1. **efficiency ratio** — `|net close move|` over the `regime_lookback_bars`
   window divided by the summed `|close-to-close|` path length, in `[0, 1]`;
2. **volatility ratio** — the mean absolute close change over the recent
   lookback divided by the mean absolute close change over the baseline window
   (`regime_lookback_bars * regime_baseline_multiple` changes ending at the
   observed candle).

Classification has a fixed precedence: `TRENDING` when the efficiency ratio is
at least `trend_threshold`; otherwise `HIGH_VOLATILITY` when the volatility
ratio is at least `high_volatility_threshold`, `LOW_VOLATILITY` when it is at
most `low_volatility_threshold`, and `RANGING` otherwise. An entirely flat
baseline (zero summed change) makes the volatility ratio mathematically
undefined; the candle then falls through to `RANGING` unless the efficiency
ratio already declared a trend. Before the baseline window fills, and whenever
the metrics are unavailable, the observation carries `None` (warmup) — the
classifier never guesses. The regime label never generates, gates, vetoes, or
modifies a signal, never alters an outcome, and never feeds back into the
pipeline; rows and segments are merely *annotated* with the observation at
their own candle.

## Stability and degradation metrics

All deltas and spreads are exact `Decimal` values from the existing
`difference`/`ratio` helpers — no floats. For each window, **degradation** is
the validation-minus-development delta of the win rate and of the average
final return; windows whose two segments both reach the configurable minimum
finalized count are *comparable* and averaged into the degradation summary.
**Stability** spans validation segments that reach the minimum: win-rate,
average-final-return, average-MFE, and average-MAE spreads (max minus min),
best and worst period labels, counts of positive and negative segments, and a
status label — `STABLE` when every sufficient segment is individually STABLE
and the win-rate spread stays within the configured maximum, `WEAK` otherwise,
`UNDERSAMPLED` when too few segments or windows reach the configured
minimums. A single segment is STABLE when its win rate reaches the configured
floor and its average final return is nonnegative. These are descriptive
labels over published records. **No statistical significance is claimed
anywhere in this layer** — sample sizes are always shown so a reader can
judge them.

## Minimum sample handling

`minimum_finalized_for_stability` and `minimum_windows_for_stability` are
configurable gates. Segments below the finalized minimum are UNDERSAMPLED,
never silently merged or dropped; zero-finalized segments still render with
undefined rates; and the stability/degradation summaries say how many segments
or windows were sufficient. Zero-signal datasets produce a complete report
with empty buckets rather than an error.

## No-lookahead guarantees

- Window planning uses only dataset indexes; a trailing partial window is
  dropped, never wrapped or back-filled.
- Each window replay consumed only its own candle slice; a window's rows,
  summaries, and replay identity equal a direct Phase 20 replay of that slice,
  and evaluating a window alone reproduces its facts inside a multi-window run.
- Truncating or wildly rewriting the declared history after a window's end
  cannot change that window's facts, any report bucket, the degradation or
  stability classification, or (for primary-candle changes) the report id.
- The regime series is prefix-stable: an observation depends only on candles
  at or before its own index; appended futures never change earlier
  observations.
- Higher-timeframe candles follow the unchanged Phase 20 availability rules:
  an HTF candle that closes after a window's end can change that window's
  `replay_id` — Phase 20 identities deliberately digest the whole declared
  history — but never its evaluated facts.

## Exact Decimal arithmetic

Every financial value is an exact `Decimal`: return/MFE/MAE sums are exact
additions, and every rate, average, delta, and spread is the existing
50-significant-digit half-even `ratio`/`difference` helper. Machine summaries
serialize Decimals as exact strings. No float enters any financial value.

## Validation-only role and non-optimization boundary

The frozen configuration artifact declares the role explicitly: optimization,
parameter selection, strategy modification, self-modification, signal
generation, signal veto, live trading, execution, advice, and statistical
significance claims are all `False`. There is nothing to optimize: window
sizes, thresholds, and minimums are configuration for the *measurement*, not
strategy parameters, and no code path selects, tunes, or rewrites anything
based on results. Reports are deterministic functions of their inputs —
repeated runs are byte-identical, and `report_id` digests the frozen
configuration artifacts, dataset identities, window layouts, and replay ids.

## Relationship to Phases 18–20

Phase 21 adds no analytical primitive. Outcomes and their classification are
Phase 18's, verbatim, including open-outcome handling. Attribution labels,
combination keys, bucket statistics, rankings, and month labels are Phase 19's.
The replay, its availability rules, its candle-slice semantics, and the
`replay_id`/`backtest_id` digests are Phase 20's. The robustness layer only
*plans windows, replays them, and reads the records* — the same discipline the
earlier consumer layers follow.

## Explicit non-goals

No live trading, order execution, exchange access, API keys, or network use.
No Telegram. No SELL/SHORT generation. No parameter optimization, walk-forward
*selection*, auto-tuning, or self-modification. No statistical-significance
testing. No indicator-driven signal generation. No performance promise,
forecast, or investment advice. "Halal" remains a design goal, not a Sharia
certification.
