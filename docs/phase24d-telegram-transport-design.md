# Phase 24D-A — Telegram Transport Design (Specification Only)

**Status:** DESIGN ONLY. No code, no dependency, no network access, no token,
no commit, no push. This document specifies the transport layer to be built in
Phase 24D-B onward, plugged into the frozen Phase 24B / Phase 24C delivery
abstractions and strictly downstream of signal generation.

**Frozen bases**
- Phase 23 freeze: `9043e70f2f268d0612e668fde86c501340540a9f`
- Phase 24B (delivery presentation core): `c468dbcfb7268f98beaae57ecd0c6f8b156117a4`
- Phase 24C (delivery orchestration & offline integration): `50d523493963c15063fa5c2b78370ffe0fc43a0c`

The system remains a **signal bot, not a live trading bot**. Telegram is
**downstream output only**. No Telegram input may influence signal generation,
halal classification, eligibility, Phase 23 governance, or strategy. No trading,
execution, broker, exchange, or order functionality exists anywhere in Phase 24.

---

## 0. Grounding — real types this design must fit (verified from code)

Every identifier below exists in the frozen Phase 24B/24C codebase as inspected.
The design does **not** invent upstream fields; where a value the transport needs
is not already carried to the sink, that gap is called out explicitly (see §4 and
the open decisions).

### 0.1 Transport boundary — `MessageSink` (frozen, `src/smcsignal/delivery/sink.py`)

```python
class MessageSink(Protocol):
    def deliver(self, attempt: DeliveryAttempt) -> DeliveryReceipt: ...
```

A sink is given only a `DeliveryAttempt`. **Important grounded fact:** the attempt
carries no rendered text or chart bytes — only identity and routing fields.

### 0.2 Records (frozen, `src/smcsignal/delivery/models.py`)

- `DeliveryState` = `NOT_ATTEMPTED`, `SENT`, `DELIVERED`, `UNKNOWN`, `FAILED`, `SKIPPED_DUPLICATE`
- `FailureCategory` = `none`, `build`, `chart`, `config`, `transport`, `timeout`, `rate_limit`, `network`, `unknown`
- `DeliveryAttempt`: `delivery_id`, `message_id`, `signal_id`, `destination_id`, `attempt_number`, `chart_artifact_id` (optional)
- `DeliveryReceipt`: `delivery_id`, `message_id`, `signal_id`, `destination_id`, `attempt_number`, `state: DeliveryState`, `failure_category: FailureCategory`, `attempted_at: datetime | None`
- `ChartAttachment`: `signal_id`, `drawing_id`, `mime_type`, `filename`, `content: str`, `artifact_id`
- `RenderedSignalMessage`: `signal_id`, `message_id`, `caption: str`, `parts: tuple[str,...]`, `chart: ChartAttachment | None`
- `DeliveryState` includes `UNKNOWN`; `FailureCategory` includes `RATE_LIMIT`, `TIMEOUT`, `NETWORK`, `TRANSPORT` — exactly the states a real transport needs.

### 0.3 Orchestration (frozen, `src/smcsignal/delivery/orchestrator.py`)

- `DeliveryCoordinator(sink, registry=..., config=..., render_config=...)`
- `DeliveryOutcome`: `signal_id`, `message_id`, `delivery_id`, `destination_id`, `rendered: RenderedSignalMessage`, `context: SignalDeliveryContext`, `state`, `failure_category`, `attempt_number`
- `DeliveryOutcome.rendered` holds the full rendered text (`caption`, `parts`) and optional `chart`. This is the one object that carries the actual payload a real transport must send — but it lives on the **coordinator's** `DeliveryOutcome`, not on the `DeliveryAttempt` handed to the sink.
- `SignalDeliveryContext`: `signal_id`, `symbol`, `timeframe`, `reference_price`, `explanation_present`, `drawing_id`
- `deliver_message(...)` retries only `FAILED` up to `max_attempts`; `UNKNOWN` and `NOT_ATTEMPTED` stop immediately; `DELIVERED`/`SENT` mark the registry.
- `OrchestrationConfig`: `enabled`, `max_attempts`, `deduplicate`.

### 0.4 Audit & identity (frozen, `src/smcsignal/delivery/audit.py`, `identity.py`)

- `DeliveryAuditRecord`: `delivery_id`, `message_id`, `signal_id`, `destination_redacted`, `state`, `failure_category`, `attempt_number`, `chart_artifact_id`, `message_digest` — **no timestamps, no secrets**.
- `redact(value, *, keep=4) -> str`
- `message_identity(signal_id)`, `delivery_identity(message_id, destination_id)` — deterministic, content-bound.
- Chart content is an **SVG string**: `render_svg(DrawingModel) -> str`, `SVG_MIME = "image/svg+xml"`.

### 0.5 Configuration style (frozen)

Strict TOML loaders that reject unknown keys (`load_delivery_config`, `load_orchestration_config`), mirroring the strict Phase 23 config style. `AnalysisConfigurationError` for bad values.

---

## 1. Architecture overview

```
Phase 1-23 (signal generation, immutable, frozen)
        │  produces immutable SignalSnapshot (status == BUY_SIGNAL, published)
        ▼
Phase 24B Presentation Core   (frozen)  ── SignalMessage.from_signal, assemble,
        │                                       render_message → RenderedSignalMessage
        ▼
Phase 24C Orchestration       (frozen)  ── DeliveryCoordinator:
        │                                       BUY gate, in-memory dedup, retry cap,
        │                                       produces DeliveryOutcome(rendered, state)
        ▼
Phase 24D Transport           (24D-B)   ── TelegramSink (implements/adapts MessageSink)
        │                                       sendMessage / sendDocument to Telegram
        ▼
Telegram (outbound only; no input back into any signal/decision path)
```

Phase 24D sits entirely below the coordinator boundary. It **must not** change how
a `SignalSnapshot` becomes a `RenderedSignalMessage`, nor how the coordinator
chooses to deliver/dedupe/retry. It only performs the actual network send and
translates the transport outcome back into the frozen `DeliveryState` /
`FailureCategory` vocabulary.

### Layering rule
- Formatting/presentation/orchestration modules never import the Telegram layer.
- The Telegram layer imports **downstream only** (models, sink, audit, identity,
  config); it never imports back into signal/SMC/ICT/halal/governance modules
  except through the read-only published snapshot types already exposed.
- The transport is one concrete `MessageSink` behind the coordinator, satisfying
  the 24A §15 "abstraction-first" decision (option C).

---

## 2. Integration with `MessageSink`

### 2.1 The contract-evolution reality (grounded, must not be glossed over)

The frozen `MessageSink.deliver(self, attempt: DeliveryAttempt)` receives an
attempt that carries **no payload**. `NullSink` and `FakeTransportSink` only need
identity/routing, which is why this works offline. A real `TelegramSink` must send
actual text and bytes, which live in `RenderedSignalMessage` (held by the
coordinator's `DeliveryOutcome.rendered`).

Phase 24C's `deliver_message` builds the `DeliveryAttempt` from the rendered
message but only passes the **attempt** to the sink. Therefore, to give the
transport its payload, Phase 24D-B must resolve one of the following (see Open
Decision D1). The design recommends the **least-invasive, additive** option that
does not modify the offline sink contract or its behavior:

**Recommended:** introduce a **24D-local transport payload** rather than rewriting
24B/24C. The coordinator already owns the `RenderedSignalMessage`; 24D provides a
thin **driver** that calls the frozen coordinator APIs to obtain a
`DeliveryOutcome` (which carries `rendered` + the delivery `state`/`delivery_id`)
and hands that outcome to the Telegram transport. The offline `MessageSink`
path, `NullSink`, `FakeTransportSink`, dedup registry, and retry cap remain
unchanged; Telegram transport reads the outcome that the coordinator produced.

- If a purely additive change to the coordinator is approved later, the preferred
  shape is to let a sink declare it wants the payload (see D1), **not** to change
  the default `deliver(attempt)` signature.
- No change is made to `RenderedSignalMessage`, `SignalMessage`, rendering rules,
  `DeliveryState`, or `FailureCategory`.

### 2.2 TelegramSink as a `MessageSink`
For symmetry and to keep the "coordinator is the only thing that touches a sink"
invariant where possible, `TelegramSink` should satisfy the structural
`MessageSink.deliver(attempt) -> DeliveryReceipt` contract **in addition** to a
payload-aware entry point. Its `deliver` is the network-facing adapter; the 24D
driver supplies the payload separately (or via a coordinated envelope) so the
transport always has both the immutable attempt identity and the content.

---

## 3. TelegramSink responsibilities

Given one rendered signal message bound to one destination, `TelegramSink` must:

1. **Resolve destination** from a logical `destination_id` to a real Telegram
   `chat_id` via the frozen-style strict config (never the raw id in source).
2. **Send the text** caption to that chat (HTML parse mode, consistent with the
   frozen 24B decision; split into `parts` if the transport limit applies).
3. **Send the optional chart** — as a **document** carrying the frozen SVG
   (`sendDocument`), because SVG is not directly renderable by `sendPhoto`; this
   preserves the 24A "SVG, no rasterization dependency in Phase 24" decision. A
   chart whose render/attach already failed in 24B is simply absent (text-only).
4. **Return one `DeliveryReceipt`** with the correct `DeliveryState` and
   `FailureCategory` so the coordinator's retry/dedup/audit semantics hold.
5. **Never mutate** the signal, the rendered message, or the attempt.
6. **Never** read inbound Telegram messages to derive signal/eligibility/halal/
   governance decisions (outbound only; no signal-command channel).
7. **Apply rate limiting and bounded retry** internally for the network send,
   reporting only the frozen state vocabulary upward.
8. **Redact** all secrets and destination values in any log/audit/error text via
   the frozen `redact`.

---

## 4. Configuration model (`[telegram]`)

A new strict config, loaded by a `load_telegram_config(path)` mirroring the
frozen `load_delivery_config`/`load_orchestration_config` (unknown keys rejected,
`AnalysisConfigurationError` on bad values). No key may touch signal, SMC/ICT,
threshold, indicator, halal, Phase 23 governance, or execution.

Fields (all transport/presentation-only), as built after the Phase 24D-C1
amendment recorded at the end of this document:

| Field | Type | Meaning |
|---|---|---|
| `enabled` | bool | master switch; default `false`. If `false` **or** no token present → sink reports `NOT_ATTEMPTED`, never a half-built send. |
| `html_parse_mode` | bool | default `true` (matches frozen 24B HTML-escape-first). |
| `max_message_chars` | int | transport caption budget; default `4096` (Telegram's documented per-message limit). Enforced by `TelegramSink` as a pre-flight guard: a longer caption is refused (`NOT_ATTEMPTED`/`CONFIG`) before any network call, and content is never truncated or re-rendered. See the budget rationale below. |
| `retry_max_attempts` | int | network-level attempts for the actual HTTP call. |
| `retry_backoff_seconds` | float | base backoff. |
| `retry_backoff_max_seconds` | float | backoff cap. |
| `network_timeout_seconds` | float | per-request timeout (drives `UNKNOWN` vs `FAILED`). |
| `requests_per_second` | float | outbound rate limit (see §11). |
| `rate_limit_capacity` | int | token-bucket capacity (burst). |
| `chart_enabled` | bool | whether to send the optional SVG chart as a document. |
| ~~`connect_timeout_seconds`~~ | — | **Removed in Phase 24D-C1** (see amendment): the stdlib transport has a single per-request timeout, so a connect-only knob could not be honored and was dropped rather than shipped inert. |

### Caption-budget rationale (`max_message_chars = 4096`)

The two numbers serve different layers and are deliberately not the same:

- The **presentation layer** targets ~4000 characters per caption: the frozen
  `DeliveryConfig.max_caption_length` default is `4000`, and `split_caption`
  splits deterministically at that budget. That is a *rendering* budget — it
  keeps captions readable and stable, and it is a presentation concern that the
  transport must not alter.
- The **transport** enforces `max_message_chars = 4096`, which is Telegram's
  actual documented per-message limit. The transport is the last line of defense
  against an API rejection, so it must bound itself by the *platform's* real
  limit rather than by the presentation preference — a caption between 4000 and
  4096 characters (reachable if an operator raises `max_caption_length`, or if a
  future renderer emits a larger caption) is legitimately sendable and must not
  be refused by the transport.

Because the frozen presentation budget (4000) sits strictly below the transport
limit (4096), the transport guard is unreachable for payloads produced by the
current renderer; it exists to make the boundary explicit and to fail *before*
the network rather than as an opaque API error. The budget is a refusal, never a
truncation: the transport reads the frozen payload and never rewrites it.

**No `bot_token`, no `chat_id` in config file.** Tokens come only from the secret
injection layer (see §5). Destinations map logical id → chat id via the strict
destination config but are treated as secrets for redaction (see §6).

---

## 5. Token handling strategy

- **Never in source, never in config, never committed.** The bot token is injected
  at construction time from an environment variable / secret store and is read
  once.
- `TelegramSink(token=None)` or `enabled=True` **without** a token must yield a
  transport that reports `NOT_ATTEMPTED` (disabled), never a half-constructed
  sender (frozen security invariant: "Missing token ⇒ transport disabled").
- The token object passed in must be an opaque value that formatting/audit code
  cannot observe. The sink keeps it only inside the network client; it is never
  serialized into `DeliveryAuditRecord`, logs, or exception strings.
- A central `redact()` (frozen) is applied to anything that could contain the
  token before it reaches a log or audit path. Any exception whose message could
  embed the token is caught at the boundary and re-raised/logged with the token
  stripped (see §13).

---

## 6. Chat/channel destination handling

- The coordinator hands the transport a logical `destination_id` (e.g. a stable
  configured channel key). The transport resolves it to a numeric Telegram
  `chat_id` **from configuration** — the raw `chat_id` is treated as sensitive and
  is never committed and only used inside the network call.
- Destination config is validated strictly: nonempty, well-formed numeric chat id
  (or `@username` for public channels), no obviously-invalid values. Invalid
  destination ⇒ the sink returns a `CONFIG`-categorized receipt / `NOT_ATTEMPTED`
  **before** any network call (matches frozen §7 failure table).
- Audit/log/error text stores only `redact(destination_id)` (frozen audit already
  stores `destination_redacted`); the full value appears only in the outbound HTTP
  request.
- Missing/unknown destination ⇒ disabled/`NOT_ATTEMPTED`, never a half-built send.

---

## 7. Message delivery flow

1. Coordinator produces a `DeliveryOutcome` carrying `rendered` (`caption`, maybe
   `parts`) and a `delivery_id` for a resolved `destination_id`.
2. 24D driver/`TelegramSink` reads the frozen `RenderedSignalMessage.caption`.
3. If `len(caption)` exceeds the transport budget (`max_message_chars`), the sink
   refuses the send *before* any network call and reports
   `NOT_ATTEMPTED`/`CONFIG` (as built in Phase 24D-C/C1). The transport sends
   `caption` in a single `sendMessage`; it never truncates and never re-renders.
   Multi-part delivery of the frozen `parts` as an ordered sequence of
   `sendMessage` calls remains a **deferred, unimplemented option** (§21); the
   refusal above is the as-built behavior, not an error path.
4. HTML parse mode is enabled because 24B escapes every dynamic value before
   rendering; the transport must use HTML mode and must **not** re-escape or
   alter the caption (it is already presentation-safe and deterministic).
5. Each `sendMessage` returns a Telegram API result. On success, a receipt with
   `DELIVERED` (or `SENT` where the API confirms the request was accepted but
   not end-to-end confirmed) is returned. On timeout/no confirmation → `UNKNOWN`.
   On a categorized transport/rate-limit failure → `FAILED` with the matching
   `FailureCategory` so the coordinator can retry within its cap.
6. The transport returns exactly one `DeliveryReceipt`; the coordinator and
   registry behavior are untouched.

---

## 8. Chart/image delivery flow

- The frozen `ChartAttachment` is SVG text (`render_svg(DrawingModel) -> str`,
   `SVG_MIME="image/svg+xml"`, `filename` from the deterministic `drawing_id`,
   `artifact_id = chart_artifact_id(...)`).
- Recommended: send the SVG as a **document** (`sendDocument`) so no rasterization
  dependency is introduced. Raster PNG-via-`sendPhoto` is a deferred option that
  would require an approved dependency (see §21 / unresolved decisions).
- **Isolation rule:** the chart is delivered independently of the text. A chart
  that is absent (attach already failed in 24B) or that fails to send must **not**
  invalidate the already-sent text. Chart failure is reported via
  `FailureCategory.CHART` on the receipt (or, when text still succeeded, the text
  remains `DELIVERED` and only the chart sub-result is logged).
- Text and chart are bound to the same immutable `signal_id` and `delivery_id`;
  nothing here re-detects SMC/ICT or re-renders charts from raw facts.

---

## 9. Delivery-state mapping

Frozen `DeliveryState` (unchanged) ↔ Telegram reality:

| Telegram outcome | Frozen state |
|---|---|
| Not attempted (disabled/no token/unknown destination/over-budget caption) | `NOT_ATTEMPTED` |
| API accepted the request (message sent) | `SENT` |
| API confirmed delivery | `DELIVERED` |
| Timeout / no confirmation (may or may not have delivered) | `UNKNOWN` (never retried silently) |
| Categorized transport/network/rate-limit failure after policy | `FAILED` |
| Same `delivery_id` already delivered in-process | `SKIPPED_DUPLICATE` (coordinator-level) |

No exactly-once claim; at-most-once by the frozen in-memory registry plus
detectable duplicates via `delivery_id`.

---

## 10. Retry/backoff design

Two distinct, non-overlapping retry scopes:

- **Coordinator scope (frozen):** retries only `FAILED` receipts, reusing the same
  `delivery_id`, up to `OrchestrationConfig.max_attempts`. `UNKNOWN` is surfaced,
  not retried. 24D does not change this.
- **Transport scope (24D, inside the sink):** the actual HTTP call is retried with
  finite attempts and bounded exponential backoff (`retry_backoff_seconds` base,
  `retry_backoff_max_seconds` cap), gated by the outbound rate limiter. After the
  transport retry budget is exhausted, the sink reports one `FAILED`/`UNKNOWN`
  receipt so the coordinator's policy governs.

Backoff is applied only between transport attempts; it never touches signal or
orchestration timestamps.

---

## 11. Rate-limit handling

- A deterministic in-process token bucket limits outbound requests
  (`requests_per_second`, `rate_limit_capacity`). This keeps the bot under
  Telegram's API limits and prevents hammering on failures.
- On a Telegram `429` (rate limited) response, the sink reads the API-suggested
  retry_after, honors the local backoff cap, and reports `FailureCategory.RATE_LIMIT`
  when the budget is exhausted so the coordinator can retry within its cap.
- `RATE_LIMIT` already exists in the frozen `FailureCategory` enum — no new field
  is invented.

---

## 12. Error categorization

Every transport error maps to the frozen `FailureCategory`:

| Error | Category |
|---|---|
| Build/invalid attempt | `build` |
| Chart send failure | `chart` |
| Bad/unknown config or destination | `config` |
| Generic transport/API error | `transport` |
| Timeout | `timeout` → surfaced as `UNKNOWN` state |
| Rate limited (`429`) | `rate_limit` |
| Connectivity/DNS/connection refused | `network` |
| Anything else | `unknown` |

Errors are caught at the network boundary, redacted, and re-thrown only as a
category-bearing result — never as an uncaught exception that could carry a token
or mutate state.

---

## 13. Logging and audit integration

- Every successful and failed delivery is funneled through the frozen audit shape:
  a `DeliveryAuditRecord`-style deterministic record (`delivery_id`, `message_id`,
  `signal_id`, `destination_redacted`, `state`, `failure_category`,
  `attempt_number`, `chart_artifact_id`, `message_digest`) — **no timestamps in the
  identity, no secrets**.
- The 24D sink reuses the frozen `redact()` and must not add secret-bearing audit
  fields.
- Log lines that could touch the token/chat id go through the redaction boundary
  before emission. No token is ever logged, serialized, or placed in an error
  string.
- Operational logs are emitted at the transport layer only; the coordinator and
  presentation layers stay silent about transport specifics.

---

## 14. Security boundaries

1. **No secrets in source or config**; token injected at construction from the
   secret layer, read once, unobservable by formatting/audit.
2. **Missing token or `enabled=false` ⇒ disabled transport** (`NOT_ATTEMPTED`),
   never a half-built sink.
3. **Raw chat ids are sensitive**: redacted everywhere except the outbound call;
   only logical `destination_id` travels with the message/audit.
4. **No inbound command parsing** that can reach signal generation, eligibility,
   halal, or governance.
5. **TLS-only** API endpoint; no plaintext transport; no webhook callback server in
   24D (see §21 open decisions for whether a webhook is ever added — defer to a
   later phase and only for delivery acknowledgements, never for signals).
6. Exceptions are redacted at the boundary (strip tokens/urls/chat ids) before
   storage/emission.

---

## 15. Governance boundaries

1. **Telegram is output-only.** It never approves/rejects Phase 23 candidates,
   never edits strategy, never tunes parameters.
2. **No governance feedback path.** There is no inbound channel through which a
   Telegram message can influence any Phase 1–23 or Phase 23 decision.
3. **No self-improvement / optimization trigger** exists in the transport. The
   transport cannot submit candidates, accept/reject hypotheses, or alter
   `improvement/` state.
4. Phase 24 is downstream-only; decisions remain owned by Phase 1–23.

---

## 16. Halal / governance invariants

- **Halal is display-only.** The transport renders whatever
   `RenderedSignalMessage` / `SignalMessage` already carries (classification from
   the real `AssetClassification` enum: `HALAL`/`HARAM`/`UNKNOWN`). It never
   re-classifies, and `UNKNOWN` never becomes halal.
- Blocking is an explicit upstream/config decision, never inferred by the
   transport. The transport does not add halal rules or blocking policy.
- No `NOT_HALAL` value is introduced anywhere in the transport (consistent with
   the 24B scope audit).
- The transport cannot modify halal eligibility or classification.

---

## 17. Failure isolation rules

1. **Chart failure never invalidates text.** Text `sendMessage` is independent of
   the optional chart document; a chart failure drops only the chart.
2. **Transport failure never mutates a signal or rendered message.** Inputs are
   frozen/immutable; a failed send yields a `FAILED`/`UNKNOWN`/`NOT_ATTEMPTED`
   record only.
3. **One destination's failure does not affect another.** Each `delivery_id` is
   independent; no cross-destination state.
4. **Network errors never crash the pipeline**; they are categorized receipts.
5. **Backoff/rate limiting** contain retries; no unbounded retry loops (transport
   budget then coordinator budget both bounded).

---

## 18. Testing strategy

Fully offline; no real network, no token.

- **Contract tests:** `TelegramSink` implements `MessageSink` and returns a
  `DeliveryReceipt` per `DeliveryState`/`FailureCategory`.
- **Fake HTTP layer:** inject a fake Telegram API responder that returns success,
  timeout, `429`, `400`, connection error — asserting each maps to the correct
  frozen state/category without network.
- **Message/chart flow:** assert caption `sendMessage` order (incl. `parts`
  splitting), SVG `sendDocument` send, and text-still-delivered when chart fails.
- **Token/missing-token:** no token ⇒ `NOT_ATTEMPTED`; token never appears in any
  record/error.
- **Rate limit/backoff:** token-bucket gating and backoff caps verified with fake
  clock; determinism asserted (no real wall clock in logic).
- **Determinism:** identical inputs → identical `delivery_id`s, ordering, and
  state (no random/machine dependence).
- **Regression:** all frozen Phase 24B (51) + Phase 24C (24) delivery tests remain
  green and untouched; scope audits confirm no telegram import in the presentation
  core and no upstream import of the transport.

---

## 19. Deployment considerations

- Transport is **optional and off by default**; the signal bot runs fully offline
  with `NullSink` unless a Telegram transport is configured and a token injected.
- The token is provided via the environment/secret store at process start; the
  config file never contains it.
- Deployment does not change Phase 1–23 behavior; enabling delivery is purely a
  downstream operational choice.
- No daemon/webhook listener in 24D; outbound polling-driven or per-signal push.
- Chart as SVG document (not photo) avoids any rasterization dependency at deploy.

---

## 20. Operational monitoring considerations

- Emit per-`delivery_id` outcome counts by `DeliveryState` and `FailureCategory`
  (deterministic, no secrets).
- Track outbound rate (requests/sec) and dropped attempts; alert only on repeated
  `FAILED`/`UNKNOWN` to avoid paging on transient network noise.
- Redact all logs (token, chat id); never log message content beyond the caption
  already published.
- A delivery's `UNKNOWN` (timeout/no confirmation) is expected occasionally;
  monitoring should distinguish `UNKNOWN` (do not hammer, surface) from
  `FAILED`/`RATE_LIMIT` (retryable within policy) from `CONFIG` (operator error,
  no network call).

---

## 21. Future-extension points

- **Multi-part caption delivery (deferred, unimplemented):** a later phase could
  send the frozen `parts` (already split deterministically by `split_caption`) as
  an ordered sequence of `sendMessage` calls, so a caption beyond the transport
  budget still delivers instead of being refused. As built through Phase 24D-C1,
  an over-budget caption is refused pre-flight (`NOT_ATTEMPTED`/`CONFIG`) with no
  network call; see §7 step 3. Nothing in the current transport iterates `parts`.
- **Raster chart delivery:** a later phase may add an approved PNG rasterization
  dependency to send the chart via `sendPhoto`; the SVG `sendDocument` path
  remains the 24D default.
- **Webhook delivery acknowledgements / richer receipts:** a later phase could add
  an inbound webhook solely to confirm delivery — never to feed signals.
- **Multiple destinations / fan-out:** the coordinator already addresses one
  logical `destination_id` per call; fan-out can be modeled as multiple calls to
  distinct `destination_id`s with independent receipts.
- **Secret store adapters:** token injection could read from a secret manager
  instead of an env var behind the same opaque interface.
- **Batch/streaming transports:** the transport abstraction generalizes to other
  messaging providers behind the same `MessageSink` boundary.
- **Replay:** the fully offline coordinator + a fake responder already allow
  deterministic replay testing of the whole pipeline on stored snapshots.

---

## 22. Deliverable 2 — new modules to be created in 24D-B

A new **sibling package** under `src/smcsignal/delivery/` so the presentation core
(`message.py`, `formatting.py`, `models.py`, `sink.py`, `config.py`, `identity.py`,
`audit.py`, `orchestrator.py`) is never modified and never imports Telegram:

- `src/smcsignal/delivery/telegram/__init__.py` — public exports.
- `src/smcsignal/delivery/telegram/config.py` — `TelegramConfig` + `load_telegram_config` (strict, no secrets).
- `src/smcsignal/delivery/telegram/destination.py` — logical `destination_id` → real `chat_id` resolution + strict validation + `redact` helpers.
- `src/smcsignal/delivery/telegram/http.py` — thin, injectable Telegram Bot API HTTP client (sendMessage/sendDocument); accepts an injected transport for tests; TLS-only.
- `src/smcsignal/delivery/telegram/sink.py` — `TelegramSink` implementing the frozen `MessageSink` (payload-aware entry + `deliver(attempt)`) and state/category mapping.
- `src/smcsignal/delivery/telegram/rate_limit.py` — deterministic in-process token bucket.
- `src/smcsignal/delivery/telegram/driver.py` — composes the frozen coordinator to obtain a `DeliveryOutcome` and hands `rendered` + attempt to the sink (resolves the payload-threading gap additively; see D1).
- `src/smcsignal/delivery/telegram/audit.py` — thin redaction/audit glue reusing frozen `DeliveryAuditRecord`/`redact`.

Tests under `tests/delivery/telegram/` (no network, injected fake responder).

`docs/phase24d-telegram-transport-design.md` is this document (kept updated).

---

## 23. Deliverable 3 — unresolved design decisions

- **D1 (critical, must resolve before 24D-B code): payload threading.** The frozen
   `MessageSink.deliver(attempt)` does not carry rendered text/bytes. Decide the
   additive mechanism: (a) a 24D driver that reads `DeliveryOutcome.rendered`
   (recommended — zero change to 24B/24C), vs (b) an opt-in additive payload method
   on the sink that the coordinator calls only when present (smaller, still
   additive, but touches the coordinator call site). This must not alter the
   default offline sink behavior.
- **D2: `SENT` vs `DELIVERED` semantics for Telegram.** Decide whether Telegram's
   API-accepted response is `SENT` and only a later confirmation is `DELIVERED`,
   or whether API-accepted is treated as `DELIVERED`. Recommendation: API-accepted
   with no per-message confirmation capability ⇒ `SENT`; keep `DELIVERED` for when
   delivery is confirmed.
- **D3: HTTP client dependency.** Direct `urllib`/stdlib vs an approved third-party
   HTTP client. 24A recommended a minimal surface (option B). Selecting an HTTP
   library requires an explicit dependency approval; stdlib keeps zero runtime
   deps.
- **D4: chart transport.** Confirm SVG-via-`sendDocument` as the 24D default and
   defer PNG-via-`sendPhoto` (needs a rasterization dependency). No photo path in
   24D without approval.
- **D5: `max_attempts` interplay.** Whether the coordinator's frozen
   `OrchestrationConfig.max_attempts` already covers transport retries, or whether
   a separate transport `retry_max_attempts` is warranted. Recommendation: keep the
   coordinator cap authoritative; transport-level retries only for the immediate
   HTTP send.
- **D6: webhook vs polling for acknowledgements.** Deferred entirely; no inbound
   listener in 24D. If ever added, it is delivery-ack only and never signal input.

---

## 24. Deliverable 4 — recommended implementation order

1. **24D-B — offline transport scaffold (no real network):** strict `TelegramConfig`
   loader, destination resolution/validation, redaction glue, deterministic token
   bucket, injected-fake HTTP client, `TelegramSink` implementing the frozen
   `MessageSink` state/category mapping, and the 24D driver for payload threading
   (resolve **D1** first). All tests run against a fake responder; `NullSink`-style
   `NOT_ATTEMPTED` on missing token. This is fully testable offline.
2. **24D-C — real HTTP delivery path:** wire the stdlib/injectable client to the
   real Telegram Bot API `sendMessage`/`sendDocument`, env/secret token injection,
   TLS, timeout→`UNKNOWN`, `429` rate-limit handling, bounded backoff, error
   redaction. Real-network smoke is optional and gated behind an explicit opt-in
   (never in the default test suite).
3. **24D-D — operational integration & hardening:** coordinator integration (via
   the 24D driver), per-destination monitoring counters, replay regression on
   stored snapshots, security review, and full offline regression of Phases
   1–23 + 24B + 24C + 24D.

Nothing in 24D-B/C/D changes Phase 24B rendering, Phase 24C orchestration
semantics, or any Phase 1–23 module.

---

## 25. Deliverable 5 — final invariants

### Architectural invariants
1. Phase 24D is strictly downstream; it reads immutable published objects only.
2. The transport is one concrete sink behind the frozen coordinator; formatting and
   presentation never touch Telegram.
3. Rendered text is sent **verbatim** (already HTML-escaped and deterministic);
   the transport never re-renders or re-escapes.
4. Chart is delivered independently of text; a chart failure degrades only the
   chart.
5. `delivery_id` is content-derived and deterministic; at-most-once by in-process
   dedup; `UNKNOWN` acknowledged (no exactly-once claim).

### Security invariants
1. No token/secret in source or config; secret injection only at construction.
2. Missing token or disabled ⇒ transport is `NOT_ATTEMPTED`, never half-built.
3. Chat ids and tokens are redacted in every audit/log/error path.
4. No plaintext transport; no inbound signal-command surface.

### Governance / halal invariants
1. Telegram never approves/rejects Phase 23 candidates, edits strategy, or tunes
   parameters.
2. No self-improvement/optimization trigger exists in the transport.
3. Halal is display-only; `UNKNOWN` never becomes halal; blocking is an explicit
   upstream/config decision.
4. No trading, execution, broker, exchange, or order functionality.

### Scope invariants
1. No modification of Phase 1–23 logic, Phase 24B rendering, or Phase 24C
   orchestration semantics.
2. No secrets committed to source control.
3. Phase 24D-B/C/D introduces **no** runtime network path into the presentation or
   orchestration core (network lives only behind the transport).
4. No Phase 24E work is begun here.

---

## Recommended next phase

**24D-B — build the offline Telegram transport scaffold** (config, destination,
rate-limit, fake-HTTP, `TelegramSink`, driver) behind the frozen abstractions,
fully testable with no network and no token, resolving Open Decision D1 first.

---

## Phase 24D-B implementation record (D1 resolution, additive; design unchanged)

Phase 24D-B resolved Open Decision **D1** (payload threading) with a small,
fully additive, offline transport foundation. No Phase 24B/24C module was
modified; no network, HTTP, secret, or transport implementation was added.

### D1 resolution — design explanation

The frozen `MessageSink.deliver(attempt)` hands a sink only a `DeliveryAttempt`
(identity + routing, no content), while the rendered payload already exists on
the frozen coordinator's `DeliveryOutcome.rendered`. The smallest additive fix
recommended by the 24D-A design was implemented:

- A new immutable **`TransportPayload`** value that joins the existing delivery
  metadata (`delivery_id`, `message_id`, `signal_id`, `destination_id`,
  `attempt_number`) with the already-rendered `RenderedSignalMessage` (caption,
  parts, optional chart). No field is invented and nothing is re-rendered.
- A pure **`transport_payload_from_outcome(outcome)`** projection from the frozen
  `DeliveryOutcome`, so a future transport obtains full payload content without
  calling back into presentation or orchestration.
- A transport-facing **`PayloadSink`** capability (`deliver_payload(payload) ->
  DeliveryReceipt`) and an offline **`OfflinePayloadSink`** test double. The real
  remote transport (Phase 24D-C) will implement `PayloadSink` and be fed by the
  coordinator's `DeliveryOutcome` through this value.

Because the payload is a *projection* of the frozen outcome (never a new render
or a competing signal model), rendering, delivery ids, dedup, audit, states, and
failure categories are all byte-for-byte unchanged.

### Files created / modified

- Created: `src/smcsignal/delivery/transport.py` (TransportPayload, PayloadSink,
  OfflinePayloadSink, transport_payload_from_outcome).
- Created: `tests/delivery/test_transport.py` (14 focused tests).
- Modified (additive exports only): `src/smcsignal/delivery/__init__.py`
  (exports the new public names; export count 38 -> 42).
- This document (additive record).

### Test coverage summary

`tests/delivery/test_transport.py`: payload carries metadata + rendered text;
chart availability when a DrawingModel is bound and absence otherwise;
determinism across identical inputs; no mutation of the outcome; `OfflinePayloadSink`
records text/chart and maps states (`DELIVERED`/`FAILED`/`UNKNOWN`); payload is
available even on a disabled/no-op path (`NOT_ATTEMPTED`); validation guards
(non-outcome rejected, message-id mismatch rejected); audit relationship
unchanged; BUY still flows and non-BUY is still rejected.

### Scope / safety confirmation

- No Telegram networking, no HTTP client, no secrets/tokens, no transport
  implementation, no environment-variable requirements, no polling/webhooks.
- Phase 24D-C has **not** started.

---

## Phase 24D-C1 amendment — config-surface hardening (D1/D2)

Phase 24D-C1 resolved two read-only audit findings against the shipped 24D-C
transport. **§4's proposed field list is amended as follows**; every other part of
the design is unchanged.

- **`connect_timeout_seconds` — removed.** §4 proposed it as a "connect-only
  timeout". The implemented transport uses the standard library's single
  `urllib` per-request timeout, which governs connect and read phases together,
  so a distinct connect-only timeout cannot be honored without replacing the
  transport stack. Exposing a knob that silently does nothing was rejected;
  `network_timeout_seconds` is the one documented timeout. Removing it keeps the
  public configuration surface truthful and preserves the timeout → `UNKNOWN`
  contract.
- **`max_message_chars` — now enforced.** Previously validated but unused. It is
  applied by `TelegramSink` as a pre-flight caption budget: a caption longer than
  the budget reports `NOT_ATTEMPTED`/`CONFIG` and makes no network call, instead
  of being sent and rejected by the API. Nothing is truncated and nothing is
  re-rendered, so transport payload semantics and delivery-state semantics are
  untouched. With the default 4096 (Telegram's documented limit) the guard is
  unreachable for real payloads, because the frozen presentation budget caps
  captions at 4000 characters.
- **D2 — example configuration added.** `config/telegram.example.toml` follows the
  standalone single-table convention of `config/improvement.example.toml`
  (descriptive header, per-key comments, strict keys, no secrets) and is
  registered in `MANIFEST.in` and `config/README.md`.

Retry semantics, delivery-state mapping, transport payload semantics, and the
`[telegram]` table's remaining keys are unchanged. No Phase 1–24C module was
modified. Phase 24D-D has **not** started.

---

## Phase 24D-C2 — design/code alignment (documentation only)

Phase 24D-C2 changed **no implementation**. It reconciled this document with the
already-approved 24D-C1 code so the specification and the shipped transport agree:

- **Field name aligned.** §4's originally proposed name — which carried a
  `default_` prefix the implementation never used — is replaced by the
  implemented `max_message_chars`. The implementation name is authoritative; no
  code, config key, or test was renamed. (The superseded name is intentionally not
  reproduced anywhere in the repository, so a repo-wide search for it returns
  nothing.)
- **Default aligned.** The documented default becomes `4096` (Telegram's actual
  per-message limit), matching `DEFAULT_MAX_MESSAGE_CHARS` in the implementation.
  The previous documented `4000` was the presentation layer's rendering budget,
  not the transport limit; §4 now carries an explicit rationale for why the two
  numbers differ and live in different layers.
- **§7 step 3 corrected.** The spec previously described sending the frozen
  `parts` as an ordered sequence of `sendMessage` calls when a caption exceeded
  the budget. That behavior was never implemented. §7 now documents the as-built
  behavior — a pre-flight refusal (`NOT_ATTEMPTED`/`CONFIG`) with no network call
  — and records multi-part delivery as a deferred, unimplemented option.
- **§9 state mapping completed.** The `NOT_ATTEMPTED` row now also covers the
  over-budget refusal, which the implementation (and 24D-C1) already produced.
- **§4 field table marked as-built**, with the removed `connect_timeout_seconds`
  struck through rather than left looking proposed-and-live.

Transport behavior, public APIs, delivery-state semantics, retry semantics, and
transport payload semantics are unchanged by this phase. Phase 24D-D has **not**
started.

---

## Phase 24D-D implementation record — coordinator integration

Phase 24D-D adds the "24D driver" deferred by §24 item 3: a thin integration
layer that connects the frozen Phase 24C coordinator to the Phase 24D-C
transport. It creates one new module and modifies no Phase 1–24C file.

### Files created / modified

- Created: `src/smcsignal/delivery/telegram/integration.py` (`TelegramPayloadBridge`,
  `TelegramDeliveryIntegration`, `TelegramDeliveryResult`, `TelegramDeliveryCounters`).
- Created: `tests/delivery/telegram/test_integration.py` (37 tests, fake transports only).
- Modified (additive exports only): `src/smcsignal/delivery/telegram/__init__.py`
  (13 -> 17 public names).
- This document (additive record).

### How the two boundaries are joined (§2.1 resolved in code)

The frozen `MessageSink.deliver(attempt)` still carries identity only, so the
transport cannot be reached through that boundary alone. The driver composes the
existing pieces rather than changing any of them:

1. **Projection.** `TelegramDeliveryIntegration.deliver(frame, destination_id)`
   asks the frozen coordinator for the `DeliveryOutcome`. The coordinator is
   constructed **without a sink**, so it performs only the frozen projection
   (BUY boundary → `SignalMessage` → best-effort chart → `assemble_signal_message`
   → deterministic `message_id`/`delivery_id`) and can never touch the network.
   This is the single render; nothing re-renders.
2. **Projection to payload.** The outcome is converted with the Phase 24D-B
   `transport_payload_from_outcome`, reused unchanged.
3. **Bridge.** `TelegramPayloadBridge` implements the frozen `MessageSink`
   protocol, holds the supplied `TransportPayload`, and forwards each attempt to
   the injected `PayloadSink` (the 24D-C `TelegramSink`, or any double). It
   refuses to send without a binding, refuses a payload whose identity
   contradicts the attempt, substitutes the attempt number the frozen retry loop
   is on, and never renders or mutates content.
4. **Frozen retry/dedup loop.** Delivery runs through the frozen `deliver_message`
   with the caller's `DeliveryRegistry` and attempt cap, so `delivery_id`
   determinism, `SKIPPED_DUPLICATE` dedup, the `FAILED`-retries-only rule, the
   no-silent-retry rule for `UNKNOWN`, and the "stop on `NOT_ATTEMPTED`" rule are
   the frozen ones, not a reimplementation.
5. **Dedup switch honoured.** The driver passes the registry only when
   `OrchestrationConfig.deduplicate` is true, mirroring the coordinator so
   orchestration configuration keeps its pinned meaning on this path.

### Retry composition (§10 preserved)

Two bounded, non-overlapping scopes remain distinct: the transport retries
internally on 429/5xx and returns `FAILED`/`RATE_LIMIT` only once its own budget
is exhausted, and only then does the driver's `max_attempts` cap retry it. A
timeout (`UNKNOWN`) is still never retried, because the reply may have been lost
after the message was accepted.

### Delivery-state and failure-category semantics

No new state or category was introduced, and no state is remapped. The
integration returns the transport's `DeliveryReceipt` unchanged, plus the frozen
outcome it projected from. `TelegramDeliveryResult.state` is the transport's
receipt state: `DELIVERED`/`SENT` (sent), `UNKNOWN` (timeout, not retried),
`FAILED` (with the transport's category: `CONFIG` for 401, `RATE_LIMIT` for 429,
`TRANSPORT` otherwise), `NOT_ATTEMPTED` (disabled transport, missing token,
unresolvable destination, over-budget caption, or disabled master switch), and
`SKIPPED_DUPLICATE` (registry already delivered it). `was_sent` is deliberately
stricter than "a payload was forwarded": a transport that refuses pre-flight
receives the payload but sends nothing.

### Observations and non-goals

- **Audit.** `TelegramDeliveryResult.audit_record()` reuses the 24D-C
  `telegram_audit_record` (deterministic, token-free, chat id redacted). It
  raises for a delivery that was never sent, because no honest record exists.
- **Monitoring counters.** `TelegramDeliveryCounters` is the §24 item 3
  per-destination tally, implemented as an immutable value recorded per finished
  delivery. It is inert bookkeeping: it cannot change a receipt, a retry
  decision, or any upstream fact.
- **Not in this phase.** §24 also lists replay regression on stored snapshots and
  a security review for 24D-D; neither is implemented here. Polling, webhooks,
  inbound message processing, Telegram commands, database persistence, and
  multi-channel fan-out remain out of scope and absent, as required.
- **Downstream-only.** `smcsignal.delivery` does not import the telegram package;
  nothing in `analysis/`, signal generation, rendering, halal, eligibility,
  governance, risk, or execution imports or observes this layer.

