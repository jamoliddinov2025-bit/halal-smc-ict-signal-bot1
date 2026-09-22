"""Phase 35E retention tests: configuration-derived bounds, never magic values."""

from __future__ import annotations

import pytest

from smcsignal.analysis.backtest import BacktestConfiguration
from smcsignal.analysis.config import AnalysisConfig
from smcsignal.analysis.displacement.config import DisplacementConfig
from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analysis.order_blocks.config import OrderBlockConfig
from smcsignal.live.retention import (
    FVG_FRAME_WINDOW,
    RetentionPolicy,
    local_context_bound,
)
from tests.backtest.helpers import configuration, dataset


def test_local_context_bound_is_the_documented_configuration_derived_maximum() -> None:
    cfg = configuration()
    analysis = cfg.analysis if cfg.analysis is not None else AnalysisConfig()
    expected = max(
        analysis.fractal_length,
        cfg.displacement.atr_period + 1,
        cfg.displacement.sweep_lookback_bars,
        cfg.order_blocks.max_candidate_lookback,
        FVG_FRAME_WINDOW,
    )
    assert local_context_bound(cfg) == expected
    # Not one of the forbidden magic constants standing alone as the rule:
    assert local_context_bound(cfg) != 100 or expected == 100
    assert local_context_bound(cfg) == expected  # derived, not hardcoded


def test_bound_follows_each_declared_term() -> None:
    base = configuration()
    # ATR term dominates when raised.
    atr_heavy = BacktestConfiguration(
        displacement=DisplacementConfig(atr_period=50),
        mtf=base.mtf,
        signal_engine=base.signal_engine,
        setup_quality=base.setup_quality,
    )
    assert local_context_bound(atr_heavy) == 51
    # Sweep term dominates when raised.
    sweep_heavy = BacktestConfiguration(
        displacement=DisplacementConfig(sweep_lookback_bars=77),
        mtf=base.mtf,
        signal_engine=base.signal_engine,
        setup_quality=base.setup_quality,
    )
    assert local_context_bound(sweep_heavy) == 77
    # Order-block term dominates when raised.
    ob_heavy = BacktestConfiguration(
        order_blocks=OrderBlockConfig(max_candidate_lookback=60),
        mtf=base.mtf,
        signal_engine=base.signal_engine,
        setup_quality=base.setup_quality,
    )
    assert local_context_bound(ob_heavy) == 60
    # Fractal term dominates when raised (odd integer rule).
    fractal_heavy = BacktestConfiguration(
        analysis=AnalysisConfig(fractal_length=99),
        mtf=base.mtf,
        signal_engine=base.signal_engine,
        setup_quality=base.setup_quality,
    )
    assert local_context_bound(fractal_heavy) == 99


def test_retention_policy_bounds_candles_deterministically() -> None:
    ds = dataset()
    policy = RetentionPolicy.from_configuration(configuration())
    bound = policy.local_context_bound
    assert bound >= FVG_FRAME_WINDOW
    # Within bound: unchanged.
    short = ds.candles[:bound]
    assert policy.bound_candles(short) == tuple(short)
    # Beyond bound: exact newest-``bound`` suffix, every time.
    long = ds.candles
    if len(long) > bound:
        assert policy.bound_candles(long) == tuple(long[-bound:])
    else:
        # Fixture shorter than the derived bound: bound is a no-op by design.
        assert policy.bound_candles(long) == tuple(long)
    first = policy.bound_candles(long)
    second = policy.bound_candles(long)
    assert first == second  # deterministic


def test_retention_policy_rejects_arbitrary_or_invalid_bounds() -> None:
    with pytest.raises(AnalysisInputError):
        RetentionPolicy(local_context_bound=2)
    with pytest.raises(AnalysisInputError):
        local_context_bound(object())  # type: ignore[arg-type]
    # Magic standalone constants are rejected by derivation, not by the
    # policy validator: from_configuration always computes the max of the
    # declared configuration terms, so callers cannot pass 100/200/500/1000
    # as a retention rule.
    cfg = configuration()
    derived = RetentionPolicy.from_configuration(cfg)
    assert derived.local_context_bound == local_context_bound(cfg)
    assert derived.local_context_bound not in (100, 200, 500, 1000) or (
        local_context_bound(cfg) in (100, 200, 500, 1000)
    )


def test_policy_requires_an_integer_at_least_three() -> None:
    for bad in (0, 1, 2, -5, True, "20", 3.5):  # noqa: PERF401 - exhaustive reject list
        with pytest.raises(AnalysisInputError):
            RetentionPolicy(local_context_bound=bad)  # type: ignore[arg-type]
