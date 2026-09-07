# Architecture — Phase 2

## Data flow

```text
Explicit TOML file / MarketDataConfig
                  |
          create_data_provider
             /           \
     CsvDataProvider   BinancePublicDataProvider
      local CSV        public HTTPS GET /api/v3/klines
             \           /
         six-field source mappings
                  |
           normalize_ohlcv
                  |
      OHLCVBatch(candles, report)
```

Both providers implement the abstract `DataProvider.fetch_ohlcv()` contract. A
provider is configured for one declared symbol/timeframe. Importing packages and
constructing providers perform no data fetch. `fetch_ohlcv()` is the explicit I/O
boundary; there is no automatic fallback to another source or synthetic data.

## Modules

| Module | Responsibility |
| --- | --- |
| `data/base.py` | Provider interface |
| `data/config.py` | Frozen settings, type/range checks, source selection values, explicit TOML loading |
| `data/factory.py` | Construct the configured provider without fetching |
| `data/models.py` | Immutable OHLCV records, serialization, batches, cleaning reports |
| `data/validation.py` | Schema, UTC/decimal conversion, missing-value policy, sorting, duplicates, history window |
| `data/csv.py` | Strict local CSV ingestion and independent snapshot replay iterators |
| `data/binance.py` | Read-only public Spot klines, bounded HTTP response, timeout, closed-candle cutoff |
| `data/errors.py` | Distinct configuration, validation, provider, HTTP, and rate-limit errors |
| `cli.py` | Informational status/help/version only; no implicit data fetch |

The public API is exported through `smcsignal.data`. The `src` layout and editable
installation prevent tests from depending on accidental repository-root imports.
Runtime dependencies remain Python's standard library only. Binance transport
and clock are injectable for offline, deterministic tests.

## Configuration versus metadata

Only the `[market_data]` table is consumed. Project/scope/safety tables in the
example describe product intent; they are not runtime trading controls. Unknown
market-data settings are errors, not silently ignored options. The provider code
has no authentication, order submission, or derivatives endpoints to enable.

Configuration holds the declared symbol/timeframe; the canonical candle has only
the six OHLCV fields. Keep a batch with its provider's configuration when retaining
provenance. A CSV cannot prove its declared market identity from those six columns.

## Testing and packaging

`tests/data/` covers normalization, configuration, CSV replay, Binance mapping,
closure cutoffs, and transport failures. `tests/conftest.py` blocks socket access
in the pytest process. All provider tests use synthetic fixtures or injected
transports; no live exchange calls or credentials are used.

The source distribution includes docs, explicit example configurations, tests,
and tiny labeled fixtures. The wheel includes the runtime package and typing
marker, not repository-local configuration or tests. Build outputs and downloaded
datasets remain Git-ignored.

## Boundaries and phase gate

This phase is **data ingestion and validation**, not analysis. It implements none
of: trend detection, SMC, BOS, CHoCH, liquidity analysis, signals, charts, Telegram,
a halal filter, backtesting, paper trading, or order execution. “Halal” remains a
design goal, not certification; this code makes no asset-eligibility decision.

Phase 2 ends with the full offline suite, packaging checks, commit, push, and
remote SHA verification. Stop here; Phase 3 requires explicit approval.
