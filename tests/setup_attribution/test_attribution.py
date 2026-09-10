from __future__ import annotations

import pytest

from smcsignal.analysis import (
    AnalysisInputError,
    HalalFilterConfig,
    SeriesProvenance,
    analyze_halal,
)
from smcsignal.analysis.setup_attribution import (
    SetupAttributionAnalyzer,
    SetupAttributionConfig,
    SetupLabel,
    analyze_setup_attribution,
    build_attribution,
    labels_for,
    methodology_version,
)
from smcsignal.analysis.setup_attribution.config import METHODOLOGY_VERSION
from smcsignal.analysis.setup_attribution.evidence import configuration_artifact
from smcsignal.analysis.setup_quality import SetupQualityConfig, analyze_setup_quality
from smcsignal.analysis.signal_eligibility import (
    SignalEligibilityConfig,
    analyze_signal_eligibility,
)
from smcsignal.analysis.signal_engine import SignalStatus, analyze_signal_engine
from tests.mtf.helpers import EIGHT, bars, ote_frames
from tests.mtf.helpers import run as mtf_run
from tests.setup_attribution.helpers import (
    impulse_candles,
    mirrored_ob_frames,
    mixed_frames,
    ote_candles,
    run,
    signal_frames,
    sweep_candles,
)
from tests.signal_engine.helpers import matching_config


def buys(snapshots):
    return [snapshot.attribution for snapshot in snapshots if snapshot.attribution is not None]


def other_series_frames():
    """The same replay facts under a different dataset identity."""

    series = SeriesProvenance(
        "BTCUSDT", "15m", "synthetic_spot", "csv", "phase19-fixture:alt-series:v1"
    )
    primary = ote_frames(bars("15m", tuple(range(20, 37)), start=EIGHT), "15m", series=series)
    halal = analyze_halal(mtf_run(primary=primary), HalalFilterConfig())
    scored = analyze_setup_quality(halal, SetupQualityConfig(10))
    eligible = analyze_signal_eligibility(scored, SignalEligibilityConfig())
    return analyze_signal_engine(eligible, matching_config(eligible[0]))


def test_default_stream_attributes_each_buy_once() -> None:
    snapshots = run()
    assert len(snapshots) == 17
    profiles = buys(snapshots)
    assert len(profiles) == 4
    for profile, index in zip(profiles, (4, 8, 12, 16), strict=True):
        assert profile.signal_id  # copied verbatim from the engine
        assert profile.labels == (SetupLabel.MTF_BULLISH,)
        assert profile.combination_key == "mtf_bullish"
        assert profile.score_total == 25 == profile.component_scores.total
        assert profile.symbol == "BTCUSDT" and profile.timeframe == "15m"
        assert profile.reference.candle_index == index
        assert profile.published_at == profile.provenance.available_at
        assert profile.attribution_id.startswith("setup-attribution:")
    assert len({profile.attribution_id for profile in profiles}) == 4


def test_profiles_copy_the_engine_context_verbatim() -> None:
    frames = signal_frames()
    snapshots = analyze_setup_attribution(frames)
    for frame, snapshot in zip(frames, snapshots, strict=True):
        if snapshot.attribution is None:
            assert frame.status is not SignalStatus.BUY_SIGNAL
            continue
        signal = frame.candidate.signal
        profile = snapshot.attribution
        assert profile.signal_id == frame.signal_id
        assert profile.setup_identity == frame.setup_identity
        assert profile.reference == signal.candle
        assert profile.published_at == signal.published_at
        assert profile.score_total == signal.score_total
        assert profile.component_scores is frame.upstream.upstream.score.breakdown


def test_sweep_fixture_labels_displacement_and_sweep_association() -> None:
    snapshots = run(signal_frames(sweep_candles()))
    by_index = {
        snapshot.attribution.reference.candle_index: snapshot.attribution
        for snapshot in snapshots
        if snapshot.attribution is not None
    }
    assert by_index[13].labels == (
        SetupLabel.BULLISH_DISPLACEMENT,
        SetupLabel.SWEEP_ASSOCIATED,
        SetupLabel.MTF_BULLISH,
    )
    assert by_index[13].combination_key == "bullish_displacement+sweep_associated+mtf_bullish"
    assert by_index[14].labels == (SetupLabel.FVG_BULLISH, SetupLabel.MTF_BULLISH)


def test_impulse_fixture_labels_order_blocks_and_premium_discount() -> None:
    snapshots = run(signal_frames(impulse_candles()))
    by_labels = {
        snapshot.attribution.reference.candle_index: snapshot.attribution.labels
        for snapshot in snapshots
        if snapshot.attribution is not None
    }
    assert SetupLabel.ORDER_BLOCK_BULLISH in by_labels[6]
    assert SetupLabel.BULLISH_DISPLACEMENT in by_labels[6]
    assert SetupLabel.FVG_BULLISH in by_labels[7]
    assert SetupLabel.PD_DISCOUNT in by_labels[4]
    assert SetupLabel.PD_EQUILIBRIUM in by_labels[5]
    assert SetupLabel.PD_PREMIUM in by_labels[8]


def test_ote_fixture_labels_inside_ote_discount_and_bearish_fvg() -> None:
    snapshots = run(signal_frames(ote_candles()))
    by_index = {
        snapshot.attribution.reference.candle_index: snapshot.attribution
        for snapshot in snapshots
        if snapshot.attribution is not None
    }
    assert by_index[21].labels == (
        SetupLabel.FVG_BEARISH,
        SetupLabel.INSIDE_OTE,
        SetupLabel.PD_DISCOUNT,
        SetupLabel.MTF_BULLISH,
    )
    assert by_index[21].combination_key == "fvg_bearish+inside_ote+pd_discount+mtf_bullish"


def test_bearish_labels_never_become_buy_profiles() -> None:
    frames = mirrored_ob_frames()
    snapshots = run(frames)
    assert not buys(snapshots)
    assert SetupLabel.ORDER_BLOCK_BEARISH in labels_for(frames[6])


def test_mixed_higher_timeframes_label_without_changing_decisions() -> None:
    frames = mixed_frames()
    snapshots = run(frames)
    assert not buys(snapshots)  # mixed confluence is not a BUY fact
    assert labels_for(frames[12]) == (SetupLabel.MTF_MIXED,)
    for snapshot in snapshots[12:]:
        assert snapshot.attribution is None


def test_every_taxonomy_label_is_observed_across_fixtures() -> None:
    seen: set[SetupLabel] = set()
    for frames in (
        signal_frames(),
        signal_frames(sweep_candles()),
        signal_frames(impulse_candles()),
        signal_frames(ote_candles()),
        mirrored_ob_frames(),
        mixed_frames(),
    ):
        for frame in frames:
            seen.update(labels_for(frame))
    assert seen == set(SetupLabel)


def test_labels_for_projects_nested_facts_only() -> None:
    frames = signal_frames()
    assert labels_for(frames[0]) == ()  # nothing has happened yet
    assert labels_for(frames[4]) == (SetupLabel.MTF_BULLISH,)
    with pytest.raises(AnalysisInputError):
        labels_for(frames[0].upstream)  # type: ignore[arg-type]


def test_build_attribution_requires_a_buy_frame() -> None:
    frames = signal_frames()
    with pytest.raises(AnalysisInputError):
        build_attribution(frames[0], SetupAttributionConfig(), "0" * 64)


def test_streaming_matches_the_batch_replay_exactly() -> None:
    frames = signal_frames(sweep_candles())
    batch = analyze_setup_attribution(frames)
    streaming_analyzer = SetupAttributionAnalyzer()
    streaming = tuple(streaming_analyzer.update(frame) for frame in frames)
    assert batch == streaming
    engine = SetupAttributionAnalyzer()
    first = tuple(engine.update(frame) for frame in frames[:10])
    second = tuple(engine.update(frame) for frame in frames[10:])
    assert first + second == batch


def test_analyzer_rejects_non_frame_and_non_iterable_inputs() -> None:
    with pytest.raises(AnalysisInputError):
        analyze_setup_attribution(None)  # type: ignore[arg-type]
    with pytest.raises(AnalysisInputError):
        analyze_setup_attribution([signal_frames()[0].upstream])  # type: ignore[list-item]


def test_analyzer_consumes_frames_from_index_zero_consecutively() -> None:
    frames = signal_frames()
    engine = SetupAttributionAnalyzer()
    with pytest.raises(AnalysisInputError):
        engine.update(frames[4])
    engine.update(frames[0])
    with pytest.raises(AnalysisInputError):
        engine.update(frames[0])  # the same candle cannot replay


def test_analyzer_attributes_each_buy_signal_at_most_once() -> None:
    frames = signal_frames()
    engine = SetupAttributionAnalyzer()
    for frame in frames[:4]:
        engine.update(frame)
    engine.update(frames[4])  # BUY
    assert engine.attributed_signal_ids == (frames[4].signal_id,)
    with pytest.raises(AnalysisInputError):
        engine.update(frames[4])  # duplicate signal id


def test_analyzer_requires_one_series_during_a_replay() -> None:
    frames = signal_frames()
    other = other_series_frames()
    assert other[0].provenance.series != frames[0].provenance.series
    engine = SetupAttributionAnalyzer()
    engine.update(frames[0])
    with pytest.raises(AnalysisInputError):
        engine.update(other[0])


def test_analyzer_state_and_configuration_artifact() -> None:
    frames = signal_frames()
    engine = SetupAttributionAnalyzer()
    assert engine.latest is None and engine.processed_count == 0
    assert engine.series is None and engine.configuration_artifact is None
    for frame in frames:
        engine.update(frame)
    assert engine.processed_count == len(frames)
    assert engine.latest is not None
    assert engine.series is frames[0].provenance.series
    artifact = engine.configuration_artifact
    assert artifact is not None and artifact == configuration_artifact(engine.config)
    assert engine.attributed_signal_ids == tuple(
        frame.signal_id for frame in frames if frame.status is SignalStatus.BUY_SIGNAL
    )


def test_methodology_version_is_frozen() -> None:
    assert methodology_version() == METHODOLOGY_VERSION == "setup-attribution-v1"
    config = SetupAttributionConfig()
    assert configuration_artifact(config) == configuration_artifact(config)
    artifact = configuration_artifact(config)
    assert b"setup-attribution-v1" in artifact
    assert b'"outcome_dependence":false' in artifact
    assert b'"strategy_invention":false' in artifact
    assert b'"mss_breaker_mitigation":"not_reachable_in_v1"' in artifact


def test_non_buy_frames_carry_frame_provenance_only() -> None:
    snapshots = run()
    flat = snapshots[0]
    assert flat.attribution is None
    assert flat.provenance.producer == "attribution-frame"
    assert flat.provenance.input_prefix_hash == flat.upstream.provenance.input_prefix_hash
    assert flat.upstream.provenance.as_reference() in flat.provenance.dependencies
    buy = snapshots[4]
    assert buy.attribution is not None
    assert buy.attribution.provenance.as_reference() in buy.provenance.dependencies
    assert buy.provenance.producer == "attribution-frame"
