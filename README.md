# Professional Halal SMC/ICT Spot Signal Bot

**Status: Phase 2 — market data foundation only. No strategy or signal logic.**

A Python foundation for a future spot-only, long-only signal tool. The current
package reads and validates OHLCV from local CSV files or Binance's unauthenticated
public **Spot** endpoint. It provides deterministic CSV replay, typed configuration,
and auditable cleaning. It does not trade or make asset-eligibility decisions.

## Requirements and installation

Python **3.11+**; no third-party runtime dependencies. On Linux/macOS:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e ".[dev]"
python -m pytest
```

On Windows, use `python -m venv .venv` and activate with
`.venv\Scripts\Activate.ps1` in PowerShell.

## Offline market data example

The default example uses five **synthetic** candles, not real Binance history:

```python
from dataclasses import asdict

from smcsignal.data import create_data_provider, load_data_config

config = load_data_config("config/example.toml")
provider = create_data_provider(config)  # no fetch during construction
batch = provider.fetch_ohlcv()  # explicit local CSV read
for candle in batch.candles:
    print(candle.to_record())
print(asdict(batch.report))
```

For sequential replay, construct `CsvDataProvider(config)` and iterate
`provider.replay()`. It yields the same latest-N snapshot oldest-first, without
sleeping or executing anything.

## Binance public Spot data

Network access occurs only when explicitly fetching from this source:

```python
from smcsignal.data import create_data_provider, load_data_config

config = load_data_config("config/binance-public.example.toml")
batch = create_data_provider(config).fetch_ohlcv()
```

No API keys or account access are required. Requests use one bounded public REST
page, a timeout, and a captured closed-candle cutoff. A result can contain fewer
than the configured limit. HTTP/rate-limit/network failures raise actionable errors;
there are no automatic retries or source fallbacks. Live availability is not
asserted by the offline test suite.

## Data contract

- Exactly `timestamp`, `open`, `high`, `low`, `close`, `volume` per candle.
- UTC, timezone-aware **opening** timestamps; integer epoch milliseconds or aware
  ISO-8601 input; no guessing between seconds, milliseconds, and microseconds.
- Finite `Decimal` OHLCV values; positive prices, nonnegative base-asset volume,
  and consistent high/low bounds.
- Ascending unique results: exact duplicates removed, conflicting duplicates rejected.
- Strict missing-value errors by default; optional audited whole-row `drop`.
- No fabricated candles, interpolation, forward filling, resampling, or gap repair.

See [methodology and limits](docs/market-data-methodology.md), especially CSV
provenance/closure assumptions, Binance page limits, and the cleaning report.

## Configuration

`[market_data]` requires `symbol`, `timeframe`, `data_source`, and `history_limit`.
CSV additionally requires `csv_path`. Optional settings control missing-cell policy
and HTTP timeout. Paths in TOML resolve relative to the configuration file.
Unknown market-data settings and credentials are rejected.

[Configuration guide](config/README.md) · [Offline example](config/example.toml) ·
[Public Binance example](config/binance-public.example.toml)

The informational CLI remains available:

```bash
smcsignal
smcsignal --help
python -m smcsignal --version
```

It does not load settings, fetch data, or start a worker automatically. Market data
operations currently use the Python API shown above.

## Project layout

```text
.
├── .editorconfig
├── .gitignore
├── MANIFEST.in
├── README.md
├── pyproject.toml
├── config/
│   ├── README.md
│   ├── binance-public.example.toml
│   └── example.toml
├── docs/
│   ├── README.md
│   ├── architecture.md
│   ├── development.md
│   └── market-data-methodology.md
├── src/smcsignal/
│   ├── __init__.py
│   ├── __main__.py
│   ├── cli.py
│   ├── py.typed
│   └── data/
│       ├── __init__.py
│       ├── base.py
│       ├── binance.py
│       ├── config.py
│       ├── csv.py
│       ├── errors.py
│       ├── factory.py
│       ├── models.py
│       └── validation.py
└── tests/
    ├── __init__.py
    ├── conftest.py
    ├── test_cli.py
    ├── test_config_template.py
    ├── test_package.py
    ├── data/
    │   ├── __init__.py
    │   ├── conftest.py
    │   ├── test_binance.py
    │   ├── test_config.py
    │   ├── test_csv.py
    │   └── test_validation.py
    └── fixtures/
        ├── README.md
        └── ohlcv.csv
```

## Development checks

```bash
python -m ruff check .
python -m ruff format --check .
python -m mypy --strict src/smcsignal
python -m pytest
python -m pip check
python -m build
```

All provider tests are offline and use synthetic fixtures/injected transports.
Environments, caches, build outputs, and downloaded root `data/` files are ignored.
See the [development guide](docs/development.md) and [architecture](docs/architecture.md).

## Explicit exclusions and next phase

No trend detection, SMC, BOS, CHoCH, liquidity analysis, signals, charts, Telegram,
or halal filter is implemented. There is no backtesting, paper trading, leverage,
margin, short selling, authentication, or order execution.

“Halal” remains a design goal, **not a Sharia certification**. This code performs
no religious screening and offers no investment advice or guarantee of profit.

[Repository](https://github.com/jamoliddinov2025-bit/halal-smc-ict-signal-bot1) ·
[Documentation](docs/README.md)

**Stop after Phase 2. Phase 3 requires explicit approval.**
