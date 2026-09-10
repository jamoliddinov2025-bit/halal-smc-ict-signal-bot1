# Documentation

Current scope: **Phase 22 — deterministic strategy-intelligence research reporting**.

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
- [Outcome tracking methodology](outcome-tracking-methodology.md): fixed-horizon
  BUY_SIGNAL outcomes, exact return/MFE/MAE, sign-based WIN/LOSS/FLAT,
  aggregate analytics, no flush of open outcomes.
- [Indicators methodology](indicators-methodology.md): supporting EMA/RSI/volume
  context with exact Decimal arithmetic; never signals, gates, or vetoes.
- [Setup attribution methodology](setup-attribution-methodology.md): closed
  twelve-label taxonomy from nested facts; outcome-independent combination keys.
- [Performance methodology](performance-methodology.md): descriptive multi-series
  statistics over published outcome records; open/finalized separated, no
  reclassification, sample-gated rankings.
- [Monthly review methodology](monthly-review-methodology.md): UTC calendar-month
  reviews with exact deltas only for sufficiently sampled comparisons; sample
  sizes always shown.
- [Visualization methodology](visualization-methodology.md): deterministic
  SVG/text drawings of published facts; semantic style tokens, canonical
  ordering, no re-detection, no raster.
- [Backtest methodology](backtest-methodology.md): chronological historical
  replay through the unchanged pipeline; exact-Decimal rows, composed
  Phase 19 aggregates, no execution or optimization.
- [Robustness methodology](robustness-methodology.md): walk-forward
  development/validation windows over the unchanged Phase 20 replay;
  cross-period, cross-asset, cross-timeframe, and regime buckets;
  degradation and stability indicators; validation only, never optimization.
- [Strategy Intelligence methodology](intelligence-methodology.md): consumer-only
  research reporting over the Phase 21 validation rows; setup, symbol,
  timeframe, month, and regime conditionals with exact winner/loser patterns,
  strength/weakness diagnostics, and deterministic sample-gated rankings;
  observational only, never optimization or advice.
- [Analytics connection methodology](analytics-connection-methodology.md): Phase 26A
  downstream observer at the real Phase 17 publication boundary; one OPEN Phase 18
  outcome per published BUY; market-evaluator-only finalization; delivery state is
  never a trade outcome; no double counting; Phase 26B deterministic lifecycle
  composition (evaluate, observe, finalize) with no new arithmetic; Phase 26C
  canonical, content-addressed ledger snapshot and restore — bytes only,
  read-only restored ledgers, evaluator resume deferred; Phase 26D durable
  ledger store (`smcsignal.persistence`) — atomic, key-validated file
  persistence of Phase 26C bytes, no resume, no live operations.
- [Evidence provenance contract](evidence-provenance-contract.md): approved shared contracts
  now used by actual liquidity/sweep producers; Phase 17 publishes spot
  `BUY_SIGNAL` facts over existing eligibility without SELL, SHORT, or orders;
  Phase 18 tracks fixed-horizon outcomes of those publications; Phase 19 adds
  consumer-only indicators, attribution, performance, review, and
  visualization layers over those records; Phase 20 replays declared
  historical datasets through the unchanged pipeline as backtests; Phase 21
  validates those publications across walk-forward windows, datasets,
  timeframes, and regimes without tuning anything; Phase 22 reports how the
  validated out-of-sample outcomes distribute across signal-time setup,
  symbol, timeframe, month, and regime profiles without optimizing or
  selecting anything.
- [Development guide](development.md): installation, offline tests, typing, and packaging.
- [Configuration](../config/README.md): separate market data and analysis tables.

No session strategy or SELL/SHORT trades are implemented; chart rendering and
Telegram delivery are implemented (Phase 24), downstream-only.
**Stop after Phase 22. Phase 23 requires explicit approval.**
