# Phase 24A — Telegram Delivery Architecture (Design Specification)

**Status:** DESIGN ONLY. No code, no Telegram library, no tokens/secrets, no network
connection, no commit, no push. This document specifies the architecture to be
built in Phase 24B onward on top of the **frozen** Phase 1–23 system.

**Frozen bases:**
- Phase 22 baseline: `9a0af2688180c50f4b1a36ef371c01e0c14b0b27`
- Phase 23 freeze: `9043e70f2f268d0612e668fde86c501340540a9f`

Phase 24 is a **signal delivery / presentation layer only**. The system remains a
signal bot, **not** a live trading bot. Telegram must not generate, alter, veto,
or feed back into any signal decision.

---

## 0. Governing rule

Phase 24 sits strictly **downstream** of the immutable, content-addressed signal
and report records. Its only job is to take already-accepted published facts and
turn them into deterministic, safely-transportable messages and chart artifacts.

Everything below that does not reference a concrete Phase 1–23 type is marked
`[to be confirmed in 24B]`. Nothing in this spec adds fields to existing models
or invents engine outputs.

---

## 1. Architecture

### 1.1 Conceptual boundary

```
  ┌──────────────────────────────────────────────────────────────┐
  │  Phase 1–23 decision & analytics system (FROZEN)             │
  │  SignalSnapshot, DrawingModel, intelligence/review reports   │
  └───────────────▲─────────────────────────────▲───────────────┘
                  │ immutable published object  │ immutable published object
                  │ (no calls back into engine) │
  ┌───────────────┴─────────────────────────────┴───────────────┐
  │  Phase 24 presentation layer (pure, offline-testable)        │
  │  SignalMessageBuilder  →  RenderedMessage (+ chart bytes)    │
  │  (reads only published fields)                               │
  └──────────────────────────────┬───────────────────────────────┘
                                 ▼
  ┌──────────────────────────────────────────────────────────────┐
  │  Phase 24 transport abstraction (MessageSink)                │
  │  DeliveryCoordinator → sink.deliver(...) → DeliveryReceipt   │
  │  TelegramSink  /  FakeTransportSink  /  NullSink             │
  └──────────────────────────────────────────────────────────────┘
```

**Properties of the boundary:**
- **Inputs:** immutable published records only — a `SignalSnapshot`
  (`signal_engine/models.py`) that is a real `BUY_SIGNAL` publication, plus
  **optional** already-produced context objects bound to the same `signal_id`:
  `SetupAttribution`, the SQS breakdown (already reachable through the snapshot),
  and a `DrawingModel` (`visualization/models.py`).
- **Outputs:** a `RenderedMessage` (text/caption + optional chart attachment
  bytes + deterministic ids) and a `DeliveryReceipt`/`DeliveryAuditRecord`.
- **Allowed dependencies:** existing immutable model classes read as data; the
  presentation/formatting layer; a small number of stdlib types (`decimal`,
  `datetime`, `dataclasses`, `html`, `re`, `hashlib`); the transport interface.
- **Forbidden dependencies:** any module that *runs* detection (signal engine,
  eligibility, halal filter, SMC/ICT analyzers, setup quality, indicators,
  intelligence, robustness, backtest) purely to reconstruct a message; any
  mutation of a signal; any network/Telegram call inside the formatting path.
- **Ownership of signal decisions:** remains entirely in Phase 1–23. Phase 24
  never creates a `Signal`, never sets `status`/`direction`, never changes a
  threshold, and never decides eligibility or halal classification.

**Invariant (architectural):** formatting is a pure function of one immutable
published object graph. `render(message) == render(same_object)` always, and
there is no code path from the delivery layer back into signal generation.

---

## 2. Signal message design

A professional BUY-spot signal message. Every field is sourced from a **fact**
the frozen models already expose, a **derived presentation** label, or the
**externally-supplied** halal status.

### 2.1 Three-way provenance of every displayed value

| Provenance | Meaning | Example fields |
|---|---|---|
| **Engine fact** | Verbatim from the published object | symbol, timeframe, status, direction, score_total, publish_threshold, reasons, timestamps, signal_id, setup_identity, classification/eligibility/bias, SQS component scores, reference close |
| **Derived presentation text** | Human label computed deterministically from facts; never a new fact | "SQS 71/75", "MTF aligned", "Discount + OTE context", human-readable reason list |
| **External halal status** | Supplied classification consumed as an input, displayed verbatim | HALAL / HARAM / UNKNOWN (+ source + freshness) |

### 2.2 Field list (proposed message schema)

Where a field is `optional`, it is shown only when the underlying object supplies
it; it is never fabricated.

1. **symbol** — `signal.symbol` (fact).
2. **timeframe** — `signal.timeframe` (fact).
3. **signal type / direction** — `signal.status` + `signal.direction` (fact).
4. **signal timestamp** — `signal.published_at` (fact); the message notes the
   referenced candle `closed_at` so timing is unambiguous.
5. **current / reference price** — the **close of the primary closed candle**
   reachable via `current_observation(snapshot.upstream)` (fact). `[confirm the
   observation object is exposed to the delivery layer in 24B]`.
6. **SMC/ICT setup** — `setup_identity` (fact) and, when present, the
   `SetupAttribution.combination_key` + `labels` (fact) rendered as readable tags.
7. **setup quality / score** — `score_total` and `publish_threshold` (fact),
   rendered as `SQS score_total/threshold` (derived).
8. **market structure context** — only those structure sub-scores/statuses the
   eligibility snapshot carries (e.g. bias, structure labels in the eligibility
   reasons). Not invented.
9. **liquidity context** — only if a liquidity sweep component score or an
   existing sweep primitive is present on the object graph; otherwise omitted.
10. **order-block / FVG / OTE context** — the SQS component scores
    (`score.breakdown.order_block|fvg|ote|premium_discount|displacement|breaker_block|
    mitigation_block|mtf|mss|liquidity_sweep`) as **engine facts**; the chart
    primitives (zones/markers) only come from the optional `DrawingModel`, never
    recomputed.
11. **supporting indicator context** — only indicators already published as
    `DrawingModel.IndicatorOverlay` primitives; never a live indicator call.
12. **risk/context warnings** — only text the upstream system already emits
    (e.g. "not advice", insuffience flags from review/intelligence). Phase 24
    adds no advisory risk warnings of its own.
13. **halal-status** — displayed as an externally supplied status (see §11).
14. **concise WHY** — reuse `render_signal_explanation(frame, attribution)`
    output (engine-fact based) and/or a deterministic derived summary of
    `signal.candidate.reasons`.
15. **provenance / signal identity** — `signal_id`, and the `evidence_id`s of the
    `evidence` tuple (facts).
16. **data timestamp / freshness** — `evidence_available_at`,
    `eligibility_available_at`, `candle_closed_at`, `published_at` (facts).

### 2.3 Design rule
The message schema is expressed as an explicit dataclass `SignalMessage`
(`{signal_id, ...}`). Builder only ever copies a fact field or renders a derived
string **from** a fact. A field with no engine source is **absent**, not
invented.

---

## 3. Telegram formatting

Formatting is deterministic and transport-safe.

- **Escaping:** Telegram supports both Markdown and HTML modes. Recommend **HTML
  parse mode with explicit escaping** (use `html.escape` on every dynamic value)
  because it is less brittle than Telegram MarkdownV2. All user-influenceable /
  dynamic text (symbol, setup labels, reason strings) is escaped before
  interpolation. Decide `[HTML vs MarkdownV2]` in 24B; the renderer keeps a single
  `escape()` seam so it is testable independently.
- **Headings / layout:** a stable layout:
  `header` / `signal identity` / `symbol · timeframe · type` / `price & score` /
  `context lines` / `why` / `halal + provenance` / `footer ("not advice")`.
- **Emoji:** allowed **only** as fixed, deterministic markers (e.g. no dynamic
  emoji). Emoji use is a single central mapping, never derived from data, so
  output is byte-stable. `[approve exact emoji set in 24B]`.
- **Decimal / price formatting:** prices formatted with a fixed,
  symbol-agnostic precision rule (e.g. up to `N` significant decimals, no
  thousand separators, trim trailing zeros). Implement one `format_price(Decimal)`
  used everywhere; never `str(float)`.
- **Long symbols:** truncate only display text at a configurable max width with an
  explicit ellipsis marker; the underlying `symbol` fact is never truncated.
- **Missing optional fields:** line omitted entirely (no empty headings). A "no
  chart" note replaces the chart block only if a chart was expected.
- **Unknown/unsupported values:** render a literal, e.g. `?`/`n/a`, using a single
  constant; never crash the build for an unknown enum member. Unknown enum values
  are surfaced and logged but the message still builds from known fields.
- **Message length:** a hard split budget (see §4.2 / Telegram 4096-byte limit);
  the renderer can emit `caption + parts` and enforce total length, splitting only
  at safe boundaries, never mid-escape.

**Invariant (formatting):** formatting never changes the underlying signal — a
`SignalSnapshot` passed to the renderer is never mutated and the rendered text is
discardable/re-renderable.

---

## 4. Chart / drawing delivery

- **Consume, don't redraw:** Phase 24 receives an **already-created**
  `DrawingModel` (from Phase 19e `compose_drawing`) for the signal's candle
  window. Phase 24 does not call SMC/ICT detectors and does not rebuild the
  drawing engine.
- **Accepted artifact:** a `DrawingModel` (immutable; carries `drawing_id`) plus a
  deterministic raster/serialization decision.
- **Signal ↔ chart identity:** charts are **linked** to a signal by the
  signal's series + candle window, but they are distinct objects. Relationship is
  recorded as `signal_id → drawing_id` mapping established **by the caller** when
  it supplies the matching drawing; Phase 24 verifies the drawing's series symbol
  & timeframe equal the signal's, and rejects a mismatch (`AnalysisInputError`)
  rather than guessing.
- **SVG vs PNG conversion ownership:** **visualization layer owns SVG** (already:
  `render_svg(model)`). **PNG/raster conversion belongs to the delivery layer**
  (Telegram photo attachments need a raster; SVG may be attached as a document).
  Phase 24A does **not** implement conversion; it only defines the seam:
  `chart_to_attachment_bytes(model) -> ChartAttachment{drawing_id, mime, bytes}`.
  `[decide in 24B: sendPhoto via a pure-Python rasterizer + no new heavy deps, OR
  sendDocument with SVG; confirm dependency policy before choosing]`.
- **Deterministic naming/identity:** attachment filename/id derived from the
  deterministic `drawing_id` (e.g. `chart-{drawing_id}.svg/png`) and the signal id;
  no clock/random in the name.
- **Failure behavior:** if the caller supplies no `DrawingModel` or chart
  preparation fails, the **text signal message is still delivered unchanged** with
  a "chart unavailable" marker. A chart failure must never alter the signal
  decision or the message facts.

**Invariant (chart):** text and chart are independent deliverables bound to the
same `signal_id`; chart failure degrades only the attachment, never the message or
the signal.

---

## 5. Delivery model / transport abstraction

Define an internal transport interface (no Telegram library chosen yet):

```
class MessageSink(Protocol):
    def deliver(self, message: RenderedMessage) -> DeliveryReceipt: ...
```

Concrete sinks:
- **NullSink** — records an `attempted` receipt, sends nothing (offline/no-op).
- **FakeTransportSink** — deterministic, configurable (records delivered / throws
  a chosen `DeliveryError` category / delays); used in tests and replay.
- **TelegramSink** — the later production implementation of the same protocol
  (24B+), configured purely from the environment/secret layer. Not built in 24A.

`DeliveryCoordinator` orchestrates: build message → prepare chart → `sink.deliver`
→ build `DeliveryReceipt`/audit record → update in-memory dedup registry. The
coordinator is the only place that touches the sink; formatting never does.

---

## 6. Idempotency and duplicate prevention

- **Message/delivery identity:** a deterministic `delivery_id` derived from
  `signal_id` (+ optional `rendering_profile`/`chart_present`) — content
  addressed, no clock/random. Same signal + same profile ⇒ same `delivery_id`.
- **Deduplication key:** `signal_id` (primary). A registry maps `signal_id →
  last_known_delivery_state`. Duplicate `signal_id` inputs are skipped
  (see §9 duplicate handling).
- **Retry semantics:** retries reuse the **same** `delivery_id` and **same**
  rendered message bytes (immutable render output), so a retry is not a new
  logical message.
- **State model (transport, honest):** `NOT_SENT`, `SENT_ATTEMPTED`,
  `DELIVERED`, `UNKNOWN` (timeout/no confirmation), `FAILED`. Because Telegram
  does not guarantee exactly-once, Phase 24 **never claims exactly-once**; it
  provides **at-most-once by dedup of input** plus **detectable duplicates** via
  `delivery_id`, and surfaces `UNKNOWN` when it cannot confirm.

**Invariant (idempotency):** `delivery_id` is a pure function of the immutable
signal; a process restart or retry cannot mint a new id, and an id already in
`DELIVERED` is never re-sent.

---

## 7. Failure handling

Failure is **categorized**, never a crash, and **never mutates the signal**.

| Condition | Behavior | State |
|---|---|---|
| Network/Telegram unavailable | mark `UNKNOWN` or `FAILED`; log redacted; keep for retry within policy | `UNKNOWN`/`FAILED` |
| Timeout | treat as `UNKNOWN` (not `FAILED`) — result may or may not have delivered | `UNKNOWN` |
| Rate limit | back off per policy; do not hammer; mark `FAILED_RETRYABLE` | `FAILED` (retryable) |
| Malformed message | build error before any transport call; audit `BUILD_FAILED`; never send partial | `NOT_SENT` |
| Chart-generation failure | drop only chart attachment; deliver text | `DELIVERED` (text) |
| Missing optional signal fields | renderer omits those fields; still delivers | `DELIVERED` |
| Duplicate signal | dedup registry short-circuits | `SKIPPED_DUPLICATE` |
| Invalid destination | config validation rejects before delivery; `FAILED_CONFIG`; no network call | `NOT_SENT` |
| Transport error | categorize and record; no retry past policy cap | terminal `FAILED`/`UNKNOWN` |

Retry policy: finite attempts + backoff, tracked per `delivery_id`, persisted only
in-process for Phase 24A/B (no DB yet).

---

## 8. Security / secrets

Secrets **never** come from source code.

- **Bot token:** supplied only via environment variable / secret store; read once
  at transport construction; never logged, never serialized, never in any audit
  field or error string.
- **Destination / chat IDs:** from configuration; treated as sensitive
  identifiers — logged only redacted (e.g. last 4 chars) in audit fields; full
  value only in the transport call.
- **Logging redaction:** a central `redact()` for tokens/ids/keys applied to every
  log line that could touch them.
- **Error-message redaction:** exceptions are caught and re-raised/logged through
  a boundary that strips tokens/urls before storing error text.
- **Configuration validation:** strict `TelegramDeliveryConfig` load that rejects
  unknown keys, blank/obviously-invalid destinations, insecure flag combos
  (mirroring the strict Phase 23 config style). Missing token ⇒ transport is
  **disabled/None**, not a half-built sink.
- **Secret storage expectation:** the design expects an env/secret-injected
  mechanism (`TELEGRAM_BOT_TOKEN`, chat ids via config). Phase 24A/B keeps the
  transport able to construct with an injected token object so tests never need a
  real one.

**Security invariant:** no Phase 24 source file contains or requests a real bot
token; formatting/audit code cannot observe the raw token.

---

## 9. Halal-status boundary

Halal classification is **external research/input** consumed upstream by the
Phase 1–17 halal filter; Phase 24 only **displays** the resulting status.

- **Display rule:** the message shows the classification that arrived with the
  signal — `HALAL` / `HARAM` / `UNKNOWN`. **Note:** the upstream enum
  (`halal_filter.classification.AssetClassification`) has `HALAL`/`HARAM`/
  `UNKNOWN`; there is **no `NOT_HALAL` value**. This spec therefore maps the
  review's "NOT_HALAL" case onto the real `HARAM` value and says so explicitly —
  no new status value is invented.
- **Must not:** automatically classify, modify, infer, or override the approved
  eligibility source.
- **Behavior by status** (only matters for assets that can reach delivery;
  recall a `BUY_SIGNAL` requires `classification is HALAL` by construction):
  - `HALAL` → displayed as approved, eligible classification.
  - `HARAM` (NOT_HALAL) → no BUY signal exists by invariant; if a context/report
    object references it, display `HARAM` verbatim and do not publish a BUY.
  - `UNKNOWN` → display `UNKNOWN`; do **not** treat as halal.
  - stale/unavailable → show a deterministic `halal-status: unavailable` marker
    plus the freshness timestamp; do not infer.
- **Blocking policy:** any policy that blocks `UNKNOWN`/stale (i.e. do not
  deliver) must be an **explicit upstream/configuration decision**, not a hidden
  Telegram rule.

**Governance invariant:** halal status flows only into presentation; Phase 24
cannot change classification or block/unblock an asset except by an explicit
configurable display/blocking policy that lives above formatting.

---

## 10. Phase 23 governance boundary

Telegram is **not** an approval mechanism for Phase 23.

- **Not implemented:** candidate-approval buttons, strategy-modifying commands,
  candidate parameter editing, optimization commands, automatic promotion, any
  mutation of Phase 23 `CandidateExperimentDef`/`HumanDecisionRecord`.
- Phase 24 may only **read and relay** immutable Phase 23 review/report output for
  presentation if desired; it never produces an `APPROVED`/`REJECTED` and never
  calls `apply_human_decision`.
- Phase 23 human governance stays a separate, non-Telegram process.

**Governance invariant:** no Phase 24 code path can change Phase 23 state, and
Telegram has no command surface that maps onto Phase 23 decisions.

---

## 11. Message lifecycle

```
Signal produced (Phase 1–23)
   │  status == BUY_SIGNAL, already published (immutable)
   ▼
Accepted for publication (upstream acceptance: signal is a published frame)
   ▼
Message rendered  (pure builder; NOT_SENT if build fails)
   ▼
Chart prepared    (optional; failure ⇒ text-only)
   ▼
Delivery attempted (DeliveryCoordinator → sink.deliver)
   ▼
Delivered / Failed / Unknown   (transport state)
   ▼
Audit record (delivery_id, signal_id, destination-redacted, attempt count,
              transport state, failure category, timestamps, chart artifact id)
```

**Publication state vs transport state are distinct:** publication is decided
upstream and is never changed by delivery. A Telegram failure turns a valid signal
into a *not-delivered* record, **never** into a non-signal.

---

## 12. Replay / historical compatibility

- The presentation layer is **pure** and offline: historical `SignalSnapshot`s +
  `DrawingModel`s (already deterministic, content-addressed) render identically
  under a `FakeTransportSink` with **no network access**.
- Replay tests: feed stored signal objects through the full `build → render →
  FakeTransportSink → audit` path; assert byte-identical messages and stable
  `delivery_id`s across runs/restarts (no clock/random in ids; timestamps are
  content fields, not identity inputs).
- Because rendering depends only on immutable published objects, Phase 24 stays
  fully testable offline and deterministically reproducible on historical data.

---

## 13. Configuration

A strict `[telegram-delivery]`-style config (load style mirrors Phase 23 config,
rejecting unknown keys). Categories (all presentation/transport only):

- **enabled/disabled:** master switch; when disabled or no token, sinks are no-op
  and nothing is sent.
- **destination configuration:** chat/channel ids, optional multiple destinations;
  validated (nonempty, redactable).
- **message formatting:** parse mode, emoji set, price precision, caption layout,
  max length budget, HTML/Markdown choice.
- **chart delivery:** on/off, artifact format (SVG-document vs raster-photo),
  failure fallback (text-only).
- **retry policy:** max attempts, backoff seconds, retryable categories.
- **deduplication policy:** in-memory registry size/TTL, whether `UNKNOWN` states
  are retried.
- **freshness policy:** max age of signal before it is flagged stale (display or
  skip) — presentation-level.
- **halal-status display policy:** how `UNKNOWN`/stale are shown or blocked
  (explicit, not hidden).

**Invariant (config):** no Phase 24 config key can change signal-generation,
thresholds, eligibility, halal classification, or Phase 23 state.

---

## 14. Observability / audit

Minimal in-process audit record fields (no DB / external monitoring in 24A):

- `delivery_id`, `signal_id`;
- `destination` (redacted);
- `attempt_count`;
- `transport_state` (`NOT_SENT|SENT_ATTEMPTED|DELIVERED|UNKNOWN|FAILED`);
- `failure_category` (`build|chart|network|timeout|rate_limit|config|duplicate|none`);
- `attempted_at` / `last_state_at` timestamps (audit only — never identity inputs);
- `chart_artifact_id` (`drawing_id` or absent);
- `message_bytes` digest (deterministic) for integrity.

The audit record is produced by the coordinator from immutable data; it never
contains secrets.

---

## 15. Dependency policy

**Recommendation: option C — build the internal transport abstraction + pure
presentation layer first; choose the concrete Telegram implementation later.**

- **A (Telegram library)** — fast path, but couples the whole codebase to a third
  party, drags network/runtime concerns into tests, and conflicts with the
  offline/testable mandate until behind the sink anyway. Not needed for 24A.
- **B (direct HTTP)** — smallest possible surface, but more manual URL/upload/retry
  plumbing and still network-coupled; fine as the eventual TelegramSink impl.
- **C (abstraction first)** — keep `formatting` and `transport` fully decoupled and
  dependency-free; introduce the concrete Telegram mechanism (library or HTTP)
  only as one `MessageSink` implementation behind the coordinator, chosen in 24B.

The core formatting/presentation layer must remain independently testable with no
network dependency.

---

## 16. Testing strategy

Focused tests (no target count):

- deterministic message rendering (same object ⇒ same bytes);
- HTML/Markdown escaping of every dynamic value (symbols, labels, reasons);
- optional/missing field handling (omitted lines, no crash);
- message length / safe split at max length;
- chart attachment identity (`drawing_id`-derived name) + signal↔chart series
  mismatch rejection;
- delivery identity stability across restarts/retries;
- duplicate prevention (registry skip on repeated `signal_id`);
- retry behavior (same id, capped attempts, backoff, `UNKNOWN` vs `FAILED`);
- failure classification table (§7);
- fake transport (recorded deliver / forced error categories);
- secret redaction (token never in logs/audit/errors);
- halal-status boundary (display verbatim; `UNKNOWN` not treated as halal;
  blocking is explicit);
- Phase 23 governance isolation (no path can approve/reject/mutate a candidate);
- no signal mutation (snapshot unchanged after render + after failed delivery);
- historical/replay compatibility (stored signals → identical output, offline);
- offline operation (full path under FakeTransportSink, no network).

---

## 17. Explicit non-goals (Phase 24A defers)

- live trading / order execution / exchange trading APIs;
- portfolio / position management;
- autonomous strategy changes;
- optimization / parameter search;
- Phase 23 automatic promotion;
- Telegram-based strategy control / commands that modify the system;
- automatic halal classification;
- production deployment / hosting / infrastructure;
- real bot credentials.

---

## 18. Phase 24 implementation sequence (24B onward)

1. **24B — pure presentation core:** message schema, deterministic builder,
   formatting/escaping, price & length rules, config load + strict validation.
2. **24B — chart binding:** accept `DrawingModel`, verify series vs signal,
   deterministic attachment identity; chart-failure fallback.
3. **24C — transport abstraction:** `MessageSink` protocol, `DeliveryCoordinator`,
   `NullSink`, `FakeTransportSink`, delivery identity + dedup registry + retry/
   failure/audit model (all offline).
4. **24C — halal + governance display boundary** and blocking-policy config.
5. **24D — TelegramSink (production):** choose Telegram mechanism (per §15),
   env/secret token injection, redaction, real send + receipts.
6. **24E — hardening:** replay fixtures, full offline regression, security review.

---

## Architectural invariants (summary)

1. Phase 24 reads immutable published objects only; no call back into signal
   generation.
2. Formatting is a pure, deterministic function; `render(same) == same`.
3. Formatting never mutates a signal; delivery never changes publication state.
4. Text and chart are independent; chart failure degrades only the attachment.
5. `delivery_id` is content-derived; at-most-once by input dedup; `UNKNOWN`
   acknowledged (no exactly-once claim).
6. No config key touches signal/eligibility/halal/Phase 23 behavior.

## Security invariants

1. No token/secret in source; env/secret injection only.
2. Logs/audit/errors redact tokens, destinations, and keys.
3. Missing token ⇒ transport disabled, not half-built.

## Governance invariants

1. Telegram never approves/rejects Phase 23 candidates or edits strategy.
2. Halal status is display-only; `UNKNOWN` never becomes halal; blocking is an
   explicit upstream/config decision.
3. Phase 24 is downstream-only; decisions remain owned by Phase 1–23.

---

## Unresolved design questions (to resolve in 24B, before code)

1. Telegram parse mode: **HTML** (recommended) vs **MarkdownV2**.
2. Exact deterministic emoji markers and whether emoji are used at all.
3. Chart attachment path: **SVG via sendDocument** vs **raster PNG via sendPhoto**,
   and the dependency cost of rasterization (kept out of 24A per policy).
4. How the delivery layer obtains the primary closed-candle close as the
   reference price (exact observation accessor).
5. Which optional context objects (SetupAttribution vs a composite) are passed by
   the caller and the exact `signal_id` binding API.
6. Whether Phase 24 will ever present non-BUY records (BEARISH_AVOID, review /
   intelligence reports) and, if so, their exact message schema.
7. Concrete retry backoff numbers and the dedup registry lifetime for a
   restart-safe design without a database.

## Recommended next phase

**24B — build the pure, offline presentation core** (message schema, deterministic
renderer, formatting/escaping, strict config) behind the §15 *abstraction-first*
decision, keeping the transport as an interface so everything remains testable
without Telegram or a token.

---

## Phase 24B implementation record (additive; design unchanged)

Phase 24B implemented the **pure, offline, transport-independent presentation
core** specified above. No Telegram library, token, chat id, network call, or
secret is present. The existing frozen Phase 1–23 APIs were read but never
modified.

### Module locations (public API in `src/smcsignal/delivery/__init__.py`)

| Concern | Module | Public names |
| --- | --- | --- |
| Presentation projection | `message.py` | `SignalMessage`, `from_signal`, `render_message`, `assemble_signal_message`, `prepare_signal_message`, `require_buy_signal` |
| Deterministic formatting | `formatting.py` | `escape_html`, `format_decimal`, `format_timestamp`, `split_caption` |
| Delivery records / states | `models.py` | `ChartAttachment`, `RenderedSignalMessage`, `DeliveryAttempt`, `DeliveryReceipt`, `DeliveryState`, `FailureCategory`, `chart_artifact_id` |
| Deterministic identity | `identity.py` | `message_identity`, `delivery_identity` |
| Strict configuration | `config.py` | `DeliveryConfig`, `load_delivery_config` |
| Chart (consume only) | `chart.py` | `svg_attachment`, `SVG_MIME` |
| Transport abstraction | `sink.py` | `MessageSink`, `NullSink`, `FakeTransportSink`, `DeliveryRegistry`, `deliver_message`, `deliver_signal` |

### Resolved 24A open questions

1. **Parse mode** — HTML. Every dynamic value is passed through
   `html.escape(..., quote=True)` before embedding; `html_escape` is frozen to
   `True` and cannot be disabled.
2. **Emoji markers** — none. Headers use plain ASCII text (`BUY` / `AVOID` /
   `NO SIGNAL`) and an em-dash separator only; fully deterministic.
3. **Chart path** — consume-only SVG via `render_svg(DrawingModel)` as an
   `image/svg+xml` string artifact. No PNG/SVG rasterization in 24B.
4. **Reference price** — the projection calls the real upstream accessor
   `current_observation(frame.upstream)` and takes `observation.candle.close`
   as the displayed `reference close`. This is the same published observation
   accessor used by the eligibility engine, so the price is the eligibility
   current-candle close, never an invented entry/stop/target.
5. **Attribution binding** — `SignalMessage.from_signal(frame, attribution)`
   requires `attribution.signal_id == frame.signal_id` when supplied; WHY text
   is delegated verbatim to the real `render_signal_explanation`.
6. **Non-BUY records** — not presented as buys. `require_buy_signal` accepts
   only `BUY_SIGNAL` snapshots; everything else raises `AnalysisInputError`.
   `BEARISH_AVOID` renders under an `AVOID` header when constructed directly in
   unit tests, and no live signal uses it for broadcast.
7. **Retry / dedup** — retries reuse one deterministic `delivery_id`; an
   in-process `DeliveryRegistry` records `DELIVERED` delivery ids for at-most-once
   skipping; `UNKNOWN` outcomes are never retried silently. No restart persistence
   (out of scope).

### Render behavior

- Preserves the immutable upstream `signal_id`; carries no invented entry /
  stop-loss / take-profit / target / probability / confidence / position fields.
- Halal is display-only from the real `AssetClassification` enum
  (`HALAL`/`HARAM`/`UNKNOWN`); no `NOT_HALAL` value is introduced.
- WHY text is the upstream `render_signal_explanation` output, re-wrapped
  line-by-line and HTML-escaped; it is never recomputed.
- Deterministic: no wall clock, randomness, locale, or environment dependence.
  Optional facts and the timestamp lines are gated by the frozen `DeliveryConfig`.

### Identity (deterministic)

- `message_identity(signal_id)` → `"message:" + sha256(digest-v1)`;
- `delivery_identity(message_id, destination_id)` →
  `"delivery:" + sha256(digest-v1)`;
- `chart_artifact_id(signal_id, drawing_id, mime)` →
  `"chart:" + sha256(digest-v1)`.
- Inputs are stable label/value digests over the same canonicalization used by
  the liquidity evidence digest; results are stable across process restarts,
  repeated formatting, retries, and equivalent inputs.

### Transport

`MessageSink` is a `Protocol` whose only method turns an immutable
`DeliveryAttempt` into a `DeliveryReceipt`. 24B ships `NullSink` (offline no-op,
`NOT_ATTEMPTED`) and `FakeTransportSink` (deterministic scripted outcomes for
tests). A real remote messaging transport is **deferred to Phase 24D** and never
imported by the presentation core.

### Scope & safety audit

- The delivery package imports only the Python stdlib and existing `smcsignal`
  modules; no `telegram`, HTTP, socket, exchange, or webhook imports.
- No Phase 1–23 source file was changed; only new files were added under
  `src/smcsignal/delivery/` and `tests/delivery/`.

### Deferred to 24C / 24D

- **24C**: delivery orchestration and offline integration. Delivered below.
- **24D**: a concrete remote-messaging `MessageSink` and its config/credentials
  handling. Not started.

---

## Phase 24C implementation record (additive; design unchanged)

Phase 24C delivered the **delivery orchestration and offline integration** layer
specified by the frozen Phase 24C scope. It builds entirely on the approved
Phase 24B public API and the frozen Phase 1-23 types; nothing was rewritten and
no upstream module was changed. There is no Telegram/SDK/network access, no
token/chat id/secret, no database, and no durable exactly-once guarantee (only
at-most-once by in-memory dedup plus a deterministic `SKIPPED_DUPLICATE`).

### Module locations (additions to `src/smcsignal/delivery/`)

| Concern | Module | Public names |
| --- | --- | --- |
| Orchestration / offline pipeline | `orchestrator.py` | `DeliveryCoordinator`, `DeliveryOutcome`, `DeliveryBatchResult`, `SignalDeliveryContext`, `OrchestrationConfig`, `load_orchestration_config` |
| Deterministic audit + redaction | `audit.py` | `DeliveryAuditRecord`, `audit_record_for`, `redact` |

`__init__.py` was extended additively to re-export the new public names.

### Pipeline (proven offline)

```
immutable SignalSnapshot (BUY_SIGNAL only)
  -> 24B SignalMessage.from_signal / assemble_signal_message (context bound)
  -> DeliveryCoordinator (in-memory DeliveryRegistry dedup + finite retry)
  -> MessageSink (NullSink / FakeTransportSink; never a network)
  -> DeliveryOutcome envelope (+ optional DeliveryAuditRecord)
```

- Only `BUY_SIGNAL` snapshots may enter the delivery path; anything else raises
  before any sink call.
- Context binding is read-only from the upstream projection: symbol, timeframe,
  reference close, why/explanation presence, and the optional `DrawingModel`
  series are bound into `SignalDeliveryContext`; no upstream field is invented.
- Chart orchestration consumes an existing `DrawingModel`; a mismatched or
  failing chart is dropped while the valid text still delivers.
- Determinism: identical input under a fresh coordinator yields byte-identical
  captions, identical `delivery_id`s and identical outcomes; no wall clock,
  randomness, machine ids, or unnecessary timestamps.
- Dedup is explicit and in-memory (owned `DeliveryRegistry`, optionally shared);
  repeated identical input yields `SKIPPED_DUPLICATE` with the same
  `delivery_id`; no persistent store.
- Batch (`deliver_many`) preserves input order and is per-signal independent
  (no cross-signal contamination, no ranking, no lookahead).
- `OrchestrationConfig` is strict/frozen and exposes only delivery controls
  (`enabled`, `max_attempts`, `deduplicate`); the `[orchestration]` loader
  rejects unknown keys.
- Audit records are deterministic, machine-readable, redact the destination,
  and carry a message-content digest; they contain no secrets.

### Scope / safety audit

- New modules import only the Python stdlib and existing `smcsignal` modules.
- No Telegram SDK, HTTP/network, socket, exchange, order, position, leverage,
  webhook, token, api-key, or bot-token surface anywhere in `delivery/`.
- No signal-generation, halal-eligibility, or Phase 23 governance change.
- No optimization / parameter tuning / automatic strategy or setup control.

### Deferred to 24D

- A concrete remote-messaging `MessageSink` transport and its secret injection /
  credentials handling. Not started.
