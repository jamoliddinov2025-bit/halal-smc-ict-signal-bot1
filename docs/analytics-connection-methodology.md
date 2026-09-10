# Phase 26A: analytics connection to the real signal pipeline

Phase 26A connects the existing Phase 18 outcome-analytics foundation to the
real signal publication path. It adds no second pipeline, no demo engine, and
no new outcome semantics: it attaches a strictly downstream observer at the
exact point where real signals are already published.

## The real connection point

The real signal pipeline publishes where Phase 17
(``SignalEngineAnalyzer.update()`` in
``smcsignal.analysis.signal_engine.analyzer``) returns a ``SignalSnapshot``.
A frame whose status is ``BUY_SIGNAL`` is a published spot BUY fact. The Phase
26A observer consumes the returned immutable snapshot at that boundary — the
same way the Phase 18 outcome tracker and the Phase 24 delivery coordinator
already consume those frames — and never reaches into the engine.
``smcsignal.analytics.ObservedSignalEngine`` is the minimal adapter that
composes a real engine instance with the observer: every ``update()`` first
runs the unchanged engine to completion and only then forwards the returned
snapshot, so analytics can never alter signal generation, SMC/ICT logic, halal
filtering, confidence, delivery, or any strategy decision.

## Observation: one published BUY opens one OPEN outcome

Observing a published BUY signal opens exactly the initial Phase 18
``SignalOutcome`` version (status ``OPEN``, ``candles_observed == 0``) through
the existing ``open_outcome()`` mapping and the existing ``outcome_identity``;
Phase 26A invents no outcome model of its own. Non-BUY frames create no
observation. Observation is idempotent per outcome identity: replaying the
same published frame returns the existing observation and never creates a
second outcome or a second count.

## Finalization belongs to the market evaluator alone

Only the real market outcome evaluator may transition ``OPEN`` to ``WIN``,
``LOSS``, or ``FLAT`` (breakeven): a finalized record accepted by
``AnalyticsObserver.record_finalized()`` must be a non-OPEN Phase 18 outcome,
and the Phase 18 model invariants admit finals only when the evaluator
observed ``horizon_bars`` later candles of the signal's own series and
classified the exact sign of the final difference. The observer accepts a
final only for an outcome it observed at the publication boundary, with the
same outcome configuration; an identical repeat is an idempotent no-op, and a
conflicting duplicate is rejected. Finalized outcomes feed ``StrategyStats``
and the UTC ``MonthlyReport`` through the frozen Phase 18 ``aggregate()``
arithmetic — statistics are never recomputed by Phase 26A.

**Documented limitation.** The current architecture has no live market feed
after publication. Until an evaluation with ``horizon_bars`` later candles
exists, an observed outcome stays ``OPEN``; open outcomes never flush, never
expire, and contribute only to open counts.

## Delivery state is never a trade outcome

``DeliveryState`` describes what a sink reported about a message
(``SENT``/``DELIVERED``/``FAILED``/…); it says nothing about the market.
``smcsignal.analytics`` does not import ``smcsignal.delivery`` at all, exposes
no API accepting a delivery record, and the test suite pins both facts. A
``DELIVERED`` Telegram receipt can therefore never produce a ``WIN`` (or any
other outcome), and a ``FAILED`` receipt can never produce a ``LOSS``.

## Guarantees

- **Downstream-only.** Nothing upstream imports ``smcsignal.analytics``; the
  package imports only Phase 17/18 records and stdlib value types (pinned by
  AST scope tests).
- **Deterministic.** No clock reads, no files, no network, no randomness;
  identical published frames produce identical observations, statistics, and
  reports.
- **Immutable.** Every record is a frozen dataclass; observation mutates no
  upstream record.
- **No double counting.** One outcome per outcome identity; one counted final
  per outcome identity; monthly totals reconcile exactly with the strategy
  totals.
