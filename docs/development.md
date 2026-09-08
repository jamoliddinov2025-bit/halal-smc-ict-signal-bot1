# Development guide

## Prerequisites

- Python 3.11 or newer.
- Git and a Python virtual environment.
- Network access for dependency installation and explicitly requested Binance
  public fetches. CSV replay and all tests work offline.

## Set up an isolated environment

From the repository root on a POSIX shell:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e ".[dev]"
```

On Windows, create the environment with `python -m venv .venv` and activate it
with `.venv\Scripts\Activate.ps1` in PowerShell instead.

The distribution and import package are both named `smcsignal`. Development tools
are optional dependencies; the runtime package depends only on Python's standard
library. Dependency ranges live in `pyproject.toml`; this phase does not include a
lockfile or claim byte-for-byte reproducible dependency resolution.

## Run the informational CLI

```bash
smcsignal
smcsignal --help
smcsignal --version
python -m smcsignal --version
```

The CLI stays informational. Use the Python API in the README to load configuration
and explicitly fetch market data; no CLI data or trading command is implemented.

## Quality checks

Run all commands from the repository root with the virtual environment active:

```bash
python -m ruff check .
python -m ruff format --check .
python -m mypy --strict src/smcsignal
python -m pytest
python -m pip check
python -m build
```

`python -m build` creates a source distribution and a wheel under the ignored
`dist/` directory. Its isolated build environment may download build dependencies.
Do not commit virtual environments, build outputs, caches, or downloaded datasets.

The tests cover:

- Agreement between the package version and installed distribution metadata.
- Console-entry-point registration and the packaged typing marker.
- Status, help, version, and rejection of unsupported CLI options.
- Running `python -m smcsignal` outside the repository's working directory.
- TOML configuration types/ranges, source selection, unknown keys, and relative paths.
- UTC/decimal OHLCV normalization, schema checks, missing cells, conflicting/exact
  duplicates, sorting, range invariants, and latest-N windowing.
- Strict CSV import, deterministic independent replay snapshots, and error handling.
- Binance public request parameters, closed-candle cutoff, response mapping, timeouts,
  HTTP 429/418/other failures, invalid JSON, and bounded response size.
- Configurable delayed fractal highs/lows, strict ties, and confirmation metadata.
- Confirmed-swing trend states/readiness and the documented BOS/CHoCH decision table.
- Prefix/full-series identity, extreme future replacement, pending pivots, and
  immutable historical snapshots against an independent prefix extrema oracle.
- Batch/stream/chunk replay equivalence, invalid-update atomicity, and CSV/Binance
  integration through the existing canonical data layer.

`tests/conftest.py` blocks socket access in the pytest process. Provider tests inject
transport responses and clocks; no live requests, exchange credentials, or external
service availability are part of the test results. Fixtures are labeled synthetic.

They do **not** validate a trading strategy, financial performance, or religious
compliance; those features and assessments do not exist in Phase 8.


## Analysis-only tests and reproducible offline example

```bash
python -m pytest tests/analysis
python -m pytest --collect-only -q tests/analysis
```

The full suite includes the approved earlier tests as well as the new analysis
suite. All parameterized cases count as collected tests. For the hand-computed
example, load both tables from `config/analysis.example.toml`, explicitly fetch
its synthetic CSV with `create_data_provider`, then call `analyze` or feed a fresh
`MarketStructureAnalyzer` one closed candle at a time. The expected event indices
are 6, 8, 12, and 13. See the README for the complete executable snippet.

Use the same starting history, configuration, and canonical candles for prefix
comparisons. Never compare a shifted latest-N batch to a long-lived stream as if
they had identical warm-up state. See [no-look-ahead guarantees](no-look-ahead.md).

## Phase 4 tests and example

```bash
python -m pytest tests/liquidity
python -m pytest --collect-only -q tests/liquidity
```

The twelve-candle `config/liquidity.example.toml` demonstration must emit a buy-side
EQH sweep at index 9 and a sell-side EQL sweep at 10. Tests additionally verify
prefix-stable identities, independently reconstructed input hashes, dependency
graphs, raw serialization, immutable lifecycle versions, strict rejection/gap
boundaries, delayed availability, and data-provider replay consistency.

No scoring is implemented or tested as a feature. No signal-count target is used.

## Phase 5 tests and example

```bash
python -m pytest tests/displacement
python -m pytest --collect-only -q tests/displacement
```

The default twenty-candle `config/displacement.example.toml` example must emit
bullish displacement at index 15 and bearish displacement at 16. Feed the existing
Phase 4 frames to `DisplacementAnalyzer`; do not run duplicate upstream detectors.
Tests cover ATR gaps/warm-up/reference timing, exact threshold boundaries, tiny/zero
ATR, doji/long-wick cases, optional sweep cohorts, raw artifacts, immutable models,
independent graph/ATR reconstruction, every-prefix equality, and future invariance.
These tests do not evaluate returns or implement a backtesting/strategy engine.

## Phase 6 tests and example

```bash
python -m pytest tests/fvg
python -m pytest --collect-only -q tests/fvg
```

`config/fvg.example.toml` must emit bullish [101,104] at index 16 and bearish
[98,106] at index 19, linked to actual C2 displacement at 15/18. Tests cover strict
geometry, one-tick/minimum equality, overlaps/nesting/opposition, inherited sweep
context, precision, source continuity, immutable JSON evidence, causal identity,
full-prefix/future-price invariance, and CSV/mocked-Binance replay. There is no fill,
entry, lifecycle, strategy, performance test, or scoring implementation.

## Phase 7 tests and example

```bash
python -m pytest tests/order_blocks
python -m pytest --collect-only -q tests/order_blocks
```

The synthetic `config/order-block.example.toml` example must create candidate 18's
bullish block at displacement/BOS 20 and candidate 21's bearish block at
displacement/CHoCH 22. Required-FVG mode publishes those candidates at 21/23,
respectively, with no earlier OB output. Tests cover selection/lookback, exact
zone and doji behavior, all confirmation modes, future-evidence exclusion,
immutable provenance, every-prefix equality, chunking, and provider integration.
This is not a strategy, performance test, or lifecycle/entry implementation.

## Phase 8 tests and example

```bash
python -m pytest tests/premium_discount
python -m pytest --collect-only -q tests/premium_discount
```

The seven-candle synthetic `config/premium-discount.example.toml` example produces
three insufficient-context frames followed by premium, discount, equilibrium, and
outside-range closes. Tests cover range orientation/selection, exact boundaries,
same-pivot/nonpositive pairs, all five immutable array sidecars, unchanged original
IDs, future HTF reference metadata only, dependency graphs, and causal replay.
No scoring, trading, risk, performance analysis, or execution is introduced.

## Packaging smoke test

After building, install the resulting wheel into a separate clean virtual
environment with `pip install --no-deps <path-to-wheel>`, then run that environment's
`python -m smcsignal --version` from outside this checkout. This checks that the
package does not rely on an editable installation or the current directory. Also
import `smcsignal.data` and `smcsignal.analysis`, replay the explicit Phase 3 CSV
fixture through the installed wheel, and verify its known structure event indices.
Also replay the Phase 4 liquidity fixture and verify sweeps at 9 and 10, prefix
identity, and public liquidity/provenance imports. Then feed the default Phase 5
fixture through the installed pipeline and verify displacement at 15/16, raw
serialization, and every-prefix equivalence. Also verify Phase 6 creation at 16/19
from the installed wheel, matching stream/batch records and prefix identities.
Then verify Phase 7 default and required-FVG publications, full pipeline reuse,
raw serialization, and prefix identity in the installed wheel. Finally verify
Phase 8 classifications, range/sidecar provenance, source identity preservation,
and full-pipeline streaming through the installed wheel.
Repository configuration and
fixtures are in the source distribution, not installed as runtime wheel resources.

## Before committing

```bash
git diff --check
git status --short
```

Review every staged file for secrets and accidental artifacts. Keep all work on
the designated working branch. Stop after Phase 8. Do not implement Phase 9 without explicit approval.
