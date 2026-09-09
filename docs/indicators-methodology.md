# Supporting indicators methodology — Phase 19a

## Purpose and scope

Phase 19a answers one context question: **what do classical smoothing and
momentum series look like over the candles the pipeline has already
published?** It consumes existing **`DisplacementSnapshot`** frames only. The
original nested liquidity/ATR objects remain the original instances, earlier
analyzers are not rerun, and upstream evidence IDs are never rewritten.

Indicators are **context and visualization support only**. They can never
generate, gate, veto, or weight a signal. No decision module (Phases 3–18)
imports or reads indicator values; an import-graph test enforces this. There
are no thresholds, crossovers, divergence rules, or oscillator strategies
here. VWAP and ADX remain deferred and out of scope.

## Indicator set

| Indicator | Definition | First value |
| --- | --- | --- |
| EMA(period) per `ema_periods` | SMA seed over the first `period` closes, then `ema = prior + k * (close − prior)` with `k = 2 / (period + 1)` | candle index `period − 1` |
| RSI(period) Wilder | mean gain/loss seed over `period` changes, then Wilder smoothing `avg = (prior * (period − 1) + change) / period`; `RSI = 100 − 100 / (1 + avg_gain / avg_loss)` | candle index `period` |
| Volume average | simple mean of the last `volume_average_period` volumes **including the current candle** | candle index `period − 1` |
| Volume ratio | `volume / volume_average` (exact descriptive ratio) | with the average |
| ATR | **reused verbatim from Phase 5** — `frame.atr_current.value` | Phase 5 timing (`atr_period` candles) |

Boundary rules: an all-gain window gives RSI 100, an all-loss window 0, and a
perfectly flat window exactly 50 (division is never attempted). Warmup candles
carry `None`; `None` is never coerced to zero.

## Exact arithmetic

All arithmetic uses 50-significant-digit `ROUND_HALF_EVEN` Decimal contexts —
the approved displacement discipline. Prices and volumes are the exact
published Decimals; no float conversion exists anywhere in the layer. The EMA
multiplier `2 / (period + 1)` and every update are computed in that context,
and tests wrap their reference computations in the same context (default
28-digit contexts silently corrupt expectations).

## Configuration

```toml
[indicators]
enabled = true
ema_periods = [20, 50]
rsi_period = 14
volume_average_period = 20
```

The table must contain exactly those four keys. `enabled` must be true.
`ema_periods` is a nonempty strictly-increasing tuple of integers from 1 to
1000; `rsi_period` and `volume_average_period` are integers from 1 to 1000.
Unknown keys — including `threshold`, `gate`, `signal_generation`, or any
decision knob — are rejected.

## Determinism, provenance, and no look-ahead

- `IndicatorSnapshot` is immutable and carries `settings`, the original
  upstream frame, `ema_values` aligned to `ema_periods`, `rsi`, reused `atr`,
  `volume`, `volume_average`, `volume_ratio`, and `candle_index`.
- Producers are frozen: `indicator-frame` per candle. The configuration
  artifact hashes once per analyzer and declares `signal_generation`, `gate`,
  `veto`, `threshold`, and `execution` **false** and `atr_source` as
  `phase5_reused`.
- The analyzer consumes frames from index zero, consecutively; state classes
  (`EMACalculator`, `RSICalculator`, `VolumeAverageCalculator`) are immutable
  and return `(next_state, value)` tuples. A failed update leaves state
  byte-identical.
- For identical input history and configuration: every prefix equals the
  corresponding full-series prefix, and batch, streaming, and chunked replays
  produce identical output sequences. Appending future candles never changes
  earlier indicator values.

## API

```python
from smcsignal.analysis import (
    IndicatorAnalyzer,
    IndicatorsConfig,
    analyze_indicators,
)

engine = IndicatorAnalyzer(IndicatorsConfig())
for displacement_frame in displacement_frames:
    snapshot = engine.update(displacement_frame)

snapshots = analyze_indicators(displacement_frames, IndicatorsConfig())
```

## Hand-computed synthetic example

`config/indicators.example.toml` reuses the synthetic Phase 13–17 history
(rising closes 20 → 36 on 15m). With a small configuration `ema_periods =
(3, 5)`, `rsi_period = 3`, `volume_average_period = 4`: EMA(3) first appears
at candle 2 with value 21 and follows the exact 50-digit ladder; EMA(5) seeds
at candle 4 with SMA 22; RSI(3) first appears at candle 3 with value 100
(strictly rising closes); uniform volumes give average 1 and ratio 1. With the
default configuration the 17-candle fixture is still inside EMA(20) warmup
(all `None`) while RSI(14) starts at candle 14 and ATR (period 3) is reused
from Phase 5 with value 2.

## Known limitations and stop boundary

- Indicators are replay-local context; there is no persistence and no
  cross-process state.
- RSI and volume ratios are not price-anchored and therefore never appear in
  price drawings (only EMA and ATR overlays are price-scaled).
- Indicators never become signals, gates, or vetoes; the import-graph test
  keeps every Phase 3–18 decision module free of indicator reads.

No live trading, orders, execution, Telegram transport, PNG/raster output,
optimization, or self-modification is implemented.

**Stop after Phase 19. Phase 20 requires explicit approval.**
