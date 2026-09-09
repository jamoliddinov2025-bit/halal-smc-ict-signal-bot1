"""No-lookahead proofs: slice equality, prefix and alternate-future invariance."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from smcsignal.analysis.backtest import ReplayDataset
from smcsignal.analysis.backtest.calculation import replay_rows
from smcsignal.analysis.backtest.replay import replay_history
from smcsignal.analysis.robustness import (
    evaluate_dataset,
    run_robustness,
    split_rows,
)
from tests.robustness.helpers import (
    EIGHT,
    bar_at,
    bars,
    configuration,
    rich_dataset,
    robustness,
)


def test_window_replay_equals_a_direct_phase20_replay_of_the_same_slice() -> None:
    # A window's rows must be exactly what the unchanged Phase 20 engine
    # publishes over the same candle slice: no robustness-specific replay.
    dataset = rich_dataset()
    config = robustness()
    result = evaluate_dataset(dataset, configuration(), config)
    for window in result.windows:
        slice_dataset = ReplayDataset(
            symbol=dataset.symbol,
            timeframe=dataset.timeframe,
            candles=dataset.candles[window.window.window_start : window.window.window_end],
            higher_candles=dataset.higher_candles,
            venue=dataset.venue,
            provider=dataset.provider,
            dataset_id=dataset.dataset_id,
        )
        replay = replay_history(slice_dataset, configuration())
        development, validation = split_rows(replay_rows(replay), config.development_bars)
        assert development == window.development.rows
        assert validation == window.validation.rows
        assert window.replay_id == replay.replay_id


def test_window_replays_are_independent_of_each_other() -> None:
    # Evaluating a single window alone must reproduce its facts inside the
    # full multi-window run: windows never share state.
    dataset = rich_dataset()
    config = robustness()
    full = evaluate_dataset(dataset, configuration(), config)
    for position, window in enumerate(full.windows):
        alone_dataset = ReplayDataset(
            symbol=dataset.symbol,
            timeframe=dataset.timeframe,
            candles=dataset.candles[window.window.window_start : window.window.window_end],
            higher_candles=dataset.higher_candles,
            venue=dataset.venue,
            provider=dataset.provider,
            dataset_id=dataset.dataset_id,
        )
        windows = evaluate_dataset(alone_dataset, configuration(), config).windows
        assert len(windows) == 1
        assert windows[0].development == window.development
        assert windows[0].validation == window.validation
        assert windows[0].replay_id == window.replay_id
        assert position == window.window.window_index


def test_prefix_invariance_fitting_windows_are_unchanged() -> None:
    # Truncating the declared history cannot change any window that already
    # fit: no candle beyond a window's end influences its facts.
    dataset = rich_dataset()
    config = robustness()
    full = evaluate_dataset(dataset, configuration(), config)
    truncated = ReplayDataset(
        symbol=dataset.symbol,
        timeframe=dataset.timeframe,
        candles=dataset.candles[:40],
        higher_candles=dataset.higher_candles,
        venue=dataset.venue,
        provider=dataset.provider,
        dataset_id=dataset.dataset_id,
    )
    shorter = evaluate_dataset(truncated, configuration(), config)
    assert [w.window.window_end for w in shorter.windows] == [26, 40]
    assert shorter.windows[0] == full.windows[0]
    assert shorter.windows[1] == full.windows[1]


def test_alternate_future_invariance_of_primary_candles() -> None:
    # Replacing every candle after a window with wildly different values
    # cannot change that window: the future is never read.
    base = rich_dataset()
    config = robustness()
    reference = evaluate_dataset(base, configuration(), config)
    wild_tail = (
        *base.candles[:54],
        *(
            bar_at(EIGHT + timedelta(minutes=15 * (54 + i)), Decimal(500 + 25 * i))
            for i in range(6)
        ),
    )
    alternate = ReplayDataset(
        symbol=base.symbol,
        timeframe=base.timeframe,
        candles=wild_tail,
        higher_candles=base.higher_candles,
        venue=base.venue,
        provider=base.provider,
        dataset_id=base.dataset_id,
    )
    changed = evaluate_dataset(alternate, configuration(), config)
    assert changed.windows[0] == reference.windows[0]
    assert changed.windows[1] == reference.windows[1]
    assert changed.windows[2] == reference.windows[2]


def test_alternate_future_invariance_of_higher_timeframe_candles() -> None:
    # An HTF candle that closes after a window's end cannot influence it.
    base = rich_dataset()
    config = robustness(development_bars=8, validation_bars=8, step_bars=8)
    reference = evaluate_dataset(base, configuration(), config)
    extended_htf = dict(base.higher_candles)
    late_hour = bar_at(EIGHT + timedelta(hours=4), Decimal(40))  # closes 13:00
    extended_htf["1h"] = (*extended_htf["1h"], late_hour)
    alternate = ReplayDataset(
        symbol=base.symbol,
        timeframe=base.timeframe,
        candles=base.candles,
        higher_candles=extended_htf,
        venue=base.venue,
        provider=base.provider,
        dataset_id=base.dataset_id,
    )
    changed = evaluate_dataset(alternate, configuration(), config)
    assert changed.windows[0].development == reference.windows[0].development
    assert changed.windows[0].validation == reference.windows[0].validation
    assert changed.windows[0].development.rows == reference.windows[0].development.rows
    assert changed.windows[0].validation.rows == reference.windows[0].validation.rows
    # Phase 20 replay identities hash the full declared histories, so the
    # appended HTF candle changes the id while leaving every fact untouched.
    assert changed.windows[0].replay_id != reference.windows[0].replay_id
    # the appended candle is available inside later windows, which may differ
    assert len(changed.windows) == len(reference.windows)


def test_regime_annotations_of_a_window_are_prefix_stable() -> None:
    # Row regimes for early windows are unchanged when the tail changes.
    base = rich_dataset()
    config = robustness()
    reference = evaluate_dataset(base, configuration(), config)
    wild_tail = (
        *base.candles[:30],
        *(bar_at(EIGHT + timedelta(minutes=15 * (30 + i)), Decimal(900 - i)) for i in range(30)),
    )
    alternate = ReplayDataset(
        symbol=base.symbol,
        timeframe=base.timeframe,
        candles=wild_tail,
        higher_candles=base.higher_candles,
        venue=base.venue,
        provider=base.provider,
        dataset_id=base.dataset_id,
    )
    changed = evaluate_dataset(alternate, configuration(), config)
    for row in reference.windows[0].validation.rows + reference.windows[0].development.rows:
        assert changed.row_regimes[row.signal_id] == reference.row_regimes[row.signal_id]
    assert changed.regime_series[:30] == reference.regime_series[:30]


def test_growing_history_extends_the_plan_without_changing_early_windows() -> None:
    dataset = rich_dataset(prices=tuple(range(20, 80)))
    config = robustness()
    reference = evaluate_dataset(dataset, configuration(), config)
    grown = ReplayDataset(
        symbol=dataset.symbol,
        timeframe=dataset.timeframe,
        candles=(
            *dataset.candles,
            *bars("15m", tuple(range(80, 88)), start=EIGHT + timedelta(minutes=15 * 60)),
        ),
        higher_candles=dataset.higher_candles,
        venue=dataset.venue,
        provider=dataset.provider,
        dataset_id=dataset.dataset_id,
    )
    extended = evaluate_dataset(grown, configuration(), config)
    assert len(extended.windows) > len(reference.windows)
    for early, later in zip(reference.windows, extended.windows, strict=False):
        assert early == later


def test_window_datasets_carry_the_parent_identity_and_exact_slice() -> None:
    from smcsignal.analysis.robustness.analyzer import _window_dataset

    dataset = rich_dataset()
    window = ReplayDataset(
        symbol=dataset.symbol,
        timeframe=dataset.timeframe,
        candles=dataset.candles[14:40],
        higher_candles=dataset.higher_candles,
        venue=dataset.venue,
        provider=dataset.provider,
        dataset_id=dataset.dataset_id,
    )
    sliced = _window_dataset(dataset, 14, 40)
    assert sliced.candles == window.candles
    assert sliced.higher_candles == window.higher_candles
    assert (sliced.symbol, sliced.timeframe, sliced.venue) == (
        window.symbol,
        window.timeframe,
        window.venue,
    )
    assert sliced.dataset_id == window.dataset_id


def test_report_facts_and_identity_are_invariant_to_primary_changes_after_the_last_window() -> None:
    # Replacing every candle strictly after the last window's end (index 54)
    # cannot change any window fact, any window replay identity, any report
    # bucket, the degradation or stability classification, or the report id:
    # each window replay consumed only its own candle slice, the regime
    # observations at or beyond index 54 are never referenced by a window,
    # and the robustness identity covers the candle count, not the tail.
    base = rich_dataset()
    config = robustness()
    reference = run_robustness([base], configuration(), config)
    wild_tail = (
        *base.candles[:54],
        *(
            bar_at(EIGHT + timedelta(minutes=15 * (54 + i)), Decimal(500 + 25 * i))
            for i in range(6)
        ),
    )
    alternate = ReplayDataset(
        symbol=base.symbol,
        timeframe=base.timeframe,
        candles=wild_tail,
        higher_candles=base.higher_candles,
        venue=base.venue,
        provider=base.provider,
        dataset_id=base.dataset_id,
    )
    changed = run_robustness([alternate], configuration(), config)
    # window facts and replay identities: signals, outcomes, attribution
    # (combination keys), segment summaries, statuses, and replay ids
    assert changed.datasets[0].windows == reference.datasets[0].windows
    assert [w.replay_id for w in changed.datasets[0].windows] == [
        w.replay_id for w in reference.datasets[0].windows
    ]
    # report-level performance statistics
    assert changed.overall == reference.overall
    assert changed.by_symbol == reference.by_symbol
    assert changed.by_timeframe == reference.by_timeframe
    assert changed.by_combination == reference.by_combination
    assert changed.by_period == reference.by_period
    assert changed.by_regime == reference.by_regime
    # robustness classification
    assert changed.degradation == reference.degradation
    assert changed.stability == reference.stability
    # the whole report identity is unchanged
    assert changed.report_id == reference.report_id
    # regime observations after the boundary do change; none are referenced
    assert changed.datasets[0].regime_series[:54] == reference.datasets[0].regime_series[:54]
    assert changed.datasets[0].regime_series[54] != reference.datasets[0].regime_series[54]


def _window_facts(result) -> list:
    """Factual window content only: periods, segments, and degradation deltas.

    Replay ids are excluded on purpose: Phase 20 hashes the declared HTF
    history into every replay identity, so an appended-but-unavailable HTF
    candle changes identities while leaving all evaluated facts identical.
    """

    return [
        (
            window.window,
            window.development,
            window.validation,
            window.win_rate_degradation,
            window.average_final_return_degradation,
        )
        for window in result.windows
    ]


def test_htf_candle_after_every_window_changes_identity_not_facts() -> None:
    # Phase 20 replay identities hash the DECLARED higher-timeframe history,
    # so appending an HTF candle that closes after the last evaluated candle
    # (21:30) changes every replay id and the report id while leaving every
    # evaluated fact untouched. Identity and facts are asserted separately.
    base = rich_dataset()
    config = robustness()
    reference = run_robustness([base], configuration(), config)
    extended_htf = dict(base.higher_candles)
    late_hour = bar_at(EIGHT + timedelta(hours=14), Decimal(40))  # closes 23:00
    extended_htf["1h"] = (*extended_htf["1h"], late_hour)
    alternate = ReplayDataset(
        symbol=base.symbol,
        timeframe=base.timeframe,
        candles=base.candles,
        higher_candles=extended_htf,
        venue=base.venue,
        provider=base.provider,
        dataset_id=base.dataset_id,
    )
    changed = run_robustness([alternate], configuration(), config)
    # facts: every window and every report statistic is identical
    assert _window_facts(changed.datasets[0]) == _window_facts(reference.datasets[0])
    assert changed.overall == reference.overall
    assert changed.by_symbol == reference.by_symbol
    assert changed.by_timeframe == reference.by_timeframe
    assert changed.by_combination == reference.by_combination
    assert changed.by_period == reference.by_period
    assert changed.by_regime == reference.by_regime
    assert changed.degradation == reference.degradation
    assert changed.stability == reference.stability
    # identities: every replay id and the report id change, by design
    assert [w.replay_id for w in changed.datasets[0].windows] != [
        w.replay_id for w in reference.datasets[0].windows
    ]
    assert changed.report_id != reference.report_id


def test_open_outcomes_at_window_end_are_never_flushed() -> None:
    # Within a window of 26 candles and a 10-bar horizon, an outcome is
    # finalized exactly when at least 10 completed candles follow the signal
    # inside the window; otherwise it stays OPEN. The robustness layer never
    # flushes open outcomes at a window or dataset boundary.
    from smcsignal.analysis.outcome_tracking.models import OutcomeStatus

    horizon = configuration().outcome_tracking.horizon_bars
    window_length = robustness().development_bars + robustness().validation_bars
    result = evaluate_dataset(rich_dataset(), configuration(), robustness())
    open_seen = 0
    finalized_seen = 0
    for window in result.windows:
        for segment in (window.development, window.validation):
            for row in segment.rows:
                completed_after = window_length - row.candle_index - 1
                if row.outcome.status is OutcomeStatus.OPEN:
                    open_seen += 1
                    assert completed_after < horizon
                    assert row.final_return is None
                else:
                    finalized_seen += 1
                    assert completed_after >= horizon
                    assert row.final_return is not None
    assert open_seen > 0 and finalized_seen > 0  # the fixture exercises both
