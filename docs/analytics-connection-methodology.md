# Analytics connection to the real signal pipeline (Phase 26A/26B)

Phase 26A connects the existing Phase 18 outcome-analytics foundation to the
real signal publication path. It adds no second pipeline, no demo engine, and
no new outcome semantics: it attaches a strictly downstream observer at the
exact point where real signals are already published. Phase 26B closes the
loop with a deterministic lifecycle composition; both remain one downstream-only
leaf (`smcsignal.analytics`) that nothing upstream imports.

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

## Phase 26B: the deterministic outcome lifecycle

``SignalOutcomeLifecycle`` removes the manual bridge between publication
observation and market evaluation. It is pure composition of the frozen
pieces — it owns no outcome arithmetic, no evaluation rule, and no statistic
of its own. For each real engine frame of one series, in order:

1. ``OutcomeTrackingAnalyzer.update()`` validates and consumes the frame,
   evaluates open outcomes against the newly closed candle, and finalizes at
   exactly the horizon (unchanged Phase 18 machinery).
2. ``AnalyticsObserver.observe()`` records the publication fact and opens the
   initial OPEN outcome.
3. Each ``completed`` final is forwarded to ``record_finalized()`` into the
   single authoritative ledger feeding ``StrategyStats`` and the UTC
   ``MonthlyReport``.

Evaluate-first ordering is atomic: an out-of-order or replayed frame raises
inside the frozen Phase 18 evaluator before any ledger state changes, matching
the Phase 18 "rejected atomically" discipline. Observe-before-finalize stays
structural: a BUY published at frame ``i`` can finalize no earlier than frame
``i + horizon_bars`` (``horizon_bars >= 1``), so its publication fact is
always recorded strictly before any final for it exists. No final ever
references a candle after the consumed frame — no look-ahead enters through
the composition. Finals reach the ledger exactly once: Phase 18 finalizes each
outcome exactly once at the horizon and the observer is idempotent per outcome
identity.

One lifecycle describes exactly one series: it requires a fresh observer and a
fresh evaluator sharing one configuration, so its ledger can never mix series
or inherit untracked finals. Multiple series mean multiple lifecycles composed
by the caller — no fleet orchestration exists here. The lifecycle adds no
persistence, clock, network, scheduler, run loop, delivery integration,
backtest identity, or new outcome semantics. It is proven equivalent to the
manual Phase 26A bridge (identical OPEN observations, finalized outcomes,
strategy statistics, and monthly reports) across WIN, LOSS, BREAKEVEN, mixed,
and open-only histories.

## Phase 26C: deterministic ledger snapshot and restore (bytes only)

Phase 26C turns the lifecycle ledger into canonical, content-addressed bytes
and back, so a later approved phase can persist those bytes — while this layer
performs no IO at all (``ledger_bytes`` returns bytes; ``load_ledger_bytes``
accepts bytes; storage is a future phase's job).

- **One canon, reused.** Bytes are produced by the repository's existing
  evidence canon (sorted compact JSON, exact ``Decimal`` text, UTC ISO-8601
  timestamps, enum values, frozen-dataclass field mapping). No competing
  serialization convention exists in this layer.
- **Exact restoration through frozen constructors.** Restore mirrors the
  frozen field sets and passes every value through the real constructors
  (``SeriesProvenance``, ``CandleReference``, ``EvidenceReference``,
  ``EvidenceProvenance``, ``OutcomeTrackingConfig``, Phase 18
  ``SignalOutcome``, Phase 26A ``SignalObservation``), so load-time validation
  is the frozen validation — nothing re-implemented.
- **Integrity.** ``snapshot_id`` digests the contents and is embedded in the
  document; on load the digest is recomputed and compared — a supplied digest
  is never trusted. Strict exact-key checks, enum/datetime/configuration
  validation, and the frozen model invariants reject tampering and malformity.
- **Read-only restored ledgers.** ``RestoredAnalyticsLedger`` exposes exactly
  the live observer's read views (observations, open outcomes, finalized
  outcomes, ``StrategyStats``, UTC ``MonthlyReport``) computed through the
  same frozen Phase 18 aggregate machinery. It accepts no new observations,
  no new finals, and no frames; evaluator continuation after a restart
  (re-feed versus checkpointing) is a future phase, and the Phase 18
  evaluator remains frozen and opaque.
- **Delivery never participates.** The module imports nothing from
  ``smcsignal.delivery`` and no API accepts a delivery record.

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
