# Professional Halal SMC/ICT Spot Signal Bot

**Status: Phase 1 — project scaffold only. No SMC/ICT logic is implemented.**

A Python foundation for a future spot-only, long-only signal tool. This version
provides packaging, an informational CLI, offline tests, a reference configuration,
and development documentation. It does **not** generate signals, connect to an
exchange, trade, or claim any performance results.

## Requirements

- Python **3.11+**.
- A virtual environment for local development.
- No third-party runtime dependencies.

## Quick start

From the repository root on Linux or macOS:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e ".[dev]"

smcsignal
python -m smcsignal --version
python -m pytest
```

On Windows, use `python -m venv .venv` and activate with
`.venv\Scripts\Activate.ps1` in PowerShell.

Expected default CLI output:

```text
Professional Halal SMC/ICT Spot Signal Bot
Phase 1: project scaffold only.
No market data, signal generation, exchange connectivity, or order execution.
```

Only default status output, `--help`, and `--version` are supported. The CLI does
not load configuration or credentials.

## Project tree

```text
.
├── .editorconfig
├── .gitignore
├── MANIFEST.in
├── README.md
├── pyproject.toml
├── config/
│   ├── README.md
│   └── example.toml
├── docs/
│   ├── README.md
│   ├── architecture.md
│   └── development.md
├── src/
│   └── smcsignal/
│       ├── __init__.py
│       ├── __main__.py
│       ├── cli.py
│       └── py.typed
└── tests/
    ├── __init__.py
    ├── test_cli.py
    ├── test_config_template.py
    └── test_package.py
```

Generated environments, caches, package metadata, and build outputs are ignored.

## Development checks

With the environment activated:

```bash
python -m ruff check .
python -m ruff format --check .
python -m pytest
python -m pip check
python -m build
```

See the [development guide](docs/development.md) for packaging checks and test
scope. Runtime code is under `src/smcsignal`; tests require installing the package,
as shown above.

## Configuration and boundaries

[`config/example.toml`](config/example.toml) is a **reference template only**.
It is not loaded or enforced by Phase 1. Its spot-only, long-only, signal-only and
disabled-feature values document intent; changing them cannot enable a feature.
See [configuration guidance](config/README.md).

This phase includes no market-data adapters, indicators, SMC/ICT detection,
signal delivery, risk engine, backtesting, exchange integration, or order
execution. No leverage, margin, short selling, or derivatives are implemented.
Never commit credentials or account data.

“Halal” is a design goal, **not a Sharia certification**. Asset and venue
eligibility and future implementation details require qualified review. This
scaffold provides no investment advice or guarantee of profit.

## Documentation and next phase

- [Documentation index](docs/README.md)
- [Architecture and explicit exclusions](docs/architecture.md)
- [Repository](https://github.com/jamoliddinov2025-bit/halal-smc-ict-signal-bot1)

**Stop at Phase 1. Phase 2 requires explicit approval.**
