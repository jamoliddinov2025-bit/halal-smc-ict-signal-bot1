# Configuration templates

`example.toml` is a version-controlled reference for the intended project scope.
It contains no credentials and can be parsed with Python 3.11's standard-library
`tomllib`.

## Phase 1 boundary

There is **no runtime configuration loader** in this phase. The CLI does not read
this directory. The template's flags are documented intentions, not implemented
security controls, and changing them cannot enable networking, signals, or orders.
Tests validate the template's syntax and baseline values only.

Any future implementation must explicitly enforce approved spot-only, long-only,
signal-only constraints. Neither leverage, margin, short selling, nor automated
order execution is part of this scaffold.

## Local files and secrets

Machine-specific files named `local*.toml` or `secrets*.toml` in this directory
are Git-ignored, but are not loaded by Phase 1. Ignore rules are not a substitute
for secret management. Never commit API keys, passwords, tokens, or account data.
Only this README and `example.toml` are explicitly included from this directory
in the source distribution.
