"""Phase 28 round-trip: declared configurations persist and restore exactly.

Every restored configuration must equal the saved one field-for-field through
the frozen constructors, match the frozen Phase 20 artifact identity, and a
restored configuration must drive the identical Phase 26H run — proving the
durable configuration is the same configuration the run was declared under.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from smcsignal.analysis.backtest import BacktestConfiguration
from smcsignal.analysis.backtest.evidence import configuration_artifact, configuration_hash
from smcsignal.analysis.config import AnalysisConfig
from smcsignal.analysis.displacement.config import DisplacementConfig
from smcsignal.analysis.fvg.config import FVGConfig
from smcsignal.analysis.halal_filter.config import FilterMode, HalalFilterConfig
from smcsignal.analysis.liquidity.config import LiquidityConfig
from smcsignal.analysis.mtf.config import MTFConfig
from smcsignal.analysis.order_blocks.config import (
    CandidateSelection,
    OrderBlockConfig,
    StructureRequirement,
    ZoneBasis,
)
from smcsignal.analysis.ote.config import OTEConfig
from smcsignal.analysis.premium_discount.config import PDConfig
from smcsignal.analysis.setup_quality.config import SetupQualityConfig
from smcsignal.analysis.signal_engine.config import SignalEngineConfig
from smcsignal.configurations import (
    FileConfigurationStore,
    MemoryConfigurationStore,
    configuration_bytes,
    load_configuration_bytes,
)
from smcsignal.datasets import FileDatasetStore
from smcsignal.persistence import FileLedgerStore, MemoryLedgerStore
from smcsignal.runs import run_declared_history
from tests.backtest.helpers import configuration, dataset
from tests.robustness.helpers import CHOP_ONLY, RISE_THEN_FALL
from tests.runs.test_series_run import assert_ledgers_equal, uninterrupted

KEY = "series-primary"
CONFIGURATION_KEY = "pipeline-default"
DATASET_KEY = "dataset-primary"
PIPELINE = configuration()

SCENARIOS = (
    ("rising", tuple(range(20, 37))),
    ("rise_then_fall", RISE_THEN_FALL),
    ("chop_only", CHOP_ONLY),
)

CUSTOM = BacktestConfiguration(
    analysis=AnalysisConfig(7),
    liquidity=LiquidityConfig("USDT", Decimal("5")),
    displacement=DisplacementConfig(
        atr_period=21,
        min_body_atr=Decimal("2.5"),
        min_range_atr=Decimal("3.5"),
        bullish_close_min=Decimal("0.8"),
        bearish_close_max=Decimal("0.2"),
        atr_floor=Decimal("0.0001"),
        sweep_lookback_bars=30,
    ),
    fvg=FVGConfig(min_gap_size=Decimal("0.5"), require_displacement=True),
    order_blocks=OrderBlockConfig(
        max_candidate_lookback=5,
        candidate_selection=CandidateSelection.EARLIEST,
        zone_basis=ZoneBasis.BODY,
        allow_doji=True,
        structure_requirement=StructureRequirement.CHOCH,
        require_fvg=True,
    ),
    premium_discount=PDConfig(Decimal("0.25")),
    ote=OTEConfig(lower_retracement=Decimal("0.705"), upper_retracement=Decimal("0.9")),
    mtf=MTFConfig(primary_timeframe="15m", higher_timeframes=("1h", "4h", "1d")),
    halal_filter=HalalFilterConfig(
        mode=FilterMode.DENY_LIST,
        allowed_assets=(),
        denied_assets=("SCAMUSDT",),
    ),
    setup_quality=SetupQualityConfig(12),
    signal_engine=SignalEngineConfig(publish_threshold=12),
)


def variants() -> tuple:
    return (
        ("helpers_fixtures", configuration()),
        ("all_factory_defaults", BacktestConfiguration()),
        ("null_analysis_table", replace(configuration(), analysis=None)),
        ("fully_custom", CUSTOM),
        ("other_thresholds", configuration(threshold=15, horizon=4)),
    )


@pytest.fixture(params=["file", "memory"])
def store(request, tmp_path):
    if request.param == "file":
        return FileConfigurationStore(tmp_path / "configurations")
    return MemoryConfigurationStore()


@pytest.mark.parametrize(("label", "original"), variants(), ids=[v[0] for v in variants()])
def test_declared_configuration_round_trips_exactly(store, label, original) -> None:
    store.save(CONFIGURATION_KEY, original)
    assert store.contains(CONFIGURATION_KEY)
    restored = store.load(CONFIGURATION_KEY)

    assert restored == original
    assert restored is not None
    assert restored.backtest == original.backtest
    assert restored.analysis == original.analysis
    assert restored.liquidity == original.liquidity
    assert restored.displacement == original.displacement
    assert restored.fvg == original.fvg
    assert restored.order_blocks == original.order_blocks
    assert restored.premium_discount == original.premium_discount
    assert restored.ote == original.ote
    assert restored.mtf == original.mtf
    assert restored.halal_filter == original.halal_filter
    assert restored.setup_quality == original.setup_quality
    assert restored.signal_eligibility == original.signal_eligibility
    assert restored.signal_engine == original.signal_engine
    assert restored.outcome_tracking == original.outcome_tracking
    assert restored.setup_attribution == original.setup_attribution
    assert restored.performance == original.performance
    assert configuration_bytes(restored) == configuration_bytes(original)


@pytest.mark.parametrize(("label", "original"), variants(), ids=[v[0] for v in variants()])
def test_restored_configuration_preserves_the_frozen_artifact_identity(original, label) -> None:
    """The frozen Phase 20 artifact is the oracle: identity never drifts."""

    restored = load_configuration_bytes(configuration_bytes(original))
    assert configuration_hash(restored) == configuration_hash(original)
    assert configuration_artifact(restored) == configuration_artifact(original)


@pytest.mark.parametrize(("label", "original"), variants(), ids=[v[0] for v in variants()])
def test_every_field_value_round_trips_losslessly(original, label) -> None:
    """Exact value domain: decimals as exact text, enums, tuples, and None."""

    restored = load_configuration_bytes(configuration_bytes(original))
    assert restored.displacement.min_body_atr == original.displacement.min_body_atr
    assert restored.mtf.higher_timeframes == original.mtf.higher_timeframes
    assert restored.halal_filter.mode == original.halal_filter.mode
    assert restored.halal_filter.allowed_assets == original.halal_filter.allowed_assets
    assert restored.halal_filter.denied_assets == original.halal_filter.denied_assets
    assert restored.order_blocks.candidate_selection == original.order_blocks.candidate_selection
    assert (restored.analysis is None) == (original.analysis is None)


def test_memory_and_file_stores_agree_on_protocol_behavior(tmp_path) -> None:
    primary = configuration()
    other = configuration(threshold=15, horizon=4)
    memory = MemoryConfigurationStore()
    file_store = FileConfigurationStore(tmp_path / "configurations")

    for candidate in (memory, file_store):
        assert candidate.load("missing") is None
        assert candidate.contains("missing") is False
        candidate.save("alpha", primary)
        candidate.save("beta", other)

    assert memory.load("alpha") == file_store.load("alpha") == primary
    assert memory.load("beta") == file_store.load("beta") == other
    assert memory.contains("alpha") is True and file_store.contains("alpha") is True


def test_missing_key_loads_nothing_and_contains_reflects_presence(store) -> None:
    assert store.load("never-saved") is None
    assert store.contains("never-saved") is False
    store.save("present", configuration())
    assert store.contains("present") is True
    assert store.load("never-saved") is None


def test_distinct_keys_remain_isolated(store) -> None:
    primary = configuration()
    other = CUSTOM
    assert configuration_bytes(primary) != configuration_bytes(other)
    store.save("configuration-one", primary)
    store.save("configuration-two", other)
    assert store.load("configuration-one") == primary
    assert store.load("configuration-two") == other


def test_repeated_identical_saves_are_deterministic(tmp_path) -> None:
    store = FileConfigurationStore(tmp_path / "configurations")
    original = CUSTOM
    store.save(CONFIGURATION_KEY, original)
    target = tmp_path / "configurations" / f"{CONFIGURATION_KEY}.configuration.json"
    first_bytes = target.read_bytes()
    store.save(CONFIGURATION_KEY, original)
    assert target.read_bytes() == first_bytes
    assert first_bytes == configuration_bytes(original)
    assert store.load(CONFIGURATION_KEY) == original


def test_changed_configuration_replaces_the_previous_one(store) -> None:
    before = configuration()
    after = configuration(threshold=15, horizon=4)
    assert configuration_bytes(before) != configuration_bytes(after)
    store.save(CONFIGURATION_KEY, before)
    assert store.load(CONFIGURATION_KEY) == before
    store.save(CONFIGURATION_KEY, after)
    assert store.load(CONFIGURATION_KEY) == after


def test_file_store_creates_the_root_on_first_save_and_reads_none_without_it(tmp_path) -> None:
    root = tmp_path / "does" / "not" / "exist" / "yet"
    store = FileConfigurationStore(root)
    assert store.load(CONFIGURATION_KEY) is None and not store.contains(CONFIGURATION_KEY)
    store.save(CONFIGURATION_KEY, configuration())
    assert store.contains(CONFIGURATION_KEY)
    assert store.load(CONFIGURATION_KEY) == configuration()


def test_one_validated_key_maps_to_exactly_one_file_under_the_root(tmp_path) -> None:
    store = FileConfigurationStore(tmp_path / "configurations")
    store.save("pipeline-a", configuration())
    store.save("pipeline-b", configuration(threshold=15, horizon=4))
    names = sorted(path.name for path in (tmp_path / "configurations").iterdir())
    assert names == ["pipeline-a.configuration.json", "pipeline-b.configuration.json"]


def test_determinism_across_independent_file_stores(tmp_path) -> None:
    original = CUSTOM
    store_a = FileConfigurationStore(tmp_path / "root-a")
    store_b = FileConfigurationStore(tmp_path / "root-b")
    store_a.save(CONFIGURATION_KEY, original)
    store_b.save(CONFIGURATION_KEY, original)
    bytes_a = (tmp_path / "root-a" / f"{CONFIGURATION_KEY}.configuration.json").read_bytes()
    bytes_b = (tmp_path / "root-b" / f"{CONFIGURATION_KEY}.configuration.json").read_bytes()
    assert bytes_a == bytes_b == configuration_bytes(original)
    assert store_a.load(CONFIGURATION_KEY) == store_b.load(CONFIGURATION_KEY) == original


@pytest.mark.parametrize(("label", "prices"), SCENARIOS)
def test_a_restored_configuration_drives_the_identical_26h_run(store, label, prices) -> None:
    store.save(CONFIGURATION_KEY, PIPELINE)
    restored = store.load(CONFIGURATION_KEY)
    assert restored is not None

    ledger_store = MemoryLedgerStore()
    ran = run_declared_history(dataset(prices=prices), restored, ledger_store, KEY)
    full = uninterrupted(prices)

    assert_ledgers_equal(ran.lifecycle, full)
    assert ran.frames_verified == len(dataset(prices=prices).candles)
    assert ran.config == restored.outcome_tracking


def test_restart_through_the_configuration_store_matches_uninterrupted(tmp_path) -> None:
    """Full restart story: configuration comes from the store both times."""

    configuration_store = FileConfigurationStore(tmp_path / "configurations")
    configuration_store.save(CONFIGURATION_KEY, PIPELINE)
    dataset_store = FileDatasetStore(tmp_path / "datasets")
    dataset_store.save(DATASET_KEY, dataset())

    ledger_root = tmp_path / "ledgers"
    first_configuration = configuration_store.load(CONFIGURATION_KEY)
    first_history = dataset_store.load(DATASET_KEY)
    assert first_configuration is not None and first_history is not None
    first_run = run_declared_history(
        first_history, first_configuration, FileLedgerStore(ledger_root), KEY
    )
    first_run.persist()
    assert first_run.recovered_from is None

    # Restart: fresh store instances over the same roots re-supply the exact
    # declared configuration and history; the durable session recovers.
    restarted_configuration = FileConfigurationStore(tmp_path / "configurations").load(
        CONFIGURATION_KEY
    )
    restarted_history = FileDatasetStore(tmp_path / "datasets").load(DATASET_KEY)
    assert restarted_configuration == PIPELINE and restarted_history == dataset()
    assert restarted_configuration is not None and restarted_history is not None
    restarted = run_declared_history(
        restarted_history, restarted_configuration, FileLedgerStore(ledger_root), KEY
    )

    assert_ledgers_equal(restarted.lifecycle, first_run.lifecycle)
    assert restarted.recovered_from is not None
    assert restarted.recovered_from == first_run.persist()
    assert_ledgers_equal(restarted.lifecycle, uninterrupted(tuple(range(20, 37))))


def test_configuration_bytes_round_trip_is_lossless() -> None:
    original = CUSTOM
    restored = load_configuration_bytes(configuration_bytes(original))
    assert restored == original
    assert configuration_bytes(restored) == configuration_bytes(original)
