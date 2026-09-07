# Market data methodology

## 1. Purpose and provider contract

Phase 2 establishes a consistent, read-only market data boundary. Each provider
binds a validated `MarketDataConfig` to one symbol/timeframe and implements:

```python
batch = provider.fetch_ohlcv()
# batch.candles: tuple[OHLCV, ...], ascending by opening time and unique
# batch.report: immutable ValidationReport
```

Results contain **up to** `history_limit` candles. Empty input is an explicit empty
batch, not a fallback signal or fabricated dataset. Configuration, invalid data,
and source failures raise distinct `MarketDataError` subclasses. Nothing retries,
switches sources, or returns cached/sample data behind the caller's back.

## 2. Canonical OHLCV schema

Every candle has exactly these fields, in this order:

| Field | Canonical type and meaning |
| --- | --- |
| `timestamp` | Timezone-aware UTC `datetime`, **candle opening time**, millisecond precision, not before the Unix epoch |
| `open` | Finite positive `Decimal` |
| `high` | Finite positive `Decimal`, at least open/low/close |
| `low` | Finite positive `Decimal`, at most open/high/close |
| `close` | Finite positive `Decimal` |
| `volume` | Finite nonnegative `Decimal`; Binance maps **base-asset volume**, not quote volume |

Input timestamps may be aware ISO-8601 strings (including `Z` or explicit offsets),
aware datetimes, or nonnegative integer **epoch milliseconds**, including integer
strings. Units are never guessed: Unix seconds or Binance archive microseconds
must be converted by the caller before import. Naive dates/times, fractional
numeric epoch timestamps, and sub-millisecond candle times are rejected.

Strings and decimals preserve source precision without binary-float rounding.
Finite Python int/float prices are also accepted through `Decimal(str(value))`,
but already-lost float precision cannot be recovered; source decimal strings are
preferred. Boolean values are not prices, volumes, or epoch timestamps.

`candle.to_record()` returns the same six columns as JSON/CSV-safe strings, using
an ISO-8601 UTC timestamp with milliseconds and exact decimal strings. No numeric
rounding or price arithmetic is performed.

## 3. Validation and cleaning order

1. Require exactly the six canonical fields on every row. Missing/extra schema
   columns are errors even when the missing-value policy is `drop`.
2. Parse timestamps and all present numerical values. Reject malformed/non-finite
   values and enforce all price bounds and timestamp precision invariants that
   can be checked on present fields, before any missing-row drop.
3. Handle missing cells according to an explicit policy:
   - `error` (default): raise with the row number and missing fields.
   - `drop`: discard the **whole incomplete row** and increment the audit count.
   - Missing tokens are `None`, blank/whitespace strings, case-insensitive
     `nan`/`null`/`none`, and numeric NaN. Infinity is invalid, not missing.
4. Remove numerically identical candles with the same normalized UTC timestamp.
   A conflicting duplicate raises an error; no arbitrary keep-first/keep-last rule.
5. Sort unique valid candles chronologically.
6. Select the latest `history_limit` candles while preserving ascending order.
   Validate the entire input first: an invalid older row is not hidden by a limit.

Missing values are never forward-filled, interpolated, zero-filled, or synthesized.
Whole missing timestamps remain gaps. **This phase does not diagnose expected
cadence/gap counts, resample intervals, or guarantee a continuous time series.**

## 4. Audit report

`ValidationReport` records:

- `input_rows`: source rows considered (including excluded Binance open candles).
- `output_rows`: returned candle count.
- `duplicates_removed`: identical timestamp/value duplicates removed.
- `missing_rows_dropped`: incomplete rows explicitly dropped.
- `rows_trimmed`: valid unique candles outside the requested latest-N window.
- `incomplete_rows_dropped`: Binance candles not closed at the request cutoff.
- `reordered`: whether valid unique input order needed chronological sorting.

For a successful fetch:

```text
input_rows = output_rows + duplicates_removed + missing_rows_dropped
             + rows_trimmed + incomplete_rows_dropped
```

Errors abort the operation; no partially valid batch is returned. Preserve the
report and the provider configuration with any downstream stored dataset.

## 5. CSV ingestion and replay

CSV files must be UTF-8 (an optional BOM is accepted), comma-delimited, with exactly
one of each canonical header. Header order may vary, but names are case-sensitive.
Short/long rows, duplicate headers, bad quoting, and invalid encoding are errors.
A blank physical record is treated as a fully missing row, not silently skipped.
A header-only file is a valid empty dataset; a zero-byte file lacks a schema and
is rejected.

The file is declared by configuration to contain one symbol/timeframe. Those
labels cannot be independently verified from six OHLCV columns. CSV rows are
assumed to be completed historical candles; unlike Binance, CSV has no upstream
close-time metadata or automatic live-candle exclusion. Supply an appropriate
historical file and validate its provenance separately.

```python
from smcsignal.data import CsvDataProvider, load_data_config

provider = CsvDataProvider(load_data_config("config/example.toml"))
for candle in provider.replay():
    print(candle.to_record())
```

`replay()` eagerly validates a snapshot and returns an independent iterator over
exactly the latest-N window used by `fetch_ohlcv()`, oldest first. Calls are
repeatable for unchanged input. Later file edits cannot change an existing
iterator. There is no sleep, simulated exchange, execution, or backtesting engine.
The whole CSV is validated in memory; large-archive streaming is outside Phase 2.
The bundled fixture is explicitly synthetic, not a Binance observation.

## 6. Binance public Spot provider

The fixed endpoint is:

```text
GET https://data-api.binance.vision/api/v3/klines
```

No API key, signature, authenticated account access, or order endpoint is used.
The implementation sends `symbol`, `interval`, `limit`, UTC `timeZone=0`, and
`endTime` set to one millisecond before an injected/local UTC clock snapshot taken
**before** the request. It requests one spare row where the 1000-row cap permits.

The upstream 12-field kline is schema-checked. Fields 0–5 map to OHLCV; field 6
supplies the close-time cutoff. A candle is excluded when its closing millisecond
is at or after the captured cutoff, including current/future candles. This also
handles calendar-month intervals without pretending a month is a fixed duration.
The host clock must be synchronized; this phase does not query exchange server
time or guarantee that an upstream closed candle can never be revised.

One bounded REST page is fetched; **there is no pagination**. At a requested limit
of 1000, an included open candle can leave only 999 closed results. Other source
shortfalls and explicit row drops can also reduce the result. No padding occurs.

Supported intervals: `1s`, `1m`, `3m`, `5m`, `15m`, `30m`, `1h`, `2h`, `4h`, `6h`,
`8h`, `12h`, `1d`, `3d`, `1w`, `1M`. Interval availability and symbol existence are
ultimately decided by Binance; symbol syntax validation is not an asset filter.

HTTP I/O uses a configurable timeout and a 2 MB response-body cap. HTTP failures
preserve status and `Retry-After`; 429/418 raise `RateLimitError`. No automatic
retry/backoff loop is started: callers must respect upstream limits before
retrying. Timeout, DNS, malformed JSON, upstream error objects, blocked-region
responses, and bad candle data do not become an empty success.

The timeout bounds individual urllib blocking I/O operations, not a strict
end-to-end deadline for a server that streams indefinitely. No caching, WebSocket,
historical archive import, or service scheduling is implemented.

Upstream reference: [Binance Spot REST market-data documentation](https://developers.binance.com/en/docs/catalog/core-trading-spot-trading/api/rest-api/market#klines).

## 7. Verification scope

All unit/provider tests are offline. The pytest process blocks socket connections;
Binance tests use injected transport responses and clocks, including the actual
urllib request boundary with a fake response. These verify protocol mapping,
validation, replay, configuration, and error handling—not live endpoint availability,
market accuracy, strategy performance, or religious compliance.

This page describes the unchanged data foundation introduced in Phase 2.
Phase 3 adds a separate [confirmed market structure layer](market-structure-methodology.md)
with [historical trend](trend-methodology.md) and [no-look-ahead guarantees](no-look-ahead.md).
It does not add liquidity pools, sweeps, displacement, fair value gaps, order blocks,
premium/discount, a signal engine, charts, Telegram, or a halal filter.
