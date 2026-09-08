from smcsignal.analysis.mtf import MTFConfig, MTFDirection, MTFEvidenceKind, analyze_mtf
from tests.mtf.helpers import (
    EIGHT,
    bars,
    four_hour_frames,
    hour_frames,
    ote_frames,
    primary_15m,
    run,
)


def test_15m_context_uses_the_latest_completed_1h_snapshot_only():
    hourly = hour_frames()
    result = analyze_mtf(primary_15m(), {"1h": hourly}, MTFConfig(higher_timeframes=("1h",)))
    before = result[3]  # 08:45
    at_nine = result[4]  # 09:00
    assert before.relations[0].latest is hourly[7]
    assert at_nine.relations[0].latest is hourly[8]
    assert at_nine.relations[0].direction is MTFDirection.BULLISH


def test_1h_and_4h_are_independent_and_not_resampled_from_the_15m_series():
    hourly = hour_frames()
    four = four_hour_frames()
    result = analyze_mtf(primary_15m(), {"1h": hourly, "4h": four})
    nine = result[4]
    noon = result[16]
    assert nine.relations[0].latest is hourly[8]
    assert nine.relations[1].latest is None
    assert noon.relations[0].latest is hourly[11]
    assert noon.relations[1].latest is four[0]
    assert nine.upstream.provenance.series.timeframe == "15m"
    assert nine.relations[0].latest.provenance.series.timeframe == "1h"


def test_bullish_htf_with_missing_second_htf_is_not_collapsed_to_neutral():
    result = run()
    nine = result[4]
    assert nine.relations[0].direction is MTFDirection.BULLISH
    assert nine.relations[1].direction is MTFDirection.INSUFFICIENT_CONTEXT
    assert nine.direction is MTFDirection.BULLISH


def test_latest_htf_ote_and_pd_classifications_are_copied_not_recomputed():
    hourly = hour_frames()
    result = analyze_mtf(primary_15m(), {"1h": hourly}, MTFConfig(higher_timeframes=("1h",)))
    relation = result[4].relations[0]
    assert relation.ote_classification is hourly[8].classification
    assert relation.pd_classification is hourly[8].upstream.classification


def test_htf_structure_context_is_referenced_with_original_id():
    hourly = hour_frames()
    result = analyze_mtf(primary_15m(), {"1h": hourly}, MTFConfig(higher_timeframes=("1h",)))
    kinds = {item.kind: item.source.evidence_id for item in result[4].relations[0].evidence}
    context = hourly[8].upstream.upstream.upstream.upstream.liquidity.context
    assert kinds[MTFEvidenceKind.STRUCTURE_CONTEXT] == context.provenance.evidence_id
    assert kinds[MTFEvidenceKind.OTE_SNAPSHOT] == hourly[8].provenance.evidence_id


def test_empty_htf_history_is_insufficient_rather_than_an_error():
    result = analyze_mtf(primary_15m(), {"1h": (), "4h": ()})
    assert all(frame.direction is MTFDirection.INSUFFICIENT_CONTEXT for frame in result)
    assert all(relation.latest is None for frame in result for relation in frame.relations)


def test_later_htf_bars_do_not_rewrite_already_published_ltf_context():
    first = run()
    saved = first[4]
    later_primary = ote_frames(bars("15m", tuple(range(20, 40)), start=EIGHT), "15m")
    second = analyze_mtf(later_primary, {"1h": hour_frames(), "4h": four_hour_frames()})
    assert second[4].provenance.evidence_id == saved.provenance.evidence_id
    assert (
        second[4].relations[0].latest.provenance.evidence_id
        == saved.relations[0].latest.provenance.evidence_id
    )
