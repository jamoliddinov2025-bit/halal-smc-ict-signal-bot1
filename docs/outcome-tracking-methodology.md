# Outcome tracking methodology — Phase 18

## Purpose and scope

Phase 18 answers one analytics question: **given already-published Phase 17 spot
signals, how did each `BUY_SIGNAL` resolve over a fixed horizon of completed
candles?** It consumes existing **`SignalSnapshot`** frames only. Nested Halal,
SQS, MTF, OTE, PD, OB, FVG, displacement, liquidity, and structure objects
remain the original instances. Upstream evidence IDs are never rewritten.
Earlier analyzers are not rerun. Future candles are not read before the stream
publishes them.

This is **consumer-only outcome analytics**, not trading: no live trading,
order execution, entries, exits, stops, targets, fees, slippage, position
sizing, sell signals, short trades, or optimization exists in this layer.
Zero tracked outcomes is a valid result. Historical outcome versions never
change.

## Tracked scope

Only `BUY_SIGNAL` frames open outcomes. `NO_SIGNAL` and `BEARISH_AVOID` frames
create nothing; they still evaluate already-open outcomes and publish an
`OutcomeSnapshot`. There are no SELL outcomes and no short-side analytics.

## Outcome lifecycle

```text
frame t   BUY_SIGNAL published ──► outcome created: OPEN, candles_observed = 0
frame t+1 … t+K                 ──► every later frame's closed candle evaluates
                                   each OPEN outcome: extremes update,
                                   candles_observed increments, a new immutable
                                   version is published in that frame's snapshot
frame t+K (horizon reached)     ──► finalizing version: WIN / LOSS / FLAT with
                                   finals frozen; the record never changes again
stream ends before t+K          ──► the outcome stays OPEN forever in that
                                   replay; no end-of-series flush exists
```

Within one frame the ordering is fixed: existing OPEN outcomes are evaluated
against the current candle first; a `BUY_SIGNAL` published on the current
candle is registered afterwards. A signal candle is therefore never inside its
own evaluation window, and one frame can complete an old outcome and open a
new one simultaneously. Transitions are one-way: OPEN → WIN, LOSS, or FLAT.

## Reference price, horizon, and formulas

- **Reference price** = the close of the signal candle (the publication-time
  factual price; not an entry, fill, or order).
- **Horizon** `horizon_bars = K` = the K completed primary candles that close
  strictly after the signal candle. Evaluation candle *j* is stream index
  `t+1 … t+K`; the close of candle `t+K` is the final close.
- `final_difference = final_close − reference_close` — **exact** Decimal
  subtraction.
- `final_return = final_difference / reference_close` — descriptive ratio.
- `mfe_return = (mfe_price − reference_close) / reference_close` and
  `mae_return = (mae_price − reference_close) / reference_close` — descriptive
  ratios.

Arithmetic follows the approved displacement discipline: exact digit-counted
contexts for sums and differences, and 50-significant-digit `ROUND_HALF_EVEN`
ratios that are **descriptive values, never gates**. Classification never
reads a ratio (below), so rounding can never flip a WIN/LOSS/FLAT result.
Division by zero is impossible because the data layer guarantees strictly
positive prices.

## WIN / LOSS / FLAT

Classification uses **only the exact sign of `final_difference`**:

| Exact comparison | Status |
| --- | --- |
| `final_difference > 0` | `WIN` |
| `final_difference < 0` | `LOSS` |
| `final_difference == 0` | `FLAT` (exact Decimal equality) |

No threshold, fee, or slippage model participates. Long-side frame only:
a close above the reference is a WIN. OPEN is not a performance claim.

## MFE and MAE

- `MFE_price` = running **maximum high** over the evaluation candles observed
  so far; `MAE_price` = running **minimum low**. Both always update before the
  horizon check, so the final candle's high/low count.
- **Tie rule:** an equal extreme does not replace the running extreme; the
  **first** occurrence keeps its price and index.
- `mfe_index` / `mae_index` are the stream candle indices of the extreme
  candles for auditability and must lie inside the observed window.
- While OPEN the values are explicitly **partial** over observed candles only,
  never extrapolated. MFE is not a take-profit; MAE is not a stop.

## Configuration

```toml
[outcome_tracking]
enabled = true
horizon_bars = 10
```

The table must contain **exactly** those two keys. `enabled` must be true.
`horizon_bars` is an integer from 1 to 10 000 (default 10). Direct
`OutcomeTrackingConfig()` uses the same defaults. Unknown keys — including
`entry`, `stop`, `target`, `fees`, `slippage`, `position_size`, and any other
trading knob — are rejected. Reference price, evaluation basis, classification
rule, and BUY-only scope are fixed methodology, not settings.

## Deterministic identity and provenance

- `outcome_id = "spot-outcome:" + sha256({methodology: "outcome-tracking-v1",
  signal_id, horizon_bars})` — stable across every lifecycle version and
  independent of future candles.
- Every lifecycle version is an immutable `SignalOutcome` with its own
  `outcome-record` evidence ID from the existing canonical codec and
  provenance factory. The key contains the full version facts (status,
  observed count, extremes, finals, availability timestamps).
- Records reuse the **current consumed prefix hash** of the signal frame they
  were published with; dependencies are the upstream `signal-frame` reference
  plus the prior outcome-version reference. `source_candles` always includes
  the signal candle and the current evaluation candle.
- `OutcomeSnapshot` (producer `outcome-frame`) retains the original
  `SignalSnapshot` instance and reuses its prefix hash. Upstream `signal_id`,
  `setup_identity`, and all nested Phase 1–16 IDs are copied verbatim, never
  recomputed. Changing only `horizon_bars` changes outcome IDs, not any
  upstream identity.
- The configuration artifact declares every non-goal (execution, entry, exit,
  stop, target, fees, slippage, position sizing, sell, short trade,
  optimization) as explicitly false.

## Causal / no-look-ahead rules

Input must be `SignalSnapshot` frames from one signal-engine configuration,
starting at index zero and remaining consecutive, chronological, unique, and
non-rewinding in availability. Series identity cannot change mid-stream. A
`BUY_SIGNAL` whose `signal_id` is already tracked is rejected with the
analyzer state unchanged; distinct signal IDs sharing one setup identity
(possible through the stateless Phase 17 mapping) remain independent outcomes.

Outcome creation reads only the signal frame's own facts. Evaluation reads
only candles published in later consumed frames; the tracker has no second
price feed and cannot see data the stream has not delivered. Local state
commits only after every check, version, and provenance construction
succeeds, so a failed `update()` leaves the last output, registry, and index
byte-identical.

For identical fixed-origin signal history and configuration:

- Every prefix equals the corresponding full-series prefix, including every
  outcome version, analytics summary, and evidence ID.
- Batch, streaming, and arbitrary chunk boundaries produce identical output
  sequences.
- Appending or replacing later frames cannot change a finalized outcome or any
  earlier published version.

Tests are software consistency checks, not a performance backtest.

## Aggregate analytics

`AnalyticsSummary` is recomputed per frame as a pure function of the outcome
registry (creation order):

| Field | Definition |
| --- | --- |
| `total_buy_signals` | outcomes ever opened |
| `open_count` | currently OPEN |
| `win_count` / `loss_count` / `flat_count` / `finalized_count` | exact integers; `win+loss+flat = finalized` |
| `final_return_sum` / `mfe_return_sum` / `mae_return_sum` | exact Decimal sums over **finalized** outcomes only |
| `win_rate` | `wins / finalized`, 50-digit descriptive ratio; `None` iff `finalized_count == 0` |
| `average_final_return` / `average_mfe_return` / `average_mae_return` | exact sum ÷ finalized, descriptive; `None` when no finals |

Open outcomes contribute only to `open_count`; partial MFE/MAE never leak into
statistics. A zero-signal stream is valid and yields zero counts, zero sums,
and `None` rates. These are software statistics of publication records — not a
backtest, performance claim, or expected-return forecast.

## API

```python
from smcsignal.analysis import (
    OutcomeTrackingAnalyzer,
    OutcomeTrackingConfig,
    analyze_outcome_tracking,
)

tracker = OutcomeTrackingAnalyzer(OutcomeTrackingConfig(horizon_bars=10))
for signal_frame in signal_frames:
    snapshot = tracker.update(signal_frame)

snapshots = analyze_outcome_tracking(signal_frames, OutcomeTrackingConfig())
```

- `SignalOutcome` — one immutable lifecycle version (identity, copied facts,
  reference, progress, extremes, finals, availability, provenance)
- `AnalyticsSummary` — deterministic per-frame aggregate view
- `OutcomeSnapshot` — original signal frame, `created`/`evaluated`/`completed`
  version tuples, analytics, frame provenance
- `OutcomeTrackingAnalyzer.open_outcomes` / `finalized_outcomes` — read-only
  creation-ordered registry views

## Hand-computed synthetic example

`config/outcome-tracking.example.toml` reuses the Phase 13–17 synthetic
15m/1h/4h history. With the SQS threshold lowered to 10, `BUY_SIGNAL`
publications occur at indices 4, 8, 12, and 16 (reference closes 24, 28, 32,
36; closes rise 20 → 36). With `horizon_bars = 10`:

| Signal | Reference | Evaluation | Result |
| --- | --- | --- | --- |
| index 4 | 24 | candles 5–14 | `WIN`, final close 34; MFE 35 @ 14; MAE 24 @ 5 |
| index 8 | 28 | candles 9–16 (8 of 10) | remains `OPEN`; partial MFE 37 @ 16; MAE 28 @ 9 |
| index 12 | 32 | candles 13–16 (4 of 10) | remains `OPEN` |
| index 16 | 36 | none | remains `OPEN`, 0 observed |

Final analytics: 4 total, 1 finalized WIN, 3 open, `win_rate = 1`,
`final_return_sum = 10/24`. Falling and flat price tails (documented in the
tests) produce hand-computed `LOSS` and exact-tie `FLAT` classifications,
including cases a float could not distinguish from zero.

## Known limitations and stop boundary

- Outcome-tracking-v1 consumes the Phase 17 frame stream only; it has no
  independent market-data access and tracks outcomes per replay, not across
  processes. There is no persistence.
- The registry retains every outcome ever opened plus the latest version per
  outcome; retained snapshot tuples inherit nested upstream history. There is
  no bounded-total-memory guarantee.
- Aggregates cover this replay's publication records only. No statistical
  inference, sample-size policy, or expected-return claim is made or implied.
- BEARISH_AVOID and NO_SIGNAL publications are intentionally untracked; a
  short-side analytics layer would require explicit approval.
- The duplicate-signal guard rejects a `BUY_SIGNAL` whose `signal_id` is
  already tracked; in a valid consecutive stream this cannot occur because
  consecutive frames embed distinct upstream evidence, so the guard is
  defense in depth against fabricated input.

No live trading, futures, leverage, short selling, stop loss, take profit,
entry price, position sizing, portfolio management, order execution, exchange
trading APIs, Telegram, chart rendering, backtesting engine, ML,
optimization, or self-improvement is implemented.

**Stop after Phase 18. Phase 19 requires explicit approval.**
