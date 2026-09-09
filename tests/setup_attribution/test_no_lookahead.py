from __future__ import annotations

from copy import deepcopy

from smcsignal.analysis.setup_attribution import (
    SetupAttributionAnalyzer,
    analyze_setup_attribution,
    labels_for,
)
from tests.setup_attribution.helpers import (
    SWEEP_ROWS,
    impulse_candles,
    row_candles,
    run,
    signal_frames,
    sweep_candles,
)


def attributions_by_index(snapshots):
    return {
        snapshot.attribution.reference.candle_index: snapshot.attribution
        for snapshot in snapshots
        if snapshot.attribution is not None
    }


def test_prefix_replays_produce_identical_attributions() -> None:
    full = analyze_setup_attribution(signal_frames(sweep_candles()))
    for cut in (14, 16, 18):
        prefix = analyze_setup_attribution(signal_frames(sweep_candles()[:cut]))
        for index, profile in attributions_by_index(prefix).items():
            assert profile == attributions_by_index(full)[index]


def test_future_candles_never_change_past_attributions() -> None:
    head = SWEEP_ROWS[:14]
    rising = SWEEP_ROWS[14:]
    falling = (
        (32, 33, 30, 31, 6),
        (31, 32, 29, 30, 6),
        (30, 31, 28, 29, 6),
        (29, 30, 27, 28, 6),
    )
    base = attributions_by_index(
        analyze_setup_attribution(signal_frames(row_candles(head + rising)))
    )
    other = attributions_by_index(
        analyze_setup_attribution(signal_frames(row_candles(head + falling)))
    )
    assert set(base) >= {4, 8, 12, 13}
    for index in (4, 8, 12, 13):
        assert base[index] == other[index]


def test_labels_are_a_pure_function_of_the_frame() -> None:
    frames = signal_frames(sweep_candles())
    for frame in frames:
        assert labels_for(frame) == labels_for(deepcopy(frame))


def test_attribution_reads_no_outcome_records() -> None:
    from pathlib import Path

    package = Path(__file__).resolve().parents[2] / "src" / "smcsignal" / "analysis"
    for module in (package / "setup_attribution").glob("*.py"):
        text = module.read_text(encoding="utf-8")
        assert "outcome_tracking" not in text, f"{module.name} must not read outcomes"
        assert "OutcomeSnapshot" not in text, f"{module.name} must not read outcomes"
        assert "final_return" not in text, f"{module.name} must not read outcomes"


def test_streaming_prefix_equals_batch_prefix() -> None:
    frames = signal_frames(sweep_candles())
    batch = analyze_setup_attribution(frames)
    engine = SetupAttributionAnalyzer()
    streamed = tuple(engine.update(frame) for frame in frames[:15])
    assert streamed == batch[:15]
    assert all(
        profile.attribution.attribution_id.startswith("setup-attribution:")
        for profile in streamed
        if profile.attribution is not None
    )


def test_attribution_never_extends_past_the_signal_cutoff() -> None:
    snapshots = run()
    for snapshot in snapshots:
        observation = snapshot.upstream.provenance
        assert snapshot.provenance.available_at == observation.available_at
        if snapshot.attribution is not None:
            profile = snapshot.attribution
            assert profile.published_at == snapshot.provenance.available_at
            assert profile.reference.candle_index == (
                snapshot.upstream.candidate.signal.candle.candle_index
            )
            for dependency in profile.provenance.dependencies:
                assert dependency.available_at <= profile.published_at


def test_labels_never_predict_outcomes() -> None:
    """The same past candles with different futures keep identical labels."""

    frames_a = signal_frames(impulse_candles())
    candles_b = impulse_candles()[:-2] + row_candles(
        ((13, 14, 11, 12, 4), (12, 13, 10, 11, 4)), start_index=13
    )
    frames_b = signal_frames(candles_b)
    for frame_a, frame_b in zip(frames_a[:-2], frames_b[:-2], strict=True):
        assert labels_for(frame_a) == labels_for(frame_b)
