# Strategy Intelligence methodology — Phase 22 research reporting

Observational research layer over Phase 21 validation results, not a trading
system and not an optimizer.

**Stop after Phase 22. Phase 23 requires explicit approval.**

## Question answered

Among the published spot `BUY_SIGNAL` facts that survived Phase 21 walk-forward
validation, how do their outcomes distribute across signal-time strategy
profiles — setup type, symbol, timeframe, month, and causal market regime —
and which sufficiently sampled profiles can be described, purely
observationally, as consistent winners, losers, strengths, or weaknesses?
Phase 22 answers exactly that question and nothing else. It is **intelligence
reporting**: it reads the records the earlier phases already published and
labels them; it never tunes, selects, re-detects, or proposes any change.

## Purpose and scope

`analysis/intelligence/` is a **consumer-only** layer over the Phase 21
`RobustnessReport`. It takes that report's out-of-sample **validation rows**
as its population, groups them by signal-time facts, and composes exact
descriptive statistics with the existing Phase 18 `aggregate` helper. It never
re-runs the Phase 20 replay, never re-detects a market regime, never recomputes
an outcome, never re-classifies a score, and never re-derives attribution. No
Phase 1–21 module is modified by the layer, and no decision module may import
it (enforced by import-graph tests). The layer never runs live, never touches
an exchange, and has no third-party runtime dependency.

## Setup intelligence

Phase 19 publishes an outcome-independent attribution profile per BUY signal and
a deterministic **combination key** (the canonical `+`-joined closed-taxonomy
labels that describe the setup type). Phase 22 groups the validation population
by that key and reports each group's descriptive statistics. A group is labeled
by its setup-type combination, so a reader can see, for a given setup family,
how the already-published outcomes were distributed. Grouping by the signal-time
setup key only: an outcome is never used to decide which group a signal belongs
to.

## Conditional analysis

Beyond the overall population, Phase 22 reports the same descriptive treatment
conditioned on other signal-time facts, each as a cell family that partitions
the population exactly once:

- **by setup** — the Phase 19 setup-type combination key;
- **by symbol** — the exchange symbol of the signal candle;
- **by timeframe** — the primary timeframe of the series;
- **by month** — the UTC calendar month of the signal candle (the existing
  Phase 19 `month_of` form);
- **by regime** — the Phase 21 causal regime annotation carried on the row
  (`TRENDING`, `RANGING`, `HIGH_VOLATILITY`, `LOW_VOLATILITY`, or an explicit
  `unclassified` bucket for warmup rows).

Every family reuses the identical cell computation; there are no conditional
formulas. Because validation segments never overlap, each described signal is
counted exactly once across the report.

## Winner/loser pattern analysis

Given a group's exact statistics, the layer computes a deterministic
**pattern** label once the group reaches the configured diagnosis minimum:

- `WINNER` when the win rate is at least `winner_win_rate_floor` **and** the
  average final return is positive;
- `LOSER` when the win rate is at most `loser_win_rate_ceiling` **and** the
  average final return is negative;
- `NEUTRAL` otherwise;
- `UNDERSAMPLED` when the finalized sample is below the diagnosis minimum
  (the raw counts always remain visible).

The configuration requires `loser_win_rate_ceiling` to sit strictly below
`winner_win_rate_floor`, so `WINNER` and `LOSER` are mutually exclusive. These
labels describe the past published population; they are not a prediction and
never select anything.

## Strength and weakness diagnostics

The pattern is read into a **diagnostic** label: `STRENGTH` for a `WINNER`,
`WEAKNESS` for a `LOSER`, and `UNDETERMINED` otherwise (including every
undersampled group). "Strength" and "weakness" here are shorthand for the
descriptive winner/loser pattern of the group's own history; they carry no
causal claim, no forward expectation, and no trade recommendation. The text
rendering and machine summary count strengths and weaknesses per family purely
so a reader can survey them.

## Deterministic ranking

Within each dimension, groups that reach the configured
`minimum_finalized_for_ranking` are assigned a contiguous 1-based **rank**
(1 = best) by a fixed comparator: descending average final return, then
descending win rate, then ascending name. Ranks are research ordering only —
they select, enable, disable, veto, or recommend nothing, and a change of rank
never affects any signal or outcome.

## Minimum sample requirements

`minimum_finalized_for_diagnosis` and `minimum_finalized_for_ranking` are the
only gates. A group is *sufficient* for a winner/loser pattern when its
finalized sample reaches the diagnosis minimum, and rank-eligible when it
reaches the ranking minimum (which is required to be at least the diagnosis
minimum, so every ranked group is also diagnosed). Below the diagnosis minimum a
group is `UNDERSAMPLED` and diagnostically `UNDETERMINED` — its raw counts are
still reported, never hidden or silently merged. No statistical significance is
claimed anywhere in this layer.

## Provenance

Every report is a deterministic function of its inputs. `report_id` digests the
frozen `intelligence-v1` configuration artifact, the sorted series keys, and —
for every validation signal in deterministic order — its signal-time facts, its
Phase 21 regime label, and its published outcome status. Decimal statistics
serialize as exact strings in the canonical-JSON machine summary. Repeating the
analysis on the same Phase 21 report is byte-identical.

## Observational vs causal limitations

Phase 22 is **observational**. A cell shows how published outcomes were
distributed for a group in the historical, out-of-sample population. A pattern,
strength, or weakness is a description of that history. It is not evidence that
the pattern will persist, not a causal explanation (the regime labels are
context annotations, not causes), and not an expectation of future returns.
Sample sizes are always shown so the reader can judge whether a group is
meaningful.

## No-lookahead and signal-time vs post-outcome separation

Phase 22 adds no new look-ahead surface because it performs no detection: it
consumes only facts Phase 21 already cut at window boundaries, and it never
reads a candle, a regime series, or a replay. Two structural guarantees keep it
that way:

- **Signal-time separation.** Cell membership is decided exclusively by
  facts available at publication — setup combination, symbol, timeframe, UTC
  month of the signal candle, and the Phase 21 regime annotation at that
  candle. Post-outcome fields (`outcome_status`, returns, MFE/MAE) appear only
  as statistics *inside* an already-formed cell; they never decide membership.
- **Fact-derived identity.** The intelligence `report_id` and machine summary
  are derived from the concrete row facts, **not** from the Phase 21 `report_id`
  or any walk-forward window identity. Appending future data, or a future tail
  whose causal regime annotation legitimately differs, can add new validation
  rows but cannot rewrite an already-formed cell, whose membership is
  signal-time determined.

## Validation-only role and non-optimization boundary

The frozen configuration artifact declares the role explicitly: optimization,
parameter selection and tuning, automatic strategy selection, threshold tuning,
self-modification, feedback into signal generation, automatic setup
enabling/disabling, signal veto, regime re-detection, signal generation, live
trading, execution, and advice are all `False`. There is nothing to optimize:
the two minimums and the two pattern bounds are measurement gates, not strategy
parameters, and no code path selects, tunes, or rewrites anything based on
results. A "finding" is exactly a sufficiently sampled cell with a resolved
pattern/diagnostic and its exact statistics and rank — nothing more, and never a
trading instruction.

## Relationship to Phase 23

Phase 23 (optimization) is **not approved and is never run**. Phase 22 stops at
describing the validated, published population. If Phase 23 is ever approved it
would be a separate later layer; Phase 22 contains no optimization machinery and
returns no artifact that could feed one. Reports flag
`phase_23_optimization: false` explicitly.
