# Analytics connection to the real signal pipeline (Phase 26A–26G)

Phase 26A connects the existing Phase 18 outcome-analytics foundation to the
real signal publication path. It adds no second pipeline, no demo engine, and
no new outcome semantics: it attaches a strictly downstream observer at the
exact point where real signals are already published. Phase 26B closes the
loop with a deterministic lifecycle composition; both remain one downstream-only
leaf (`smcsignal.analytics`) that nothing upstream imports. Phase 26C makes the
ledger snapshotable and restorable as canonical, content-addressed bytes; Phase
26D persists those bytes durably; Phase 26E turns a persisted snapshot plus the
series' regenerated frame history back into a verified live lifecycle; Phase
26F supplies the sanctioned production seam for regenerating that frame
history itself; and Phase 26G composes all of it into the single-series ledger
session — open-or-recover, continue, checkpoint.

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

## Phase 26D: the durable ledger store boundary

Phase 26D is the repository's first writer, placed deliberately outside the
IO-free analytics leaf as a new downstream package, ``smcsignal.persistence``
(``LedgerStore`` protocol, ``MemoryLedgerStore`` offline reference
implementation, ``FileLedgerStore`` explicit filesystem boundary).

- **One connection, one format.** The store consumes only the Phase 26C
  public API: writes are exactly ``ledger_bytes(snapshot)`` and reads
  delegate entirely to ``load_ledger_bytes``. No second persistence format,
  no second digest, no parsing or validation of ledger JSON inside the store
  — integrity stays single-sourced in Phase 26C.
- **Key safety.** A key is an opaque caller label (one per series ledger),
  validated against a closed charset: empty/oversized keys, separators,
  traversal fragments (``..``), unsafe edges, and reserved device names are
  rejected. A validated key maps to exactly one file under the root — never
  outside it.
- **Atomic writes.** The payload lands in a sibling temporary file and is
  swapped into place with ``os.replace``; a failure before the swap removes
  the temporary file, leaves the previous snapshot intact, and leaves no
  residue.
- **No resume.** ``load`` returns a Phase 26C snapshot (which yields a
  read-only ``RestoredAnalyticsLedger``); it never rebuilds a live observer
  or lifecycle, resumes an evaluator, ingests frames, or merges anything.
  Evaluator continuation remains a future phase.
- **No live operations.** No scheduler, polling, feed, clock, run loop, fleet
  orchestration, monitoring, delivery, database, network, encryption,
  compression, retention, rotation, or version history.

## Phase 26E: verified ledger recovery and continuation

Phase 26E answers the question the Phase 26C/26D docs deliberately deferred:
*after a restart, how does a series continue its ledger where it left off?*
The answer is **replay, not checkpointing** — the only mechanism that keeps the
frozen Phase 18 evaluator opaque.

- **The mechanism.** ``recover_lifecycle(frames, expected)`` builds a fresh
  Phase 26B lifecycle (fresh observer, fresh evaluator) under the
  configuration carried by the expected Phase 26C snapshot, replays the
  series' historical ``SignalSnapshot`` frames through the unchanged 26B
  ``update()`` loop (evaluate → observe → forward finals), and then projects
  the reconstruction through Phase 26C ``snapshot_ledger()``. Because Phases
  3–18 are deterministic, replaying the identical frame history reproduces the
  identical ledger; the stored snapshot is the verification oracle, not the
  state source.
- **The acceptance rule.** The reconstructed snapshot must equal ``expected``
  exactly — observations, OPEN and finalized outcomes, ordering, settings,
  configuration hash, and the content-addressed ``snapshot_id``. On equality
  the live lifecycle is returned inside ``RecoveredLedger`` ready for
  continuation; feeding it further frames behaves exactly like uninterrupted
  operation (new BUYs observed, finalization at exactly the horizon, identical
  statistics and monthly reports).
- **Atomic refusal.** Any difference raises ``AnalysisInputError`` before
  anything is returned: no partially recovered lifecycle escapes, the frozen
  expected snapshot is never mutated, and the failure is deterministic.
  Sequence violations (replayed, out-of-order, gapped frames) raise inside the
  frozen Phase 18 evaluator before any ledger state changes.
- **No new semantics.** Recovery invents no outcome rule, no evaluation rule,
  and no statistic. The configuration is derived from the expected snapshot and
  never supplied separately, so a recovered lifecycle can never run under a
  configuration other than the one the snapshot records. The Phase 18 evaluator
  is never serialized, never checkpointed, and never re-implemented.
- **Offline and one-way.** Recovery performs no IO, clock, or network, and
  imports nothing from ``smcsignal.persistence``, delivery, monitoring, or
  data. Persistence stays the caller-side snapshot source (the 26D store
  ``load()``s the snapshot and hands it in), and the dependency direction stays
  ``persistence -> analytics``. One snapshot plus one frame history describe one
  series; composing multiple series is the caller's orchestration and does not
  exist here. No scheduler, run loop, feed, websocket, polling, fleet,
  monitoring, Telegram, database, remote persistence, encryption, compression,
  retention, history, or evaluator serialization.

## Phase 26F: the deterministic series frame source

Phase 26E's recovery contract requires the series' historical frames
"regenerated through the real Phase 3-17 chain" — yet that regeneration lived
only in test glue until Phase 26F gave it a sanctioned production seam
(``smcsignal.series``: ``SeriesFrameSource``, ``series_frames``).

- **Real publication frames only.** Every emitted frame is the actual Phase 17
  ``SignalSnapshot`` the frozen signal engine published inside the frozen
  Phase 20 ``HistoricalReplay``, consumed from ``ReplayStep.signal``. No frame
  is manufactured, reconstructed, or re-published by Phase 26F.
- **Reuse, not duplication.** The source wraps one ``HistoricalReplay`` and
  owns no analyzer composition of its own: the liquidity→engine chain, the
  draw cursor, chronological order, and higher-timeframe availability gating
  all stay exactly where the frozen replay owns them. The replay's internal
  Phase 18/19 outcome and attribution work is inherited frozen behavior; it
  is not exposed and connects to nothing downstream.
- **Deterministic and prefix-stable.** Identical dataset and configuration
  produce identical frame sequences across independent sources; each declared
  candle is processed exactly once in order; ``update()`` past the declared
  history raises through the frozen machinery; and the first ``k`` frames of a
  complete history equal all frames of the corresponding declared prefix,
  because no future candle can influence a published fact. That prefix
  stability is precisely what Phase 26E recovery relies on when regenerating
  history.
- **Frames in, frames out.** The source knows no ledger, snapshot, store,
  recovery, or delivery state; feeding a Phase 26B lifecycle, persisting
  Phase 26C bytes, or recovering through Phase 26E stays the caller's
  composition. It performs no IO, clock, network, websocket, scheduler, or
  concurrency; candle acquisition remains caller-owned at the Phase 2
  boundary. It imports only ``smcsignal.analysis.*`` — never analytics,
  persistence, delivery, monitoring, or any provider. It is not a lifecycle,
  session, coordinator, run loop, scheduler, or application runtime; no live
  trading, execution, exchange, fleet, database, candle persistence,
  monitoring wiring, or strategy change exists here.

## Phase 26G: the single-series ledger session

Phase 26G supplies the operational unit the arc was missing
(``smcsignal.sessions``: ``open_ledger_session``, ``LedgerSession``): given a
store, a key, an explicitly declared outcome configuration, and the series'
caller-supplied frame history, open the series' ledger — verified — ready to
continue.

- **Explicit configuration, never defaulted.** ``open_ledger_session(store,
  key, config, history)`` requires the caller's ``OutcomeTrackingConfig``.
  Nothing is defaulted, inferred, replaced, or overridden: a fresh session is
  built under exactly the supplied configuration, so ``fresh(config,
  history)`` equals the uninterrupted lifecycle over the same inputs by
  construction. With a stored entry, its settings must equal the supplied
  configuration before any replay — the stored settings act as a verification
  constraint on the caller's single declared configuration, never as a
  competing source; a mismatch refuses atomically.
- **Counts trigger; equality authorizes.** Recovery replays the history
  through the unchanged 26B machinery. The observer ledger is strictly
  append-only, so observation and finalization counts are monotone; reaching
  the stored snapshot's counts merely triggers the candidate comparison.
  Authorization is always complete ``LedgerSnapshot`` equality — settings,
  configuration hash, every observation and final, content-addressed
  identity. A wrong history with coincidentally similar counts fails the full
  comparison; a wrong configuration cannot start the replay; a truncated
  history never matches. ``store.save()`` is never called before successful
  verification, so a failed open leaves the stored bytes completely
  unchanged.
- **Plateau semantics.** Non-BUY frames leave the ledger untouched, so one
  complete snapshot can correspond to several consecutive frame boundaries;
  the original physical persist boundary is not uniquely identifiable, and
  Phase 26C is deliberately not modified to make it so. ``frames_verified``
  therefore documents exactly one thing: the number of supplied history
  frames actually replayed during the verified reconstruction at open.
- **Session discipline.** ``update()`` delegates to the real Phase 26B
  lifecycle and performs no persistence IO; ``persist()`` explicitly
  snapshots and saves; a successful open ends with one approved open-time
  write, so a session is always durable immediately after open. The session
  owns no frame source (``smcsignal.series`` stays caller-composed), no
  second ledger or outcome model, and no state machine beyond the live
  lifecycle plus immutable provenance facts (``recovered_from``,
  ``frames_verified``). No clock, file, network, scheduler, concurrency,
  fleet, delivery, or monitoring capability exists here — its only IO is the
  Phase 26D store it was given. One session describes one series; no run
  loop, application runtime, or service framework.

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
