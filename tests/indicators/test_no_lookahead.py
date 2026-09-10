from __future__ import annotations

from copy import deepcopy

import pytest

from smcsignal.analysis import IndicatorAnalyzer, IndicatorsConfig, analyze_indicators
from tests.indicators.helpers import SMALL, displacement_frames, run


@pytest.mark.parametrize("cut", range(18))
def test_every_prefix_equals_the_full_series_prefix(cut: int) -> None:
    frames = displacement_frames()
    assert analyze_indicators(frames[:cut], SMALL) == run()[:cut]


def test_future_price_shocks_leave_published_values_identical() -> None:
    from tests.indicators.helpers import candles_for

    short_frames = displacement_frames()
    long_frames = displacement_frames(candles_for((*tuple(range(20, 37)), 100, 90, 250)))
    assert short_frames == long_frames[:17]
    short = run(frames=short_frames)
    long = analyze_indicators(long_frames, SMALL)
    assert short == long[:17]
    assert [s.provenance.evidence_id for s in short] == [
        s.provenance.evidence_id for s in long[:17]
    ]


def test_published_snapshots_are_immutable_under_continuation() -> None:
    frames = displacement_frames()
    tracker = IndicatorAnalyzer(SMALL)
    retained = tuple(tracker.update(frame) for frame in frames[:12])
    saved = deepcopy(retained)
    for frame in frames[12:]:
        tracker.update(frame)
    assert retained == saved
    assert [snapshot.provenance.evidence_id for snapshot in retained] == [
        snapshot.provenance.evidence_id for snapshot in saved
    ]


def test_default_configuration_prefix_invariance() -> None:
    frames = displacement_frames()
    full = analyze_indicators(frames, IndicatorsConfig())
    for cut in (0, 1, 7, 14, 17):
        assert analyze_indicators(frames[:cut], IndicatorsConfig()) == full[:cut]
