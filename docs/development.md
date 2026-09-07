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
compliance; those features and assessments do not exist in Phase 3.


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

## Packaging smoke test

After building, install the resulting wheel into a separate clean virtual
environment with `pip install --no-deps <path-to-wheel>`, then run that environment's
`python -m smcsignal --version` from outside this checkout. This checks that the
package does not rely on an editable installation or the current directory. Also
import `smcsignal.data` and `smcsignal.analysis`, replay the explicit Phase 3 CSV
fixture through the installed wheel, and verify its known structure event indices. Repository configuration and
fixtures are in the source distribution, not installed as runtime wheel resources.

## Before committing

```bash
git diff --check
git status --short
```

Review every staged file for secrets and accidental artifacts. Keep all work on
the designated working branch. Stop after Phase 3. Do not implement Phase 4 without approval.
