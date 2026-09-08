from copy import deepcopy

import pytest

from smcsignal.analysis.setup_quality import SetupQualityConfig
from smcsignal.analysis.signal_engine import (
    SignalReason,
    SignalStatus,
    analyze_signal_engine,
    build_signal_snapshot,
)
from smcsignal.analysis.signal_engine.evidence import setup_identity
from tests.signal_eligibility.helpers import run as eligibility_run
from tests.signal_engine.helpers import analyzer, matching_config, run


def test_one_buy_signal_per_setup_identity():
    frames = run(sqs_config=SetupQualityConfig(10))
    buys = [frame for frame in frames if frame.status is SignalStatus.BUY_SIGNAL]
    assert buys
    assert [frame.setup_identity for frame in buys] == list(
        dict.fromkeys(frame.setup_identity for frame in buys)
    )
    assert all(frame.direction.value == "LONG" for frame in buys)


def test_exact_duplicate_setup_is_no_signal_with_reason():
    frames = run(sqs_config=SetupQualityConfig(10))
    first = frames[4]
    assert first.status is SignalStatus.BUY_SIGNAL
    later = [frame for frame in frames[5:] if frame.setup_identity == first.setup_identity]
    assert later
    for frame in later:
        assert frame.status is SignalStatus.NO_SIGNAL
        assert frame.candidate.reasons == (SignalReason.DUPLICATE_SETUP,)
        assert frame.setup_identity == first.setup_identity
        assert frame.signal_id != first.signal_id


def test_stateless_mapping_still_emits_buy_without_duplicate_tracking():
    upstream = eligibility_run(sqs_config=SetupQualityConfig(10))
    mapped = tuple(build_signal_snapshot(frame, matching_config(frame)) for frame in upstream)
    assert mapped[4].status is SignalStatus.BUY_SIGNAL
    assert mapped[5].status is SignalStatus.BUY_SIGNAL
    assert mapped[5].setup_identity == mapped[4].setup_identity
    streamed = analyze_signal_engine(upstream, matching_config(upstream[0]))
    assert streamed[4].status is SignalStatus.BUY_SIGNAL
    assert streamed[5].status is SignalStatus.NO_SIGNAL
    assert streamed[5].candidate.reasons == (SignalReason.DUPLICATE_SETUP,)


def test_repeated_processing_is_idempotent():
    left = run(sqs_config=SetupQualityConfig(10))
    right = run(sqs_config=SetupQualityConfig(10))
    assert left == right
    assert [frame.signal_id for frame in left] == [frame.signal_id for frame in right]
    assert [frame.setup_identity for frame in left] == [frame.setup_identity for frame in right]


def test_replay_does_not_create_additional_buy_signals():
    upstream = eligibility_run(sqs_config=SetupQualityConfig(10))
    config = matching_config(upstream[0])
    first = analyze_signal_engine(upstream, config)
    second = analyze_signal_engine(upstream, config)
    assert first == second
    assert [frame.status for frame in first] == [frame.status for frame in second]
    assert sum(frame.status is SignalStatus.BUY_SIGNAL for frame in first) == sum(
        frame.status is SignalStatus.BUY_SIGNAL for frame in second
    )


@pytest.mark.parametrize("cut", range(18))
def test_chunk_sizes_publish_the_same_signal_set(cut):
    upstream = eligibility_run(sqs_config=SetupQualityConfig(10))
    engine = analyzer(matching_config(upstream[0]))
    outputs = []
    for chunk in (upstream[:cut], (), upstream[cut:]):
        outputs.extend(engine.update(frame) for frame in chunk)
    expected = run(upstream)
    assert tuple(outputs) == expected
    assert [frame.signal_id for frame in outputs] == [frame.signal_id for frame in expected]
    assert [frame.status for frame in outputs] == [frame.status for frame in expected]


def test_different_setup_identities_remain_independent():
    frames = run(sqs_config=SetupQualityConfig(10))
    buys = [frame for frame in frames if frame.status is SignalStatus.BUY_SIGNAL]
    identities = [frame.setup_identity for frame in buys]
    assert len(set(identities)) >= 2
    assert len(identities) == len(set(identities))
    assert {frame.setup_identity for frame in frames[4:8]} != {frames[8].setup_identity}


def test_missing_optional_evidence_ids_are_omitted_not_invented():
    frames = eligibility_run()
    early = setup_identity(frames[0])
    assert early.startswith("spot-setup:")
    assert frames[0].provenance.evidence_id not in early
    assert frames[1].provenance.evidence_id not in early
    assert setup_identity(frames[0]) == setup_identity(frames[1])
    assert setup_identity(frames[0]) != setup_identity(frames[4])


def test_setup_identity_is_deterministic_and_ignores_future_candles():
    upstream = eligibility_run(sqs_config=SetupQualityConfig(10))
    engine = analyzer(matching_config(upstream[0]))
    retained = tuple(engine.update(frame) for frame in upstream[:8])
    saved = deepcopy(retained)
    for frame in upstream[8:]:
        engine.update(frame)
    assert retained == saved
    assert [frame.setup_identity for frame in retained] == [frame.setup_identity for frame in saved]
    assert [frame.signal_id for frame in retained] == [frame.signal_id for frame in saved]
    assert setup_identity(upstream[4]) == retained[4].setup_identity


def test_no_duplicate_buy_signal_across_the_stream():
    frames = run(sqs_config=SetupQualityConfig(10))
    buys = [frame.setup_identity for frame in frames if frame.status is SignalStatus.BUY_SIGNAL]
    duplicates = [
        frame for frame in frames if frame.candidate.reasons == (SignalReason.DUPLICATE_SETUP,)
    ]
    assert buys
    assert duplicates
    assert len(buys) == len(set(buys))
    assert all(frame.status is SignalStatus.NO_SIGNAL for frame in duplicates)
    assert {frame.setup_identity for frame in duplicates} <= set(buys)
