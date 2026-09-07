# Development guide

## Prerequisites

- Python 3.11 or newer.
- Git and a Python virtual environment.
- Network access for the initial dependency installation only; the tests and
  application code do not require network access.

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

The CLI has no strategy, data, configuration-loading, or trading commands.

## Quality checks

Run all commands from the repository root with the virtual environment active:

```bash
python -m ruff check .
python -m ruff format --check .
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
- The syntax and intended baseline values of the reference TOML template.

They do **not** validate a trading strategy, financial performance, or religious
compliance; those features and assessments do not exist in Phase 1.

## Packaging smoke test

After building, install the resulting wheel into a separate clean virtual
environment with `pip install --no-deps <path-to-wheel>`, then run that environment's
`python -m smcsignal --version` from outside this checkout. This checks that the
package does not rely on an editable installation or the current directory.

## Before committing

```bash
git diff --check
git status --short
```

Review every staged file for secrets and accidental artifacts. Keep all work on
the designated working branch. Do not implement Phase 2 without approval.
