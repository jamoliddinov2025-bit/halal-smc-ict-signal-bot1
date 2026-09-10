"""Phase 26A: a REAL published SignalSnapshot creates an analytics observation.

These tests exercise the actual production path: the real Phase 3-16 chain
builds real eligibility facts, the real Phase 17 ``SignalEngineAnalyzer``
publishes the snapshots, and the Phase 26A observer is attached at exactly that
publication boundary. No demo pipeline and no synthetic shortcut stand in for
the engine.
"""

from __future__ import annotations

from hashlib import sha256

from smcsignal.analysis.outcome_tracking import OutcomeStatus, OutcomeTrackingConfig
from smcsignal.analysis.outcome_tracking.calculation import open_outcome
from smcsignal.analysis.outcome_tracking.evidence import (
    configuration_artifact,
    outcome_identity,
)
from smcsignal.analysis.outcome_tracking.models import current_observation
from smcsignal.analytics import AnalyticsObserver
from tests.analytics.helpers import buy_frames, observed_engine, publish, real_engine

EXPECTED_BUY_COUNT = 4  # audited Phase 18 fixture: BUY publications at 4, 8, 12, 16


def test_real_published_snapshots_create_open_outcome_observations() -> None:
    adapter, eligible = observed_engine()
    frames = publish(adapter, eligible)
    buys = buy_frames(frames)
    assert len(buys) == EXPECTED_BUY_COUNT

    observer = adapter.observer
    assert len(observer.observations) == EXPECTED_BUY_COUNT
    for frame, observation in zip(buys, observer.observations, strict=True):
        observation_candle = current_observation(frame)
        outcome = observation.outcome
        assert outcome.status is OutcomeStatus.OPEN
        assert outcome.candles_observed == 0
        assert outcome.signal_id == frame.signal_id
        assert outcome.setup_identity == frame.setup_identity
        assert outcome.reference == observation_candle.reference
        assert outcome.reference_close == observation_candle.candle.close
        assert outcome.mfe_price is None and outcome.mae_price is None
        assert outcome.final_close is None and outcome.final_index is None


def test_non_buy_snapshots_create_no_observation() -> None:
    adapter, eligible = observed_engine()
    frames = publish(adapter, eligible)
    buys = set(buy_frames(frames))
    non_buys = [frame for frame in frames if frame not in buys]
    assert non_buys, "the fixture must contain non-publication frames"
    observed_ids = {observation.signal_id for observation in adapter.observer.observations}
    for frame in non_buys:
        assert frame.signal_id not in observed_ids
    assert len(adapter.observer.observations) == EXPECTED_BUY_COUNT


def test_observation_reuses_the_exact_phase18_open_record() -> None:
    observer = AnalyticsObserver()
    adapter, eligible = observed_engine(observer)
    frames = publish(adapter, eligible)
    first_buy = buy_frames(frames)[0]
    config_hash = sha256(configuration_artifact(OutcomeTrackingConfig())).hexdigest()
    expected = open_outcome(first_buy, OutcomeTrackingConfig(), config_hash)
    assert observer.observations[0].outcome == expected
    assert observer.observations[0].outcome_id == outcome_identity(
        first_buy, OutcomeTrackingConfig()
    )


def test_adapter_publishes_exactly_what_the_real_engine_publishes() -> None:
    adapter, eligible = observed_engine()
    frames = publish(adapter, eligible)
    engine = real_engine(eligible)
    reference = tuple(engine.update(frame) for frame in eligible)
    assert frames == reference
    assert adapter.engine.latest == reference[-1]
    assert adapter.engine.processed_count == len(reference)


def test_streaming_and_batch_observation_agree() -> None:
    adapter, eligible = observed_engine()
    frames = publish(adapter, eligible)
    batch = AnalyticsObserver().observe_all(frames)
    assert batch == adapter.observer.observations
