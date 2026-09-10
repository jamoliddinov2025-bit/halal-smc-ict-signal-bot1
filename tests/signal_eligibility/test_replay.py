import pytest

from smcsignal.analysis import AnalysisInputError
from smcsignal.analysis.setup_quality import analyze_setup_quality
from smcsignal.analysis.signal_eligibility import (
    EligibilityStatus,
    MarketBias,
    analyze_signal_eligibility,
)
from tests.analysis.helpers import bar
from tests.halal_filter.helpers import mtf_for
from tests.halal_filter.helpers import run as halal_run
from tests.setup_quality.helpers import run as sqs_run
from tests.signal_eligibility.helpers import analyzer, run


def test_batch_and_stream_reuse_exact_upstream_objects():
    raw = sqs_run()
    engine = analyzer()
    actual = tuple(engine.update(frame) for frame in raw)
    assert actual == analyze_signal_eligibility(raw)
    assert all(frame.upstream is original for frame, original in zip(actual, raw, strict=True))
    assert engine.latest is actual[-1] and engine.processed_count == len(raw)
    assert engine.series == raw[0].provenance.series
    assert [frame.status for frame in actual] == [EligibilityStatus.NOT_ELIGIBLE] * len(actual)
    assert [frame.bias for frame in actual] == [MarketBias.NEUTRAL] * 4 + [
        MarketBias.LONG_BIAS
    ] * 13


@pytest.mark.parametrize("cut", range(18))
def test_every_chunk_boundary_is_identical(cut):
    frames = sqs_run()
    engine = analyzer()
    outputs = []
    for chunk in (frames[:cut], (), frames[cut:]):
        outputs.extend(engine.update(frame) for frame in chunk)
    assert tuple(outputs) == run()


def test_empty_and_single_pass_inputs():
    class Once:
        used = False

        def __iter__(self):
            assert not self.used
            self.used = True
            yield from sqs_run()

    assert analyze_signal_eligibility(Once()) == run()
    assert analyze_signal_eligibility(()) == ()
    engine = analyzer()
    assert engine.series is None and engine.latest is None
    assert engine.configuration_artifact is None


def test_independent_interleaved_engines_do_not_share_state():
    first, second = analyzer(), analyzer()
    for frame in sqs_run():
        assert first.update(frame) == second.update(frame)


@pytest.mark.parametrize("value", [None, {}, "data", True, bar(0, 100)])
def test_invalid_raw_inputs_are_atomic(value):
    frames = sqs_run()
    engine = analyzer()
    before = tuple(engine.update(frame) for frame in frames[:5])
    state = (engine.latest, engine.processed_count, engine.configuration_artifact)
    with pytest.raises(AnalysisInputError):
        engine.update(value)
    assert (engine.latest, engine.processed_count, engine.configuration_artifact) == state
    assert (*before, engine.update(frames[5])) == analyze_signal_eligibility(frames[:6])


def test_noniterable_primary_fails():
    with pytest.raises(AnalysisInputError):
        analyze_signal_eligibility(None)


def test_shifted_or_duplicate_indices_are_rejected():
    engine = analyzer()
    source = sqs_run()
    with pytest.raises(AnalysisInputError):
        engine.update(source[1])
    engine.update(source[0])
    with pytest.raises(AnalysisInputError):
        engine.update(source[0])


def test_series_cannot_change_mid_stream():
    engine = analyzer()
    engine.update(analyze_setup_quality(halal_run(mtf_for("BTCUSDT")))[0])
    with pytest.raises(AnalysisInputError):
        engine.update(analyze_setup_quality(halal_run(mtf_for("ETHUSDT")))[1])


def test_identical_replays_keep_the_same_status_and_bias():
    assert [(frame.status, frame.bias) for frame in run()] == [
        (frame.status, frame.bias) for frame in run(sqs_run())
    ]
