# Visualization methodology — Phase 19e

## Purpose and scope

Phase 19e renders **already-published facts** as deterministic drawings. It
consumes finished primary `OTESnapshot` replays plus optional aligned
`SignalSnapshot`, `IndicatorSnapshot`, and `OutcomeSnapshot` replays, projects
them onto drawing primitives, and renders those primitives as **SVG or plain
text**. The composer never reruns a detector, never reclassifies an outcome,
and never invents a fact. Visualization is consumer-only: no decision module
imports it, and drawings never feed back into signals.

SVG output is vector XML text; there is **no PNG/raster output and no image
library**. The layer is stdlib-only.

## Drawing primitives

Five immutable primitives with exact Decimal coordinates and semantic
`StyleToken` roles (`bullish`, `bearish`, `neutral`, `reference`, `overlay`):

| Primitive | Represents |
| --- | --- |
| `LevelLine` | horizontal price level (liquidity pool reference price, equilibrium midpoint) |
| `ZoneRect` | price zone (FVG, order block, dealing range, OTE zone) |
| `EventMarker` | candle-anchored event (BUY signal, sweep, displacement, finalized outcome) |
| `TextAnnotation` | short note (the BUY's score) |
| `IndicatorOverlay` | one price-scaled indicator point (EMA values, reused ATR) |

The **drawing specification is the model**. Models carry tokens, never
colors; renderers map tokens deterministically (`COLORS` in `svg.py`, `MARKS`
in `text.py`). The model layer contains no color values at all.

## Canonical ordering and identity

Primitives are stored in one deterministic order: levels, zones, markers,
annotations, overlays — each group sorted by its own coordinates
(price, index, label). Any other order is rejected by the model.
`drawing_id = "drawing:" + sha256({methodology, settings, symbol, timeframe,
candle references, primitives})` — stable across renders and independent of
rendering. `candles` are the copied `ObservedCandle` records, from index zero
consecutive and chronological; primitive indices must lie inside the candle
window.

## Fact-to-primitive mapping

| Published fact | Primitive |
| --- | --- |
| `LiquidityPool.pool_updates` (first version per pool ID) | `LevelLine` at the reference price |
| `SweepEvent` | `EventMarker` at the breach candle and extreme price |
| `DisplacementEvent` | `EventMarker` at the detection candle close |
| `FVGEvent` | open-ended `ZoneRect` over the gap boundaries |
| `OrderBlockEvent` | open-ended `ZoneRect` over the zone boundaries |
| confirmed `DealingRange` / `Equilibrium` | `ZoneRect` (bounded) / `LevelLine` |
| confirmed `OTEZone` | open-ended `ZoneRect` |
| `BUY_SIGNAL` frame | `EventMarker` + score `TextAnnotation` at the signal close |
| completed `SignalOutcome` | `EventMarker` at the final candle/close (`WIN` bullish, `LOSS` bearish, `FLAT` neutral); open outcomes draw nothing |
| `IndicatorSnapshot` | `IndicatorOverlay` points for EMA values and reused ATR |

RSI and volume ratios are **not price-anchored** and are never drawn.
Laterally arriving pool versions draw one level per pool ID. Facts are drawn
at the frame where they became available.

## Renderers

- `render_svg(model)` — one standalone SVG document: white background,
  monospace title with symbol/timeframe/candle count and a "not advice"
  disclaimer, one candlestick per candle (green up / red down), dashed
  levels, translucent zones, circular markers with labels, square overlay
  points, Decimal axis bounds. Output parses as well-formed XML; every
  numeric attribute is a plain decimal literal (no scientific notation,
  NaN, or infinity).
- `render_text(model)` — a character grid: `text_rows` price bands from the
  model domain, one column per candle, `#`/`.` for closes, `-` levels, `=`
  zones, `^ v o * +` tokens, plus a fixed legend and the sorted primitive
  label list.
- `render_signal_explanation(frame, attribution=None)` — a plain-text
  explanation of one published signal from its own facts: status, candle,
  score vs threshold, all eleven SQS components, engine reasons, and (when
  supplied) the attribution labels and combination key. Descriptive only.

Canvas numbers are the only settings. No theme, color, or style knobs exist.

## Configuration

```toml
[visualization]
enabled = true
svg_width = 800
svg_height = 400
text_rows = 24
```

The table must contain exactly those four keys; `svg_width`/`svg_height` are
integers 100–10000 and `text_rows` 5–200. Unknown keys — including `theme`,
`png`, or any transport knob — are rejected.

## Causal / no-look-ahead rules

Aligned inputs must replay the same series, candles, and length as the
primary replay (identity-checked frame by frame). Drawings are a pure
function of consumed frames: every prefix drawing equals the corresponding
full-series prefix, appending different future candles never changes past
primitives, and a prefix drawing can never show an outcome that finalizes
later. Rendering is a pure function of the model and never mutates it.
Import-graph tests keep the package free of analyzer construction and outcome
reclassification.

## API

```python
from smcsignal.analysis import (
    VisualizationConfig,
    compose_drawing,
    render_svg,
    render_text,
    render_signal_explanation,
)

model = compose_drawing(
    primary_ote_frames,
    signals=signal_frames,
    indicators=indicator_frames,
    outcomes=outcome_frames,
    config=VisualizationConfig(),
)
svg = render_svg(model)
grid = render_text(model)
```

## Hand-computed synthetic example

`config/visualization.example.toml` reuses the synthetic sweep history
(18 candles). The composed drawing carries 63 primitives: two liquidity
levels (21, 23), a bullish FVG zone (26–31, from candle 14), a sell-side
sweep marker at candle 12 (extreme 21), a bullish displacement marker at
candle 13, six BUY markers with score annotations (indices 4, 8, 12, 13, 14,
16), one finalized WIN outcome marker at candle 14 (close 33), and EMA(3)/
EMA(5)/ATR overlay points from Phase 19a. The golden text grid and the
well-formed SVG document are exact-tested; the price domain is the exact
Decimal interval [19, 37].

## Known limitations and stop boundary

- Drawings cover exactly the supplied replay window; there is no persistence,
  live updating, or delivery.
- Overlapping primitives can occlude each other in the text grid; the sorted
  primitive list always states everything that is present.
- No interactive charts, themes, animations, PNG, or Telegram transport
  exists; those remain out of scope.

No live trading, orders, execution, Telegram, optimization, or
self-modification is implemented.

**Stop after Phase 19. Phase 20 requires explicit approval.**
