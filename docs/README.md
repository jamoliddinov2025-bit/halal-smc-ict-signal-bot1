# Documentation

Current scope: **Phase 17 — deterministic spot signal engine**.

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
- [Breaker Block methodology](breaker-block-methodology.md): original OB eligibility,
  strict first closing violation, displacement/MSS confirmation, exact zones, and timing.
- [Mitigation Block methodology](mitigation-block-methodology.md): original OB
  eligibility, interior range overlap, first interaction only, Breaker policy, and timing.
- [OTE methodology](ote-methodology.md): Phase 8 dealing-range retracements,
  inclusive 0.62–0.79 close classification, before-open timing, and independent zones.
- [MTF confluence methodology](mtf-confluence-methodology.md): completed-candle HTF
  eligibility at the LTF open, independent labels, unweighted MIXED confluence.
- [Halal filter methodology](halal-filter-methodology.md): allow/deny registry
  enforcement, explicit UNKNOWN, no internet or autonomous religious rulings.
- [Setup quality methodology](setup-quality-methodology.md): integer 0–100 score
  from nested facts, HARAM/UNKNOWN gate, missing-evidence zeros, threshold 75.
- [Signal eligibility methodology](signal-eligibility-methodology.md): HALAL plus
  SQS threshold gate, nested directional votes, conflict stays NEUTRAL.
- [Signal engine methodology](signal-engine-methodology.md): spot `BUY_SIGNAL`
  from eligible `LONG_BIAS`, `BEARISH_AVOID` from eligible `SHORT_BIAS`,
  one-per-setup, causal publication.
- [Evidence provenance contract](evidence-provenance-contract.md): approved shared contracts
  now used by actual liquidity/sweep producers; Phase 17 publishes spot
  `BUY_SIGNAL` facts over existing eligibility without SELL, SHORT, or orders.
- [Development guide](development.md): installation, offline tests, typing, and packaging.
- [Configuration](../config/README.md): separate market data and analysis tables.

No session strategy, SELL/SHORT trades, charts, or Telegram is implemented.
**Stop after Phase 17. Phase 18 requires explicit approval.**
