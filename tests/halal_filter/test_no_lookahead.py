from copy import deepcopy

import pytest

from smcsignal.analysis.halal_filter import AssetClassification, analyze_halal
from smcsignal.analysis.mtf import analyze_mtf
from tests.halal_filter.helpers import analyzer, mtf_for, run
from tests.mtf.helpers import EIGHT, bars, four_hour_frames, hour_frames, ote_frames


@pytest.mark.parametrize("cut", range(18))
def test_every_prefix_equals_corresponding_full_output_including_ids(cut):
    frames = mtf_for()
    full = analyze_halal(frames)
    prefix = analyze_halal(frames[:cut])
    assert prefix == full[:cut]
    assert [frame.provenance.evidence_id for frame in prefix] == [
        frame.provenance.evidence_id for frame in full[:cut]
    ]
    assert [frame.decision.provenance.evidence_id for frame in prefix] == [
        frame.decision.provenance.evidence_id for frame in full[:cut]
    ]


def test_unknown_asset_prefixes_remain_ineligible():
    frames = mtf_for("ADAUSDT")
    full = analyze_halal(frames)
    for cut in (0, 1, 8, len(frames)):
        prefix = analyze_halal(frames[:cut])
        assert prefix == full[:cut]
        assert all(frame.classification is AssetClassification.UNKNOWN for frame in prefix)
        assert all(frame.eligible is False for frame in prefix)


def test_future_mtf_frames_cannot_reclassify_historical_registry_decisions():
    original = mtf_for()
    full = analyze_halal(original)
    mixed_ote = ote_frames(
        (
            *[frame.upstream.upstream.observation.candle for frame in original[:8]],
            *bars("15m", tuple(range(40, 49)), start=EIGHT.replace(hour=10)),
        ),
        "15m",
    )
    changed = analyze_halal(analyze_mtf(mixed_ote, {"1h": hour_frames(), "4h": four_hour_frames()}))
    assert [frame.provenance.evidence_id for frame in changed[:8]] == [
        frame.provenance.evidence_id for frame in full[:8]
    ]
    assert changed[0].decision.provenance.evidence_id == full[0].decision.provenance.evidence_id
    assert all(frame.classification is AssetClassification.HALAL for frame in changed[:8])


def test_retained_snapshots_never_mutate_after_future_updates():
    engine = analyzer()
    raw = mtf_for()
    retained = tuple(engine.update(frame) for frame in raw[:10])
    saved = deepcopy(retained)
    for frame in raw[10:]:
        engine.update(frame)
    assert retained == saved == run()[:10]


def test_prefix_hash_is_the_consumed_mtf_prefix_not_a_future_file_hash():
    frames = run()
    raw = mtf_for()
    assert frames[0].provenance.input_prefix_hash == raw[0].provenance.input_prefix_hash
    assert frames[-1].provenance.input_prefix_hash == raw[-1].provenance.input_prefix_hash
    assert frames[0].provenance.input_prefix_hash != frames[-1].provenance.input_prefix_hash
