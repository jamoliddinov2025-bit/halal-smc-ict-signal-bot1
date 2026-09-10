import pytest

from smcsignal.analysis import AnalysisInputError
from smcsignal.analysis.halal_filter import AssetClassification, analyze_halal
from tests.analysis.helpers import bar
from tests.halal_filter.helpers import analyzer, mtf_for, run


def test_batch_and_stream_reuse_exact_upstream_objects():
    raw = mtf_for()
    engine = analyzer()
    actual = tuple(engine.update(frame) for frame in raw)
    assert actual == analyze_halal(raw)
    assert all(frame.upstream is original for frame, original in zip(actual, raw, strict=True))
    assert engine.latest is actual[-1] and engine.processed_count == len(raw)
    assert engine.series == raw[0].provenance.series
    assert all(frame.classification is AssetClassification.HALAL for frame in actual)
    assert all(frame.eligible is True for frame in actual)


@pytest.mark.parametrize("cut", range(18))
def test_every_chunk_boundary_is_identical(cut):
    frames = mtf_for()
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
            yield from mtf_for()

    assert analyze_halal(Once()) == run()
    assert analyze_halal(()) == ()
    engine = analyzer()
    assert engine.series is None and engine.latest is None
    assert engine.configuration_artifact is None


def test_independent_interleaved_engines_do_not_share_state():
    first, second = analyzer(), analyzer()
    for frame in mtf_for():
        assert first.update(frame) == second.update(frame)


@pytest.mark.parametrize("value", [None, {}, "data", True, bar(0, 100)])
def test_invalid_raw_inputs_are_atomic(value):
    frames = mtf_for()
    engine = analyzer()
    before = tuple(engine.update(frame) for frame in frames[:5])
    state = (engine.latest, engine.processed_count, engine.configuration_artifact)
    with pytest.raises(AnalysisInputError):
        engine.update(value)
    assert (engine.latest, engine.processed_count, engine.configuration_artifact) == state
    assert (*before, engine.update(frames[5])) == analyze_halal(frames[:6])


def test_noniterable_primary_fails():
    with pytest.raises(AnalysisInputError):
        analyze_halal(None)


def test_shifted_or_duplicate_indices_are_rejected():
    engine = analyzer()
    source = mtf_for()
    with pytest.raises(AnalysisInputError):
        engine.update(source[1])
    engine.update(source[0])
    with pytest.raises(AnalysisInputError):
        engine.update(source[0])


def test_series_cannot_change_mid_stream():
    engine = analyzer()
    engine.update(mtf_for("BTCUSDT")[0])
    with pytest.raises(AnalysisInputError):
        engine.update(mtf_for("ETHUSDT")[1])


def test_identical_replays_keep_the_same_classifications():
    assert [frame.classification for frame in run()] == [
        frame.classification for frame in run(mtf_for())
    ]
