# No-look-ahead guarantees — Phase 3

## Contract

At observation `t`, only completed canonical OHLCV candles `0..t` may influence
outputs. All runs compared below start from the **same initial history and
configuration**, with identical, valid, completed prefix candles.

For `analyze`, there is one immutable snapshot per processed candle:

```python
full = analyze(candles, config)
for n in range(len(candles) + 1):
    assert analyze(candles[:n], config) == full[:n]
```

Replacing or appending any valid future candles at indices `>= n` must not change
`full[:n]`. This includes all historical trend directions/evidence, structure
events/levels, and swing-confirmation metadata—not just the latest direction.

## Why a symmetric fractal is still causal

For `fractal_length = 2r+1`, a pivot at `p` needs right-side candles up to `p+r`.
It is **not published at p**. The detector emits it only while processing the
closed candle `t=p+r`, using the completed trailing window `[t-2r, t]`.

Therefore a Swing carries two distinct coordinates:

- `pivot_index` / `pivot_timestamp`: where the extremum occurred.
- `confirmed_index` / `confirmed_timestamp`: the confirming candle's index and
  opening-time identifier; the Swing is available only after that candle closes.

A future candle may confirm or disqualify a previously *pending* pivot. It cannot
change an already published result. No provisional pivot, centered retrospective
label at `p`, end-of-series flush, or synthetic right-side padding is emitted.

For the flat `detect_swings` API, compare prefixes by **confirmation**, not pivot:

```python
full_swings = detect_swings(candles, config)
assert detect_swings(candles[:n], config) == tuple(s for s in full_swings if s.confirmed_index < n)
```

Filtering by `pivot_index < n` would wrongly import confirmations from the future.

## State-transition argument

Let `S[t-1]` be the analyzer's private state after candle `t-1`. Processing `t`
reads only `S[t-1]` and `candle[t]`:

1. Validate chronological order/type and advance the bounded trailing window.
2. Compute candidate confirmations using only that window, ending at `t`.
3. Check close-to-close crossings using the prior close, active levels, and
   trend through `t-1`. No level confirmed on `t` is used in a break on `t`.
4. Activate the newly confirmed swings; retain only the latest two of each kind.
5. Derive the new trend and publish the immutable output for `t`.

No step receives future data, a final-series statistic, or the final series
length. Identical initial state and prefix candles therefore give identical state
and output at every prefix step, by induction. The batch helper is literally the
same stream-update loop, not a separate vectorized retrospective algorithm.

Invalid duplicate/out-of-order/noncanonical stream updates are rejected before
state mutation. They cannot advance the index or edit the last snapshot. Swings,
trend states, events, and snapshot collections are frozen, so keeping earlier
returned objects does not retain a mutable view into the engine's queues.

## Tests that exercise the guarantee

`tests/analysis/test_no_lookahead.py` includes:

- Every prefix of seeded, varied OHLCV series, for total window lengths 3, 5, 7,
  and 9, compared against corresponding full-series snapshots.
- Replacement of *all* future prices at every cutoff with alternating extreme
  valid prices; previously available results must remain identical.
- Appended future candles, including checks on the original unconfirmed tail.
- Flat swing-prefix comparisons using confirmation indices.
- A pivot that future candles can either confirm or invalidate, without changing
  the shared historical prefix.
- Retained snapshots compared to independent prefix runs and deep copies after
  the stream processes future data.
- An independent closed-prefix extrema oracle and confirmation-delay metadata.
- A hand-computed sequence with actual bullish/bearish BOS and CHoCH events, so
  passing the causal tests is not merely an artifact of always returning no events.

`test_replay.py` also compares individual updates, arbitrary chunk boundaries,
single-pass iterables, independent analyzer instances, CSV replay, and mocked
Binance normalization. `test_bos.py` checks that same-candle new confirmations or
trend labels do not retroactively change event classification.

## What this guarantee does not mean

- Candle opening timestamps are identifiers, **not** claims that a close-based
  result was knowable at the candle open. Feed completed candles only.
- It does not make unclosed live rows or manually falsified confirmation metadata
  safe. CSV closure/provenance remain caller assumptions; Binance's data-layer
  closed-candle cutoff relies on an appropriately synchronized host clock.
- Changing earlier candles, configuration, or the starting history changes the
  problem. Vendor revisions, dropped rows, gaps, or latest-N window shifts can
  legitimately change a fresh replay. This is not a promise of equal outputs
  across unequal input histories or persisted incremental state.
- Upstream normalization may reject malformed files before any replay; the
  prefix guarantee compares successful analysis of valid canonical sequences.
- No wall-clock scheduling, intrabar execution ordering, same-close fills,
  backtest profitability, trading recommendations, or asset eligibility follows
  from these guarantees.

All tests are offline. Phase 3 stops at confirmed swings, historical trend, BOS,
and CHoCH. It does not implement any of the excluded Phase 4 features.
