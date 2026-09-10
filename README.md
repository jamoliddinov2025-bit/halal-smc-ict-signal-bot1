# Professional Halal SMC/ICT Spot Signal Bot

**Status: Phase 22 — deterministic strategy-intelligence research reporting.**

A Python foundation with a validated OHLCV data layer and incremental market
structure analysis. It reads local CSV or unauthenticated Binance public **Spot**
data, confirms fractal swings without backdating them, and produces immutable
per-candle structure, liquidity, displacement, FVG, OB, PD, MSS, Breaker,
Mitigation, OTE, multi-timeframe confluence, registry-based classification,
integer setup-quality snapshots, eligibility decisions, spot publication
records, and fixed-horizon outcome analytics for published buys. It does **not** generate SELL or SHORT trades, execute orders, or issue
religious rulings. UNKNOWN assets are never silently treated as HALAL. HARAM and
UNKNOWN receive quality score 0 and are never eligible.

## Install and verify

Python **3.11+**; no third-party runtime dependencies. On Linux/macOS:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e ".[dev]"
python -m pytest
```

On Windows, create the environment with `python -m venv .venv` and activate with
`.venv\Scripts\Activate.ps1` in PowerShell.

## Phase 18 offline outcome tracking example

Outcome tracking consumes existing Phase 17 signal frames only; it has no
second price feed and never reruns earlier detectors. The example reuses the
**synthetic** Phase 13 15m/1h/4h history from
`config/outcome-tracking.example.toml`, with the SQS threshold lowered to 10 so
`BUY_SIGNAL` publications exist (indices 4, 8, 12, 16):

```python
from smcsignal.analysis import (
    SeriesProvenance,
    OutcomeTrackingConfig,
    analyze_liquidity,
    analyze_displacement,
    analyze_fvg,
    analyze_order_blocks,
    analyze_pd,
    analyze_ote,
    analyze_mtf,
    analyze_halal,
    analyze_setup_quality,
    analyze_signal_eligibility,
    analyze_signal_engine,
    analyze_outcome_tracking,
    load_analysis_config,
    load_liquidity_config,
    load_displacement_config,
    load_fvg_config,
    load_order_block_config,
    load_pd_config,
    load_ote_config,
    load_mtf_config,
    load_halal_filter_config,
    load_signal_eligibility_config,
    load_signal_engine_config,
)
from smcsignal.analysis.setup_quality import SetupQualityConfig
from smcsignal.analysis.signal_engine import SignalEngineConfig
from smcsignal.data import CsvDataProvider, MarketDataConfig, load_data_config

path = "config/outcome-tracking.example.toml"
data = load_data_config(path)
mtf = load_mtf_config(path)
halal = load_halal_filter_config(path)
eligibility = load_signal_eligibility_config(path)


def frames(candles, timeframe):
    series = SeriesProvenance(
        data.symbol,
        timeframe,
        "synthetic_spot",
        "csv",
        f"signal-demo:{timeframe}:v1:from-first-row:missing=error",
    )
    a = analyze_liquidity(
        candles,
        series=series,
        config=load_liquidity_config(path),
        analysis_config=load_analysis_config(path),
    )
    b = analyze_displacement(
        a, load_displacement_config(path), price_unit=load_liquidity_config(path).price_unit
    )
    c = analyze_order_blocks(analyze_fvg(b, load_fvg_config(path)), load_order_block_config(path))
    return analyze_ote(analyze_pd(c, load_pd_config(path)), load_ote_config(path))


primary = frames(CsvDataProvider(data).fetch_ohlcv().candles, "15m")
hourly = frames(
    CsvDataProvider(
        MarketDataConfig(
            symbol="BTCUSDT",
            timeframe="1h",
            data_source="csv",
            history_limit=500,
            csv_path="tests/fixtures/mtf-1h.csv",
        )
    )
    .fetch_ohlcv()
    .candles,
    "1h",
)
four = frames(
    CsvDataProvider(
        MarketDataConfig(
            symbol="BTCUSDT",
            timeframe="4h",
            data_source="csv",
            history_limit=500,
            csv_path="tests/fixtures/mtf-4h.csv",
        )
    )
    .fetch_ohlcv()
    .candles,
    "4h",
)
filtered = analyze_halal(analyze_mtf(primary, {"1h": hourly, "4h": four}, mtf), halal)
scored = analyze_setup_quality(filtered, SetupQualityConfig(10))
eligible = analyze_signal_eligibility(scored, eligibility)
signals = analyze_signal_engine(eligible, SignalEngineConfig(publish_threshold=10))
snapshots = analyze_outcome_tracking(signals, OutcomeTrackingConfig(horizon_bars=10))
print(snapshots[14].completed[0].status.value)
print(snapshots[-1].analytics.open_count)
```

```text
WIN
3
```

The index-4 buy (reference close 24) is evaluated on candles 5–14: final close
34 → `WIN`, MFE 35 @ 14, MAE 24 @ 5. The index-8/12/16 buys stay `OPEN` at
end-of-series; no flush exists. `OutcomeTrackingAnalyzer(config).update(signal_frame)`
is the streaming equivalent. Classification uses only the exact sign of the
final close difference (`FLAT` on exact ties), arithmetic is exact Decimal, and
analytics aggregate finalized outcomes only. This is analytics of publication
records: no entries, exits, fees, sizing, or execution. See
[outcome tracking methodology](docs/outcome-tracking-methodology.md).

## Phase 19 offline analytics and visualization example

Phase 19 layers are pure consumers of published frames: indicators read
displacement frames for context only, attribution projects nested facts onto
a closed label taxonomy, performance recomputes descriptive statistics over
outcome records, monthly review renders those statistics, and visualization
composes drawings from existing facts without re-detecting anything. The
example continues the same synthetic history and chain as Phase 18 above
(`signals` from `analyze_signal_engine`, `primary` from the `frames` helper):

```python
from smcsignal.analysis import (
    IndicatorsConfig,
    OutcomeTrackingConfig,
    analyze_indicators,
    analyze_outcome_tracking,
    analyze_performance,
    analyze_setup_attribution,
    build_monthly_reviews,
    compose_drawing,
    render_text,
)
from smcsignal.analysis.setup_quality.calculation import nested_displacement

outcomes = analyze_outcome_tracking(signals, OutcomeTrackingConfig(horizon_bars=10))
attributions = analyze_setup_attribution(signals)
report = analyze_performance({"btc": outcomes}, {"btc": attributions})
reviews = build_monthly_reviews(report)
print(report.overall.win_count, report.by_month[0].name)
print(reviews[0].comparison_line())

indicators = analyze_indicators(
    tuple(nested_displacement(f.upstream.upstream.upstream) for f in signals),
    IndicatorsConfig(ema_periods=(3, 5), rsi_period=3, volume_average_period=4),
)
drawing = compose_drawing(primary, signals=signals, indicators=indicators, outcomes=outcomes)
print(render_text(drawing).splitlines()[0])
```

```text
1 2024-01
comparison: none (first reviewed month)
BTCUSDT 15m; candles 17; not advice
```

Every Phase 19 layer is deterministic and replay-local: identical inputs
produce identical indicators, profiles, reports, reviews, and drawings, with
`indicator-frame`, `setup-attribution:`, `performance-report:`,
`monthly-review:`, and `drawing:` digest identities. Indicators never
generate or veto signals (an import-graph test keeps decision modules
indicator-free); attribution is outcome-independent; performance copies
statuses without reclassification; review shows sample sizes always;
visualization carries semantic style tokens, never colors. See
[indicators methodology](docs/indicators-methodology.md),
[setup attribution methodology](docs/setup-attribution-methodology.md),
[performance methodology](docs/performance-methodology.md),
[monthly review methodology](docs/monthly-review-methodology.md), and
[visualization methodology](docs/visualization-methodology.md).

## Phase 20 offline historical replay and backtest example

A backtest is a deterministic chronological replay of one declared historical
dataset through the **unchanged** Phase 3–19 pipeline: the replay draws the
next declared candle, pushes it through the existing analyzers, and composes
the existing Phase 18/19 outcome, attribution, and performance records. No
second strategy, detector, classifier, or formula exists, and no decision
module can import the layer. The example replays the same synthetic 15m/1h/4h
history as Phase 18, with the SQS threshold again lowered to 10:

```python
from datetime import UTC, datetime

from smcsignal.analysis import (
    BacktestConfiguration,
    ReplayDataset,
    render_backtest_text,
    run_backtest,
)
from smcsignal.analysis import (
    AnalysisConfig,
    DisplacementConfig,
    LiquidityConfig,
    SetupQualityConfig,
    SignalEngineConfig,
)
from smcsignal.data import CsvDataProvider, MarketDataConfig, load_data_config

path = "config/outcome-tracking.example.toml"
data = load_data_config(path)
hourly_data = MarketDataConfig(
    symbol="BTCUSDT",
    timeframe="1h",
    data_source="csv",
    history_limit=500,
    csv_path="tests/fixtures/mtf-1h.csv",
)
four_data = MarketDataConfig(
    symbol="BTCUSDT",
    timeframe="4h",
    data_source="csv",
    history_limit=500,
    csv_path="tests/fixtures/mtf-4h.csv",
)

dataset = ReplayDataset(
    symbol=data.symbol,
    timeframe="15m",
    candles=CsvDataProvider(data).fetch_ohlcv().candles,
    higher_candles={
        "1h": CsvDataProvider(hourly_data).fetch_ohlcv().candles,
        "4h": CsvDataProvider(four_data).fetch_ohlcv().candles,
    },
    dataset_id="backtest-demo:v1",
)
config = BacktestConfiguration(
    analysis=AnalysisConfig(3),
    liquidity=LiquidityConfig("USDT"),
    displacement=DisplacementConfig(atr_period=3),
    setup_quality=SetupQualityConfig(10),
    signal_engine=SignalEngineConfig(publish_threshold=10),
)

report = run_backtest([dataset], config)
overall = report.performance.overall
print(overall.total_buy_signals, overall.win_count, overall.win_rate)
print(report.signals[0].candle_index, report.signals[0].outcome_status.value)
text = render_backtest_text(report, generated_at=datetime(2024, 2, 1, tzinfo=UTC))
print(text.splitlines()[8])
```

```text
4 1 1
4 WIN
overall all: signals 4, open 3, finalized 1 (win 1, loss 0, flat 0)
```

The replay publishes the same four `BUY_SIGNAL` facts as the Phase 17/18
examples (indices 4, 8, 12, 16; the index-4 buy finalizes `WIN` on candle 14),
because it is literally the same pipeline. Replay steps are identical for any
chunking of updates, prefix replays reproduce the full replay exactly, and
alternate futures never change past signals; `replay_id` and `backtest_id`
digest identities cover the configuration, the dataset, the exact candle
prefixes, and every published record. Rows copy the published signal,
attribution, and outcome facts with exact Decimal ratios; aggregates come from
the existing Phase 19c performance report. This is historical research of
publication records: no orders, execution, fees, sizing, optimization, or
advice. See [backtest methodology](docs/backtest-methodology.md).

## Phase 21 offline walk-forward robustness example

Robustness validation slices one declared history into sequential
development/validation windows and replays each window as one independent
**unchanged** Phase 20 backtest, then reports cross-period, cross-symbol,
cross-timeframe, and regime buckets, development-to-validation degradation,
and stability labels. It validates the existing strategy; it never tunes,
selects, or modifies anything. The synthetic example (30 rising candles, then
30 choppy candles) uses a small window configuration:

```python
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from smcsignal.analysis import (
    AnalysisConfig,
    BacktestConfiguration,
    DisplacementConfig,
    LiquidityConfig,
    ReplayDataset,
    RobustnessConfig,
    SetupQualityConfig,
    SignalEngineConfig,
    render_robustness_text,
    run_robustness,
)
from smcsignal.analysis.mtf import timeframe_seconds
from smcsignal.data import OHLCV

midnight = datetime(2024, 1, 1, tzinfo=UTC)
start = datetime(2024, 1, 1, 8, tzinfo=UTC)


def bar(opened_at, close):
    return OHLCV(
        timestamp=opened_at,
        open=Decimal(close),
        high=Decimal(close) + 1,
        low=Decimal(close) - 1,
        close=Decimal(close),
        volume=Decimal(1),
    )


def bars(timeframe, prices, opened):
    step = timedelta(seconds=timeframe_seconds(timeframe))
    return tuple(bar(opened + index * step, price) for index, price in enumerate(prices))


prices = (*range(20, 50), *(30 - index % 5 for index in range(30)))
dataset = ReplayDataset(
    symbol="BTCUSDT",
    timeframe="15m",
    candles=bars("15m", prices, start),
    higher_candles={
        "1h": bars("1h", (15, 10, 16, 20, 18, 12, 17, 22, 19, 18, 17, 16), midnight),
        "4h": bars("4h", (15,), start),
    },
    dataset_id="robustness-demo:v1",
)
backtest = BacktestConfiguration(
    analysis=AnalysisConfig(3),
    liquidity=LiquidityConfig("USDT"),
    displacement=DisplacementConfig(atr_period=3),
    setup_quality=SetupQualityConfig(10),
    signal_engine=SignalEngineConfig(publish_threshold=10),
)
robustness = RobustnessConfig(
    development_bars=12,
    validation_bars=14,
    step_bars=14,
    regime_lookback_bars=3,
    regime_baseline_multiple=2,
    minimum_finalized_for_stability=1,
    minimum_windows_for_stability=2,
)

report = run_robustness([dataset], backtest, robustness)
overall = report.overall
print(overall.total_buy_signals, overall.finalized_count, overall.win_count, overall.open_count)
print(report.stability.status.value, report.degradation.comparable_window_count)
text = render_robustness_text(report, generated_at=datetime(2024, 2, 1, tzinfo=UTC))
print(text.splitlines()[7])
```

```text
16 4 1 12
WEAK 2
overall all
```

Three windows are planned (validation periods never overlap because
`step_bars >= validation_bars`); the out-of-sample overall bucket counts the
16 validation signals once (4 finalized: 1 win, 3 flat; 12 still open at their
window ends), and the stability label over the two sufficiently sampled
windows is WEAK — a descriptive statement with sample sizes shown, not a
significance claim. Each window equals a direct Phase 20 replay of its own
candle slice, so windows never share state and no future candle can change an
earlier window. Regime labels (TRENDING/RANGING/HIGH_VOLATILITY/LOW_VOLATILITY)
are causal annotations only: they never generate, veto, or modify signals.
See [robustness methodology](docs/robustness-methodology.md).

## Phase 22 offline strategy-intelligence example

Strategy Intelligence is a consumer-only layer over the Phase 21 report: it
reads the validated, out-of-sample validation rows and reports how their
published outcomes distribute across signal-time strategy profiles, with
deterministic winner/loser patterns, strength/weakness diagnostics, and
sample-gated research ranks. It never re-runs the replay, re-detects a regime,
recomputes an outcome, or selects anything.

```python
from smcsignal.analysis import (
    analyze_report,
    render_intelligence_text,
    load_intelligence_config,
)
from smcsignal.analysis.robustness import run_robustness

robust = run_robustness(datasets, backtest_configuration, robustness_config)  # Phase 21
config = load_intelligence_config("config/intelligence.example.toml")
research = analyze_report(robust, config)  # IntelligenceReport
print(render_intelligence_text(research))  # deterministic text
```

The overall population is the Phase 21 validation rows, each counted exactly
once. Each cell carries exact Decimal statistics plus its `pattern`
(WINNER/LOSER/NEUTRAL/UNDERSAMPLED), `diagnostic`
(STRENGTH/WEAKNESS/UNDETERMINED), and deterministic `rank` (1 = best) among
groups that reach the configured ranking minimum. The intelligence `report_id`
is derived from the concrete row facts — never the Phase 21 report id — so
appended futures cannot rewrite an observed cell. See
[strategy-intelligence methodology](docs/intelligence-methodology.md).

## Phase 17 offline spot signal example

The signal engine only reads already-published Phase 16 eligibility facts. It
does not rerun earlier analyzers, invent a second bias, or emit orders.
`config/signal-engine.example.toml` reuses the **synthetic** Phase 13
15m/1h/4h history:

```python
from smcsignal.analysis import (
    SeriesProvenance,
    analyze_liquidity,
    analyze_displacement,
    analyze_fvg,
    analyze_order_blocks,
    analyze_pd,
    analyze_ote,
    analyze_mtf,
    analyze_halal,
    analyze_setup_quality,
    analyze_signal_eligibility,
    analyze_signal_engine,
    load_analysis_config,
    load_liquidity_config,
    load_displacement_config,
    load_fvg_config,
    load_order_block_config,
    load_pd_config,
    load_ote_config,
    load_mtf_config,
    load_halal_filter_config,
    load_setup_quality_config,
    load_signal_eligibility_config,
    load_signal_engine_config,
)
from smcsignal.data import CsvDataProvider, MarketDataConfig, load_data_config

path = "config/signal-engine.example.toml"
data = load_data_config(path)
liquidity = load_liquidity_config(path)
mtf = load_mtf_config(path)
halal = load_halal_filter_config(path)
quality = load_setup_quality_config(path)
eligibility = load_signal_eligibility_config(path)
engine = load_signal_engine_config(path)


def frames(candles, timeframe):
    series = SeriesProvenance(
        data.symbol,
        timeframe,
        "synthetic_spot",
        "csv",
        f"signal-demo:{timeframe}:v1:from-first-row:missing=error",
    )
    a = analyze_liquidity(
        candles, series=series, config=liquidity, analysis_config=load_analysis_config(path)
    )
    b = analyze_displacement(a, load_displacement_config(path), price_unit=liquidity.price_unit)
    c = analyze_order_blocks(analyze_fvg(b, load_fvg_config(path)), load_order_block_config(path))
    return analyze_ote(analyze_pd(c, load_pd_config(path)), load_ote_config(path))


primary = frames(CsvDataProvider(data).fetch_ohlcv().candles, "15m")
hourly = frames(
    CsvDataProvider(
        MarketDataConfig(
            symbol="BTCUSDT",
            timeframe="1h",
            data_source="csv",
            history_limit=500,
            csv_path="tests/fixtures/mtf-1h.csv",
        )
    )
    .fetch_ohlcv()
    .candles,
    "1h",
)
four = frames(
    CsvDataProvider(
        MarketDataConfig(
            symbol="BTCUSDT",
            timeframe="4h",
            data_source="csv",
            history_limit=500,
            csv_path="tests/fixtures/mtf-4h.csv",
        )
    )
    .fetch_ohlcv()
    .candles,
    "4h",
)
filtered = analyze_halal(analyze_mtf(primary, {"1h": hourly, "4h": four}, mtf), halal)
scored = analyze_setup_quality(filtered, quality)
eligible = analyze_signal_eligibility(scored, eligibility)
results = analyze_signal_engine(eligible, engine)
print(results[0].status.value, results[0].direction.value)
print(results[4].status.value, results[4].direction.value)
```

```text
NO_SIGNAL NONE
NO_SIGNAL NONE
```

`SignalEngineAnalyzer(config).update(eligibility_frame)` is the streaming
equivalent. Index 0 is HALAL-only below threshold (`NEUTRAL`). Index 4 adds
directional MTF (`LONG_BIAS`) but still fails default `publish_threshold = 75`.
HARAM and UNKNOWN force `NO_SIGNAL`. Eligible `SHORT_BIAS` is `BEARISH_AVOID`,
never a short. Lowering only the SQS threshold to 10, with a matching engine
threshold, makes index 4 `BUY_SIGNAL`; later same-identity candles are
`duplicate_setup`. Upstream eligibility objects and IDs are unchanged.

This is a spot publication record only: no SELL, SHORT, entries, Telegram,
ranking, or trading. See
[signal engine methodology](docs/signal-engine-methodology.md) for mapping,
gates, identity, and limits.

## Existing Phase 16 offline signal eligibility example

The eligibility engine only reads already-published nested Halal/SQS facts. It
does not rerun earlier analyzers, force a direction, or emit trades.
`config/signal-eligibility.example.toml` reuses the **synthetic** Phase 13
15m/1h/4h history:

```python
from smcsignal.analysis import (
    SeriesProvenance,
    analyze_liquidity,
    analyze_displacement,
    analyze_fvg,
    analyze_order_blocks,
    analyze_pd,
    analyze_ote,
    analyze_mtf,
    analyze_halal,
    analyze_setup_quality,
    analyze_signal_eligibility,
    load_analysis_config,
    load_liquidity_config,
    load_displacement_config,
    load_fvg_config,
    load_order_block_config,
    load_pd_config,
    load_ote_config,
    load_mtf_config,
    load_halal_filter_config,
    load_setup_quality_config,
    load_signal_eligibility_config,
)
from smcsignal.data import CsvDataProvider, MarketDataConfig, load_data_config

path = "config/signal-eligibility.example.toml"
data = load_data_config(path)
liquidity = load_liquidity_config(path)
mtf = load_mtf_config(path)
halal = load_halal_filter_config(path)
quality = load_setup_quality_config(path)
eligibility = load_signal_eligibility_config(path)


def frames(candles, timeframe):
    series = SeriesProvenance(
        data.symbol,
        timeframe,
        "synthetic_spot",
        "csv",
        f"eligibility-demo:{timeframe}:v1:from-first-row:missing=error",
    )
    a = analyze_liquidity(
        candles, series=series, config=liquidity, analysis_config=load_analysis_config(path)
    )
    b = analyze_displacement(a, load_displacement_config(path), price_unit=liquidity.price_unit)
    c = analyze_order_blocks(analyze_fvg(b, load_fvg_config(path)), load_order_block_config(path))
    return analyze_ote(analyze_pd(c, load_pd_config(path)), load_ote_config(path))


primary = frames(CsvDataProvider(data).fetch_ohlcv().candles, "15m")
hourly = frames(
    CsvDataProvider(
        MarketDataConfig(
            symbol="BTCUSDT",
            timeframe="1h",
            data_source="csv",
            history_limit=500,
            csv_path="tests/fixtures/mtf-1h.csv",
        )
    )
    .fetch_ohlcv()
    .candles,
    "1h",
)
four = frames(
    CsvDataProvider(
        MarketDataConfig(
            symbol="BTCUSDT",
            timeframe="4h",
            data_source="csv",
            history_limit=500,
            csv_path="tests/fixtures/mtf-4h.csv",
        )
    )
    .fetch_ohlcv()
    .candles,
    "4h",
)
filtered = analyze_halal(analyze_mtf(primary, {"1h": hourly, "4h": four}, mtf), halal)
scored = analyze_setup_quality(filtered, quality)
results = analyze_signal_eligibility(scored, eligibility)
print(results[0].status.value, results[0].bias.value, results[0].eligible)
print(results[4].status.value, results[4].bias.value, results[4].eligible)
```

```text
NOT_ELIGIBLE NEUTRAL False
NOT_ELIGIBLE LONG_BIAS False
```

`SignalEligibilityAnalyzer(config).update(score_frame)` is the streaming
equivalent. Index 0 is HALAL-only below threshold (`NEUTRAL`). Index 4 adds
directional MTF (`LONG_BIAS`) but still fails default `publish_threshold = 75`.
HARAM and UNKNOWN force `NOT_ELIGIBLE` / `NEUTRAL`. Conflicting nested votes
stay `NEUTRAL`. Upstream SQS/Halal objects and IDs are unchanged.

This is a gate only: no BUY/SELL, entries, Telegram, ranking, or trading. See
[signal eligibility methodology](docs/signal-eligibility-methodology.md) for
votes, gates, and limits.

## Existing Phase 15 offline setup quality example

The scorer only reads already-published nested Halal/MTF facts. It does not
rerun earlier analyzers, fit weights, or emit trades.
`config/setup-quality.example.toml` reuses the **synthetic** Phase 13 15m/1h/4h
history:

```python
from smcsignal.analysis import (
    SeriesProvenance,
    analyze_liquidity,
    analyze_displacement,
    analyze_fvg,
    analyze_order_blocks,
    analyze_pd,
    analyze_ote,
    analyze_mtf,
    analyze_halal,
    analyze_setup_quality,
    load_analysis_config,
    load_liquidity_config,
    load_displacement_config,
    load_fvg_config,
    load_order_block_config,
    load_pd_config,
    load_ote_config,
    load_mtf_config,
    load_halal_filter_config,
    load_setup_quality_config,
)
from smcsignal.data import CsvDataProvider, MarketDataConfig, load_data_config

path = "config/setup-quality.example.toml"
data = load_data_config(path)
liquidity = load_liquidity_config(path)
mtf = load_mtf_config(path)
halal = load_halal_filter_config(path)
quality = load_setup_quality_config(path)


def frames(candles, timeframe):
    series = SeriesProvenance(
        data.symbol,
        timeframe,
        "synthetic_spot",
        "csv",
        f"sqs-demo:{timeframe}:v1:from-first-row:missing=error",
    )
    a = analyze_liquidity(
        candles, series=series, config=liquidity, analysis_config=load_analysis_config(path)
    )
    b = analyze_displacement(a, load_displacement_config(path), price_unit=liquidity.price_unit)
    c = analyze_order_blocks(analyze_fvg(b, load_fvg_config(path)), load_order_block_config(path))
    return analyze_ote(analyze_pd(c, load_pd_config(path)), load_ote_config(path))


primary = frames(CsvDataProvider(data).fetch_ohlcv().candles, "15m")
hourly = frames(
    CsvDataProvider(
        MarketDataConfig(
            symbol="BTCUSDT",
            timeframe="1h",
            data_source="csv",
            history_limit=500,
            csv_path="tests/fixtures/mtf-1h.csv",
        )
    )
    .fetch_ohlcv()
    .candles,
    "1h",
)
four = frames(
    CsvDataProvider(
        MarketDataConfig(
            symbol="BTCUSDT",
            timeframe="4h",
            data_source="csv",
            history_limit=500,
            csv_path="tests/fixtures/mtf-4h.csv",
        )
    )
    .fetch_ohlcv()
    .candles,
    "4h",
)
filtered = analyze_halal(analyze_mtf(primary, {"1h": hourly, "4h": four}, mtf), halal)
results = analyze_setup_quality(filtered, quality)
print(results[0].total, results[0].threshold_passed)
print(results[4].total, results[4].threshold_passed)
```

```text
10 False
25 False
```

`SetupQualityAnalyzer(config).update(halal_frame)` is the streaming equivalent.
Index 0 is HALAL-only (10). Index 4 adds directional MTF (25). Missing nested
events contribute 0, not failure. Default `publish_threshold = 75` therefore
does not pass. HARAM and UNKNOWN force total 0. Upstream Halal objects and IDs
are unchanged.

This is integer quality only: no entries, Telegram, ranking, or trading. See
[setup quality methodology](docs/setup-quality-methodology.md) for weights,
gates, and limits.

## Existing Phase 14 offline halal asset filter example

The filter only enforces a caller-supplied registry. It does not fetch the
internet, scrape screening sites, or make autonomous religious decisions.
`config/halal-filter.example.toml` reuses the **synthetic** Phase 13 15m/1h/4h
history:

```python
from smcsignal.analysis import (
    SeriesProvenance,
    analyze_liquidity,
    analyze_displacement,
    analyze_fvg,
    analyze_order_blocks,
    analyze_pd,
    analyze_ote,
    analyze_mtf,
    analyze_halal,
    classify_asset,
    load_analysis_config,
    load_liquidity_config,
    load_displacement_config,
    load_fvg_config,
    load_order_block_config,
    load_pd_config,
    load_ote_config,
    load_mtf_config,
    load_halal_filter_config,
)
from smcsignal.data import CsvDataProvider, MarketDataConfig, load_data_config

path = "config/halal-filter.example.toml"
data = load_data_config(path)
liquidity = load_liquidity_config(path)
mtf = load_mtf_config(path)
halal = load_halal_filter_config(path)

print(classify_asset("BTCUSDT", halal).value)
print(classify_asset("ADAUSDT", halal).value)
print(classify_asset("btcusdt", halal).value)


def frames(candles, timeframe):
    series = SeriesProvenance(
        data.symbol,
        timeframe,
        "synthetic_spot",
        "csv",
        f"halal-demo:{timeframe}:v1:from-first-row:missing=error",
    )
    a = analyze_liquidity(
        candles, series=series, config=liquidity, analysis_config=load_analysis_config(path)
    )
    b = analyze_displacement(a, load_displacement_config(path), price_unit=liquidity.price_unit)
    c = analyze_order_blocks(analyze_fvg(b, load_fvg_config(path)), load_order_block_config(path))
    return analyze_ote(analyze_pd(c, load_pd_config(path)), load_ote_config(path))


primary = frames(CsvDataProvider(data).fetch_ohlcv().candles, "15m")
hourly = frames(
    CsvDataProvider(
        MarketDataConfig(
            symbol="BTCUSDT",
            timeframe="1h",
            data_source="csv",
            history_limit=500,
            csv_path="tests/fixtures/mtf-1h.csv",
        )
    )
    .fetch_ohlcv()
    .candles,
    "1h",
)
four = frames(
    CsvDataProvider(
        MarketDataConfig(
            symbol="BTCUSDT",
            timeframe="4h",
            data_source="csv",
            history_limit=500,
            csv_path="tests/fixtures/mtf-4h.csv",
        )
    )
    .fetch_ohlcv()
    .candles,
    "4h",
)
results = analyze_halal(analyze_mtf(primary, {"1h": hourly, "4h": four}, mtf), halal)
print(results[0].decision.symbol, results[0].classification.value, results[0].eligible)
```

```text
HALAL
UNKNOWN
HALAL
BTCUSDT HALAL True
```

`HalalFilterAnalyzer(config).update(mtf_frame)` is the streaming equivalent.
Allow-list members are `HALAL`; everything else is `UNKNOWN`, never a silent
approval. Deny-list members are `HARAM`; everything else is `UNKNOWN`, never
`HALAL`. Only `HALAL` is eligible for future signal phases, which are not
implemented here. Upstream MTF objects and IDs are unchanged.

This is registry enforcement only: no scores, entries, Telegram, or trading. See
[halal filter methodology](docs/halal-filter-methodology.md) for modes, unknown
handling, and limits.

## Existing Phase 13 offline multi-timeframe example

The included 15m/1h/4h datasets are **synthetic**, not exchange observations or
returns. Each timeframe is analysed independently; 1h/4h bars are not resampled
from 15m:

```python
from smcsignal.analysis import (
    SeriesProvenance,
    analyze_liquidity,
    analyze_displacement,
    analyze_fvg,
    analyze_order_blocks,
    analyze_pd,
    analyze_ote,
    analyze_mtf,
    load_analysis_config,
    load_liquidity_config,
    load_displacement_config,
    load_fvg_config,
    load_order_block_config,
    load_pd_config,
    load_ote_config,
    load_mtf_config,
)
from smcsignal.data import CsvDataProvider, MarketDataConfig, load_data_config

path = "config/mtf.example.toml"
data, liquidity, mtf = load_data_config(path), load_liquidity_config(path), load_mtf_config(path)


def frames(candles, timeframe):
    series = SeriesProvenance(
        data.symbol,
        timeframe,
        "synthetic_spot",
        "csv",
        f"mtf-demo:{timeframe}:v1:from-first-row:missing=error",
    )
    a = analyze_liquidity(
        candles, series=series, config=liquidity, analysis_config=load_analysis_config(path)
    )
    b = analyze_displacement(a, load_displacement_config(path), price_unit=liquidity.price_unit)
    c = analyze_order_blocks(analyze_fvg(b, load_fvg_config(path)), load_order_block_config(path))
    return analyze_ote(analyze_pd(c, load_pd_config(path)), load_ote_config(path))


primary = frames(CsvDataProvider(data).fetch_ohlcv().candles, "15m")
hourly = frames(
    CsvDataProvider(
        MarketDataConfig(
            symbol="BTCUSDT",
            timeframe="1h",
            data_source="csv",
            history_limit=500,
            csv_path="tests/fixtures/mtf-1h.csv",
        )
    )
    .fetch_ohlcv()
    .candles,
    "1h",
)
four = frames(
    CsvDataProvider(
        MarketDataConfig(
            symbol="BTCUSDT",
            timeframe="4h",
            data_source="csv",
            history_limit=500,
            csv_path="tests/fixtures/mtf-4h.csv",
        )
    )
    .fetch_ohlcv()
    .candles,
    "4h",
)
results = analyze_mtf(primary, {"1h": hourly, "4h": four}, mtf)
print(results[4].direction.value, results[4].relations[1].latest is None)
print(results[16].relations[1].latest is not None)
```

```text
BULLISH True
True
```

`MTFAnalyzer(config, higher=...).update(ote_frame)` is the streaming equivalent.
It consumes already-generated OTE frames and does not rerun prior engines. Strict
v1 treats HTF evidence as eligible only when `available_at <= primary open`.
A 4h candle 08:00–12:00 is unknown to a 15m observation at 09:00. Multiple HTFs
remain independent; disagreement is `MIXED`, never a score.

This is context only: no entries, stops, targets, scores, or trading. See
[MTF methodology](docs/mtf-confluence-methodology.md) for exact eligibility,
labels, and limits.

## Existing Phase 12 offline OTE example

The included 10-candle dataset is **synthetic**, not exchange observations or returns:

```python
from smcsignal.analysis import (
    SeriesProvenance,
    analyze_liquidity,
    analyze_displacement,
    analyze_fvg,
    analyze_order_blocks,
    analyze_pd,
    analyze_ote,
    load_analysis_config,
    load_liquidity_config,
    load_displacement_config,
    load_fvg_config,
    load_order_block_config,
    load_pd_config,
    load_ote_config,
)
from smcsignal.data import create_data_provider, load_data_config

path = "config/ote.example.toml"
data, liquidity = load_data_config(path), load_liquidity_config(path)
series = SeriesProvenance(
    data.symbol,
    data.timeframe,
    "synthetic_spot",
    data.data_source,
    "ote-demo:v1:from-first-row:missing=error",
)
a = analyze_liquidity(
    create_data_provider(data).fetch_ohlcv().candles,
    series=series,
    config=liquidity,
    analysis_config=load_analysis_config(path),
)
b = analyze_displacement(a, load_displacement_config(path), price_unit=liquidity.price_unit)
c = analyze_order_blocks(analyze_fvg(b, load_fvg_config(path)), load_order_block_config(path))
frames = analyze_ote(analyze_pd(c, load_pd_config(path)), load_ote_config(path))
for frame in frames:
    print(frame.upstream.observation.reference.candle_index, frame.classification.value)
```

```text
0 INSUFFICIENT_CONTEXT
1 INSUFFICIENT_CONTEXT
2 INSUFFICIENT_CONTEXT
3 INSUFFICIENT_CONTEXT
4 INSUFFICIENT_CONTEXT
5 INSIDE_OTE
6 BELOW_OTE
7 ABOVE_OTE
8 INSIDE_OTE
9 INSIDE_OTE
```

`OTEAnalyzer().update(pd_frame)` is the streaming equivalent. It consumes the
actual existing Phase 8 output without rerunning prior engines. Strict v1 maps the
current Phase 8 dealing range to an inclusive 0.62–0.79 retracement interval using
exact Decimal arithmetic. Bullish ranges measure down from the high; bearish ranges
measure up from the low. The stored interval is ordered so the lower bound is not
above the upper bound.

A close is classified only against a range that was already known before that bar
opened. The candle that confirms a new range cannot use that future information.
If Phase 8 has no valid current range, the label is `INSUFFICIENT_CONTEXT`; there
is no silent older-range fallback. Exact 62% and 79% closes are inside. Wicks and
opens are not the evaluation basis.

This is location context only: no entries, stops, targets, scores, or trading. See
[OTE methodology](docs/ote-methodology.md) for exact geometry, timing, multiple-range
policy, and limits.

## Existing Phase 11 offline Mitigation example

The included 25-candle dataset is **synthetic**, not exchange observations or returns:

```python
from smcsignal.analysis import (
    SeriesProvenance,
    analyze_liquidity,
    analyze_displacement,
    analyze_fvg,
    analyze_order_blocks,
    analyze_pd,
    analyze_mss,
    analyze_breaker_blocks,
    analyze_mitigation_blocks,
    load_analysis_config,
    load_liquidity_config,
    load_displacement_config,
    load_fvg_config,
    load_order_block_config,
    load_pd_config,
    load_mss_config,
    load_breaker_block_config,
    load_mitigation_block_config,
)
from smcsignal.data import create_data_provider, load_data_config

path = "config/mitigation-block.example.toml"
data, liquidity = load_data_config(path), load_liquidity_config(path)
series = SeriesProvenance(
    data.symbol,
    data.timeframe,
    "synthetic_spot",
    data.data_source,
    "mitigation-demo:v1:from-first-row:missing=error",
)
a = analyze_liquidity(
    create_data_provider(data).fetch_ohlcv().candles,
    series=series,
    config=liquidity,
    analysis_config=load_analysis_config(path),
)
b = analyze_displacement(a, load_displacement_config(path), price_unit=liquidity.price_unit)
c = analyze_order_blocks(analyze_fvg(b, load_fvg_config(path)), load_order_block_config(path))
d = analyze_mss(analyze_pd(c, load_pd_config(path)), load_mss_config(path))
e = analyze_breaker_blocks(d, load_breaker_block_config(path))
frames = analyze_mitigation_blocks(e, load_mitigation_block_config(path))
for frame in frames:
    for block in frame.events:
        print(
            block.original_ob_confirmation_index,
            block.confirmation_index,
            block.direction.value,
            block.original_lower_boundary,
            block.original_upper_boundary,
        )
```

```text
20 21 bullish 13 15
23 24 bearish 16 23
```

`MitigationBlockAnalyzer().update(breaker_frame)` is the streaming equivalent. It
consumes the actual existing Phase 10 output without rerunning prior engines.
Strict v1 uses a previously known OB's first interior **range overlap** with the
original zone. Completely outside candles and exact endpoint touches do not
qualify. Only the first valid post-publication interaction is emitted. All
independent qualifying sources are retained in original publication order; zones
and source IDs are never changed. If the OB later becomes a Breaker, later overlaps
do not create a first mitigation; any already-published mitigation stays immutable.

This is interaction evidence only: no entries, retests, remaining-size tracking,
live zone manager, or trading. See
[Mitigation methodology](docs/mitigation-block-methodology.md) for exact geometry,
source eligibility, Breaker policy, timing, and limits.

## Existing Phase 10 offline Breaker example

The included 28-candle dataset is **synthetic**, not exchange observations or returns:

```python
from smcsignal.analysis import (
    SeriesProvenance,
    analyze_liquidity,
    analyze_displacement,
    analyze_fvg,
    analyze_order_blocks,
    analyze_pd,
    analyze_mss,
    analyze_breaker_blocks,
    load_analysis_config,
    load_liquidity_config,
    load_displacement_config,
    load_fvg_config,
    load_order_block_config,
    load_pd_config,
    load_mss_config,
    load_breaker_block_config,
)
from smcsignal.data import create_data_provider, load_data_config

path = "config/breaker-block.example.toml"
data, liquidity = load_data_config(path), load_liquidity_config(path)
series = SeriesProvenance(
    data.symbol,
    data.timeframe,
    "synthetic_spot",
    data.data_source,
    "breaker-demo:v1:from-first-row:missing=error",
)
a = analyze_liquidity(
    create_data_provider(data).fetch_ohlcv().candles,
    series=series,
    config=liquidity,
    analysis_config=load_analysis_config(path),
)
b = analyze_displacement(a, load_displacement_config(path), price_unit=liquidity.price_unit)
c = analyze_order_blocks(analyze_fvg(b, load_fvg_config(path)), load_order_block_config(path))
d = analyze_mss(analyze_pd(c, load_pd_config(path)), load_mss_config(path))
frames = analyze_breaker_blocks(d, load_breaker_block_config(path))
for frame in frames:
    for block in frame.events:
        print(
            block.original_ob_confirmation_index,
            block.confirmation_index,
            block.direction.value,
            block.lower_boundary,
            block.upper_boundary,
        )
```

```text
20 22 bearish 13 15
22 27 bullish 16 18
```

`BreakerBlockAnalyzer().update(mss_frame)` is the streaming equivalent. It consumes
the actual existing Phase 9 output without rerunning prior engines. Strict v1 uses
a previously known OB's first strict opposing **closing** violation, with matching
same-candle displacement and MSS. Wicks/equality do not qualify. All independent
qualifying sources are retained in original publication order; zones and source IDs
are never changed. Missing confirmation produces a rejected first-violation record,
not a Breaker to be upgraded later.

This is formation evidence only: no entries, retests, mitigation, live zone manager,
or trading. See [Breaker methodology](docs/breaker-block-methodology.md) for exact
boundaries, source eligibility, rejection reasons, timing, relationships, and limits.

## Existing Phase 9 MSS example

The new 28-observation dataset is **synthetic**, not live market data:

```python
from smcsignal.analysis import (
    SeriesProvenance,
    analyze_liquidity,
    analyze_displacement,
    analyze_fvg,
    analyze_order_blocks,
    analyze_pd,
    analyze_mss,
    load_analysis_config,
    load_liquidity_config,
    load_displacement_config,
    load_fvg_config,
    load_order_block_config,
    load_pd_config,
    load_mss_config,
)
from smcsignal.data import create_data_provider, load_data_config

path = "config/mss.example.toml"
data, liquidity = load_data_config(path), load_liquidity_config(path)
series = SeriesProvenance(
    data.symbol,
    data.timeframe,
    "synthetic_spot",
    data.data_source,
    "mss-demo:v1:from-first-row:missing=error",
)
a = analyze_liquidity(
    create_data_provider(data).fetch_ohlcv().candles,
    series=series,
    config=liquidity,
    analysis_config=load_analysis_config(path),
)
b = analyze_displacement(a, load_displacement_config(path), price_unit=liquidity.price_unit)
c = analyze_fvg(b, load_fvg_config(path))
d = analyze_order_blocks(c, load_order_block_config(path))
e = analyze_pd(d, load_pd_config(path))
frames = analyze_mss(e, load_mss_config(path))
for frame in frames:
    for event in frame.events:
        print(event.detection_index, event.direction.value, event.evidence.level_price)
```

```text
22 bearish 13
27 bullish 15
```

`MSSAnalyzer().update(pd_frame)` is the streaming equivalent and consumes the
original Phase 8 frame once. Strict MSS requires directional Phase 3 control known
before the bar opens, a previously confirmed opposite structure level, an actual
current opposing CHoCH, and matching Phase 5 displacement. Continuation BOS,
wick-only/equal closes, missing displacement, and prior ranging control do not qualify.

Original liquidity/sweep, structure, displacement, concurrent FVG, matching OB,
and PD references are retained without changing earlier objects/IDs. Concurrent
FVG context is **not** attributed to the current displacement's future C3, and no
later evidence enriches an old MSS. No trend label is forcibly reversed.

See [MSS methodology](docs/mss-methodology.md) for exact confirmation/availability,
strict invariant configuration, relationship roles, causality, and limitations.
This is analysis evidence, not signals, entries, risk rules, or scores.

## Existing Phase 8 Premium/Discount example

The new seven-candle fixture is **synthetic**, not live market data:

```python
from smcsignal.analysis import (
    SeriesProvenance,
    analyze_liquidity,
    analyze_displacement,
    analyze_fvg,
    analyze_order_blocks,
    analyze_pd,
    load_analysis_config,
    load_liquidity_config,
    load_displacement_config,
    load_fvg_config,
    load_order_block_config,
    load_pd_config,
)
from smcsignal.data import create_data_provider, load_data_config

path = "config/premium-discount.example.toml"
data, liquidity = load_data_config(path), load_liquidity_config(path)
series = SeriesProvenance(
    data.symbol,
    data.timeframe,
    "synthetic_spot",
    data.data_source,
    "pd-demo:v1:from-first-row:missing=error",
)
a = analyze_liquidity(
    create_data_provider(data).fetch_ohlcv().candles,
    series=series,
    config=liquidity,
    analysis_config=load_analysis_config(path),
)
b = analyze_displacement(a, load_displacement_config(path), price_unit=liquidity.price_unit)
c = analyze_fvg(b, load_fvg_config(path))
d = analyze_order_blocks(c, load_order_block_config(path))
frames = analyze_pd(d, load_pd_config(path))
for frame in frames:
    print(frame.observation.reference.candle_index, frame.classification.value)
```

```text
0 INSUFFICIENT_CONTEXT
1 INSUFFICIENT_CONTEXT
2 INSUFFICIENT_CONTEXT
3 PREMIUM
4 DISCOUNT
5 EQUILIBRIUM
6 OUTSIDE_RANGE
```

`PDAnalyzer().update(order_block_frame)` is the equivalent streaming API. It
consumes the original upstream frames once. The latest confirmed high/low pair
sets a fixed range; pivot order gives bullish/bearish orientation. Same-pivot or
nonpositive pairs give insufficient context, never an older-range fallback.

Equilibrium is the exact Decimal midpoint. Its default band has zero half-width;
a configurable range fraction can widen it. Closes outside the range are explicitly
`OUTSIDE_RANGE`. New liquidity/sweep/displacement/FVG/OB records receive separate
immutable PD annotations, preserving their original objects and IDs. Zone midpoint
labels include endpoint labels so they cannot be mistaken for whole-zone placement.

See [Premium/Discount methodology](docs/premium-discount-methodology.md) for exact
selection, boundaries, publication-time context, provenance, and limitations.
HTF references on Phase 8 frames remain local-series hooks; Phase 13 joins
already-generated HTF frames without changing Phase 8.

## Existing Phase 7 Order Block example

This example uses 24 **synthetic** candles, not live exchange observations:

```python
from smcsignal.analysis import (
    SeriesProvenance,
    analyze_order_blocks,
    analyze_fvg,
    analyze_displacement,
    analyze_liquidity,
    load_order_block_config,
    load_fvg_config,
    load_displacement_config,
    load_liquidity_config,
    load_analysis_config,
)
from smcsignal.data import create_data_provider, load_data_config

path = "config/order-block.example.toml"
data, liquidity = load_data_config(path), load_liquidity_config(path)
series = SeriesProvenance(
    data.symbol,
    data.timeframe,
    "synthetic_spot",
    data.data_source,
    "order-block-demo:v1:from-first-row:missing=error",
)
liquidity_frames = analyze_liquidity(
    create_data_provider(data).fetch_ohlcv().candles,
    series=series,
    config=liquidity,
    analysis_config=load_analysis_config(path),
)
displacement_frames = analyze_displacement(
    liquidity_frames,
    load_displacement_config(path),
    price_unit=liquidity.price_unit,
)
fvg_frames = analyze_fvg(displacement_frames, load_fvg_config(path))
frames = analyze_order_blocks(fvg_frames, load_order_block_config(path))
for frame in frames:
    for block in frame.events:
        print(
            block.candidate_index,
            block.confirmation_index,
            block.direction.value,
            block.zone_lower_boundary,
            block.zone_upper_boundary,
            block.structure_event.kind.value,
        )
```

```text
18 20 bullish 13 15 BOS
21 22 bearish 19 23 CHoCH
```

`OrderBlockAnalyzer().update(fvg_frame)` is the streaming equivalent; it consumes
the actual existing Phase 6 frame without rerunning any upstream engine. The
default chooses the nearest previously known opposite candle within 10 prior
observations, uses its full range, rejects dojis, and requires matching displacement
plus same-candle BOS/CHoCH. The displacement must close strictly beyond the zone.

Explicit alternatives include earliest selection, body zones, neutral-doji
eligibility, BOS-only/CHoCH-only/displacement-only structure, and required next-bar
FVG confirmation. Requiring FVG delays the example's publications to 21/23, while
candidate indices remain 18/21. No future confirmation edits an already observable
block. Sweep context is inherited from the original displacement.

These are formation facts only: **no lifecycle, entries, signals, scores, or trading**.
See [Order Block methodology](docs/order-block-methodology.md) for exact rules,
causal timing, provenance, configuration, and limitations.

## Existing Phase 6 FVG example

The twenty-candle fixture is **synthetic**, not live market data:

```python
from smcsignal.analysis import (
    SeriesProvenance,
    analyze_displacement,
    analyze_fvg,
    analyze_liquidity,
    load_analysis_config,
    load_displacement_config,
    load_fvg_config,
    load_liquidity_config,
)
from smcsignal.data import create_data_provider, load_data_config

path = "config/fvg.example.toml"
data = load_data_config(path)
liquidity = load_liquidity_config(path)
series = SeriesProvenance(
    data.symbol,
    data.timeframe,
    "synthetic_spot",
    data.data_source,
    "fvg-demo:v1:from-first-row:missing=error",
)
liquidity_frames = analyze_liquidity(
    create_data_provider(data).fetch_ohlcv().candles,
    series=series,
    config=liquidity,
    analysis_config=load_analysis_config(path),
)
displacement_frames = analyze_displacement(
    liquidity_frames,
    load_displacement_config(path),
    price_unit=liquidity.price_unit,
)
frames = analyze_fvg(displacement_frames, load_fvg_config(path))
for frame in frames:
    for gap in frame.events:
        print(
            gap.detection_index,
            gap.direction.value,
            gap.lower_boundary,
            gap.upper_boundary,
            gap.gap_size,
        )
```

```text
16 bullish 101 104 3
19 bearish 98 106 8
```

`FVGAnalyzer().update(displacement_frame)` is the streaming equivalent. It consumes
the original Phase 5 frames without rerunning earlier engines. C1/C2/C3 are the
last three **observed** candles; a strict outer-wick gap becomes knowable only
when C3 closes. Equality is never a gap. Minimum size is an inclusive absolute
price distance; default zero still requires a positive gap. Displacement is
optional unless `require_displacement=true`, which requires matching C2 evidence.

Sweep context is copied from C2's published context, never reselected at C3.
Creation records never mutate on later touches/fills: **lifecycle tracking is not
implemented**. These records are analysis facts, not signals or tradeable-zone claims.
Read [FVG methodology](docs/fvg-methodology.md) for exact geometry, availability,
configuration, causal relationships, provenance, and limitations.

## Existing Phase 5 displacement example

The included twenty-candle dataset is **synthetic**, not live exchange data:

```python
from smcsignal.analysis import (
    SeriesProvenance,
    analyze_displacement,
    analyze_liquidity,
    load_analysis_config,
    load_displacement_config,
    load_liquidity_config,
)
from smcsignal.data import create_data_provider, load_data_config

path = "config/displacement.example.toml"
data_config = load_data_config(path)
liquidity_config = load_liquidity_config(path)
series = SeriesProvenance(
    data_config.symbol,
    data_config.timeframe,
    "synthetic_spot",
    data_config.data_source,
    "displacement-demo:v1:from-first-row:missing=error",
)
liquidity_frames = analyze_liquidity(
    create_data_provider(data_config).fetch_ohlcv().candles,
    series=series,
    config=liquidity_config,
    analysis_config=load_analysis_config(path),
)
frames = analyze_displacement(
    liquidity_frames,
    load_displacement_config(path),
    price_unit=liquidity_config.price_unit,
)
for frame in frames:
    for event in frame.events:
        print(
            event.detection_index,
            event.direction.value,
            event.body_size,
            event.range_size,
            event.atr_reference.end_index,
            event.sweep_context.value,
        )
```

Expected raw analysis events (index, direction, body, range, ATR reference index, context):

```text
15 bullish 3 5 14 none
16 bearish 5 7 15 none
```

`DisplacementAnalyzer.update` consumes an **existing Phase 4 frame**, not raw candles:

```python
from smcsignal.analysis import DisplacementAnalyzer, LiquidityAnalyzer
from smcsignal.data import CsvDataProvider

liquidity = LiquidityAnalyzer(
    series=series,
    config=liquidity_config,
    analysis_config=load_analysis_config(path),
)
displacement = DisplacementAnalyzer(
    load_displacement_config(path),
    price_unit=liquidity.config.price_unit,
)
for candle in CsvDataProvider(data_config).replay():
    result = displacement.update(liquidity.update(candle))
```

This reuses the upstream engine once; precomputed frames can also be consumed
without rerunning any provider, trend, pool, or sweep detection.

**Defaults:** prior SMA ATR(14), body ≥ 1.0× ATR, range ≥ 1.5× ATR, bullish close
location ≥ 0.70 / bearish ≤ 0.30, absolute ATR floor zero, optional preceding-sweep
window 20 observed bars. ATR excludes the candidate; first eligibility is index
15. Exact comparisons do not use rounded ratios. A sweep is not required, and
same-candle or not-yet-known sweeps are never retroactively attached.

See [displacement methodology](docs/displacement-methodology.md) for true-range
calculation, warm-up, thresholds, contextual timing, exact numeric boundaries,
provenance, and limitations. These are analysis facts, **not BUY/SELL signals**.

## Existing Phase 4 liquidity and sweep example

This retained example uses twelve **synthetic** candles, not real exchange observations:

```python
from smcsignal.analysis import (
    SeriesProvenance,
    analyze_liquidity,
    load_analysis_config,
    load_liquidity_config,
)
from smcsignal.data import create_data_provider, load_data_config

path = "config/liquidity.example.toml"
data_config = load_data_config(path)
series = SeriesProvenance(
    data_config.symbol,
    data_config.timeframe,
    "synthetic_spot",
    data_config.data_source,
    "liquidity-demo:v1:from-first-row:missing=error",
)
frames = analyze_liquidity(
    create_data_provider(data_config).fetch_ohlcv().candles,
    series=series,
    config=load_liquidity_config(path),
    analysis_config=load_analysis_config(path),
)
for frame in frames:
    for event in frame.sweeps:
        print(
            event.breach.reference.candle_index,
            event.side.value,
            event.pool.kind.value,
            event.extreme_price,
            event.reclaim_close,
        )
```

Expected raw analysis events:

```text
9 buy_side equal_highs 17 12
10 sell_side equal_lows 8 12
```

Use `LiquidityAnalyzer(...).update(candle, available_at=...)` for streaming or
explicit arrival-time replay. The default assumes availability at the declared
bar close. `active_pools` is a read-only current view; frame `pool_updates` are
immutable lifecycle **deltas**. Neither pools nor sweeps are trading signals.

**Conventions:** confirmed high/low swings seed buy-side/sell-side pools. Separate
confirmed members inside a fixed first-member tolerance band form equal highs/lows
(default: exact equality). Only a pool known before the bar opens can be swept.
A strict breach and same-candle close fully through the band confirms a sweep;
gap starts and failed returns invalidate instead. Each entity retires at its first
breach. No pending sweep, multi-bar reclaim, or retrospective relabeling occurs.

See [Phase 4 methodology](docs/liquidity-sweep-methodology.md) for equality rules,
multiple-target events, exact provenance/identity formats, delay assumptions,
source-archive responsibilities, and memory/replay limits.

## Existing Phase 3 structure example

The included example uses fourteen **synthetic** candles, not Binance observations:

```python
from smcsignal.analysis import analyze, load_analysis_config
from smcsignal.data import create_data_provider, load_data_config

path = "config/analysis.example.toml"
data_config = load_data_config(path)
analysis_config = load_analysis_config(path)
provider = create_data_provider(data_config)  # no fetch at construction
candles = provider.fetch_ohlcv().candles  # explicit local CSV read
snapshots = analyze(candles, analysis_config)

for snapshot in snapshots:
    for event in snapshot.events:
        print(event.candle_index, event.kind.value, event.direction.value, event.level.price)
```

Expected analysis labels:

```text
6 BOS bullish 19
8 CHoCH bearish 13
12 BOS bearish 9
13 CHoCH bullish 15
```

These are hand-computed software-test outcomes, not trade recommendations or
performance evidence. The example uses a compact total three-candle fractal;
normal defaults use a total five-candle window.

## Existing Phase 3 streaming API

Use a fresh analyzer and feed each **completed candle once**:

```python
from smcsignal.analysis import MarketStructureAnalyzer
from smcsignal.data import CsvDataProvider

engine = MarketStructureAnalyzer(analysis_config)
for candle in CsvDataProvider(data_config).replay():
    snapshot = engine.update(candle)
    print(snapshot.candle_index, snapshot.trend.direction.value, snapshot.trend.ready)
```

`analyze` executes these same sequential updates. Invalid duplicate/out-of-order
updates are rejected before state mutation. The analyzer never fetches data or
revises past snapshots. Use one instance per series/configuration; do not feed a
previously processed batch into that instance again.

## Existing Phase 3 structure contract

- **Swing:** a strict high/low within an odd `fractal_length` window. For length 5,
  the pivot is confirmed two candles later, only after the right flank closes.
  Equal extrema are rejected; unconfirmed tail candidates are not emitted.
- **TrendState:** bullish for the latest two confirmed higher highs and higher
  lows, bearish for lower highs and lower lows, otherwise ranging. Insufficient
  evidence is explicitly `ready=False`, not a claim of actual consolidation.
- **BOS:** a strict closing-price crossing of an active confirmed level in the
  direction of the trend published through the previous candle.
- **CHoCH:** the same crossing against that previous trend; an opposing-break
  warning, not an automatic trend reversal or a trading signal.
- **Ranging-state crossings:** consume their level but receive no BOS/CHoCH label.
  Wick-only breaks/equality are not events; a level can be consumed only once.
- **Availability:** Swing pivot coordinates and confirmation coordinates are
  separate. Timestamp fields identify candle OPEN times; results are available
  only after the relevant observation/confirmation candle closes.

For identical valid closed-candle prefixes, starting history, and configuration,
prefix outputs match the corresponding full-series outputs. Future candles do
not rewrite historical results. See the [no-look-ahead guarantee and tests](docs/no-look-ahead.md).

These are explicit project conventions; market-structure terminology varies.
Read [structure definitions](docs/market-structure-methodology.md) and
[trend methodology](docs/trend-methodology.md) before interpreting the labels.

## Existing market data layer

Both providers return canonical `timestamp`, `open`, `high`, `low`, `close`, and
`volume`, with UTC opening timestamps, exact decimal values, ascending unique
candles, strict schema/price validation, and a cleaning audit. Missing cells error
by default; explicit whole-row dropping is available, never price fabrication.

```python
from smcsignal.data import create_data_provider, load_data_config

# Explicit network access; public Spot data, no API key or account access.
config = load_data_config("config/binance-public.example.toml")
batch = create_data_provider(config).fetch_ohlcv()
```

The Binance source requests one bounded page and excludes candles not closed at
its captured cutoff; it can return fewer than the requested history limit. There
are no automatic retries or source fallbacks. CSV provenance, single-series labels,
and completed-candle status are caller assumptions. Cadence gaps are not repaired.
Live Binance availability is not asserted by the offline tests.

[Market data methodology](docs/market-data-methodology.md) · [Configuration guide](config/README.md)

## Configuration and warm-up

- `[market_data]`: symbol, timeframe, source, history limit, plus source-specific options.
- `[analysis]`: exactly `fractal_length`, an odd **total window size** from 3 to 1001.
  Direct `AnalysisConfig()` defaults to 5; the TOML loader requires an explicit key.
- `[liquidity]`: explicit `price_unit` and quoted `equal_tolerance_bps` (default zero).
- `[displacement]`: explicit ATR/body/range/close/context settings; no scoring settings.
- `[fvg]`: quoted `min_gap_size` (default zero) and boolean `require_displacement` (default false).
- `[order_blocks]`: explicit selection/zone/lookback, structure, doji, and FVG-confirmation rules.
- `[premium_discount]`: quoted `equilibrium_half_width_fraction`, default zero.
- `[mss]`: explicit structure/displacement requirement flags, both mandatory true.
- `[breaker_blocks]`: strict displacement/MSS requirements, close-through invalidation, original OB zone.
- `[mitigation_blocks]`: range-intersection geometry, first interaction only, ignore-after-breaker.
- `[ote]`: quoted 0.62/0.79 retracements, inclusive boundaries, close evaluation.
- `[mtf]`: enabled completed-candle join of configured higher timeframes onto a primary series.
- `[halal_filter]`: allow-list or deny-list registry; default allow list BTCUSDT/ETHUSDT/BNBUSDT/SOLUSDT.
- `[setup_quality]`: integer `publish_threshold` (default 75); component weights are not configurable.
- `[signal_eligibility]`: `enabled = true` and `conflict_policy = "neutral"` only.
- `[signal_engine]`: `enabled = true`, matching `publish_threshold`, `spot_only = true`, `duplicate_policy = "one_per_setup"`.
- `config/signal-engine.example.toml`: hand-audited Phase 17 spot publication example on synthetic MTF history.
- `config/outcome-tracking.example.toml`: hand-audited Phase 18 outcome analytics example on the same synthetic history.
- `config/indicators.example.toml`: Phase 19a supporting-indicator context example (EMA/RSI/volume, ATR reused).
- `config/setup-attribution.example.toml`: Phase 19b closed-taxonomy attribution example.
- `config/performance.example.toml`: Phase 19c descriptive performance-report example.
- `config/review.example.toml`: Phase 19d monthly review example.
- `config/visualization.example.toml`: Phase 19e deterministic SVG/text drawing example.
- `config/backtest.example.toml`: Phase 20 full-pipeline historical replay and backtest example.
- `config/robustness.example.toml`: Phase 21 full-pipeline walk-forward robustness example.
- `config/intelligence.example.toml`: Phase 22 full-pipeline strategy-intelligence research example.
- `config/signal-eligibility.example.toml`: hand-audited Phase 16 eligibility example on synthetic MTF history.
- `config/setup-quality.example.toml`: hand-audited Phase 15 integer score example on synthetic MTF history.
- `config/halal-filter.example.toml`: hand-audited Phase 14 registry example on synthetic MTF history.
- `config/mtf.example.toml`: hand-audited Phase 13 15m/1h/4h causal confluence example.
- `config/ote.example.toml`: hand-audited Phase 12 OTE location example.
- `config/mitigation-block.example.toml`: hand-audited Phase 11 first-interaction evidence.
- `config/breaker-block.example.toml`: hand-audited Phase 10 opposite-zone formations.
- `config/mss.example.toml`: default-threshold synthetic MSS and relationship example.
- `config/premium-discount.example.toml`: exact range/equilibrium and all five PD labels.
- `config/order-block.example.toml`: hand-audited Phase 7 formation example.
- `config/fvg.example.toml`: complete default Phase 6 geometry/evidence example.
- `config/displacement.example.toml`: default Phase 5 warm-up and displacement example.
- `config/liquidity.example.toml`: hand-audited Phase 4 pool/sweep example.
- `config/analysis.example.toml`: retained Phase 3 structure example.
- `config/example.toml`: retains the tiny five-candle data fixture; expect
  insufficient confirmed-swing evidence, not fabricated trend/BOS results.

Separate loaders consume each table. Unknown options are errors. Configuration
is immutable within an analyzer. Changing the starting history/latest-N window
changes warm-up and is not equivalent to continuing an existing stream.

## Layout

```text
config/                       # Explicit market data and analysis examples
src/smcsignal/
├── data/                     # Approved OHLCV providers and validation
├── analysis/
│   ├── __init__.py            # Public API
│   ├── config.py             # Validated fractal configuration
│   ├── errors.py             # Typed input/configuration failures
│   ├── signal_engine/        # Phase 17 spot BUY_SIGNAL / BEARISH_AVOID; not SELL, SHORT, or orders
│   ├── outcome_tracking/     # Phase 18 fixed-horizon BUY outcome analytics; not trading
│   ├── backtest/             # Phase 20 deterministic historical replay; not execution
│   ├── robustness/           # Phase 21 walk-forward validation; never optimization
│   ├── intelligence/         # Phase 22 research reporting over validation rows; never optimization
│   ├── visualization/        # Phase 19e deterministic SVG/text drawings of published facts
│   ├── review/               # Phase 19d monthly descriptive review over one performance report
│   ├── performance/          # Phase 19c descriptive multi-series performance analytics
│   ├── setup_attribution/    # Phase 19b closed-taxonomy BUY confluence labels; outcome-free
│   ├── indicators/           # Phase 19a EMA/RSI/volume context; never signals
│   ├── signal_eligibility/   # Phase 16 HALAL+threshold gate and nested bias; not BUY/SELL
│   ├── setup_quality/        # Phase 15 integer 0–100 score from nested facts; not a signal
│   ├── halal_filter/         # Phase 14 config-driven registry; no autonomous rulings
│   ├── mtf/                  # Phase 13 causal HTF confluence from existing OTE frames
│   ├── ote/                  # Phase 12 OTE location context from existing dealing ranges
│   ├── mitigation_blocks/    # Phase 11 first-interaction mitigation evidence only
│   ├── breaker_blocks/       # Phase 10 first-violation formation evidence only
│   ├── mss/                  # Phase 9 strict opposing-break/displacement evidence
│   ├── premium_discount/     # Phase 8 ranges, exact equilibrium, and immutable sidecars
│   ├── order_blocks/         # Phase 7 confirmed formation, never lifecycle or entries
│   ├── fvg/                  # Phase 6 strict three-candle creation evidence
│   ├── displacement/         # Phase 5 metrics, ATR evidence, and frame-native producer
│   ├── liquidity/            # Phase 4 models, producer, artifacts, config, time
│   ├── provenance.py         # Approved shared evidence contracts
│   ├── models.py             # Immutable swings, trend, events, snapshots
│   ├── swings.py             # Delayed strict fractal confirmation
│   ├── trend.py              # Confirmed high/low-pair classification
│   └── structure.py          # Incremental BOS/CHoCH and batch replay
├── cli.py                    # Informational only
└── py.typed
tests/data/                   # Existing offline data-provider/validation tests
tests/analysis/               # Existing structure and shared-provenance tests
tests/liquidity/              # Pools, sweeps, lifecycle, artifacts, and causal replay
tests/displacement/           # ATR, displacement, context, boundaries, and causal replay
tests/fvg/                    # FVG geometry, relationships, provenance, and causal replay
tests/order_blocks/           # Selection, confirmations, timing, provenance, and causal replay
tests/premium_discount/       # Range/band boundaries, sidecars, provenance, and causal replay
tests/mss/                    # MSS definitions, relationships, immutable evidence, causal replay
tests/breaker_blocks/         # Strict conversions, rejection facts, exact timing, causal replay
tests/ote/                    # Retracement geometry, close classification, timing, causal replay
tests/signal_engine/          # spot mapping, gates, one-per-setup, provenance, causal replay
tests/indicators/             # exact EMA/RSI/volume values, warmup, provenance, causal replay
tests/setup_attribution/      # taxonomy, BUY-only profiles, outcome-independence, causal replay
tests/performance/            # bucket statistics, rankings, multi-series, causal replay
tests/review/                 # UTC months, sample-size gating, golden text, causal replay
tests/visualization/          # primitives, composer, renderers, no-lookahead, causal replay
tests/backtest/               # datasets, replay ordering, determinism, no-lookahead, isolation
tests/robustness/             # window planning, regimes, degradation, stability, determinism
tests/intelligence/           # patterns, diagnostics, rankings, partitioning, no-lookahead, isolation
tests/signal_eligibility/     # HALAL+threshold gate, long/short/neutral, conflict, causal replay
tests/setup_quality/          # integer awards, HARAM/UNKNOWN gate, threshold flag, causal replay
tests/halal_filter/           # allow/deny, unknown, case, provenance, and causal replay
tests/mtf/                    # HTF eligibility, independent labels, MIXED confluence, causal replay
tests/mitigation_blocks/      # First-interaction geometry, Breaker policy, exact timing, causal replay
tests/fixtures/               # Tiny, explicitly synthetic CSV fixtures
docs/                         # Architecture and methodologies
```

## Development checks

```bash
python -m ruff check .
python -m ruff format --check .
python -m mypy --strict src/smcsignal
python -m pytest
python -m pytest tests/analysis
python -m pytest tests/liquidity
python -m pytest tests/displacement
python -m pytest tests/fvg
python -m pytest tests/order_blocks
python -m pytest tests/premium_discount
python -m pytest tests/mss
python -m pytest tests/breaker_blocks
python -m pytest tests/mitigation_blocks
python -m pytest tests/ote
python -m pytest tests/mtf
python -m pytest tests/halal_filter
python -m pytest tests/setup_quality
python -m pytest tests/signal_eligibility
python -m pytest tests/signal_engine
python -m pytest tests/outcome_tracking
python -m pytest tests/indicators
python -m pytest tests/setup_attribution
python -m pytest tests/performance
python -m pytest tests/review
python -m pytest tests/visualization
python -m pytest tests/backtest
python -m pytest tests/robustness
python -m pytest tests/intelligence
python -m pip check
python -m build
```

Tests are offline, with socket access blocked in the pytest process. Causality
tests cover all prefixes, future-price shocks, delayed/pending pivots, immutable
snapshots, hand-computed event outcomes, and batch/stream/CSV replay consistency.
Environments, caches, builds, and downloaded root `data/` files are Git-ignored.
See the [development guide](docs/development.md).

`smcsignal`, `smcsignal --help`, and `python -m smcsignal --version` remain
informational; they do not load configuration, fetch data, or start analysis.

## Evidence provenance through Phase 22

`LiquidityPool`, `SweepEvent`, `ATRReference`, `DisplacementEvent`, `FVGEvent`, and `OrderBlockEvent` compose the approved immutable provenance
contract. They retain source/producer identity, configuration and consumed-prefix
hashes, raw candle/swing evidence, historical context, true availability instants,
and exact snapshot dependencies. Older pool versions are never edited in place.
`evidence_json` serializes complete raw records without floating-point price loss.

Phase 15 adds `ScoreBreakdown`, `SetupQualityScore`, and `ScoreSnapshot` on the
same contract. The integer 0–100 total uses frozen weights, a HARAM/UNKNOWN hard
gate, missing-evidence zeros, and `publish_threshold` default 75. Quality over
quantity remains: zero passing scores is valid, and there are no signal-count
targets. `threshold_passed` is not a BUY/SELL signal.

Phase 16 adds `SignalEligibility`, `EligibilityDecision`, and
`EligibilitySnapshot` on the same contract. Eligibility requires HALAL plus the
upstream threshold flag. Nested votes produce `LONG_BIAS`, `SHORT_BIAS`, or
`NEUTRAL`. Conflicting evidence stays `NEUTRAL`. Historical decisions never
change. `ELIGIBLE` is not a BUY/SELL signal.

Phase 17 adds `Signal`, `SignalCandidate`, and `SignalSnapshot` on the same
contract. Eligible `LONG_BIAS` maps to `BUY_SIGNAL`. Eligible `SHORT_BIAS` maps
to `BEARISH_AVOID`. HARAM, UNKNOWN, failed threshold, `NEUTRAL`, missing
evidence, and already-published setups map to `NO_SIGNAL`. Historical
publications never change. `BUY_SIGNAL` is not an order.

Phase 18 adds `SignalOutcome`, `AnalyticsSummary`, and `OutcomeSnapshot` on the
same contract. Only `BUY_SIGNAL` publications open outcomes; each lifecycle
version is immutable with a stable `outcome_id` independent of future candles.
Classification is the exact sign of the final close difference over a fixed
horizon; MFE/MAE are running extremes with first-occurrence ties; open
outcomes are never flushed; aggregates cover finalized outcomes only and
undefined rates are `None`. Historical outcome versions never change.

Phase 19 adds consumer-only layers on the same discipline:
`IndicatorSnapshot` frames (context only; decision modules cannot read them),
`SetupAttribution`/`AttributionSnapshot` (outcome-independent labels from
nested facts, digest identities), `PerformanceBucket`/`PerformanceReport`
(descriptive statistics copied from outcome records, never reclassified),
`MonthlyReview`/`MonthComparison` (UTC-month text with sample sizes always),
and `DrawingModel` with its primitives (semantic style tokens, canonical
order, digest identity; SVG/text renderers re-detect nothing).

Phase 20 adds `ReplayDataset`, `BacktestConfiguration`, `ReplayStep`,
`ReplayResult`, `BacktestSignalResult`, and `BacktestReport` on the same
discipline. A replay draws declared candles chronologically through the
unchanged pipeline; the engine never reads a primary candle beyond its draw
cursor, published HTF evidence predates the primary open, and the canonical
frames pass through by identity. `replay_id`/`backtest_id` digest the frozen
configuration, dataset identity, exact candle prefixes, and every published
record; aggregates are the existing Phase 19c report, never recomputed.
Backtests are descriptive research records, not execution, optimization, or
advice.

Phase 21 adds `RobustnessConfig`, `WalkForwardWindow`, `RegimeObservation`,
`SegmentStats`, `WindowResult`, `DatasetRobustnessResult`, and
`RobustnessReport` on the same discipline. Every window is one unchanged
Phase 20 replay of its own candle slice; validation periods never overlap and
no candle after a window's end can change its facts. Outcomes and buckets are
the existing Phase 18/19 records; regime labels are causal annotations that
never generate, veto, or modify anything. Degradation and stability metrics
are exact Decimal deltas, spreads, and STABLE/WEAK/UNDERSAMPLED labels gated
by configurable minimums, with sample sizes always shown and no significance
claims. `report_id` digests the frozen configuration artifacts, dataset
identities, window layouts, and replay ids. Robustness is validation only —
never optimization, selection, or advice.

Phase 22 adds `IntelligenceConfig`, `IntelligenceCell`, and
`IntelligenceReport` on the same discipline. It consumes the Phase 21
validation rows — each counted exactly once — and groups them by signal-time
strategy profiles (the Phase 19 setup combination key, symbol, timeframe, UTC
month, and the Phase 21 regime annotation) with no replay, no regime
re-detection, and no outcome recomputation. Each cell carries exact Decimal
statistics and deterministic WINNER/LOSER/NEUTRAL/UNDERSAMPLED patterns,
STRENGTH/WEAKNESS diagnostics, and contiguous sample-gated ranks. Cell
membership uses signal-time facts only; the intelligence `report_id` is
derived from row facts, never the Phase 21 report id, so appended futures
cannot rewrite an observed cell. Intelligence is observational research only —
never optimization, selection, or advice.

Phase 26A connects the existing Phase 18 analytics foundation to the real
signal publication path without touching it. The connection point is the
immutable `SignalSnapshot` exactly where the Phase 17
`SignalEngineAnalyzer.update()` publishes it; `smcsignal.analytics` is a new
downstream-only leaf (nothing upstream imports it) containing
`AnalyticsObserver`, `ObservedSignalEngine`, `SignalObservation`,
`StrategyStats`, `MonthlySummary`, and `MonthlyReport`. Every published
`BUY_SIGNAL` opens exactly one initial Phase 18 `SignalOutcome` version with
`OPEN` status via the existing `open_outcome()` mapping; observation is
idempotent per outcome identity, so replaying a publication can never create a
second outcome or double-count a signal. Only the real market outcome
evaluator (later candles of the signal's own series, through the Phase 18
`advance_outcome()` machinery) may transition `OPEN` to `WIN`, `LOSS`, or
`FLAT` (breakeven); finalized versions are recorded exactly once per outcome
identity, a conflicting duplicate is rejected, and statistics are the frozen
Phase 18 `aggregate()` arithmetic — never recomputed. `DeliveryState` is a
transport fact and is never interpreted as a trade outcome: a Telegram
`DELIVERED` receipt can never become a `WIN`. Documented limitation: the
current architecture has no live market feed after publication, so an outcome
stays `OPEN` — never flushed, never expired — until a horizon evaluation
exists. Analytics never alters signal generation, SMC/ICT logic, halal
filtering, confidence, delivery, or strategy decisions.

Phase 26B closes the outcome loop with `SignalOutcomeLifecycle` and
`LifecycleStep` in the same downstream-only `smcsignal.analytics` leaf. The
lifecycle is pure composition of frozen pieces and owns no arithmetic of its
own: each real engine frame is first consumed by the unchanged Phase 18
evaluator (so sequence violations raise atomically before any ledger change),
then its publication fact is observed, and only then are the evaluator's
`completed` finals recorded into the single authoritative ledger that feeds
`StrategyStats` and the UTC `MonthlyReport` through the frozen Phase 18
`aggregate()`. Per-frame order is pinned — evaluate, observe, finalize — which
makes observe-before-finalize structural: a BUY published at frame `i` can
finalize no earlier than frame `i + horizon_bars`, and no final ever
references a candle after the consumed frame. One lifecycle describes exactly
one series (a fresh observer and a fresh evaluator sharing one
configuration); multiple series mean multiple lifecycles composed by the
caller. The lifecycle adds no persistence, clock, network, scheduler, run
loop, delivery integration, fleet orchestration, backtest identity, or new
outcome semantics, and it is proven equivalent to the manual Phase 26A bridge
across WIN, LOSS, BREAKEVEN, mixed, and open-only histories.

Phase 26C adds the deterministic ledger snapshot and restore layer
(``LedgerSnapshot``, ``snapshot_ledger``, ``ledger_bytes``,
``load_ledger_bytes``, ``RestoredAnalyticsLedger``) in the same
downstream-only leaf. It projects the observer's public read views into
canonical, content-addressed bytes through the existing evidence canon (no
second serialization convention) and restores them through the actual frozen
model constructors, so load-time validation is the frozen Phase 18/26A
validation itself. The embedded snapshot identity is always recomputed on
load and never trusted; tampered, truncated, or malformed bytes are rejected.
The phase is bytes only — no file, database, network, clock, scheduler, or
run loop; writing bytes to storage belongs to a future phase. Restored
ledgers are read-only historical facts: they accept no new observations, no
new finals, and no frames, and they resume no evaluator; evaluator
continuation after a restart remains a future phase by design. Delivery state
never participates.

Phase 26D adds the durable ledger store (`smcsignal.persistence`:
`LedgerStore` protocol, `MemoryLedgerStore`, `FileLedgerStore`) — the
repository's first writer, placed deliberately outside the IO-free analytics
leaf. The store consumes only the Phase 26C public API: writes are exactly
`ledger_bytes(snapshot)` and reads delegate entirely to `load_ledger_bytes`,
so no second format, digest, or validation exists. One validated key maps to
one file under the store root; keys are checked against a closed charset and
can never traverse out of the root. Writes are atomic — a sibling temporary
file swapped into place with `os.replace` — so a reader sees either the
complete previous snapshot or the complete new one, and a failure before the
swap leaves no residue. The store resumes nothing: loading yields a Phase 26C
snapshot (a read-only restored ledger), never a live lifecycle or evaluator.
No scheduler, polling, feed, clock, fleet, monitoring, delivery, database,
network, encryption, compression, retention, rotation, or version history is
introduced.

Phase 26E adds ledger recovery and continuation
(`smcsignal.analytics.recovery`: `recover_lifecycle`, `RecoveredLedger`) — a
verified, replay-based restart. Given the series' regenerated historical
frames and the expected Phase 26C snapshot (typically loaded from the Phase
26D store), recovery rebuilds a fresh Phase 26B lifecycle under the
snapshot's own configuration, replays those frames through the unchanged 26B
machinery, and returns the live lifecycle for continuation only when the
reconstructed Phase 26C snapshot equals the expected one exactly — records,
OPEN and finalized outcomes, ordering, settings, configuration hash, and
content-addressed identity. It is replay, not checkpointing: the Phase 18
evaluator stays frozen and opaque, no evaluator internals are serialized, and
no ledger state is merged. Any mismatch fails atomically — no partially
recovered lifecycle escapes and the expected snapshot is never mutated.
Recovery performs no IO, no clock, no network, and imports nothing from
persistence, delivery, monitoring, or data; persistence remains the
caller-side snapshot source and the dependency direction stays
`persistence -> analytics`. No scheduler, run loop, feed, websocket, polling,
fleet, multi-series orchestration, monitoring, Telegram, database, remote
persistence, encryption, compression, retention, history, or evaluator
serialization is introduced.

## Phase boundary

No session strategy, SELL/SHORT trades, charts, or Telegram is implemented.
There is no authentication, order execution, leverage/margin/shorting, live
trading, or trading-performance claim. Phase 20 backtesting is offline
historical replay of published facts only — never execution or advice.
Phase 21 robustness is offline walk-forward validation of those same
published facts — never optimization, selection, or advice. Phase 22
strategy intelligence is offline observational reporting over those validated
facts — never optimization, selection, or advice.

“Halal” remains a design goal, **not a Sharia certification**. The code performs
no religious screening and offers no investment advice or guarantee of profit.

[Repository](https://github.com/jamoliddinov2025-bit/halal-smc-ict-signal-bot1) ·
[Architecture](docs/architecture.md) · [Documentation index](docs/README.md)

**Stop after Phase 22. Phase 23 requires explicit approval.**
