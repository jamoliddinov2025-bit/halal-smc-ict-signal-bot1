from copy import deepcopy

import pytest

from smcsignal.analysis.halal_filter import analyze_halal
from smcsignal.analysis.mtf import analyze_mtf
from smcsignal.analysis.setup_quality import analyze_setup_quality
from smcsignal.analysis.signal_eligibility import (
    EligibilityStatus,
    MarketBias,
    analyze_signal_eligibility,
)
from tests.halal_filter.helpers import mtf_for
from tests.halal_filter.helpers import run as halal_run
from tests.mtf.helpers import EIGHT, bars, four_hour_frames, hour_frames, ote_frames
from tests.setup_quality.helpers import run as sqs_run
from tests.signal_eligibility.helpers import analyzer, run


@pytest.mark.parametrize("cut", range(18))
def test_every_prefix_equals_corresponding_full_output_including_ids(cut):
    frames = sqs_run()
    full = analyze_signal_eligibility(frames)
    prefix = analyze_signal_eligibility(frames[:cut])
    assert prefix == full[:cut]
    assert [frame.provenance.evidence_id for frame in prefix] == [
        frame.provenance.evidence_id for frame in full[:cut]
    ]
    assert [frame.decision.provenance.evidence_id for frame in prefix] == [
        frame.decision.provenance.evidence_id for frame in full[:cut]
    ]
    assert [(frame.status, frame.bias) for frame in prefix] == [
        (frame.status, frame.bias) for frame in full[:cut]
    ]


def test_unknown_asset_prefixes_remain_ineligible():
    frames = analyze_setup_quality(halal_run(mtf_for("ADAUSDT")))
    full = analyze_signal_eligibility(frames)
    for cut in (0, 1, 8, len(frames)):
        prefix = analyze_signal_eligibility(frames[:cut])
        assert prefix == full[:cut]
        assert all(frame.status is EligibilityStatus.NOT_ELIGIBLE for frame in prefix)
        assert all(frame.bias is MarketBias.NEUTRAL for frame in prefix)


def test_future_mtf_frames_cannot_rewrite_historical_eligibility():
    original = mtf_for()
    full = analyze_signal_eligibility(analyze_setup_quality(analyze_halal(original)))
    mixed_ote = ote_frames(
        (
            *[frame.upstream.upstream.observation.candle for frame in original[:8]],
            *bars("15m", tuple(range(40, 49)), start=EIGHT.replace(hour=10)),
        ),
        "15m",
    )
    changed = analyze_signal_eligibility(
        analyze_setup_quality(
            analyze_halal(analyze_mtf(mixed_ote, {"1h": hour_frames(), "4h": four_hour_frames()}))
        )
    )
    assert [frame.provenance.evidence_id for frame in changed[:8]] == [
        frame.provenance.evidence_id for frame in full[:8]
    ]
    assert [frame.decision.provenance.evidence_id for frame in changed[:8]] == [
        frame.decision.provenance.evidence_id for frame in full[:8]
    ]
    assert [(frame.status, frame.bias) for frame in changed[:8]] == [
        (frame.status, frame.bias) for frame in full[:8]
    ]


def test_retained_snapshots_never_mutate_after_future_updates():
    engine = analyzer()
    raw = sqs_run()
    retained = tuple(engine.update(frame) for frame in raw[:10])
    saved = deepcopy(retained)
    for frame in raw[10:]:
        engine.update(frame)
    assert retained == saved == run()[:10]


def test_prefix_hash_is_the_consumed_score_prefix_not_a_future_file_hash():
    frames = run()
    raw = sqs_run()
    assert frames[0].provenance.input_prefix_hash == raw[0].provenance.input_prefix_hash
    assert frames[-1].provenance.input_prefix_hash == raw[-1].provenance.input_prefix_hash
    assert frames[0].provenance.input_prefix_hash != frames[-1].provenance.input_prefix_hash
