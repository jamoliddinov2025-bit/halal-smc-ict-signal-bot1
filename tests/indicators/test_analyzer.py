from __future__ import annotations

from decimal import Decimal

import pytest

from smcsignal.analysis import (
    AnalysisConfigurationError,
    AnalysisInputError,
    IndicatorAnalyzer,
    IndicatorsConfig,
    analyze_indicators,
)
from tests.analysis.helpers import bar
from tests.indicators.helpers import (
    SMALL,
    analyzer,
    displacement_frames,
    ratio_like,
    run,
)


def test_default_configuration_warm_up_boundaries() -> None:
    frames = displacement_frames()
    snapshots = analyze_indicators(frames, IndicatorsConfig())
    assert len(snapshots) == 17
    for snapshot in snapshots:
        assert snapshot.ema_values == (None, None)  # 17 candles < EMA 20
        assert snapshot.volume_average is None and snapshot.volume_ratio is None
    rsi_values = [snapshot.rsi for snapshot in snapshots]
    assert rsi_values[:14] == [None] * 14
    assert rsi_values[14:] == [Decimal(100)] * 3  # strictly rising closes
    atr_values = [snapshot.atr for snapshot in snapshots]
    assert atr_values[:3] == [None] * 3
    assert atr_values[3:] == [Decimal(2)] * 14  # Phase 5 ATR, high-low = 2


def test_small_configuration_matches_hand_computed_values() -> None:
    snapshots = run()
    assert [str(value) for value in snapshots[0].ema_values[0:1]] == [str(None)]
    ema3 = [snapshot.ema_values[0] for snapshot in snapshots]
    assert ema3[:2] == [None, None]
    assert ema3[2:] == [Decimal(21 + index - 2) for index in range(2, 17)]
    ema5 = [snapshot.ema_values[1] for snapshot in snapshots]
    assert ema5[:4] == [None] * 4
    assert ema5[4] == Decimal(22)  # SMA(20..24)
    from tests.indicators.helpers import at50

    step = at50(lambda: Decimal(22) + (Decimal(25) - Decimal(22)) * (Decimal(2) / Decimal(6)))
    assert ema5[5] == step
    rsi = [snapshot.rsi for snapshot in snapshots]
    assert rsi[:3] == [None] * 3
    assert rsi[3:] == [Decimal(100)] * 14
    averages = [snapshot.volume_average for snapshot in snapshots]
    assert averages[:3] == [None] * 3
    assert averages[3:] == [Decimal(1)] * 14
    assert all(snapshot.volume_ratio == Decimal(1) for snapshot in snapshots[3:])


def test_volume_context_uses_real_volumes() -> None:
    from tests.indicators.helpers import candles_for

    candles = candles_for(tuple(range(20, 26)), volumes=(1, 2, 3, 4, 5, 6))
    snapshots = run(candles=candles)
    results = [(s.volume_average, s.volume_ratio) for s in snapshots]
    assert results == [
        (None, None),
        (None, None),
        (None, None),
        (Decimal("2.5"), Decimal("1.6")),
        (Decimal("3.5"), ratio_like(Decimal(5), Decimal("3.5"))),
        (Decimal("4.5"), ratio_like(Decimal(4), Decimal(3))),
    ]


def test_batch_and_stream_reuse_exact_upstream_objects() -> None:
    frames = displacement_frames()
    engine = analyzer()
    actual = tuple(engine.update(frame) for frame in frames)
    expected = run()
    assert actual == expected
    assert all(
        snapshot.upstream is original for snapshot, original in zip(actual, frames, strict=True)
    )
    assert engine.latest is actual[-1]
    assert engine.processed_count == len(frames)
    assert engine.series == frames[0].provenance.series
    assert engine.configuration_artifact is not None


@pytest.mark.parametrize("cut", range(18))
def test_every_chunk_boundary_is_identical(cut: int) -> None:
    frames = displacement_frames()
    engine = analyzer()
    outputs = []
    for chunk in (frames[:cut], (), frames[cut:]):
        outputs.extend(engine.update(frame) for frame in chunk)
    assert tuple(outputs) == run()


def test_empty_and_single_pass_inputs() -> None:
    class Once:
        used = False

        def __iter__(self):
            assert not self.used
            self.used = True
            yield from displacement_frames()

    assert analyze_indicators(Once(), SMALL) == run()
    assert analyze_indicators((), SMALL) == ()
    engine = analyzer()
    assert engine.series is None and engine.latest is None
    assert engine.configuration_artifact is None


def test_noniterable_primary_fails() -> None:
    with pytest.raises(AnalysisInputError):
        analyze_indicators(None, SMALL)


def test_constructor_rejects_foreign_configuration() -> None:
    with pytest.raises(AnalysisConfigurationError):
        IndicatorAnalyzer(config=object())
    with pytest.raises(AnalysisConfigurationError):
        analyze_indicators((), config=object())


@pytest.mark.parametrize("value", [None, {}, "data", True, bar(0, 100)])
def test_invalid_raw_inputs_are_atomic(value: object) -> None:
    frames = displacement_frames()
    engine = analyzer()
    before = tuple(engine.update(frame) for frame in frames[:5])
    state = (engine.latest, engine.processed_count, engine.configuration_artifact)
    with pytest.raises(AnalysisInputError):
        engine.update(value)
    assert (engine.latest, engine.processed_count, engine.configuration_artifact) == state
    assert (*before, engine.update(frames[5])) == run()[:6]


def test_shifted_or_duplicate_indices_are_rejected() -> None:
    frames = displacement_frames()
    engine = analyzer()
    with pytest.raises(AnalysisInputError):
        engine.update(frames[1])
    engine.update(frames[0])
    with pytest.raises(AnalysisInputError):
        engine.update(frames[0])
    assert engine.processed_count == 1


def test_interleaved_analyzers_do_not_share_state() -> None:
    frames = displacement_frames()
    first, second = analyzer(), analyzer()
    for frame in frames:
        assert first.update(frame) == second.update(frame)


def test_provenance_reuses_upstream_prefix_and_series() -> None:
    snapshots = run()
    for snapshot in snapshots:
        assert (
            snapshot.provenance.input_prefix_hash == snapshot.upstream.provenance.input_prefix_hash
        )
        assert snapshot.provenance.series == snapshot.upstream.provenance.series
        assert snapshot.provenance.producer == "indicator-frame"
        assert snapshot.provenance.evidence_id.startswith("indicator-frame:")
        assert (
            snapshot.candle_index
            == snapshot.upstream.liquidity.context.observation.reference.candle_index
        )
