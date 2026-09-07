# Market data configuration

Phase 2 explicitly loads `[market_data]` using `smcsignal.data.load_data_config`.
The informational CLI does not load settings or fetch data automatically.

- `example.toml`: offline CSV example using a tiny **synthetic** test fixture.
- `binance-public.example.toml`: explicit unauthenticated Binance Spot REST example.

## Settings

| Key | Contract |
| --- | --- |
| `symbol` | Required exchange symbol, e.g. `BTCUSDT`; 2–30 ASCII alphanumeric characters, normalized to uppercase. Slash/derivative notation is rejected. |
| `timeframe` | Required, case-sensitive supported interval; `1m` means minute, `1M` means month. |
| `data_source` | Required: `csv` or `binance_public`; no fallback or automatic selection. |
| `history_limit` | Required integer from 1 to 1000. Return **up to** this many candles, ascending, from the latest validated window. |
| `csv_path` | Required only for CSV and forbidden for Binance. Relative TOML paths resolve against the TOML directory. |
| `missing_value_policy` | `error` (default) or explicit `drop`; never fill prices or fabricate candles. |
| `timeout_seconds` | Finite number greater than zero and at most 60; default 10. Applies to HTTP I/O, not CSV. |

Unknown keys inside `[market_data]`, including credentials, are rejected. The four
required settings cannot be silently supplied by defaults. Direct `MarketDataConfig`
construction resolves relative CSV paths against the working directory at construction.
Files are not opened until configuration loading or an explicit provider fetch.

The existing `[project]`, `[scope]`, and `[safety]` tables document intent only and
are not consumed by the data loader. In particular, `authenticated_exchange_access`
is not a toggle for public data; no authenticated/execution implementation exists.
No halal filter or asset-eligibility decision is implemented.

## Secrets and local data

Do not add API keys, tokens, passwords, or account information. Binance public
klines need no exchange credentials. Local `config/local*.toml` and
`config/secrets*.toml` are ignored, but ignoring a path is not secret management.
Downloaded data under the root `data/` directory is also ignored.

The source distribution explicitly includes only the three configuration files
listed in `MANIFEST.in`, not arbitrary local TOML files. See the
[market data methodology](../docs/market-data-methodology.md) for validation,
replay, missing-data behavior, and important limits.
