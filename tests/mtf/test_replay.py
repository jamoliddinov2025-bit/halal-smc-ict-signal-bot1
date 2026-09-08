import pytest

from smcsignal.analysis import AnalysisInputError
from smcsignal.analysis.mtf import MTFConfig, analyze_mtf
from tests.analysis.helpers import bar
from tests.mtf.helpers import analyzer, higher, hour_frames, primary_15m, run


def test_batch_and_stream_reuse_exact_upstream_objects():
    raw = primary_15m()
    engine = analyzer()
    actual = tuple(engine.update(frame) for frame in raw)
    assert actual == analyze_mtf(raw, higher())
    assert all(frame.upstream is original for frame, original in zip(actual, raw, strict=True))
    assert engine.latest is actual[-1] and engine.processed_count == len(raw)
    assert engine.series == raw[0].provenance.series


@pytest.mark.parametrize("cut", range(18))
def test_every_chunk_boundary_is_identical(cut):
    frames = primary_15m()
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
            yield from primary_15m()

    assert analyze_mtf(Once(), higher()) == run()
    assert analyze_mtf((), higher()) == ()
    engine = analyzer()
    assert engine.series is None and engine.latest is None
    assert engine.configuration_artifact is None


def test_independent_interleaved_engines_do_not_share_state():
    first, second = analyzer(), analyzer()
    for frame in primary_15m():
        assert first.update(frame) == second.update(frame)


@pytest.mark.parametrize("value", [None, {}, "data", True, bar(0, 100)])
def test_invalid_raw_inputs_are_atomic(value):
    frames = primary_15m()
    engine = analyzer()
    before = tuple(engine.update(frame) for frame in frames[:5])
    state = (engine.latest, engine.processed_count, engine.configuration_artifact)
    with pytest.raises(AnalysisInputError):
        engine.update(value)
    assert (engine.latest, engine.processed_count, engine.configuration_artifact) == state
    assert (*before, engine.update(frames[5])) == analyze_mtf(frames[:6], higher())


def test_noniterable_primary_fails():
    with pytest.raises(AnalysisInputError):
        analyze_mtf(None, higher())


def test_noniterable_htf_fails():
    with pytest.raises(AnalysisInputError):
        analyzer(frames={"1h": None, "4h": ()})


def test_identical_replays_keep_the_same_direction_labels():
    assert [frame.direction for frame in run()] == [frame.direction for frame in run(primary_15m())]


def test_htf_available_at_must_not_rewind_inside_the_supplied_history():
    hourly = hour_frames()
    broken = (*hourly[:2], hourly[0])
    with pytest.raises(AnalysisInputError):
        analyzer(config=MTFConfig(higher_timeframes=("1h",)), frames={"1h": broken})
