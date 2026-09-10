# Self-Improvement Methodology (Phases 23A / 23B / 23C)

This document describes the research-and-improvement framework that Phase 23
adds on top of the frozen Phase 22 strategy-intelligence foundation. It is a
**structured, human-supervised research loop**, not autonomous self-modification:
the software can derive findings and hypotheses, confirm them under explicit
human supervision, run controlled experiments, and compare candidate deltas
against a golden baseline — but it never adopts anything on its own and never
changes its own decision logic or any Phase 1–22 code.

All Phase 1–22 decision logic is untouched by this framework. Phase 23 is
entirely additive and consumes the published public APIs of Phases 20/21/22
and of the Phase 23 modules without modifying them.

## The lifecycle

The framework implements one observational lifecycle. Nothing in it is
automatic beyond producing a `REVIEW_REQUIRED` recommendation:

```
OBSERVE                  → consume an already-published Phase 22 intelligence
                           report (no replay, no recompute, no future data)
FIND WEAKNESS            → derive descriptive evidence-quality-gated findings
PROPOSE HYPOTHESIS       → frame PROPOSED hypotheses from sufficient findings
HUMAN CONFIRMATION       → explicit CONFIRMED / REJECTED decision (operator
                           identity + rationale; timestamp recorded for audit
                           only and excluded from the identity)
EXPLICIT CANDIDATE       → a CandidateExperimentDef is created ONLY from a
                           CONFIRMED hypothesis and an allow-listed delta
CONTROLLED EXPERIMENT    → isolated candidate configuration = frozen baseline
                           + explicit deltas; run through the unchanged
                           Phase 20/21/22 engines
EVALUATION / COMPARISON  → exact-Decimal candidate−baseline comparison over
                           the two isolated runs
HUMAN REVIEW             → a research-review report; decision defaults to
                           REVIEW_REQUIRED
HUMAN APPROVAL/REJECTION → APPROVED / REJECTED are reachable ONLY through the
                           explicit human-decision operation
```

The system must **never** automatically perform
`OPTIMIZE → SELECT → MODIFY → DEPLOY`. There is no such code path.

## Design principles

1. **Human decision, software measurement.** No automatic status beyond
   `REVIEW_REQUIRED` (a recommendation). `APPROVED` / `REJECTED` come only from
   the explicit human decision. Hypotheses stay `PROPOSED` until explicit human
   confirmation. Improved historical numbers never automatically imply
   adoption, selection, or deployment.
2. **Deterministic identity.** Every finding, hypothesis, confirmation,
   candidate, and review report has a stable, content-addressed id. Ids and
   outputs never depend on clocks, randomness, environment ordering, or display
   timestamps. A human confirmation wall-clock timestamp may never be part of a
   deterministic identity.
3. **Golden baseline.** Phase 22 (`0.22.0`, commit `9a0af26`) is the immutable
   reference. Candidates reference its canonical configuration hash and compare
   against it; they never silently replace it and never mutate it.
4. **Closed candidate surface.** A candidate may change **only** a setting on
   the closed Phase 23A allow-list (`ALLOWED_SURFACES`). Everything else is
   rejected; an unsupported hypothesis cannot be turned into a candidate.
5. **Exact arithmetic.** All numeric improvement comparisons are exact
   `Decimal` differences of `candidate - baseline`. Nothing is silently rounded,
   and no improvement / rank / selection claim may be made below
   `minimum_finalized_for_comparison = 30` finalized outcomes.
6. **No invented metrics.** Improvement only copies metrics the engines already
   provide. Data is never fabricated to force an improvement.
7. **No look-ahead.** Finding derivation and hypothesis generation consume only
   an already-published report; they never reach the evaluation/comparison
   engines or run the Phase 20/21/22 layers themselves, so they cannot see
   future validation outcomes.

## Phase 23A — foundational models and the approval state machine

`src/smcsignal/analysis/improvement/` foundations:

* Strict `ImprovementConfig` with a mandatory `human_approval_required = true`
  and an immutable frozen-baseline config hash.
* Immutable proposal/candidate models (`Hypothesis`, `CandidateExperimentDef`,
  `CandidateDelta`, `HumanDecisionRecord`) with deterministic identities.
* The closed `ALLOWED_SURFACES` allow-list over already-frozen config settings,
  each anchored to its real baseline value.
* The approval state machine:
  `PROPOSED → EXPERIMENTAL → EVALUATED → REVIEW_REQUIRED`, plus `FAILED`.
  `transition` never produces `APPROVED`/`REJECTED`; only
  `apply_human_decision` (carrying an explicit `HumanDecisionRecord`) may move a
  candidate to `APPROVED` or `REJECTED`. A `FAILED` or `REJECTED` candidate is
  terminal and can never reach approval.
* `APPROVED` means approved **for future consideration only**. There is no
  promotion/deployment mechanism; an approved candidate cannot modify the
  production strategy or any signal-generation behavior.

## Phase 23B — offline evaluation and baseline comparison

* `EvaluationProtocol` declares the frozen protocol (dataset identity, symbol /
  timeframe scope, historical range, walk-forward `RobustnessConfig`, planned
  validation windows, minimum finalized sample). `ExperimentDeclaration`
  validation rejects an incomplete protocol on any dimension.
* `evaluate_config` / `evaluate_candidate` run the **unchanged** Phase 20/21/22
  engines over an **isolated candidate configuration** that is the frozen
  baseline plus only the candidate's explicit deltas (via `dataclasses.replace`
  — the baseline object is never mutated). It copies only engine metrics.
* `compare` produces exact-Decimal `candidate - baseline` rows; a row is
  `conclusive` only when both sides reach the finalized minimum. Robustness
  degradation/stability dimensions are never treated as conclusive outcome
  evidence.

## Phase 23C — findings, hypotheses, confirmation, candidate creation, review

* `derive_findings` / `finding_from_cell` / `classify_evidence` derive
  descriptive weakness findings from an already-published Phase 22
  `IntelligenceReport`, gated by evidence quality
  (`SUFFICIENT` / `INSUFFICIENT_SAMPLE` / `INCOMPLETE` / `FAILED`).
* `generate_hypotheses` frames `PROPOSED` hypotheses from sufficient findings;
  it never tunes, selects, or sweeps a value (a human supplied the delta) and
  never claims causation or future performance.
* `confirm_hypothesis` records an explicit human `CONFIRMED` / `REJECTED`
  decision with operator identity and rationale; the wall-clock `confirmed_at`
  is retained for audit only and excluded from `confirmation_id`.
* `create_candidate_from_confirmed_hypothesis` is the single bridge to a
  candidate. It requires a `CONFIRMED` confirmation of this exact hypothesis, a
  matching baseline hash, an allow-listed delta, and a fully validated
  declaration. A `REJECTED` confirmation or any off-list delta is rejected — the
  allow-list is never expanded to accommodate it.
* `build_review_report` / `review_json` / `review_text` produce a deterministic
  research-review report whose `display_decision` defaults to `REVIEW_REQUIRED`
  unless an explicit human decision exists. A failed evaluation is reported as
  evidence quality `FAILED`.

## Honest-limitations contract

The review report and all derived prose carry hard-coded caveats and never
claim:

* that an observed weakness **caused** a result (no causal claim);
* that any result will reproduce in the future (no future guarantee);
* statistical significance unless it was separately established;
* that a small or incomplete sample shows improvement;
* that a candidate has been adopted (nothing is adopted in Phase 23);
* that better historical numbers are a selection or a recommendation;
* future-performance prediction, trading advice, or a production deployment.

## Out of scope / prohibited in Phase 23

The following are explicitly **not** part of Phase 23 and remain prohibited:

* Telegram, exchange / live / order execution, promotion;
* dynamic expansion of the candidate allow-list (a different delta is a
  different candidate and must pass Phase 23A surface validation);
* alteration of signal logic, thresholds, enablement, eligibility, or halal
  status, directly or through any Phase 23 API;
* autonomous self-modification or auto-selection / auto-optimization /
  auto-search;
* feeding Phase 23 results back into signal generation;
* rollback / promotion / deployment infrastructure (not implemented);
* Phases 24/25+.

Phase 23 ends at a **freeze / validation report**. Nothing is committed, pushed,
adopted, or deployed without a subsequent, separate human-approved phase.
