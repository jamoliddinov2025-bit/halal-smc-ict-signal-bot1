from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest

from smcsignal.analysis import AnalysisInputError
from smcsignal.analysis.setup_attribution import (
    SetupAttribution,
    SetupLabel,
    current_observation,
)
from tests.setup_attribution.helpers import run, signal_frames

CANONICAL = (
    "bullish_displacement",
    "sweep_associated",
    "fvg_bullish",
    "fvg_bearish",
    "order_block_bullish",
    "order_block_bearish",
    "inside_ote",
    "pd_discount",
    "pd_premium",
    "pd_equilibrium",
    "mtf_bullish",
    "mtf_mixed",
)


def test_taxonomy_is_the_closed_canonical_order() -> None:
    assert tuple(label.value for label in SetupLabel) == CANONICAL
    assert len(SetupLabel) == 12
    # MSS, Breaker, and Mitigation facts are unreachable on the signal graph.
    assert not any(
        token in value
        for value in (label.value for label in SetupLabel)
        for token in ("mss", "breaker", "mitigation")
    )


def buy_profile():
    snapshots = run()
    buy = next(snapshot for snapshot in snapshots if snapshot.attribution is not None)
    return buy.attribution


def test_combination_key_joins_canonical_order() -> None:
    profile = buy_profile()
    assert profile.labels == (SetupLabel.MTF_BULLISH,)
    assert profile.combination_key == "mtf_bullish"
    widened = replace(
        profile,
        labels=(SetupLabel.PD_PREMIUM, SetupLabel.MTF_BULLISH),
    )
    assert widened.combination_key == "pd_premium+mtf_bullish"


def test_labels_must_be_unique_setup_labels_in_canonical_order() -> None:
    profile = buy_profile()
    for invalid in (
        (SetupLabel.MTF_BULLISH, SetupLabel.PD_PREMIUM),  # out of canonical order
        (SetupLabel.MTF_BULLISH, SetupLabel.MTF_BULLISH),  # duplicates
        [SetupLabel.MTF_BULLISH],  # not a tuple
        ("mtf_bullish",),  # raw strings are not labels
    ):
        with pytest.raises(AnalysisInputError):
            replace(profile, labels=invalid)


def test_empty_label_tuple_is_a_legal_observation() -> None:
    profile = replace(buy_profile(), labels=())
    assert profile.labels == () and profile.combination_key == ""


def test_score_total_must_match_the_copied_breakdown() -> None:
    profile = buy_profile()
    assert profile.component_scores.total == profile.score_total
    with pytest.raises(AnalysisInputError):
        replace(profile, score_total=profile.score_total + 1)
    with pytest.raises(AnalysisInputError):
        replace(profile, score_total=101)


def test_text_fields_must_be_nonempty_and_trimmed() -> None:
    profile = buy_profile()
    for name, value in (
        ("attribution_id", ""),
        ("attribution_id", " padded "),
        ("signal_id", ""),
        ("setup_identity", ""),
        ("symbol", ""),
        ("timeframe", " 15m"),
    ):
        with pytest.raises(AnalysisInputError):
            replace(profile, **{name: value})


def test_published_at_must_stay_at_the_signal_cutoff() -> None:
    profile = buy_profile()
    assert profile.published_at == profile.provenance.available_at
    with pytest.raises(AnalysisInputError):
        replace(profile, published_at=profile.published_at + timedelta(minutes=1))


def test_producer_and_series_invariants_hold() -> None:
    profile = buy_profile()
    assert profile.provenance.producer == "attribution-record"
    assert profile.provenance.series == profile.reference.series
    assert profile.reference in profile.provenance.source_candles


def test_attribution_exists_exactly_on_buy_frames() -> None:
    snapshots = run()
    for snapshot in snapshots:
        buy = snapshot.upstream.status.value == "BUY_SIGNAL"
        assert (snapshot.attribution is not None) is buy


def test_snapshot_pairs_profile_with_its_own_signal_only() -> None:
    snapshots = run()
    buy = next(snapshot for snapshot in snapshots if snapshot.attribution is not None)
    flat = next(snapshot for snapshot in snapshots if snapshot.attribution is None)
    with pytest.raises(AnalysisInputError):
        replace(flat, attribution=buy.attribution)
    with pytest.raises(AnalysisInputError):
        replace(buy, attribution=None)


def test_snapshot_keeps_the_upstream_frame_unchanged() -> None:
    frames = signal_frames()
    snapshots = run(frames)
    assert all(
        snapshot.upstream is original for snapshot, original in zip(snapshots, frames, strict=True)
    )
    assert current_observation(frames[0]) == current_observation(snapshots[0].upstream)


def test_settings_must_be_typed() -> None:
    profile = buy_profile()
    with pytest.raises(AnalysisInputError):
        replace(profile, settings="enabled")  # type: ignore[arg-type]
    with pytest.raises(AnalysisInputError):
        SetupAttribution(
            settings="enabled",  # type: ignore[arg-type]
            attribution_id=profile.attribution_id,
            signal_id=profile.signal_id,
            setup_identity=profile.setup_identity,
            symbol=profile.symbol,
            timeframe=profile.timeframe,
            labels=profile.labels,
            score_total=profile.score_total,
            component_scores=profile.component_scores,
            reference=profile.reference,
            published_at=profile.published_at,
            provenance=profile.provenance,
        )
