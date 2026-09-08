# Halal asset filter methodology — Phase 14

## Scope and inputs

Phase 14 answers one eligibility question: **does the caller-supplied registry
classify this series symbol as HALAL, HARAM, or UNKNOWN?**

It is a configuration-driven registry layer. It does not fetch the internet,
call external APIs, scrape screening sites, issue a fatwa, or make autonomous
religious decisions. The operator is responsible for the list. The engine only
enforces that list.

`HalalFilterAnalyzer.update` consumes an existing Phase 13 **`MTFSnapshot`**.
Nested OTE, PD, OB, FVG, displacement, liquidity, and structure objects remain
the original instances. Upstream evidence IDs are never rewritten.

Classifications:

| Label | Meaning in this phase |
| --- | --- |
| `HALAL` | The symbol is present on the configured allow list. |
| `HARAM` | The symbol is present on the configured deny list. |
| `UNKNOWN` | The symbol is not present on the active list. |

Only `HALAL` is eligible for **future** signal-generation phases. Those phases
are not implemented here. `UNKNOWN` must never be silently treated as `HALAL`.
`HARAM` is never eligible. Deny-list mode never emits `HALAL`: unlisted assets
stay `UNKNOWN`.

## 1. Registry modes

Default configuration:

```toml
[halal_filter]
mode = "allow_list"

allowed_assets = [
  "BTCUSDT",
  "ETHUSDT",
  "BNBUSDT",
  "SOLUSDT"
]
```

`allow_list`:

- listed assets = `HALAL`
- others = `UNKNOWN`

`deny_list`:

- denied assets = `HARAM`
- others = `UNKNOWN`

Allow-list TOML contains exactly `mode` and `allowed_assets`. Deny-list TOML
contains exactly `mode` and `denied_assets`. Unknown keys, including score,
signal, or scraping settings, are rejected. Direct Python construction uses
`HalalFilterConfig()` / `HalalFilterConfig(FilterMode.DENY_LIST, (), (...))`.

The active list must be a nonempty tuple of exchange symbols matching the
market-data contract: 2–30 alphanumeric characters, normalized to uppercase.
Duplicates after normalization are rejected. Allow-list configs cannot carry
denied assets; deny-list configs cannot carry allowed assets.

This default list is a **software starting registry**, not a claim that those
assets are religiously approved.

## 2. Classification and eligibility

```text
normalize(symbol) → uppercase alphanumeric identity
allow_list + listed   → HALAL   (eligible)
allow_list + absent   → UNKNOWN (ineligible)
deny_list  + listed   → HARAM   (ineligible)
deny_list  + absent   → UNKNOWN (ineligible)
```

`classify_asset(symbol, config)` is the pure registry lookup. Invalid symbols
are errors, not `UNKNOWN`. Case differences such as `btcusdt` and `BTCUSDT`
classify identically; series identity is not rewritten.

`HalalSnapshot.eligible` is true **if and only if** the classification is
`HALAL`. The analyzer still publishes ineligible snapshots so UNKNOWN/HARAM
remain explicit. Future signal phases, once approved, must require
`eligible is True` and must not coerce UNKNOWN into HALAL.

Reasons are mechanical, not religious rulings:

- `listed_in_allow_list`
- `absent_from_allow_list`
- `listed_in_deny_list`
- `absent_from_deny_list`

## 3. Immutable models, provenance, and API

```python
from smcsignal.analysis import (
    HalalFilterAnalyzer,
    HalalFilterConfig,
    analyze_halal,
    classify_asset,
)

print(classify_asset("BTCUSDT"))
print(classify_asset("ADAUSDT"))

halal = HalalFilterAnalyzer(HalalFilterConfig())
for mtf_frame in mtf_frames:
    result = halal.update(mtf_frame)

# Alternative for a fresh/precomputed iterable:
results = analyze_halal(mtf_frames)
```

- `AssetClassification` — `HALAL`, `HARAM`, `UNKNOWN`
- `HalalDecision` — normalized symbol, mode, reason, eligibility, registry provenance
- `HalalSnapshot` — original MTF frame, reused decision, frame provenance

Records are frozen/slotted and satisfy `ProvenancedEvidence`. The decision is
registry evidence: empty `source_candles`, prefix hash of series/symbol identity
only, no future-file hash. The snapshot reuses the current MTF consumed-prefix
hash, depends on the MTF frame plus the decision, and includes the current
source candle. Producer version is 1.

The same `HalalDecision` object is reused for the bound series. Changing only
the registry changes filter IDs, not upstream MTF IDs.

## 4. Streaming, replay, and no-lookahead contract

Input must start at index zero and remain consecutive, chronological, unique,
and nondecreasing in availability. Series identity cannot change mid-stream.
Local state commits only after validation, model construction, and provenance
succeed. Failed input does not alter the last output or advance the index.

Classification does not read future candles, prices, or HTF context. Appending
or replacing later MTF frames cannot change prior registry decisions or
snapshot identities.

For identical fixed-origin MTF history and configuration:

- Every prefix equals the corresponding full-series prefix, including labels,
  eligibility, decision IDs, and provenance hashes.
- Batch, streaming, and arbitrary chunks produce identical results.

Tests are software consistency checks, not a Sharia audit or performance
backtest.

## 5. Hand-computed synthetic example

`config/halal-filter.example.toml` uses the Phase 13 synthetic 15m/1h/4h
history. Default allow-list results:

| Symbol | Mode | Classification | Eligible |
| --- | --- | --- | --- |
| `BTCUSDT` / `btcusdt` | allow_list | HALAL | true |
| `ETHUSDT` | allow_list | HALAL | true |
| `ADAUSDT` | allow_list | UNKNOWN | false |
| `XYZUSDT` | deny_list of `XYZUSDT` | HARAM | false |
| `BTCUSDT` | deny_list of `XYZUSDT` | UNKNOWN | false |

These are registry lookups against the configured lists, not exchange
observations, entries, or expected returns.

## 6. Known limitations and stop boundary

- Enforces only the supplied allow/deny registry. It does not rank tokens,
  consult scholars, download screening databases, or infer protocol activity.
- Deny-list mode cannot approve assets. Absence from a deny list is UNKNOWN.
- Core state holds one decision plus the latest snapshot; retained outputs
  inherit nested upstream history. There is no bounded-total-memory guarantee.
- Existing source-truth, arrival-time, fixed-history, and caller-owned archive
  assumptions remain in force.

No Setup Quality Score, 75+ threshold, signal generation, BUY/SELL, probability,
profitability claims, entries, stops, targets, risk/reward, position sizing,
trade management, Telegram, scraping, live trading, credentials, futures,
leverage, backtesting, monthly statistics, AI optimization, portfolio
management, or strategy ranking is implemented in this phase.
Phase 15 implements the integer setup quality score separately.
