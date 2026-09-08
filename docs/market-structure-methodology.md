# Market structure methodology — Phase 3

SMC/ICT terminology is not universally standardized. This page defines the exact
project conventions for swings, BOS, and CHoCH; it does not claim a profitable
strategy or implement any signal, entry, exit, or execution rule.

## 1. Inputs and observation time

Input is a single chronological series of immutable `smcsignal.data.OHLCV`
records. Feed each **completed candle once**, with a strictly increasing UTC
opening timestamp. Analysis does not sort, deduplicate, resample, fetch data, or
accept an in-place revision of a previously processed candle.

Index `t` is a zero-based **observation index from the start of this replay**.
All timestamp fields identify candle OPEN times, matching the data layer. A result
for candle `t` is available only **after candle t closes**, not at its opening time.
The caller/data provider is responsible for supplying completed candles.

## 2. Confirmed swings

`fractal_length = L` is the **total odd window width**, from 3 through 1001.
Define `r = (L - 1) / 2`. The default `L=5` uses two candles on the left, the pivot,
and two candles on the right; its confirmation delay is two closed candles.

For a candidate pivot at `p`:

- **Swing high:** `high[p] > high[j]` for every other observed candle `j` in
  `[p-r, p+r]`.
- **Swing low:** `low[p] < low[j]` for every other observed candle `j` in
  `[p-r, p+r]`.
- Equality with **any** neighbor disqualifies that pivot kind. Plateaus are not
  resolved with arbitrary first/last tie-breaking.
- High and low conditions are independent. An outside candle can qualify as both;
  the deterministic publication order is high, then low. This does not infer an
  intrabar price path or label a sweep.

A swing is first published at **`t = p + r`**, when the complete right flank is
historical. `Swing` records `pivot_index`, `pivot_timestamp`, `price`, `kind`,
`confirmed_index`, and `confirmed_timestamp`. The pivot coordinates locate the
extremum, not when it became knowable. Never treat pivot time as confirmation time.

The first possible confirmation is index `L-1`; insufficient left/right flanks
produce nothing. Pending tail candidates are not emitted when a batch ends.
No padding, provisional swing output, or retrospective editing occurs.

Fractals count observed candles, not elapsed wall-clock intervals. Missing
intervals are not synthesized or diagnosed here; the data-quality assumptions
in the [market data methodology](market-data-methodology.md) still apply.

## 3. Active structure levels

Maintain the most recently confirmed high and low as two independent active
levels. A newly confirmed level replaces the older level of the same kind,
whether or not that older level has been broken. This is **latest-confirmed-swing
logic**, not protected-swing selection, liquidity grouping, or a hierarchy of
internal/external structures.

A level has only one crossing opportunity. Once crossed by closing prices it is
consumed, even if the trend is ranging and no event is classified. Remaining
beyond the level or later crossing it again cannot emit a duplicate event. A
newly confirmed Swing is a new level even if its price equals an older level.

## 4. Close-only crossing rule

At closed candle `t`, use **only active levels and the trend state published
through `t-1`**:

- Upward crossing of high `H`: `close[t-1] <= H.price < close[t]`.
- Downward crossing of low `L`: `close[t-1] >= L.price > close[t]`.

A wick crossing without a closing crossing is not a structure event. A close
exactly at a level is not a break, but can be the starting close of the next
crossing. Gap opens are allowed: classification waits for the completed candle's
close and does not invent an intrabar order of events.

Only the latest active level of each kind is checked; older retired levels do
not cause multiple simultaneous events. A single close-to-close movement cannot
cross both ways, so there is at most one classified event per candle.

## 5. BOS and CHoCH definitions

**BOS — Break of Structure:** a qualifying closing crossing **in the direction
of the previously established swing-derived trend**.

**CHoCH — Change of Character:** a qualifying closing crossing **against that
previously established trend**. It is an opposing-break warning, not an automatic
trend reversal or a trade signal.

| Trend through t-1 | Crossing | Event | Event direction |
| --- | --- | --- | --- |
| Bullish | Above the active high | BOS | Bullish |
| Bullish | Below the active low | CHoCH | Bearish |
| Bearish | Below the active low | BOS | Bearish |
| Bearish | Above the active high | CHoCH | Bullish |
| Ranging / insufficient evidence | Either | No classified event; level is consumed | None |

`StructureEvent` captures the event candle index/timestamp, event kind/direction,
the immutable broken `Swing`, previous/current closes, and `trend_before`.
The broken level must have been confirmed **strictly before** the event candle.
There is no bootstrap BOS label for the first break in a ranging/warm-up state.

## 6. Per-candle processing order

1. Validate input ordering/type before mutating stream state; advance the bounded
   fractal window and calculate confirmations available on this candle.
2. Check this candle's closing crossing against **prior** active levels and
   **prior** trend. Consume crossed levels and classify eligible events.
3. Publish this candle's newly confirmed swings and replace active levels.
4. Recompute trend using the latest two confirmed highs and lows available now.
5. Publish one frozen `AnalysisSnapshot`; never update older snapshots.

New confirmations are deliberately not activated before step 2. Otherwise a
higher high confirmed on this same candle could erase a valid crossing of the
previously active high. Likewise a newly established trend cannot retrospectively
classify a crossing that occurred while the previously published trend was ranging.

Trend is independent confirmed-swing evidence; CHoCH alone does not flip it.
See [trend methodology](trend-methodology.md) for the exact classification rule.

## 7. Hand-computed synthetic example

`config/analysis.example.toml` binds `tests/fixtures/market_structure.csv` with
`L=3` (`r=1`). There are fourteen synthetic candles, not Binance observations.
Their closes are `10, 15, 12, 18, 14, 16, 20, 17, 12, 16, 10, 14, 8, 18`;
each high is close+1 and each low is close-1.

| Event index | Prior trend | Event | Broken pivot | Level | Close |
| --- | --- | --- | --- | --- | --- |
| 6 | Bullish | Bullish BOS | High at 3, confirmed at 4 | 19 | 20 |
| 8 | Bullish | Bearish CHoCH | Low at 4, confirmed at 5 | 13 | 12 |
| 12 | Bearish | Bearish BOS | Low at 10, confirmed at 11 | 9 | 8 |
| 13 | Bearish | Bullish CHoCH | High at 11, confirmed at 12 | 15 | 18 |

Trend first becomes bullish at 5. It remains bullish at the CHoCH at 8, becomes
ranging at 9, and becomes bearish at 10 when the lower high is confirmed. The
crossing at 10 is **not** a BOS: the prior trend at 9 was ranging. These expected
indices, prices, directions, and confirmation coordinates are asserted in tests.

## 8. API and limits

`MarketStructureAnalyzer.update(candle)` returns one `AnalysisSnapshot` containing
this candle's `confirmed_swings` and `events` **deltas**, and a full `TrendState`
with the latest two highs/lows. `analyze(candles, config)` runs those same updates
and returns a tuple of per-candle snapshots. `detect_swings` returns confirmations
in publication order, not retroactively attached output at pivot time.

The stream holds a bounded L-candle window, two highs, two lows, active levels,
and the latest snapshot. The batch helper additionally stores O(number of candles)
outputs. There are no provisional updates, automatic resets, source polling,
multiple-series mixing, persistence, or rolling-window equivalence guarantees.

[No-look-ahead guarantees and tests](no-look-ahead.md) explain the fixed-prefix
contract and its limits. Phase 4 adds a separate [liquidity/sweep layer](liquidity-sweep-methodology.md)
without changing these structure definitions. Displacement, fair value gaps,
order blocks, premium/discount, signals, charts, Telegram, halal filtering, and
scoring remain unimplemented. Phase 5 requires explicit approval.
