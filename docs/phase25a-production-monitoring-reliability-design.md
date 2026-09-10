# Phase 25-A — Production Monitoring & Reliability Design (Specification Only)

**Status:** DESIGN ONLY. No implementation, no code, no configuration, no tests.
**Baseline:** Phase 24D `44d2515` + regression-guard maintenance `b3a9013`.
**Scope:** observation of an already-frozen pipeline. This document proposes nothing
that can change signal generation, halal classification, eligibility, governance,
risk, or delivery decisions.

This is a **Telegram signal bot, not a trading system**. Phase 25 adds no exchange
or broker access, no order execution, no leverage, no position management, and no
autonomous trading. Telegram remains strictly downstream and output-only.

---

## 0. How to read this document — fact markers

Every load-bearing statement is tagged:

- **[Existing]** — verified by reading the file/symbol named in the current tree at
  the baseline above. Not a memory or an assumption.
- **[Proposed]** — new Phase 25 design that does not exist today.
- **[Deferred]** — deliberately postponed; explicitly *not* decided here.

A proposal is never written as though it already exists. Where this document
contradicts a prior description, the source tree wins; §2 records the places where
the code is materially different from what the Phase 25 brief assumed.

---

## 1. Purpose

Provide a deterministic, secret-free, strictly-downstream **observability layer**
that answers, for an operator:

1. Is the pipeline running, and did the last run complete?
2. Did the market data arrive complete, ordered, and fresh — without us touching it?
3. Is the signal engine producing the distribution we expect, at the latency we expect?
4. Is Telegram delivery succeeding, and if not, in which category?
5. What is the current health of each component, and what changed?
6. Can we raise an operational alert later without that alerting path feeding back
   into signal production?

The purpose is **detection and reporting only**. Monitoring never repairs, retries,
vetoes, reinterprets, or optimizes anything it observes. A monitoring outage must be
invisible to production; a production outage must be visible to monitoring.

---

## 2. Current architecture findings (verified)

This section is the evidentiary basis for every later boundary decision.

### 2.1 Package layout **[Existing]**

- `src/smcsignal/` — `__init__.py` (`__version__ = "0.22.0"`, `__all__ = ["__version__"]`),
  `cli.py`, `__main__.py`, `py.typed`, plus three subpackages:
  - `data/` — `models.py`, `config.py`, `validation.py`, `base.py`, `csv.py`,
    `binance.py`, `factory.py`, `errors.py`
  - `analysis/` — 26 subpackages (`liquidity`, `displacement`, `fvg`, `order_blocks`,
    `premium_discount`, `mss`, `breaker_blocks`, `mitigation_blocks`, `ote`, `mtf`,
    `halal_filter`, `setup_quality`, `signal_eligibility`, `signal_engine`,
    `outcome_tracking`, `indicators`, `setup_attribution`, `performance`, `review`,
    `visualization`, `backtest`, `robustness`, `intelligence`, `improvement`, …)
  - `delivery/` — `models.py`, `identity.py`, `config.py`, `formatting.py`, `message.py`,
    `chart.py`, `audit.py`, `sink.py`, `orchestrator.py`, `transport.py`, and the
    `telegram/` subpackage (8 modules).
- 195 Python files under `src/`.
- Public facades are explicit and large: `smcsignal.analysis.__all__` has **252**
  names; `smcsignal.delivery.__all__` has **42** names; the top-level
  `smcsignal.__all__` has exactly one name (`__version__`).

### 2.2 There is no long-running process **[Existing] — materially important**

Verified absent from `src/`:

- no `logging` import of any kind, and no logger objects;
- no `while True`, `schedule`, `apscheduler`, `daemon`, `cron`, `threading`,
  `asyncio`, `signal.signal`, or `atexit`;
- no filesystem or database persistence of runtime state.

`cli.py` is **informational only**: it builds an `argparse` parser, supports
`--version`, prints four lines of status text, and returns `0`. It does not fetch
data, run analysis, or deliver anything. `__main__.py` simply calls `main`.

**Consequence for this design.** The brief asks for "application/process health",
"startup/shutdown", and "stale processing" monitoring. There is currently no
process to monitor — the system is a synchronous library plus a status CLI.
Phase 25 must therefore **not** assume a daemon. Instead it introduces an explicit,
caller-scoped **monitored run** (§5.4) as the unit of startup/shutdown and staleness.
Inventing a daemon and then monitoring it would be a much larger, unspecified change.

### 2.3 Everything is synchronous and push-free **[Existing]**

Analyzers are called explicitly and return immutable snapshots. There is no event
loop, no queue, no background worker, and no callback registry. Analyzers construct
with a config and expose an explicit method; construction performs no I/O
(`DataProvider.__init__` stores config only; `fetch_ohlcv()` is the explicit fetch).

**Consequence.** Monitoring is **[Proposed]** pull/observe at explicit call sites,
synchronously, with no threads. See §4.3 and §5.5.

### 2.4 Market data already produces a cleaning audit **[Existing] — a key reuse point**

`smcsignal.data.models`:

- `OHLCV` — frozen, slotted: `timestamp: datetime` plus five `Decimal` prices.
  Validates tz-aware timestamps, converts to UTC, requires non-negative epoch time
  at **millisecond precision**, and enforces finite decimals plus high/low bounds.
- `ValidationReport` — frozen, slotted:
  `input_rows`, `output_rows`, `duplicates_removed`, `missing_rows_dropped`,
  `rows_trimmed`, `incomplete_rows_dropped`, `reordered: bool`.
- `OHLCVBatch` — frozen, slotted: `candles: tuple[OHLCV, ...]`, `report: ValidationReport`.

`data/validation.py::normalize_ohlcv` produces that report and **removes identical
duplicates**, rejects conflicting duplicates at the same timestamp, and applies an
explicit `MissingValuePolicy`. `DataProvider.fetch_ohlcv()` documents that providers
"never substitute synthetic data or silently switch to another source".

**Consequence.** Duplicate/malformed/missing-candle detection largely **already
exists** as data-cleanup counts. Phase 25 must **consume `ValidationReport`**, not
re-derive it and not re-validate (re-deriving would duplicate logic and risk
disagreeing with what actually happened). **[Proposed]** §8.

### 2.5 Time helpers exist for interval/gap reasoning **[Existing]**

- `analysis/mtf/timeframes.py::timeframe_seconds(timeframe) -> int` — exact UTC
  duration of a fixed interval; raises for `1M`.
- `analysis/liquidity/time.py::candle_close_time(opened_at, timeframe) -> datetime`.
- `SeriesProvenance` — `symbol`, `timeframe`, `venue`, `provider`, `dataset_id`.
- `EvidenceProvenance` — `evidence_id`, `series`, `producer`, `producer_version`,
  `configuration_hash`, `input_prefix_hash`, `available_at: datetime`.
- `CandleReference` carries `closed_at`, the exclusive completed-bar boundary.

**Consequence.** Gap and interval anomaly detection has exact primitives already.
Freshness can be expressed against `timeframe_seconds` and `closed_at` rather than
invented thresholds. **[Proposed]** §8.

### 2.6 Signal engine is immutable and already carries status **[Existing]**

`analysis/signal_engine/models.py`:

- `SignalStatus` = `BUY_SIGNAL | BEARISH_AVOID | NO_SIGNAL`.
- `SignalReason` has 14 members (`halal_asset`, `sqs_threshold_passed`, `long_bias`,
  `mtf_alignment`, `bullish_mss`, `bullish_displacement`, `discount_context`,
  `ote_context`, `not_halal`, `sqs_below_threshold`, `neutral_bias`,
  `bearish_spot_avoid`, `missing_required_evidence`, `duplicate_setup`).
- `SignalSnapshot` is frozen with `settings`, `upstream`, `candidate`, `provenance`,
  and computed `status`, `direction`, `signal_id`, `setup_identity`. Its
  `__post_init__` enforces many cross-consistency invariants.

**Consequence.** Signal-rate and distribution monitoring reads the already-computed
`status`/`reason` off the frozen snapshot. Monitoring never recomputes a status and
never influences one. **[Proposed]** §9.

### 2.7 Delivery states, categories, and audit **[Existing]**

`delivery/models.py`:

- `DeliveryState` = `NOT_ATTEMPTED | SENT | DELIVERED | UNKNOWN | FAILED | SKIPPED_DUPLICATE`.
- `FailureCategory` = `NONE | BUILD | CHART | CONFIG | TRANSPORT | TIMEOUT |
  RATE_LIMIT | NETWORK | UNKNOWN`.
- `DeliveryAttempt` — identity + routing only, optional `chart_artifact_id`.
- `DeliveryReceipt` — `delivery_id`, `message_id`, `signal_id`, `destination_id`,
  `attempt_number`, `state`, `failure_category`, and `attempted_at: datetime | None = None`.
- **`attempted_at` is declared but is never populated anywhere in `src/` or `tests/`**
  (verified by grep). The field exists and is always `None`.

`delivery/audit.py`:

- `redact(value, *, keep=4)` — deterministic, lossy, fixed marker `<redacted>`.
- `DeliveryAuditRecord` — frozen, secret-free, **deliberately carries no timestamps**
  ("they would break determinism"); destination only in redacted form.
- `audit_record_for(outcome)` projects a `DeliveryOutcome` into that record.

`delivery/identity.py` documents that identities "never depend on a clock, a random
UUID, a process id, a memory address, or machine-specific values", so they are stable
across restarts.

**Consequences.**

1. `attempted_at` **[Deferred]** — a timestamp slot already exists but is unused.
   Phase 25 should *not* start populating a frozen delivery field; delivery latency
   should be observed from a monitoring-side clock injection instead. Changing what
   `attempted_at` means would be a Phase 1–24 behavior change.
2. `DeliveryAuditRecord` already proves the project's convention that **audit records
   are clock-free**. Phase 25 health events, by contrast, genuinely need time
   (§5.7, §15). The two must not be conflated.
3. **No delivery-latency measurement exists today.** §6.6 is therefore partly new
   instrumentation, not merely aggregation.

### 2.8 Phase 24D already contains an immutable-counter precedent **[Existing]**

`delivery/telegram/integration.py`:

- `TelegramDeliveryCounters` — frozen, slotted; `delivered`, `sent`, `unknown`,
  `failed`, `not_attempted`, `skipped_duplicate`; `__post_init__` validates each is a
  non-negative `int`; `record(state)` returns `replace(self, **{name: value + 1})`;
  properties `total`, `delivered_total`.
- `TelegramDeliveryResult` — frozen; `receipt`, `outcome`, `payload`; properties
  `state`, `failure_category`, `delivery_id`, `signal_id`, `destination_id`,
  `attempt_number`, `delivered`, `was_sent`; `audit_record(chat_id=...)`.
- `TelegramDeliveryIntegration` — injected `PayloadSink`, optional coordinator,
  registry, `max_attempts`; keeps per-destination counters and exposes
  `counters(destination_id)` and a **sorted** `counter_summary` mapping.
- `TelegramPayloadBridge` — adapts the frozen `MessageSink` boundary to the payload
  boundary; refuses to send without a bound payload or on identity mismatch.

**Consequence.** The project already has a small, well-formed metrics precedent
(immutable dataclass + monotonic `record()` + exact-path validation). Phase 25
generalizes this shape rather than inventing a different one. **[Proposed]** §7.

### 2.9 Delivery orchestration is in-memory and restart-lossy **[Existing]**

- `DeliveryRegistry` — an in-process `set[str]` of delivered `delivery_id`s.
  Its docstring: "Restart persistence is out of scope"; it is the deterministic
  at-most-once guard *within a process run*.
- `DeliveryCoordinator` — injected `MessageSink | None`, optional `registry`,
  `OrchestrationConfig`, `DeliveryConfig`. `OrchestrationConfig` = `enabled`,
  `max_attempts`, `deduplicate`.
- `deliver_message` — the frozen retry/dedup loop: returns `NOT_ATTEMPTED` when no
  sink; skips when the registry already delivered; stops on `NOT_ATTEMPTED`; does
  **not** retry `UNKNOWN`; retries only `FAILED` up to `max_attempts`.

**Consequence.** Monitoring cannot assume durable history. **[Proposed]** §16.

### 2.10 Phase 23 governance is explicitly human-gated **[Existing]**

`analysis/improvement/state.py`: `HUMAN_ONLY = frozenset({APPROVED, REJECTED})`,
with `can_transition`, `transition`, `apply_human_decision`. `models.py`:
"No Phase 23 code path may produce an APPROVED or REJECTED status on its own."
`hypotheses.py`: "never sets APPROVED, never tunes a parameter, never selects a".

**Consequence.** Phase 25 may read Phase 23 *status and evidence* for reporting but
must never call `transition`/`apply_human_decision` and must never influence a
candidate. **[Proposed]** §11.6 and §20.

### 2.11 Two strong conventions Phase 25 must match **[Existing]**

1. **Strict table configuration.** Every loader (`load_delivery_config`,
   `load_orchestration_config`, `load_telegram_config`, and the 22 analysis loaders)
   reads an exact `[table]`, rejects the wrong key set with
   `AnalysisConfigurationError`, validates every value with small typed helpers, and
   returns a `frozen=True, slots=True` dataclass. `TelegramConfig` deliberately
   contains **no token and no chat id**.
2. **Deterministic serialization.** `analysis/liquidity/evidence.py` defines the
   shared canon: `_json_value` (dataclasses → dicts, `Decimal` → canonical
   scientific string, `datetime` → tz-aware UTC ISO-8601 with `Z`, `Enum` → value),
   `evidence_json` = `json.dumps(..., sort_keys=True, separators=(",", ":"),
   ensure_ascii=True)`, `canonical_bytes`, and `digest` = `sha256(canonical_bytes)`.
   Every identity in the project (`message:`, `delivery:`, `chart:`, evidence ids)
   is built on this.

### 2.12 Tests forbid the network globally **[Existing] — a hard constraint**

`tests/conftest.py` installs an **`autouse`** fixture `block_network` that
monkeypatches `socket.create_connection`, `socket.getaddrinfo`, `socket.socket.connect`,
and `socket.socket.connect_ex` to raise
`AssertionError("Network access is forbidden in unit tests; inject a fake transport")`.

**Consequence.** No Phase 25 test can perform network I/O even by accident. Any
alerting path must be injected and faked. This directly shapes §14.

### 2.13 Test and documentation conventions **[Existing]**

- Tests live in per-area directories (`tests/delivery/`, `tests/robustness/`,
  `tests/intelligence/`, …) with an optional `helpers.py` and `conftest.py`.
- Scope/isolation tests are an established pattern: `tests/delivery/test_scope.py`
  audits a package with an AST import scan against a `FORBIDDEN_TOKENS` tuple
  (including `telegram`, `requests`, `urllib`, `socket`, `ccxt`, `place_order`,
  `api_key`, `webhook`, `apply_human_decision`, `bot_token`);
  `tests/improvement/test_isolation.py` asserts the namespace drags in no
  trading/network component and does not grow the top-level facade.
- Regression guards pin historical `git` baselines and compare against exact-path
  allow-lists (see §11.7).
- Docs are indexed in `docs/README.md`; phase design docs are named
  `docs/phase24a-…`, `docs/phase24d-…`, so `phase25a-…` matches convention.
- `MANIFEST.in` contains `recursive-include docs *.md`, so a new file under
  `docs/` is packaged automatically — **no packaging change is required by this
  phase** (verified).

### 2.14 Suite and mutation reality **[Existing]**

- Full suite at this baseline: **3789 passed, 0 failed**.
- No scoreboard or histogram infrastructure exists; nothing in `src/` counts runs.

---

## 3. Monitoring boundary

### 3.1 The insertion point **[Proposed]**

A new top-level package:

```
src/smcsignal/monitoring/
```

It imports upstream (`smcsignal.data.*`, `smcsignal.analysis.*`,
`smcsignal.delivery.*`) and **nothing upstream imports it**. This mirrors the
already-approved `delivery/telegram/` isolation: the parent `smcsignal.delivery`
does not import the telegram subpackage (verified), and `smcsignal.__all__` grows
by nothing.

The dependency graph stays a DAG:

```
data → analysis → delivery → monitoring        (observation edges, one way)
```

Explicitly forbidden edges **[Proposed, enforced by test]**:

```
monitoring → signal generation        (veto / modify / reinterpret)
monitoring → halal classification
monitoring → signal eligibility
monitoring → Phase 23 human decision
monitoring → delivery retry / routing / content
monitoring → configuration mutation
monitoring → order / broker / exchange / position / leverage
```

Enforcement is a scope test in the exact style of `tests/delivery/test_scope.py`:
AST-scan every module under `src/smcsignal/monitoring/` for forbidden imports and
tokens, plus an import-graph test asserting no module outside `monitoring/`
references it.

### 3.2 Why observation is safe by construction **[Proposed]**

Three structural properties, not mere discipline:

1. **Direction.** Monitoring holds references to *already-produced immutable values*
   (`OHLCVBatch`, `ValidationReport`, `SignalSnapshot`, `DeliveryReceipt`,
   `DeliveryOutcome`, `TelegramDeliveryResult`). None of them exposes a setter, a
   policy hook, or a call back into a producer. A monitor literally has no handle
   with which to change a decision.
2. **Return values.** Every monitoring entry point returns a monitoring value
   (an event, a metric, or a health state) or `None`. No monitoring API returns a
   signal, a classification, an eligibility decision, a receipt, or a retry hint.
3. **No write path.** Monitoring never receives a caller's mutable engine, registry,
   sink, or config. It receives *results* and a config of its own.

### 3.3 Where it must NOT be inserted **[Proposed]**

Monitoring code must not be placed inside:

- any `analyze_*` / `detector` / `calculation` module (would sit on the signal path);
- `delivery/orchestrator.py` or `delivery/sink.py` (frozen; and inline hooks would
  put monitoring on the critical path and risk altering retry/dedup semantics);
- `delivery/telegram/sink.py` (frozen transport);
- `analysis/improvement/*` (governance);
- any config loader (monitoring must not be able to influence validated settings).

Instrumentation of an existing call site is **[Deferred]** to Phase 25B review; the
default is that callers *pass results in*, requiring zero changes to frozen files.

---

## 4. Components to monitor

Each component is observed through values that already exist, or through an explicit,
caller-supplied measurement. "Source" names the exact existing symbol.

| # | Component | What is observed | Source **[Existing]** | New instrumentation? |
|---|---|---|---|---|
| 4.1 | Market data acquisition | fetch success/failure, error class | `DataProvider.fetch_ohlcv`, `MarketDataError` family | no |
| 4.2 | Market data integrity | duplicates, missing, trimmed, malformed, reordered | `ValidationReport` | no |
| 4.3 | Data freshness | candle age, gap vs interval, staleness | `OHLCV.timestamp`, `candle_close_time`, `timeframe_seconds` | age measurement only |
| 4.4 | Data series identity | dataset/symbol/timeframe/provider | `SeriesProvenance` | no |
| 4.5 | Signal engine throughput | counts of `BUY_SIGNAL`/`BEARISH_AVOID`/`NO_SIGNAL` | `SignalSnapshot.status` | no |
| 4.6 | Signal engine reasons | distribution over `SignalReason` | `SignalSnapshot` / candidate | no |
| 4.7 | Signal distribution shift | rate change vs a caller-declared expectation | derived | yes (aggregation only) |
| 4.8 | Analysis latency | per-stage duration | caller-supplied monotonic readings | yes |
| 4.9 | Telegram delivery outcomes | `NOT_ATTEMPTED`, `SENT`, `DELIVERED`, `UNKNOWN`, `FAILED`, `SKIPPED_DUPLICATE` | `TelegramDeliveryResult.state` | no |
| 4.10 | Delivery failure categories | `CONFIG`, `TRANSPORT`, `TIMEOUT`, `RATE_LIMIT`, `NETWORK`, `CHART`, `BUILD` | `.failure_category` | no |
| 4.11 | Chart/document delivery | chart send failure without text invalidation | `TelegramSink.last_chart_failure`, `DeliveryOutcome.chart_present` | no |
| 4.12 | Delivery configuration | unresolvable destination, missing token, disabled transport | `NOT_ATTEMPTED` + `FailureCategory.CONFIG` | no |
| 4.13 | Rate limiting | transport rate-limit rejections | `FailureCategory.RATE_LIMIT`, `TelegramRateLimiter` | no |
| 4.14 | Delivery audit | redacted, deterministic per-delivery record | `DeliveryAuditRecord`, `TelegramAuditRecord` | no |
| 4.15 | Monitored run lifecycle | run start/end/completion | **[Proposed]** explicit session | yes |
| 4.16 | Monitoring self-health | monitor exceptions, dropped observations | **[Proposed]** internal | yes |
| 4.17 | Phase 23 operational status | candidate status, experiment outcome — **read only** | `CandidateStatus`, `ExperimentResult` | no |

**Note on 4.11 [Existing].** Chart failure isolation is already correct in the
transport: `TelegramSink._send_chart_best_effort` records a failure in
`last_chart_failure` and never changes the text receipt. Monitoring must preserve
that property, i.e. a chart-failure event is informational and must never be
reported as a text-delivery failure.

---

## 5. Health-state model

### 5.1 States **[Proposed]**

Exactly four, no more:

| State | Meaning |
|---|---|
| `HEALTHY` | Recent observations are within declared tolerance; no open anomaly. |
| `DEGRADED` | An anomaly is present but the component still performs its function. |
| `FAILING` | The component cannot perform its function (or an unrecoverable condition is open). |
| `UNKNOWN` | No valid observation, or observations are too old to be trusted. |

`UNKNOWN` is **not** invented padding: it is required because "we have not looked"
must never be reported as "healthy". This is the single most important correctness
property of an observability layer, and the brief explicitly lists `UNKNOWN` too.

Rejected alternatives: a numeric 0–100 score (implies false precision and invites
threshold-tuning, which is one step from optimization); a 5+ level scale (no
operational distinction); a boolean (cannot express DEGRADED).

### 5.2 Immutability **[Proposed]**

Health state is **immutable**: `frozen=True, slots=True`, matching every model in the
project. Transitions are pure functions `(state, event) -> state` returning a new
value. No in-place mutation, so an observer that merely holds a state cannot corrupt it.

### 5.3 Component identity **[Proposed]**

A closed `MonitoredComponent` enum (exact-path, no free strings), so events and states
cannot be filed under a typo'd or invented component:

`MARKET_DATA`, `DATA_INTEGRITY`, `SIGNAL_ENGINE`, `DELIVERY`, `TELEGRAM_TRANSPORT`,
`MONITORING`, `GOVERNANCE_OBSERVER`, `RUN_LIFECYCLE`.

### 5.4 The monitored run — the honest unit of lifecycle **[Proposed]**

Because there is no daemon (§2.2), lifecycle is expressed by an explicit,
caller-scoped **`MonitoredRun`**:

- `MonitoringSession.begin(...)` emits `RUN_STARTED` and returns a run identity;
- `session.record_*(...)` observes components during the run;
- `session.end(...)` emits `RUN_COMPLETED` or `RUN_FAILED` and returns an immutable
  `MonitoringReport`.

This gives "startup/shutdown" and "stale processing" real meaning without inventing a
scheduler. **[Deferred]** whether a future phase adds a real supervised process; that
would be a separate, larger proposal.

### 5.5 Evaluation model: synchronous, explicit, no threads **[Proposed]**

Health is a **pure function of an ordered event/metric sequence plus an injected
clock**. All entry points are synchronous and non-blocking. No threads, no asyncio, no
background timers — consistent with §2.3 and avoidable complexity. If a future phase
needs continuous evaluation, it can call the same pure function on a schedule
externally; the evaluation itself stays deterministic and testable.

### 5.6 Rollup and precedence **[Proposed]**

Overall health is derived, not stored, with explicit precedence:

`FAILING` > `DEGRADED` > `UNKNOWN` > `HEALTHY`

i.e. worst-known wins, and `UNKNOWN` outranks `HEALTHY` (an unobserved component
prevents a false all-clear). The rollup is a declared, documented function over the
component map — never a weighted score.

### 5.7 Time is injected, never ambient **[Proposed]**

Every model that needs time takes it as an explicit argument. **No call to
`datetime.now()`, `time.time()`, or `time.monotonic()` may appear inside a monitoring
computation.** Two clocks are taken as separate injected callables:

- `wall_clock() -> datetime` — tz-aware UTC, used only for `observed_at` in events;
- `monotonic_clock() -> float` — used for all durations/latency, so a wall-clock
  adjustment (NTP step, DST-independent UTC jump) cannot produce a negative latency.

This resolves "how should timestamps be represented" and "how should clock problems be
handled" (§21, D-4/D-13): wall time is for human-facing attribution, monotonic time is
for measurement, and a regression in either is itself an event.

---

## 6. Event model

### 6.1 `HealthEvent` **[Proposed]**

A frozen, slotted dataclass:

| Field | Type | Notes |
|---|---|---|
| `event_id` | `str` | deterministic content identity, prefix `health:` |
| `component` | `MonitoredComponent` | closed enum (§5.3) |
| `severity` | `Severity` | `INFO`, `WARNING`, `CRITICAL` |
| `code` | `HealthCode` | closed enum of conditions (§6.3) |
| `observed_at` | `datetime` | tz-aware UTC, injected wall clock |
| `subject_id` | `str \| None` | e.g. redacted destination, `signal:`/`delivery:` id |
| `detail` | `tuple[tuple[str, str], ...]` | canonical, sorted, secret-free key/value pairs |
| `run_id` | `str \| None` | the `MonitoredRun` this belongs to |

Validation follows house style: closed enums (not strings), tz-aware timestamps,
positive ints where applicable, and a `subject_id` that is **already redacted** where it
is sensitive (§13).

### 6.2 Deterministic event identity and deduplication **[Proposed]**

```
event_id = "health:" + sha256(canonical_json({
    "methodology": "monitoring-event-v1",
    "component": <value>, "severity": <value>, "code": <value>,
    "subject_id": <value or null>, "detail": <sorted pairs>,
}))
```

`run_id` and `observed_at` are **excluded** from the identity, so:

- the same condition recurring in the same run or a later run yields the **same
  `event_id`** — which makes deduplication possible;
- deduplication then aggregates rather than spams: a repeated identical failure
  produces one `HealthEventAggregate` with `count`, `first_seen`, `last_seen`,
  instead of N events.

This is the concrete answer to "how should repeated identical failures be
deduplicated" (§21, D-11). Distinct subjects (different destinations) remain distinct
events because `subject_id` participates in the identity.

`canonical_json` **[Existing]** is `evidence_json` from
`analysis/liquidity/evidence.py`; `digest` is reused unchanged.

### 6.3 Condition codes **[Proposed]**

A closed enum, grouped by component (illustrative, to be frozen in 25B):

- lifecycle: `RUN_STARTED`, `RUN_COMPLETED`, `RUN_FAILED`, `RUN_STALE`
- monitoring self: `MONITOR_INTERNAL_FAILURE`, `MONITOR_OBSERVATION_DROPPED`,
  `CLOCK_REGRESSION`, `CLOCK_UNREADABLE`
- data acquisition: `DATA_FETCH_FAILED`, `DATA_PROVIDER_HTTP_ERROR`,
  `DATA_RATE_LIMITED`, `DATA_UNAVAILABLE`
- data integrity: `DATA_DUPLICATES_REMOVED`, `DATA_MISSING_ROWS_DROPPED`,
  `DATA_INCOMPLETE_ROWS_DROPPED`, `DATA_ROWS_TRIMMED`, `DATA_REORDERED`,
  `DATA_CONFLICTING_DUPLICATE`, `DATA_MALFORMED`
- freshness: `DATA_STALE`, `DATA_GAP`, `DATA_INTERVAL_ANOMALY`, `DATA_FUTURE_TIMESTAMP`
- signal engine: `SIGNAL_ENGINE_ERROR`, `SIGNAL_DISTRIBUTION_SHIFT`,
  `SIGNAL_PRODUCTION_STALLED`, `SIGNAL_LATENCY_HIGH`
- delivery: `DELIVERY_FAILED`, `DELIVERY_UNKNOWN`, `DELIVERY_NOT_ATTEMPTED`,
  `DELIVERY_TIMEOUT`, `DELIVERY_RATE_LIMITED`, `DELIVERY_HTTP_FAILURE`,
  `DELIVERY_CONFIG_ERROR`, `DELIVERY_REPEATED_FAILURES`, `CHART_DELIVERY_FAILED`
- governance observation: `GOVERNANCE_STATUS_OBSERVED` (informational only)

Codes are **conditions**, never instructions. There is no code named "retry",
"skip", "disable", or "reload", because no event may imply an action.

### 6.4 Severity is a property of the code, not the caller **[Proposed]**

Each code maps to a default severity in one declared table, so severity cannot be
inflated or suppressed at a call site. A `WARNING`/`CRITICAL` designation for a code
is fixed at review time.

### 6.5 Events are additive and non-mutating **[Proposed]**

Recording an event returns a new immutable aggregate; it cannot alter any previously
recorded event, any metric, or any delivery value. A `NullMonitor` **[Proposed]** (a
no-op observer, mirroring the existing `NullSink` precedent) allows call sites to run
with monitoring disabled at zero cost and zero behavior change.

---

## 7. Metrics model

### 7.1 Shapes **[Proposed]**

- `CounterMetric` — immutable `int` total, exact-path validated non-negative
  (`bool` rejected), `increment(n=1) -> CounterMetric`. Generalizes the
  `TelegramDeliveryCounters` shape **[Existing]** (§2.8).
- `DurationMetric` — `count: int`, `total: timedelta`, `min: timedelta`,
  `max: timedelta`. `timedelta` is exact, so no float error accrues; there is no
  percentile machinery (see §7.4).
- `RatioMetric` — `numerator: int`, `denominator: int`, exposed as a `Decimal`
  computed at read time with an explicit quantize/precision policy. **`Decimal`, not
  `float`**, matching the project's Decimal discipline.
- `GaugeMetric` — a latest-value snapshot with `observed_at`, used for things like
  data age and current health.
- `MetricSummary` — a frozen, sorted, deterministic container of all of the above.

### 7.2 Which metrics require exact types **[Proposed]**

| Quantity | Type | Why |
|---|---|---|
| counts (attempted, sent, failed, unknown, duplicates) | `int` | discrete, exact |
| latency / age / staleness | `timedelta` | exact, no float drift |
| rates and success ratios | `Decimal` | exact rational reporting, house style |
| thresholds and tolerances | `Decimal` | compared exactly, configures cleanly |
| wall-clock instants | `datetime` (UTC) | attribution only, never arithmetic on durations |

`float` is used **nowhere** in monitoring arithmetic, so that a metric never varies
with platform float formatting. Serialization follows `evidence_json`, which already
renders `Decimal` canonically **[Existing]**.

### 7.3 Required metric set (phase 1) **[Proposed]**

- delivery: attempted, `SENT`, `DELIVERED`, `UNKNOWN`, `FAILED`, `NOT_ATTEMPTED`,
  `SKIPPED_DUPLICATE`, by failure category, by redacted destination, plus delivery
  duration where the caller supplies monotonic readings;
- charts: chart sends attempted/succeeded/failed (kept strictly separate from text);
- signal: total evaluated, per-status counts, per-reason counts, throughput per run;
- data: rows in/out, duplicates removed, missing dropped, incomplete dropped,
  trimmed, reordered flag, candles per run, observed max gap vs expected interval,
  data age at observation;
- run: runs started/completed/failed, events by severity, monitoring self-failures.

### 7.4 Deliberately excluded **[Deferred]**

Percentiles/quantiles, histograms, EWMA/rolling windows over wall clock, and any
adaptive baseline. They add non-determinism and drift toward "tuning", which is
out of scope for an observability layer. Revisit only with an explicit requirement.

### 7.5 Windowing **[Proposed]**

Windows are **explicit and supplied by the caller** (a run id, or an ordered index
range), never derived from the wall clock. So "last N observations" is deterministic
and reproducible in tests, while a wall-clock window is not. Wall-clock windowing is
**[Deferred]**.

---

## 8. Data freshness monitoring

### 8.1 Principle **[Proposed]**

Monitoring **observes the pipeline's own outputs** and never repairs, re-validates,
re-orders, re-fetches, or substitutes data. This is already the data layer's stated
contract **[Existing]**: providers "never substitute synthetic data or silently switch
to another source".

### 8.2 Duplicates and malformed rows — consume, do not re-derive **[Proposed]**

`ValidationReport` **[Existing]** is read as-is:

| Report field | Interpretation |
|---|---|
| `duplicates_removed > 0` | `DATA_DUPLICATES_REMOVED` (INFO/WARNING by declared threshold) |
| `missing_rows_dropped > 0` | `DATA_MISSING_ROWS_DROPPED` |
| `incomplete_rows_dropped > 0` | `DATA_MALFORMED` / `DATA_INCOMPLETE_ROWS_DROPPED` |
| `rows_trimmed > 0` | `DATA_ROWS_TRIMMED` (informational) |
| `reordered is True` | `DATA_REORDERED` (the input was not ascending) |
| `input_rows != output_rows` | reconciliation; consistency check on the report itself |

A **conflicting duplicate** already raises in `normalize_ohlcv` **[Existing]**; the
monitor observes the raised `DataValidationError` type as `DATA_CONFLICTING_DUPLICATE`
and records it. Monitoring never suppresses that exception and never changes the policy.

### 8.3 Gaps and interval anomalies **[Proposed]**

Given an ascending `OHLCVBatch` and its `timeframe`:

- expected step = `timeframe_seconds(timeframe)` **[Existing]** (exact; not applicable
  to `1M`, which that function rejects — so `1M` freshness is **[Deferred]** to an
  explicit calendar rule);
- a gap where `next.timestamp - current.timestamp > expected` yields `DATA_GAP` with
  the shortfall as a `timedelta`;
- a non-multiple step yields `DATA_INTERVAL_ANOMALY`;
- `next.timestamp <= current.timestamp` yields `DATA_REORDERED`/`DATA_INTERVAL_ANOMALY`
  (the data layer should already have ordered it, so this is a real anomaly);
- a timestamp materially after the injected observation time yields
  `DATA_FUTURE_TIMESTAMP` (CRITICAL: a look-ahead indicator).

Computation is read-only over the immutable tuple. **The monitor never drops,
inserts, or shifts a candle.**

### 8.4 Staleness **[Proposed]**

```
age = wall_clock_now - last_candle.closed_at   (computed via injected clock)
```

- `age > stale_after` ⇒ `DATA_STALE` (severity by threshold band);
- expressed against `timeframe_seconds` with an explicit, configurable multiplier
  rather than an invented constant;
- negative `age` (candle in the future) ⇒ `DATA_FUTURE_TIMESTAMP`, never treated as
  "very fresh";
- no candles at all ⇒ `DATA_UNAVAILABLE`, and component health becomes `UNKNOWN`
  (not `HEALTHY`) — §5.1.

`stale_after` is configured, not hard-coded, and **never** modifies the pipeline.

---

## 9. Signal-engine monitoring

### 9.1 Observe only **[Proposed]**

The monitor receives already-computed `SignalSnapshot` values (or their `status`/
`reason`) and counts them. It never constructs a snapshot, never calls an analyzer,
never re-evaluates a gate, and never provides a veto. There is no API shape in which a
monitoring result can be fed back into signal production — structurally, because the
snapshot is frozen and the monitor returns only monitoring values (§3.2).

### 9.2 Distribution and shift detection **[Proposed]**

- counts per `SignalStatus` (`BUY_SIGNAL`, `BEARISH_AVOID`, `NO_SIGNAL`) **[Existing]**;
- counts per `SignalReason` over the 14 existing members **[Existing]**;
- production rate per run (per evaluated candle and per run);
- a shift is reported as `SIGNAL_DISTRIBUTION_SHIFT` when the observed `BUY_SIGNAL`
  share leaves a **caller-declared** band (`Decimal` bounds, exact comparison).

Crucially, a shift is **informational**. It must not pause, suppress, gate, or
override a signal, and the design provides no such capability. There is no
"auto-disable on anomaly" feature and none will be added.

### 9.3 Stalls and errors **[Proposed]**

- zero evaluations in a run that expected candles ⇒ `SIGNAL_PRODUCTION_STALLED`;
- an exception raised by an analyzer is observed by **type** (e.g. `AnalysisInputError`,
  `AnalysisConfigurationError` **[Existing]**) as `SIGNAL_ENGINE_ERROR` — and is
  re-raised to the caller unchanged. Monitoring never swallows a production error;
  the caller decides.

### 9.4 Latency **[Proposed]**

Per-stage durations come from caller-supplied monotonic readings, wrapped by a small
helper (§4.8). This is genuinely new instrumentation, and the design keeps it
**outside** the analyzer: the caller times the call, so no frozen module changes.

### 9.5 Explicit negative guarantee **[Proposed]**

Stated once, unambiguously: **no monitoring result can change a signal's existence,
status, direction, reason, identity, or eligibility.** If it ever can, the design has
failed. §18.4 tests this directly.

---

## 10. Telegram delivery monitoring

### 10.1 Consume existing outcomes **[Proposed]**

The natural input is the already-frozen `TelegramDeliveryResult` and/or
`DeliveryReceipt` **[Existing]**. `TelegramDeliveryResult` already exposes `state`,
`failure_category`, `delivery_id`, `signal_id`, `destination_id`, `attempt_number`,
`delivered`, `was_sent` — precisely a monitoring event source, with no new coupling.

### 10.2 Observation table **[Proposed]**

| Observed | Event | Severity |
|---|---|---|
| `DELIVERED` | success counter (no event by default) | — |
| `SENT` | success counter, distinct from `DELIVERED` | — |
| `UNKNOWN` | `DELIVERY_UNKNOWN` | WARNING (ambiguous, not retried) |
| `FAILED` + `TRANSPORT` | `DELIVERY_FAILED` | WARNING |
| `FAILED` + `TIMEOUT` | `DELIVERY_TIMEOUT` | WARNING |
| `FAILED` + `RATE_LIMIT` | `DELIVERY_RATE_LIMITED` | WARNING |
| `FAILED` + `CONFIG` | `DELIVERY_CONFIG_ERROR` | CRITICAL (operator action) |
| `FAILED` + `CHART` | `CHART_DELIVERY_FAILED` | INFO/WARNING, text unaffected |
| `NOT_ATTEMPTED` | `DELIVERY_NOT_ATTEMPTED` | INFO (disabled/absent token) |
| `SKIPPED_DUPLICATE` | dedup counter only | — |
| N consecutive `FAILED` to one destination | `DELIVERY_REPEATED_FAILURES` | CRITICAL |

`UNKNOWN` is deliberately a WARNING, not a failure: the frozen contract treats it as
"may or may not have delivered" and never retries it **[Existing]**. Reporting it as
FAILED would misrepresent the frozen semantics.

### 10.3 Chart isolation preserved **[Proposed]**

A chart failure is reported against the chart, never as a text-delivery failure,
mirroring `TelegramSink.last_chart_failure` **[Existing]**. The text receipt is the
sole authority for text delivery.

### 10.4 Downstream-only guarantee **[Proposed]**

Monitoring cannot re-send, retry, reorder, re-target, alter a caption, change parse
mode, adjust the rate limiter, or mark a delivery delivered. It receives results and
returns counters. The retry/dedup loop in `deliver_message` **[Existing]** remains the
only thing that decides retries.

### 10.5 Destinations are redacted **[Proposed]**

Monitoring records destinations only via the existing `redact()` **[Existing]**
(`<redacted>:1234`). Raw chat ids never enter an event, a metric, or a log (§13).

### 10.6 Repeated failures **[Proposed]**

Consecutive failures per redacted destination are counted. The threshold is
**configuration**, and crossing it emits an event — never a behavioral change. No
monitoring feature disables a destination or a transport.

---

## 11. Failure isolation

### 11.1 Isolation matrix **[Proposed]**

| A fails | Effect on B |
|---|---|
| Telegram delivery | signal generation unaffected; only delivery health degrades |
| Chart rendering/sending | text delivery unaffected (already true **[Existing]**) |
| Signal engine | delivery never invoked; delivery health becomes `UNKNOWN`, not `FAILING` |
| Market data | downstream becomes `UNKNOWN`/`FAILING`; no synthetic data is invented |
| **Monitoring** | production entirely unaffected (the core requirement) |
| One monitored component | other components' health is unchanged; rollup reflects the worst |

### 11.2 Monitoring never propagates an exception **[Proposed]**

Every observation is wrapped so that an exception inside monitoring is converted into
a `MONITOR_INTERNAL_FAILURE` event and **swallowed** — never re-raised into a
production call path. This is the deliberate asymmetry with §9.3: a *production*
exception is observed and re-raised; a *monitoring* exception is contained.

### 11.3 Monitoring is off the critical path **[Proposed]**

No monitoring entry point performs I/O, blocks on a lock, spawns a thread, sleeps, or
retries. Worst-case cost is bounded, in-memory arithmetic plus a hash. If a
subsystem is configured with a `NullMonitor`, cost is a no-op call.

### 11.4 Self-defense against alert storms **[Proposed]**

The dedup of §6.2 bounds event growth; alerting (§14) is rate-limited **independently
of** the delivery rate limiter, so a monitoring storm can never consume the transport's
send budget.

### 11.5 Monitoring failure is never silent **[Proposed]**

Containment must not mean invisibility: self-failures increment a counter and raise a
`MONITOR_INTERNAL_FAILURE` event so an operator learns that their *view* is broken.
`UNKNOWN` health (§5.1) is the default when monitoring stops producing observations.

### 11.6 Phase 23 isolation **[Proposed]**

Monitoring may **read** candidate status, hypothesis records, experiment results, and
evidence for reporting. It must never call `transition` or `apply_human_decision`
**[Existing, human-only]**, never approve/reject, never rank candidates, never select
a strategy, never modify config, and never deploy anything. Reading is via the
published reporting surfaces (`reporting.py`, `surfaces.py`). §20 forbids it outright.

### 11.7 Regression-guard interaction **[Existing]**

The two historical guards
(`tests/robustness/test_regression.py::test_decision_modules_are_unmodified_since_the_phase20_baseline`,
`tests/intelligence/test_regression.py::test_phase22_sources_are_the_only_src_changes_from_baseline`)
compare `git diff` against the Phase 20/21 baselines using **exact-path** allow-lists,
including the Phase 24 `delivery/` paths. **Consequence:** adding
`src/smcsignal/monitoring/` will make those guards fail until the new paths are added
to both allow-lists — the same maintenance already performed once. This is expected
and must be handled as explicit, reviewed allow-list maintenance (exact paths only,
never a wildcard or prefix), not by weakening the guards.

---

## 12. Logging and audit design

### 12.1 Current state **[Existing]**

There is **no logging anywhere** in `src/` (§2.2). Existing "audit" artifacts are
frozen dataclasses (`DeliveryAuditRecord`, `TelegramAuditRecord`) produced by pure
projection, deliberately clock-free and secret-free.

### 12.2 Proposed logging stance **[Proposed]**

- Monitoring **does not configure or install** a logger and **does not import
  `logging` at module scope in a way that emits by default**. Emitting must be
  opt-in via an injected sink.
- The primary artifact is a **structured, serializable record** (event/metric),
  produced deterministically. Human-readable rendering is a *separate, optional*
  formatter, so the data layer stays pure and testable.
- If `logging` is used for operator output, it is only through an injected adapter,
  and it emits **already-redacted** values. Monitoring never logs raw ids, tokens,
  chat ids, or captions.

### 12.3 Two artifact families, deliberately distinct **[Proposed]**

| Family | Timestamps | Determinism | Purpose |
|---|---|---|---|
| Delivery/Telegram audit **[Existing]** | none (by design) | full | exactly reproducible delivery record |
| Monitoring events **[Proposed]** | `observed_at` from injected clock | deterministic *given* the same injected clock and sequence | operational timeline |

Conflating them would either break delivery-audit determinism or make operational
timelines untimeable. Keeping them separate is a design requirement, not a detail.

### 12.4 Serialization **[Proposed]**

Every monitoring model exposes a `to_record()` producing only `str`/`int`/`bool`/`None`
plus canonical strings, so it round-trips through `evidence_json` **[Existing]** and is
safe to store or transmit. `datetime` → UTC ISO-8601 with `Z`; `timedelta` → an exact
total-milliseconds string; `Decimal` → the existing canonical string form; enums →
their `.value`. No floats, no `repr()`, no object addresses, no `id()`.

### 12.5 Report shape **[Proposed]**

`MonitoringReport` — frozen: run identity, component health map (sorted), metric
summary, event aggregates (sorted by `event_id`), and a deterministic `report_id`
digest over the whole content (mirroring `report_id`/`review_id`/`outcome_id`
conventions **[Existing]**).

---

## 13. Secret-safety design

### 13.1 Rules **[Proposed]**

1. **Never store** a bot token, api key, chat id, raw destination, webhook secret, or
   credential in any monitoring event, metric, report, or log.
2. **Redact by construction**: destinations enter only via `redact()` **[Existing]**;
   monitoring models accept a pre-redacted `subject_id` and validate the shape rather
   than accepting a raw id.
3. **No secrets in exceptions.** Monitoring must not include raw exception `repr` for
   transport failures in an event (it could embed a URL containing a token). Only the
   exception *type* and a closed-set reason are recorded.
4. **Config carries no secrets.** Following `TelegramConfig` **[Existing]**, the
   monitoring config table contains no token/chat id; thresholds and switches only.
5. **Token hygiene on the delivery side is unchanged** **[Existing]**:
   `TelegramSink` takes the token by injection; `redact_token` returns a fixed marker;
   the token lives only inside the HTTP client.
6. **Tests assert absence**: every monitoring model is checked to contain no
   token-shaped literal and no raw destination.

### 13.2 Threat model **[Proposed]**

Primary risk is **accidental disclosure via logs and reports** (a pasted report, a
shared log file), not an external attacker. Hence: redaction at the boundary,
canonical closed-set fields, no free-form text carrying identifiers.

### 13.3 Privacy **[Proposed]**

No personal data is collected. No user/account identifiers, no message content beyond
the existing deterministic `message_digest` **[Existing]**, and no caption text is
stored in monitoring artifacts. Telegram remains the only external system, and
monitoring adds no new outbound flow (alerting is deferred, §14).

---

## 14. Alerting boundary

### 14.1 Decision: **do not** reuse the existing delivery transport **[Proposed]**

Operational alerts must **not** be sent through the existing `TelegramSink` /
`TelegramDeliveryIntegration` instance. Reasons, in order of weight:

1. **Feedback loop.** Monitoring observes delivery; delivering *through* the observed
   component makes monitoring a participant in it, and a delivery failure would
   silence the alarm about that failure — the worst possible failure mode.
2. **Blast radius.** A token/destination misconfiguration that breaks delivery would
   also break alerting; one injected alert sink must be able to serve a *different*
   destination and credential.
3. **Budget contention.** The transport has a token-bucket limiter **[Existing]**. An
   alert storm consuming that budget would suppress customer-facing signals — a
   monitoring-induced production incident.
4. **Noise and loops.** Alert-loops-into-alerting could recurse; the delivery path has
   no notion of "this is a system message".
5. **Test discipline.** The global `block_network` fixture **[Existing]** already
   mandates injected, faked transports; a separate alert abstraction keeps that clean.

### 14.2 Proposed shape **[Proposed]**

An `AlertSink` **protocol** with a no-op `NullAlertSink` default (mirroring the
existing `NullSink` precedent **[Existing]**):

```
alert(event) -> None      # best-effort, must never raise into production
```

- Alerting is **opt-in and injected**; absent injection, nothing is sent.
- A future `TelegramAlertSink` would be a **separate transport instance** with its own
  token, destination, and rate limiter — never the delivery sink instance.
- The alert path is **rate-limited independently** (§11.4) and applies the same
  deduplication.
- Alert sink exceptions are contained exactly like monitoring exceptions (§11.2).

### 14.3 What is not decided here **[Deferred]**

Whether alerts go to Telegram at all, to a log file, to an email/webhook, or nowhere;
severity→channel routing; quiet hours; escalation. All deferred to a reviewed Phase
25E. **No alerting is implemented now**, and the design deliberately makes it optional
so the rest of Phase 25 ships with alerting absent.

---

## 15. Determinism requirements

### 15.1 Obligations **[Proposed]**

1. **No ambient time.** All time is injected (§5.7).
2. **No randomness.** No `random`, no UUIDs, no `hash()` of mutable objects.
3. **No environment dependence.** No env-var reads, no locale, no filesystem scan
   order, no dict-iteration-order dependence (all outputs sorted).
4. **No floats in arithmetic** (§7.2).
5. **Stable identities.** `event_id` is content-derived (§6.2), built on the existing
   `digest` **[Existing]**, which already renders `datetime`/`Decimal` canonically.
6. **Pure transitions.** Health transitions and aggregations are pure functions of
   (state, ordered observations, injected clock).
7. **Reproducible reports.** The same injected clock values, config, and observation
   sequence must yield a byte-identical `MonitoringReport`.

### 15.2 Where determinism is legitimately relaxed **[Proposed]**

`observed_at` reflects real time in production; a report is therefore deterministic
**given** the injected clock, not clock-independent. The testing strategy (§18)
supplies fixed clocks, matching how the project already tests time-dependent behavior.

### 15.3 Consequence **[Proposed]**

Monitoring artifacts can be snapshot-tested and diffed across runs, and a regression in
monitoring behavior is detectable exactly like any other project behavior.

---

## 16. Persistence considerations

### 16.1 Decision: **in-memory only** for the first implementation **[Proposed]**

Consistent with `DeliveryRegistry` ("Restart persistence is out of scope") and with the
project's explicit no-database stance **[Existing]**. No database, no files, no
network. A database would also be the first mutable state store in the project and
would need its own operational and privacy review.

### 16.2 Serialization contract enables later persistence **[Proposed]**

Because every model has a deterministic `to_record()` (§12.4), a future
`MonitoringStore` **protocol** can persist them without touching monitoring logic. The
design fixes the *shape* now, the *storage* later.

### 16.3 Restart behavior — specified, not accidental **[Proposed]**

- On a new `MonitoredRun`, a `RUN_STARTED` event is emitted.
- All health states start as `UNKNOWN` (§5.1) — never `HEALTHY` — because nothing has
  been observed yet in this run. This is the honest answer to "what happens after
  process restart".
- Counters reset per run; cross-run comparison is **[Deferred]** until a store exists.
- No state is inferred across restarts: a monitor must not assume a previous run's
  conditions still hold.

### 16.4 Retention **[Deferred]**

Retention, rotation, and compaction are deferred with persistence.

---

## 17. Configuration considerations

### 17.1 A strict table, per house convention **[Proposed]**

A new `[monitoring]` table read by `load_monitoring_config(path)`, with:

- exact key set, unknown keys rejected, `AnalysisConfigurationError` on bad values;
- `frozen=True, slots=True` `MonitoringConfig`;
- **no secrets, no tokens, no chat ids**;
- every value validated by small typed helpers following `TelegramConfig`
  **[Existing]**.

### 17.2 Likely keys **[Proposed]**

`enabled` (default `False` — opt-in, matching `TelegramConfig.enabled`),
`stale_after_intervals` (a multiplier over `timeframe_seconds`), `gap_tolerance`,
`repeated_failure_threshold`, `distribution_shift_bounds` (Decimal low/high),
`max_events_per_run`, `alert_enabled` (default `False`), `alert_min_severity`.
Exact names/defaults frozen in 25B.

### 17.3 Hard constraints on configuration **[Proposed]**

No key may reference a signal, SMC/ICT threshold, indicator, halal rule, eligibility,
governance decision, delivery routing, or execution. Monitoring configuration controls
**observation only**. A scope test asserts the config field names contain none of the
forbidden concepts (same idea as the existing `config/README.md` guarantees and
`test_template_keeps_future_capabilities_disabled` **[Existing]**).

### 17.4 Packaging **[Existing]**

`recursive-include docs *.md` already covers the design doc, so **this phase needs no
`MANIFEST.in` or packaging change**. A future implementation phase would add
`config/monitoring.example.toml` plus an explicit `include config/monitoring.example.toml`
line, mirroring the existing `include config/telegram.example.toml` entry **[Existing]**.

---

## 18. Testing strategy

Deterministic, offline, injected-clock, no production mutation. All tests inherit the
`autouse` `block_network` guard **[Existing]**, so no test can reach the network.

### 18.1 Test groups **[Proposed]**

| Area | Tests |
|---|---|
| Health transitions | table-driven `(state, code, severity) -> state`; worst-wins precedence; `UNKNOWN` outranks `HEALTHY`; `FAILING` first |
| Event creation | closed-enum validation; severity comes from the code table; bad component/code rejected |
| Event identity/dedup | same condition ⇒ same `event_id`; different subject ⇒ different id; aggregates carry `count`/`first_seen`/`last_seen` |
| Metric aggregation | counters monotonic and exact; `timedelta` min/max/total; `Decimal` ratios quantized as specified; `bool` rejected as an int |
| Stale-data detection | age vs `stale_after`; boundary exactness; zero candles ⇒ `UNKNOWN` |
| Duplicate detection | `ValidationReport` counts map to the right codes; conflicting duplicate raises and is observed |
| Gap/interval | exact gaps vs `timeframe_seconds`; non-multiple step; out-of-order; `1M` deferred path |
| Processing latency | monotonic-derived durations; non-monotonic readings ⇒ `CLOCK_REGRESSION` |
| Telegram outcome observation | every `DeliveryState` × `FailureCategory` mapping, incl. `UNKNOWN` as WARNING and `CONFIG` as CRITICAL |
| Chart isolation | chart failure does not become a text failure |
| Repeated failures | threshold from config; independent of the transport limiter |
| Failure isolation | injected monitor raising does not propagate to the caller; production error still re-raised unchanged |
| Restart behavior | new run ⇒ `RUN_STARTED`; all health `UNKNOWN`; counters reset |
| Serialization | `to_record()` keys/types; round-trip through `evidence_json`; sorted output |
| Secret redaction | no token-shaped literal, no raw chat id, no caption text in any record |
| Determinism | same clock+config+sequence ⇒ identical report and `report_id`; repeated runs byte-identical |
| Scope boundaries | AST import scan for forbidden modules/tokens; no upstream module imports monitoring; config field-name screen |

### 18.2 Negative tests — monitoring cannot change production **[Proposed]**

These mirror the existing `test_scope.py` / `test_isolation.py` precedent and are the
most important tests in the phase:

1. A `SignalSnapshot` observed by the monitor is byte-identical afterwards (frozen, so
   this also proves no mutation channel exists).
2. A `ValidationReport`/`OHLCVBatch` is unchanged; no candle is added, removed, or
   reordered by monitoring.
3. Halal classification and eligibility values are unchanged; monitoring exposes no
   API that accepts or returns them for modification.
4. Risk-related outputs (if any are observed) are unchanged.
5. A `DeliveryReceipt`/`TelegramDeliveryResult` is unchanged, and monitoring cannot
   cause a re-send: the fake transport is asserted to have received exactly the same
   calls with monitoring enabled and disabled.
6. `apply_human_decision`/`transition` are never imported or called from monitoring
   (AST scan + a runtime assertion that a candidate's status is untouched).
7. With monitoring enabled vs a `NullMonitor`, all upstream outputs are identical.
8. A raising monitor leaves the caller's result and exception semantics identical.

### 18.3 Isolation from the frozen suite **[Existing]**

Because nothing upstream imports `monitoring/`, existing tests are unaffected, and the
new tests cannot perturb the 3789-test baseline. The only expected collateral is the
**allow-list maintenance** described in §11.7, handled explicitly.

### 18.4 Verification gate **[Proposed]**

Phase 25 implementation is not "done" until: `ruff check`, `ruff format --check`,
`mypy --strict`, the monitoring suite, the full suite, the package build, the isolated
wheel import audit, and the scope/downstream audit all pass — the same battery used for
Phase 24.

---

## 19. Security and privacy considerations

- **Secrets (repeated because it is the top risk):** redaction by construction,
  closed-set fields, no exception text, no config secrets (§13).
- **Least privilege:** monitoring needs read access to results only. It should require
  no new credential, and any future alert credential is separate from the delivery
  credential (§14.1).
- **Injection surface:** monitoring consumes immutable values and cannot issue commands
  to any system; there is no eval/exec/dynamic import and no shell access.
- **Integrity of the view:** if monitoring is compromised or buggy, the worst outcome
  is a wrong *report*; it cannot cause a false signal, change a classification, or
  trigger a delivery. This containment is the design's security property.
- **Availability:** monitoring must not be able to make production unavailable —
  no blocking, no I/O, exceptions contained (§11).
- **Data minimization:** no personal data, no caption text, no raw identifiers (§13.3).
- **No look-ahead:** the future-timestamp check (§8.3) guards against a monitoring
  *report* implying data availability that does not exist; monitoring never alters data.

---

## 20. Explicit non-goals

Phase 25 does **not** and will **not**:

1. Change signal generation, SMC/ICT detection, indicators, halal classification,
   setup quality, eligibility, or the signal engine.
2. Retry, re-send, re-route, suppress, or edit any Telegram delivery.
3. Repair, fill, drop, reorder, or substitute market data.
4. Veto, gate, modify, reinterpret, or create a signal based on any observation.
5. Modify any configuration, threshold, weight, or rule.
6. Approve, reject, rank, or select anything in Phase 23, or bypass human governance.
7. Optimize, auto-tune, or self-improve any parameter.
8. Execute orders, or touch brokers, exchanges, positions, leverage, or margin.
9. Add exchange authentication, websockets, or any inbound Telegram flow
   (no polling, no webhooks, no commands, no inbound message processing).
10. Add a database or any durable state store.
11. Introduce multi-channel routing or fan-out.
12. Add percentile/baseline/adaptive analytics (§7.4).
13. Populate or redefine the frozen `DeliveryReceipt.attempted_at` field (§2.7).
14. Change `DeliveryAuditRecord`/`TelegramAuditRecord` or their clock-free determinism.
15. Add a long-running daemon or scheduler (§5.4).
16. Implement alerting (design only, §14).

---

## 21. Open design decisions

| # | Decision | Resolution |
|---|---|---|
| D-1 | What is a health event? | **[Resolved]** A frozen, content-identified record of a *condition* on a component, with closed component/severity/code enums, an injected `observed_at`, an optional redacted subject, and canonical detail pairs (§6). |
| D-2 | What is a degraded component? | **[Resolved]** An open anomaly while the component still functions; `FAILING` when it cannot. Precedence `FAILING > DEGRADED > UNKNOWN > HEALTHY` (§5.6). |
| D-3 | Should health state be immutable? | **[Resolved]** Yes — frozen dataclasses and pure transitions, matching every project model (§5.2). |
| D-4 | How are timestamps represented? | **[Resolved]** Injected tz-aware UTC `datetime` for attribution; injected monotonic for durations; never ambient (§5.7, §15.1). |
| D-5 | Which metrics require `Decimal`? | **[Resolved]** Ratios/rates/thresholds; counts are `int`, durations `timedelta`; no floats (§7.2). |
| D-6 | Synchronous or asynchronous? | **[Resolved]** Synchronous, non-blocking, no threads (§5.5). |
| D-7 | Where do hooks exist? | **[Resolved]** At caller boundaries — results are passed in; no hooks inside frozen modules. Call-site instrumentation, if any, is **[Deferred]** to 25B review (§3.3). |
| D-8 | Consume existing immutable outcomes? | **[Resolved]** Yes — `ValidationReport`, `SignalSnapshot`, `TelegramDeliveryResult`, `DeliveryReceipt`, audit records. Do not re-derive (§2.4, §8.2, §10.1). |
| D-9 | Deduplicate repeated failures? | **[Resolved]** Content-derived `event_id` excluding time/run; aggregates with `count`/`first_seen`/`last_seen` (§6.2). |
| D-10 | Persist across restarts? | **[Resolved]** No — in-memory first; a `to_record()` contract enables a future store (§16.1, §16.2). |
| D-11 | What happens after restart? | **[Resolved]** New run, `RUN_STARTED`, all health `UNKNOWN`, counters reset (§16.3). |
| D-12 | How are clock problems handled? | **[Resolved]** Dual injected clocks; detect and report non-monotonic readings/wall regressions as `CLOCK_REGRESSION`; never derive a negative duration (§5.7, §18.1). |
| D-13 | Minimum overhead? | **[Resolved]** Bounded in-memory arithmetic plus one hash per event; no I/O, sleeps, locks, or threads; `NullMonitor` costs a no-op call (§11.3, §6.5). |
| D-14 | Operator-visible surface? | **[Proposed]** A deterministic `MonitoringReport` (health map, metrics, event aggregates) plus optionally injected formatters; the CLI is **not** changed in this design. **[Deferred]** whether `cli.py` grows a status view. |
| D-15 | Do Telegram alerts use `MessageSink`/`PayloadSink`? | **[Resolved]** No — a separate `AlertSink` protocol with a null default; a future Telegram alert sink is a *separate* transport instance with its own credential/limiter (§14). |
| D-16 | How is monitoring kept out of the upstream dependency graph? | **[Resolved]** `smcsignal.monitoring` is imported by nothing upstream; enforced by an AST/import-graph scope test; `smcsignal.__all__` does not grow (§3.1). |
| D-17 | What if monitoring itself fails? | **[Resolved]** Contained into `MONITOR_INTERNAL_FAILURE`, counted, never propagated; health becomes `UNKNOWN`, never a false `HEALTHY` (§11.2, §11.5). |
| D-18 | Does monitoring need its own top-level facade exports? | **[Deferred]** Lean: prefer `smcsignal.monitoring.__all__` only, leaving `smcsignal.__all__` at `["__version__"]`. Confirm in 25B. |
| D-19 | Cross-run comparison and retention. | **[Deferred]** Tied to persistence (§16.4). |
| D-20 | `1M` freshness rule. | **[Deferred]** `timeframe_seconds` rejects `1M`; needs an explicit calendar rule (§8.3). |

---

## 22. Proposed Phase 25 implementation breakdown

Design-only sequencing; each phase is independently reviewable and none is started here.

| Phase | Scope | Deliverable |
|---|---|---|
| **25A** | **This document.** | `docs/phase25a-production-monitoring-reliability-design.md` |
| 25B | Core models: `MonitoredComponent`, `Severity`, `HealthCode`, `HealthState`, `HealthEvent`, `HealthEventAggregate`, metric shapes, `MonitoringConfig` + `load_monitoring_config`, `NullMonitor`, health transition + rollup functions. Pure, no observers. | `src/smcsignal/monitoring/{models,health,config,metrics}.py` + tests |
| 25C | Observers for market data and signal engine: `ValidationReport` mapping, gap/staleness/interval analysis, signal-status/reason distribution. Read-only. | `monitoring/observers/{data,signals}.py` + tests |
| 25D | Delivery observers: `DeliveryState` × `FailureCategory` mapping, chart isolation, repeated-failure counting, redacted destinations; `MonitoredRun`/`MonitoringSession` lifecycle and `MonitoringReport`. | `monitoring/observers/delivery.py`, `monitoring/session.py`, `monitoring/report.py` + tests |
| 25E | Alerting (gated, opt-in): `AlertSink` protocol, `NullAlertSink`, severity routing, independent rate limiting, self-containment. Requires explicit approval; may be dropped. | `monitoring/alerting.py` + tests |
| 25F | Integration hardening: optional call-site instrumentation, `config/monitoring.example.toml`, `MANIFEST.in` line, `docs/README.md` entry, regression-guard allow-list maintenance (§11.7), full battery. | config + docs + packaging + guard maintenance |

**Sequencing rules.** 25B must land before 25C/25D. 25E is optional and never a
prerequisite. 25F is the only phase permitted to touch packaging or guard tests, and
only with the exact-path discipline required by §11.7.

---

## Appendix A — Verified fact index

Facts asserted **[Existing]** in this document, with their sources:

| Fact | Source |
|---|---|
| `__version__ == "0.22.0"`; `smcsignal.__all__ == ["__version__"]` | `src/smcsignal/__init__.py` |
| 252 names in the analysis facade; 42 in the delivery facade | `analysis/__init__.py`, `delivery/__init__.py` |
| No logging/threads/asyncio/scheduler/persistence anywhere in `src/` | grep over `src/` (no matches) |
| CLI is informational; prints four lines and returns `0` | `src/smcsignal/cli.py` |
| `block_network` autouse fixture forbids sockets in all tests | `tests/conftest.py` |
| `OHLCV` frozen, Decimal, UTC ms precision; `ValidationReport` fields; `OHLCVBatch` | `data/models.py` |
| Providers never substitute synthetic data | `data/base.py` docstring |
| Duplicate/missing/incomplete handling | `data/validation.py::normalize_ohlcv` |
| `timeframe_seconds`; `candle_close_time` | `analysis/mtf/timeframes.py`, `analysis/liquidity/time.py` |
| `SignalStatus` (3), `SignalReason` (14), frozen `SignalSnapshot` | `analysis/signal_engine/models.py` |
| `DeliveryState` (6), `FailureCategory` (9), `DeliveryReceipt` incl. unused `attempted_at` | `delivery/models.py` |
| `redact()`, clock-free `DeliveryAuditRecord`, `audit_record_for` | `delivery/audit.py` |
| Identities never depend on clock/pid/uuid | `delivery/identity.py` |
| `EvidenceProvenance`, `SeriesProvenance`, `CandleReference.closed_at` | `analysis/provenance.py` |
| `evidence_json`/`canonical_bytes`/`digest` canon | `analysis/liquidity/evidence.py` |
| `TelegramDeliveryCounters`/`Result`/`Integration`/`PayloadBridge` | `delivery/telegram/integration.py` |
| `DeliveryRegistry` is in-process only; "Restart persistence is out of scope for Phase 24B" | `delivery/sink.py` |
| `NullSink` / `MessageSink` protocol precedent for a no-op injected observer | `delivery/sink.py` |
| `TelegramSink.last_chart_failure` records chart failure without affecting text | `delivery/telegram/sink.py` |
| `TelegramAuditRecord` is clock-free and secret-free | `delivery/telegram/audit.py` |
| `config/telegram.example.toml` + per-table `config/*.example.toml` naming convention | `config/`, `MANIFEST.in` |
| `test_template_keeps_future_capabilities_disabled` guards the config template | `tests/test_config_template.py` |
| `dependencies = []` — no runtime third-party dependency | `pyproject.toml` |
| `HUMAN_ONLY`, `can_transition`, `transition`, `apply_human_decision` | `analysis/improvement/state.py` |
| Strict exact-key config loaders; `TelegramConfig` has no token/chat id | `delivery/config.py`, `orchestrator.py`, `telegram/config.py` |
| `test_scope.py` AST/`FORBIDDEN_TOKENS` precedent; `test_isolation.py` facade guard | `tests/delivery/test_scope.py`, `tests/improvement/test_isolation.py` |
| `recursive-include docs *.md` (design doc needs no packaging change) | `MANIFEST.in` |
| Guard baselines `cfe51fbe`/`141b2a5` with exact-path allow-lists | `tests/robustness/test_regression.py`, `tests/intelligence/test_regression.py` |
| Suite at baseline: 3789 passed, 0 failed | full-suite run at `b3a9013` |

## Appendix B — Explicitly unverified / not claimed

- No production supervisor, systemd unit, container, or deployment artifact was
  inspected; statements about "the process" are limited to §2.2's findings about `src/`.
- No third-party telemetry/metrics/alerting library is present or proposed as a
  dependency; `pyproject.toml` declares `dependencies = []` **[Existing]** and this
  design adds none.
- Operational SLOs, on-call rotations, and dashboards are out of scope.
