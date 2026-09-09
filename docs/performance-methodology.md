# Performance methodology — Phase 19c

## Purpose and scope

Phase 19c answers one reporting question: **how did published signals
distribute across reporting groups?** It consumes finished Phase 18
`OutcomeSnapshot` replays and Phase 19b `AttributionSnapshot` replays and
recomputes exact descriptive statistics per group. It is **descriptive
analytics of software publication records** — not a backtest, not a trading
performance claim, not an expected-return forecast, and not advice.

Statuses are **copied, never reclassified**: the layer imports the approved
Phase 18 return helpers (`aggregate`, `mfe_return`, `mae_return`,
`final_return`) and never calls `classify_outcome`.

## Reporting groups

| Group | Buckets |
| --- | --- |
| `overall` | one `all` bucket |
| `symbol` / `timeframe` | one per distinct series identity |
| `month` | UTC calendar month (`YYYY-MM`) of the signal candle open |
| `label` | one per `SetupLabel`, in canonical taxonomy order — attributed signals only |
| `combination` | one per `combination_key` — attributed signals only |

Each `PerformanceBucket` carries exact counts (`total`, `open`, `win`, `loss`,
`flat`, `finalized`), exact Decimal return sums, and descriptive rates that
are `None` **iff** `finalized == 0` (undefined is never zero). Open and
finalized outcomes stay strictly separated: open records contribute only to
the open count and never touch sums or rates.

## Rankings and sample sizes

`best_combination` / `worst_combination` quote the highest and lowest
`average_final_return` among **sufficient** combination buckets — those with
at least `minimum_finalized_for_ranking` finalized outcomes (default 10,
range 1–1000). Ties keep the first bucket in deterministic sort order.
Insufficient groups are never ranked, but their raw counts are always
visible; nothing is hidden behind the minimum.

## Multi-series input

Input is a caller-keyed mapping of finished replays (`{series_key:
outcome_frames}` plus an optional aligned attribution mapping). Keys are
sorted deterministically; unsorted input produces sorted output. Validation
requires: nonempty tuples, frames from index zero consecutive, one outcome
and one attribution configuration across series, one series identity per
replay, alignment between the outcome and attribution frames of a key, and
globally unique signal IDs (a signal belongs to exactly one series).

## Deterministic identity

`report_id = "performance-report:" + sha256({methodology, settings,
outcome_settings, attribution_settings, series_payload})` where the payload
carries every signal's `signal_id` plus its outcome and attribution evidence
IDs. Identical inputs yield identical reports; the report is a pure function
of consumed records and never mutates them.

## Configuration

```toml
[performance]
enabled = true
minimum_finalized_for_ranking = 10
```

The table must contain exactly those two keys. Unknown keys — including
`rank_by`, `advice`, `optimization`, or any decision knob — are rejected.

## Causal / no-look-ahead rules

The layer reads **only published records**: latest versions come from the
frames actually supplied, so a cut replay reports exactly the finalizations
the stream had published — a prefix report can never show a future
finalization. An import-graph test keeps the package free of signal-engine
reads and any reclassification. Inputs are never mutated.

## API

```python
from smcsignal.analysis import (
    PerformanceConfig,
    analyze_performance,
)

report = analyze_performance(
    {"btc": outcome_frames, "eth": eth_outcome_frames},
    {"btc": attribution_frames, "eth": eth_attribution_frames},
    PerformanceConfig(),
)
report.overall.win_rate
report.by_month  # sorted YYYY-MM buckets
report.best_combination  # None until a bucket reaches the minimum
```

## Hand-computed synthetic example

`config/performance.example.toml` reuses the synthetic history. The
17-candle rising replay yields 4 BUYs, 1 finalized WIN, 3 open:
`final_return_sum = 10/24`, `win_rate = 1`. The flat tail yields seven BUYs
with one exact-tie FLAT (`win_rate = 0`). Multi-series BTC/ETH input merges
into an 11-signal report with per-symbol buckets; the January/February
long-replay pair fills two UTC month buckets (4 + 1 finalized WINs). The
sweep-long replay demonstrates gated ranking: with
`minimum_finalized_for_ranking = 10` no combination is ranked; with 1,
`mtf_bullish` is best and the displacement+sweep combination worst.

## Known limitations and stop boundary

- Reports cover exactly the supplied replays; there is no persistence,
  sampling, or statistical inference.
- Small-sample rates are shown with their exact counts; the reader, never the
  code, judges sufficiency — the ranking minimum only gates best/worst
  quotations.
- Performance buckets never feed back into signal decisions; decision modules
  cannot import this package.

No live trading, orders, execution, fees, slippage modeling, Telegram,
optimization, or self-modification is implemented.

**Stop after Phase 19. Phase 20 requires explicit approval.**
