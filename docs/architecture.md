# Architecture and Phase 1 scope

## What exists

The project uses a Python `src` layout so that tests exercise an installed package
rather than accidentally importing directly from the repository root.

| Location | Responsibility |
| --- | --- |
| `src/smcsignal/__init__.py` | Package metadata; single source of the version |
| `src/smcsignal/cli.py` | Informational status, help, and version output only |
| `src/smcsignal/__main__.py` | Support `python -m smcsignal` |
| `src/smcsignal/py.typed` | Mark the package as shipping inline type annotations |
| `tests/` | Offline scaffold, CLI, metadata, and template tests |
| `config/` | Non-executable reference configuration and guidance |
| `docs/` | Scope and development documentation |
| `pyproject.toml` | Build metadata, development dependencies, pytest, and Ruff |

There are no third-party runtime dependencies. Importing the package has no
application-level side effects. CLI execution only prints information; it does
not load configuration, read credentials, access market data, or submit orders.

The source distribution includes the reference configuration, documentation, and
tests. The wheel includes the `smcsignal` package, typing marker, and CLI entry
point; it does not install the repository's tests or configuration directory.

## Intent, not an implemented strategy

The intended product is a spot-only, long-only signal tool. The configuration
file documents that intent, but Phase 1 has no trading engine or runtime safety
policy evaluator. Template flags must not be mistaken for enforced controls.

## Explicitly out of scope

- Market-data ingestion, exchange clients, authentication, or external services.
- SMC/ICT analysis, indicators, setups, scoring, or signal generation.
- Signal delivery, Telegram integration, scheduling, or background workers.
- Backtesting, paper trading, execution, risk sizing, or portfolio management.
- Runtime configuration parsing, secret storage, or databases.
- Leverage, margin, short selling, derivatives, or automated orders.

No placeholder strategy implementations, fabricated signals, or performance
claims are provided.

## Halal qualification

“Halal” describes the project's intended constraints, not a Sharia certification.
Asset eligibility, venue mechanics, fees, and any future implementation require
appropriate qualified review. A software scaffold cannot establish compliance
or guarantee investment outcomes.

## Phase gate

Phase 1 ends with a tested, packaged, committed, and remotely verified scaffold.
Phase 2 requires explicit approval. Nothing here starts a bot or a trading loop.
