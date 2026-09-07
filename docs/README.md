# Documentation

Current scope: **Phase 3 — confirmed swings, historical trend, BOS, and CHoCH**.

- [Architecture](architecture.md): package boundaries, per-candle data flow, and immutable outputs.
- [Market data methodology](market-data-methodology.md): the approved data layer and its limits.
- [Market structure methodology](market-structure-methodology.md): fractal confirmation, active
  levels, BOS/CHoCH definitions, exact crossing rules, and a hand-computed example.
- [Trend methodology](trend-methodology.md): confirmed HH/HL versus LH/LL, ranging, and readiness.
- [No-look-ahead guarantees](no-look-ahead.md): availability indices, state-transition argument,
  prefix/future-shock tests, and input/history assumptions.
- [Development guide](development.md): installation, offline tests, typing, and packaging.
- [Configuration](../config/README.md): separate market data and analysis tables.

No liquidity pools, sweeps, displacement, fair value gaps, order blocks,
premium/discount, signal engine, charts, Telegram, or halal filter is implemented.
Phase 4 requires explicit approval.
