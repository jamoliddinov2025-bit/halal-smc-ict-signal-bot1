# Documentation

Current scope: **Phase 9 — strict displacement-confirmed Market Structure Shift evidence**.

- [Architecture](architecture.md): package boundaries, per-candle data flow, and immutable outputs.
- [Market data methodology](market-data-methodology.md): the approved data layer and its limits.
- [Market structure methodology](market-structure-methodology.md): fractal confirmation, active
  levels, BOS/CHoCH definitions, exact crossing rules, and a hand-computed example.
- [Trend methodology](trend-methodology.md): confirmed HH/HL versus LH/LL, ranging, and readiness.
- [No-look-ahead guarantees](no-look-ahead.md): availability indices, state-transition argument,
  prefix/future-shock tests, and input/history assumptions.
- [Liquidity and sweep methodology](liquidity-sweep-methodology.md): actual pool formation,
  equality bands, sweep rules, lifecycle, causal artifacts, and replay limits.
- [Displacement methodology](displacement-methodology.md): prior ATR, objective inclusive
  boundaries, optional sweep context, availability, deterministic evidence, and limits.
- [FVG methodology](fvg-methodology.md): strict geometry/minimums, C2 displacement/sweep
  context, creation timing, immutable evidence, and no lifecycle/strategy assumptions.
- [Order Block methodology](order-block-methodology.md): candidate selection, exact zones,
  mandatory displacement, structure/FVG choices, immutable timing, and provenance.
- [Premium/Discount methodology](premium-discount-methodology.md): latest confirmed opposing
  pairs, exact midpoint/bands, categorical close/array context, and immutable sidecars.
- [MSS methodology](mss-methodology.md): pre-known directional structure, confirmed
  opposing level breaks, displacement, exact relationships, publication, and causality.
- [Evidence provenance contract](evidence-provenance-contract.md): approved shared contracts
  now used by actual liquidity/sweep producers; scoring remains deferred to Phase 11.
- [Development guide](development.md): installation, offline tests, typing, and packaging.
- [Configuration](../config/README.md): separate market data and analysis tables.

No breaker/ mitigation blocks, OTE, session strategy, signals, charts, Telegram, halal filter, or scoring is implemented.
Phase 10 requires explicit approval.
