# Professional Halal SMC/ICT Spot Signal Bot

**Status: Phase 4 — liquidity pools and sweep detection, with immutable provenance.**

A Python foundation with a validated OHLCV data layer and incremental market
structure analysis. It reads local CSV or unauthenticated Binance public **Spot**
data, confirms fractal swings without backdating them, and produces immutable
per-candle trend/structure, liquidity-pool, and sweep snapshots. It does **not** generate trading signals,
execute orders, or make asset-eligibility decisions.

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

## Phase 4 offline liquidity and sweep example

The new example uses twelve **synthetic** candles, not real exchange observations:

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

## Streaming API

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

## Evidence provenance in actual Phase 4 outputs

`LiquidityPool` and `SweepEvent` now compose the approved immutable provenance
contract. They retain source/producer identity, configuration and consumed-prefix
hashes, raw candle/swing evidence, historical context, true availability instants,
and exact snapshot dependencies. Older pool versions are never edited in place.
`evidence_json` serializes complete raw records without floating-point price loss.

The [evidence contract](docs/evidence-provenance-contract.md) also preserves the
**deferred** scoring policy: future configurable threshold default 75, quality over
quantity, valid zero-signal outcomes, and no signal-count targets. No scoring or
active threshold configuration is implemented in Phase 4.

## Phase boundary

No displacement, fair value gaps, order blocks,
premium/discount, signal engine, charts, Telegram, halal filter, or scoring is implemented.
There is no authentication, order execution, leverage/margin/shorting, backtesting,
or trading-performance claim.

“Halal” remains a design goal, **not a Sharia certification**. The code performs
no religious screening and offers no investment advice or guarantee of profit.

[Repository](https://github.com/jamoliddinov2025-bit/halal-smc-ict-signal-bot1) ·
[Architecture](docs/architecture.md) · [Documentation index](docs/README.md)

**Stop after Phase 4. Phase 5 requires explicit approval.**
