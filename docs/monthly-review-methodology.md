# Monthly review methodology — Phase 19d

## Purpose and scope

Phase 19d renders one **descriptive monthly review** per UTC calendar month of
a Phase 19c performance report. It recomputes nothing: month buckets are
copied from the report, statuses are already final, and the only arithmetic
is exact Decimal month-over-month deltas. Reviews are software statistics of
publication records — **not advice, not a forecast, and not a performance
claim**.

## Monthly buckets

A month bucket is `YYYY-MM`, the UTC calendar month of the signal candle's
`opened_at`. `build_monthly_reviews(report)` returns one review per report
month in chronological order. Each review pairs its month bucket with the
chronologically previous month bucket of the same report. The first month has
no comparison by construction.

## Comparisons and sample sizes

`MonthComparison` carries **both sample sizes always** (`current_finalized`,
`previous_finalized`). Deltas exist only when **both** months reach
`minimum_finalized_for_comparison` finalized outcomes (default 10, range
1–1000):

- `both_sufficient` is true exactly when both months reach the minimum.
- `win_rate_delta` and `average_final_return_delta` are exact Decimal
  differences; they are `None` whenever either month is insufficient.
- Insufficient comparisons are rendered as an explicit suppression line that
  still shows both sample sizes and the configured minimum.

Sample sizes are never hidden. A reader can always see how much evidence
stands behind each month.

## Deterministic identity and rendering

`review_id = "monthly-review:" + sha256({methodology, settings, facts})`
where the facts carry the month, the report identity, the bucket, and the
previous month name. `render_review_text` is a pure function of the reviews
and the UTC generation instant; identical inputs render identically, and
rendering never mutates the reviews. Output is plain text only — no markup,
no charts, no transport.

## Configuration

```toml
[review]
enabled = true
minimum_finalized_for_comparison = 10
```

The table must contain exactly those two keys. Unknown keys — including
`advice`, `forecast`, or any decision knob — are rejected.

## Validation rules

Input must be one `PerformanceReport` with sorted unique month buckets; a
report without months (zero-signal replays) cannot be reviewed. Reviews
require their exact month bucket, comparisons pair exactly with the previous
bucket, sample sizes must match the buckets, and `both_sufficient` must match
the configured minimum. All invariants are enforced in the immutable models.

## API

```python
from smcsignal.analysis import (
    ReviewConfig,
    build_monthly_reviews,
    render_review_text,
)
from datetime import UTC, datetime

reviews = build_monthly_reviews(report)
text = render_review_text(reviews, generated_at=datetime.now(UTC))
```

## Hand-computed synthetic example

`config/review.example.toml` reuses the synthetic history. The
January/February long-replay report produces two reviews. 2024-01: 4 signals,
4 finalized, 4 wins, `average_final_return = 10/24 + … / 4`, comparison
"none (first reviewed month)". 2024-02: 1 signal, 1 finalized WIN. Under the
default minimum 10 the comparison is suppressed with both sample sizes shown
("samples 1 and 4 finalized; minimum 10"); under minimum 1 the exact deltas
appear (`win_rate delta 0`, `average_final_return delta 0.1589…`). Both
renderings are golden-tested.

## Known limitations and stop boundary

- Reviews cover exactly the supplied report; there is no persistence,
  scheduling, or delivery.
- Deltas are raw differences; no seasonality, significance testing, or trend
  inference is attempted or implied.
- Review text never feeds back into signal decisions; decision modules cannot
  import this package.

No live trading, orders, execution, Telegram transport, optimization, or
self-modification is implemented.

**Stop after Phase 19. Phase 20 requires explicit approval.**
