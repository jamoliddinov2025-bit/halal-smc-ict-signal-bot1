# Backtest methodology — Phase 20 historical replay and backtesting foundation

Historical research layer, not a trading system.

**Stop after Phase 20. Phase 21 requires explicit approval.**

## Question answered

If the existing SMC/ICT signal engine had processed a declared historical OHLCV
dataset chronologically, candle by candle, which `BUY_SIGNAL` facts would it
have published, and what did the existing fixed-horizon outcome tracking record
afterward? A backtest answers exactly that question and nothing else. It is a
replay of the unchanged pipeline over historical data, never a second strategy,
an optimizer, or a performance claim.

## Datasets

A `ReplayDataset` declares one symbol, one primary timeframe, the primary
candle tuple, optional higher-timeframe candle tuples, and the series identity
(venue, provider, `dataset_id`). Candles must be chronological, unique, and
nonoverlapping within their timeframe; higher timeframes must be strictly
higher integer multiples of the primary. Higher histories may be empty. Datasets
are offline histories: no exchange, network, or live feed is read, and no
third-party runtime dependency exists.

## Replay

`HistoricalReplay.update()` draws exactly the next declared primary candle and
pushes it through the existing streaming analyzers — liquidity, displacement,
FVG, order blocks, premium/discount, OTE, the existing MTF join (gated by its
completed-candle availability cursor), the halal filter, setup quality,
eligibility, and the signal engine — then the existing Phase 18 outcome
tracking and Phase 19b attribution. The engine never indexes a primary candle
beyond its draw cursor, and every published higher-timeframe reference was
available before the primary candle opened. Batch `replay_history` and any
chunking of `update()` calls produce byte-identical steps, frames, and replay
identities. No Phase 1–19 module is modified, and no decision module imports
this layer.

## Results

A `ReplayResult` retains the published frames untouched and carries a
deterministic `replay_id` — a SHA-256 digest over the frozen configuration
artifact, the dataset identity, the exact candle prefixes fed through the
chain, and every published record id. `run_backtest` replays one or more
datasets once each and composes the existing Phase 19c `PerformanceReport` for
aggregation (overall, symbol, timeframe, label, combination, month); no
statistic is recomputed. `BacktestSignalResult` rows copy the signal identity,
candle index and timestamps, direction, score, setup identity, attribution
labels and combination key, and the latest Phase 18 outcome version with its
status, final/MFE/MAE ratios from the existing helpers. All arithmetic is exact
`Decimal`; no floats enter any financial value.

## Reporting

`render_backtest_text` renders one deterministic plain-text summary (dataset
identities, overall counts, win rate, return/MFE/MAE aggregates, best/worst
combinations, and per-group buckets with sample sizes always shown).
`machine_summary` renders the same facts as canonical JSON bytes with exact
Decimal strings. Both are pure functions of the report (plus the explicit UTC
generation instant for text). Zero-signal datasets are valid and render
deterministically.

## Anti-lookahead guarantees

Structural guarantees plus tests prove: identical inputs replay identically;
prefix replays reproduce the corresponding prefix of the full replay exactly;
alternate futures never change past signals; no future candle is read during
generation; candles are processed strictly chronologically; results are
independent of chunking; horizon semantics are exactly Phase 18's; and the
canonical frames pass through by identity, never mutated. Open outcomes stay
open at end-of-series: no flush exists.

## Explicit non-goals

No live trading, order execution, exchange trading APIs, API keys, position
management, sizing, leverage, fees, slippage, parameter optimization, strategy
modification, self-learning, machine-generated rules, indicator-only signals,
Telegram transport, or automatic deployment exists or will be added silently.
Backtests are descriptive records of published facts, not advice.

**Stop after Phase 20. Phase 21 requires explicit approval.**
