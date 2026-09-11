"""Phase 28 integrity: tamper rejection, key safety, atomicity, and boundaries.

Disk corruption is rejected exclusively through the canonical document checks
and the frozen constructors; keys can never escape the root; a failed save
never exposes a partial configuration; and the configurations leaf imports
only its sanctioned frozen surfaces — never analytics, persistence, series,
sessions, runs, datasets, delivery, monitoring, or any network/execution
machinery.
"""

from __future__ import annotations

import ast
import json
import os
from pathlib import Path

import pytest

from smcsignal.analysis.config import AnalysisConfig
from smcsignal.analysis.errors import AnalysisConfigurationError, AnalysisInputError
from smcsignal.analysis.liquidity.evidence import canonical_bytes
from smcsignal.configurations import (
    FileConfigurationStore,
    MemoryConfigurationStore,
    configuration_bytes,
    load_configuration_bytes,
)
from tests.backtest.helpers import configuration, dataset


def test_tampered_disk_contents_are_rejected(tmp_path):
    store = FileConfigurationStore(tmp_path / "configurations")
    original = configuration()
    store.save("pipeline", original)
    target = tmp_path / "configurations" / "pipeline.configuration.json"

    flipped = bytearray(target.read_bytes())
    flipped[len(flipped) // 2] ^= 0x01
    target.write_bytes(bytes(flipped))
    with pytest.raises(AnalysisInputError):
        store.load("pipeline")

    content = configuration_bytes(original)
    target.write_bytes(content[: len(content) // 2])  # truncation
    with pytest.raises(AnalysisInputError):
        store.load("pipeline")

    target.write_bytes(content + b"corruption")  # append
    with pytest.raises(AnalysisInputError):
        store.load("pipeline")

    target.write_bytes(content)  # healing restores the exact configuration
    assert store.load("pipeline") == original


def _mutated_document(**changes: object) -> bytes:
    document = json.loads(configuration_bytes(configuration()))
    document.update(changes)
    return canonical_bytes(document)


@pytest.mark.parametrize(
    "mutation",
    [
        {"methodology": "declared-configuration:v9"},
        {"kind": "something-else"},
        {"configuration_digest": "configuration:0000"},
        {"role": "production"},
        {"live_trading": True},
    ],
    ids=[
        "wrong-methodology",
        "wrong-kind",
        "digest-tamper",
        "role-tamper",
        "role-flag-tamper",
    ],
)
def test_schema_digest_and_role_tampering_is_rejected(mutation) -> None:
    with pytest.raises(AnalysisInputError):
        load_configuration_bytes(_mutated_document(**mutation))


def test_identity_tampering_fails_the_recomputed_digest() -> None:
    """A schema-valid value change must fail the content digest, never the schema."""

    document = json.loads(configuration_bytes(configuration()))
    document["pipeline"]["outcome_tracking"]["horizon_bars"] = 9
    with pytest.raises(AnalysisInputError, match="content digest"):
        load_configuration_bytes(canonical_bytes(document))


def test_structural_tampering_is_rejected() -> None:
    extra = json.loads(configuration_bytes(configuration()))
    extra["unexpected"] = True
    with pytest.raises(AnalysisInputError):
        load_configuration_bytes(canonical_bytes(extra))

    missing = json.loads(configuration_bytes(configuration()))
    del missing["pipeline"]
    with pytest.raises(AnalysisInputError):
        load_configuration_bytes(canonical_bytes(missing))

    pipeline_as_list = json.loads(configuration_bytes(configuration()))
    pipeline_as_list["pipeline"] = []
    with pytest.raises(AnalysisInputError):
        load_configuration_bytes(canonical_bytes(pipeline_as_list))

    unknown_table = json.loads(configuration_bytes(configuration()))
    unknown_table["pipeline"]["mystery"] = {"enabled": True}
    with pytest.raises(AnalysisInputError):
        load_configuration_bytes(canonical_bytes(unknown_table))

    missing_table = json.loads(configuration_bytes(configuration()))
    del missing_table["pipeline"]["fvg"]
    with pytest.raises(AnalysisInputError):
        load_configuration_bytes(canonical_bytes(missing_table))

    backtest_extra_key = json.loads(configuration_bytes(configuration()))
    backtest_extra_key["backtest"]["extra"] = 1
    with pytest.raises(AnalysisInputError):
        load_configuration_bytes(canonical_bytes(backtest_extra_key))

    analysis_wrong_shape = json.loads(configuration_bytes(configuration()))
    analysis_wrong_shape["pipeline"]["analysis"] = {"wrong": 1}
    with pytest.raises(AnalysisInputError):
        load_configuration_bytes(canonical_bytes(analysis_wrong_shape))

    non_nullable_table_null = json.loads(configuration_bytes(configuration()))
    non_nullable_table_null["pipeline"]["liquidity"] = None
    with pytest.raises(AnalysisInputError):
        load_configuration_bytes(canonical_bytes(non_nullable_table_null))


def test_strict_value_types_are_enforced_before_the_constructors() -> None:
    bool_in_int_slot = json.loads(configuration_bytes(configuration()))
    bool_in_int_slot["pipeline"]["outcome_tracking"]["horizon_bars"] = True
    with pytest.raises(AnalysisInputError, match="must be an integer"):
        load_configuration_bytes(canonical_bytes(bool_in_int_slot))

    int_in_bool_slot = json.loads(configuration_bytes(configuration()))
    int_in_bool_slot["backtest"]["enabled"] = 1
    with pytest.raises(AnalysisInputError, match="must be a boolean"):
        load_configuration_bytes(canonical_bytes(int_in_bool_slot))

    decimal_as_number = json.loads(configuration_bytes(configuration()))
    decimal_as_number["pipeline"]["displacement"]["min_body_atr"] = 2.5
    with pytest.raises(AnalysisInputError, match="canonical Decimal text"):
        load_configuration_bytes(json.dumps(decimal_as_number).encode("utf-8"))

    non_finite_decimal = json.loads(configuration_bytes(configuration()))
    non_finite_decimal["pipeline"]["displacement"]["min_body_atr"] = "NaN"
    with pytest.raises(AnalysisInputError, match="finite"):
        load_configuration_bytes(canonical_bytes(non_finite_decimal))

    garbage_decimal = json.loads(configuration_bytes(configuration()))
    garbage_decimal["pipeline"]["displacement"]["min_body_atr"] = "abc"
    with pytest.raises(AnalysisInputError):
        load_configuration_bytes(canonical_bytes(garbage_decimal))

    bad_enum_value = json.loads(configuration_bytes(configuration()))
    bad_enum_value["pipeline"]["order_blocks"]["candidate_selection"] = "sideways"
    with pytest.raises(AnalysisInputError, match="CandidateSelection"):
        load_configuration_bytes(canonical_bytes(bad_enum_value))

    enum_wrong_type = json.loads(configuration_bytes(configuration()))
    enum_wrong_type["pipeline"]["order_blocks"]["candidate_selection"] = 7
    with pytest.raises(AnalysisInputError):
        load_configuration_bytes(canonical_bytes(enum_wrong_type))

    tuple_as_string = json.loads(configuration_bytes(configuration()))
    tuple_as_string["pipeline"]["mtf"]["higher_timeframes"] = "1h"
    with pytest.raises(AnalysisInputError, match="canonical list"):
        load_configuration_bytes(canonical_bytes(tuple_as_string))

    tuple_bad_element = json.loads(configuration_bytes(configuration()))
    tuple_bad_element["pipeline"]["halal_filter"]["allowed_assets"] = ["BTCUSDT", 5]
    with pytest.raises(AnalysisInputError):
        load_configuration_bytes(canonical_bytes(tuple_bad_element))

    untrimmed_text = json.loads(configuration_bytes(configuration()))
    untrimmed_text["pipeline"]["liquidity"]["price_unit"] = " USDT "
    with pytest.raises(AnalysisInputError):
        load_configuration_bytes(canonical_bytes(untrimmed_text))


def test_frozen_constructor_validation_runs_unmodified_at_load() -> None:
    """Constructible-looking documents still fail the frozen configuration rules."""

    threshold_mismatch = json.loads(configuration_bytes(configuration()))
    threshold_mismatch["pipeline"]["setup_quality"]["publish_threshold"] = 12
    with pytest.raises(AnalysisConfigurationError, match="publish_threshold"):
        load_configuration_bytes(canonical_bytes(threshold_mismatch))

    duplicated_timeframes = json.loads(configuration_bytes(configuration()))
    duplicated_timeframes["pipeline"]["mtf"]["higher_timeframes"] = ["1h", "1h"]
    with pytest.raises(AnalysisConfigurationError):
        load_configuration_bytes(canonical_bytes(duplicated_timeframes))

    reversed_ote = json.loads(configuration_bytes(configuration()))
    reversed_ote["pipeline"]["ote"]["lower_retracement"] = "9e-1"
    reversed_ote["pipeline"]["ote"]["upper_retracement"] = "62e-2"
    with pytest.raises(AnalysisConfigurationError):
        load_configuration_bytes(canonical_bytes(reversed_ote))

    allow_list_with_deny = json.loads(configuration_bytes(configuration()))
    allow_list_with_deny["pipeline"]["halal_filter"]["denied_assets"] = ["XYZUSDT"]
    with pytest.raises(AnalysisConfigurationError):
        load_configuration_bytes(canonical_bytes(allow_list_with_deny))

    disabled_backtest = json.loads(configuration_bytes(configuration()))
    disabled_backtest["backtest"]["enabled"] = False
    with pytest.raises(AnalysisConfigurationError):
        load_configuration_bytes(canonical_bytes(disabled_backtest))

    disabled_eligibility = json.loads(configuration_bytes(configuration()))
    disabled_eligibility["pipeline"]["signal_eligibility"]["enabled"] = False
    with pytest.raises(AnalysisConfigurationError):
        load_configuration_bytes(canonical_bytes(disabled_eligibility))


def test_load_requires_canonical_bytes() -> None:
    for candidate in ("{}", {"not": "bytes"}, None, 42):
        with pytest.raises(AnalysisInputError, match="canonical bytes"):
            load_configuration_bytes(candidate)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "bad_key",
    [
        "",
        "  ",
        "a" * 129,
        "/",
        "/absolute/key",
        "..",
        "../escape",
        "a/../b",
        "a/b",
        "a\\b",
        "a\\..\\b",
        ".hidden",
        "trailing.",
        "-leading-dash",
        "with space",
        "tab\tkey",
        "unicode-é",
        "CON",
        "nul",
        "COM1",
        "lpt9",
        123,
        b"pipeline",
        None,
    ],
)
def test_unsafe_keys_are_rejected(tmp_path, bad_key):
    store = FileConfigurationStore(tmp_path / "configurations")
    with pytest.raises(AnalysisInputError):
        store.save(bad_key, configuration())  # type: ignore[arg-type]
    with pytest.raises(AnalysisInputError):
        store.load(bad_key)  # type: ignore[arg-type]
    with pytest.raises(AnalysisInputError):
        store.contains(bad_key)  # type: ignore[arg-type]
    assert list((tmp_path / "configurations").glob("*")) == []


def test_valid_keys_with_internal_dots_dashes_colons_are_accepted(tmp_path):
    store = FileConfigurationStore(tmp_path / "configurations")
    for key in ("BTCUSDT-15m", "pipeline.v2", "btcusdt:15m:primary", "a.b-c:d"):
        store.save(key, configuration())
        assert store.load(key) == configuration()


def test_only_backtest_configurations_can_be_saved(tmp_path):
    for candidate in (
        FileConfigurationStore(tmp_path / "configurations"),
        MemoryConfigurationStore(),
    ):
        for forbidden in (
            dataset(),
            b"bytes",
            {"not": "a configuration"},
            42,
            "BacktestConfiguration",
            AnalysisConfig(5),
        ):
            with pytest.raises(AnalysisInputError, match="BacktestConfiguration"):
                candidate.save("pipeline", forbidden)  # type: ignore[arg-type]
        with pytest.raises(AnalysisInputError, match="BacktestConfiguration"):
            configuration_bytes(forbidden)  # type: ignore[arg-type]


def test_save_failure_before_replace_keeps_previous_configuration_intact(tmp_path, monkeypatch):
    """Injected failure BEFORE os.replace: A intact, no partial, B savable later."""

    store = FileConfigurationStore(tmp_path / "configurations")
    configuration_a = configuration()
    configuration_b = configuration(threshold=15, horizon=4)
    assert configuration_bytes(configuration_a) != configuration_bytes(configuration_b)
    store.save("pipeline", configuration_a)
    files_before = sorted(path.name for path in (tmp_path / "configurations").iterdir())

    def broken_replace(src, dst):
        raise OSError("injected failure before replace")

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", broken_replace)
        with pytest.raises(OSError, match="injected"):
            store.save("pipeline", configuration_b)

    assert store.load("pipeline") == configuration_a  # previous configuration loads
    target = tmp_path / "configurations" / "pipeline.configuration.json"
    assert target.read_bytes() == configuration_bytes(configuration_a)  # no partial target
    assert sorted(path.name for path in (tmp_path / "configurations").iterdir()) == files_before
    store.save("pipeline", configuration_b)  # a later save works normally
    assert store.load("pipeline") == configuration_b


def test_failed_save_into_a_fresh_key_leaves_no_residue(tmp_path, monkeypatch):
    store = FileConfigurationStore(tmp_path / "configurations")

    def broken_replace(src, dst):
        raise OSError("injected failure before replace")

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", broken_replace)
        with pytest.raises(OSError, match="injected"):
            store.save("brand-new", configuration())

    assert list((tmp_path / "configurations").glob("*")) == []
    assert store.load("brand-new") is None and not store.contains("brand-new")
    store.save("brand-new", configuration())
    assert store.contains("brand-new")


CONFIGURATIONS_ROOT = Path(__file__).resolve().parents[2] / "src" / "smcsignal" / "configurations"


def test_configurations_leaf_imports_only_sanctioned_modules() -> None:
    allowed_stdlib = {
        "__future__",
        "dataclasses",
        "decimal",
        "enum",
        "json",
        "os",
        "pathlib",
        "re",
        "types",
        "typing",
    }
    allowed_smcsignal = {
        "smcsignal.analysis.backtest",
        "smcsignal.analysis.backtest.config",
        "smcsignal.analysis.config",
        "smcsignal.analysis.displacement.config",
        "smcsignal.analysis.errors",
        "smcsignal.analysis.fvg.config",
        "smcsignal.analysis.halal_filter.config",
        "smcsignal.analysis.liquidity.config",
        "smcsignal.analysis.liquidity.evidence",
        "smcsignal.analysis.mtf.config",
        "smcsignal.analysis.order_blocks.config",
        "smcsignal.analysis.ote.config",
        "smcsignal.analysis.outcome_tracking.config",
        "smcsignal.analysis.performance.config",
        "smcsignal.analysis.premium_discount.config",
        "smcsignal.analysis.setup_attribution.config",
        "smcsignal.analysis.setup_quality.config",
        "smcsignal.analysis.signal_eligibility.config",
        "smcsignal.analysis.signal_engine.config",
    }
    for path in sorted(CONFIGURATIONS_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level > 0:
                    continue
                modules = [node.module] if node.module else []
            else:
                continue
            for module in modules:
                root = module.split(".")[0]
                if root == "smcsignal":
                    assert module in allowed_smcsignal, (
                        f"{path.name} imports {module}: configurations consumes only the frozen "
                        "configuration models, the evidence canon, and analysis errors"
                    )
                else:
                    assert root in allowed_stdlib, f"{path.name} imports unexpected {module}"
                assert not module.startswith(
                    (
                        "smcsignal.analytics",
                        "smcsignal.persistence",
                        "smcsignal.series",
                        "smcsignal.sessions",
                        "smcsignal.runs",
                        "smcsignal.datasets",
                        "smcsignal.delivery",
                        "smcsignal.monitoring",
                    )
                )
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in ("print", "input", "exec", "eval", "__import__", "open")
            if isinstance(node, (ast.Name, ast.Attribute)):
                identifier = node.id if isinstance(node, ast.Name) else node.attr
                assert "Telegram" not in identifier and "Delivery" not in identifier
            if isinstance(node, ast.Attribute):
                assert node.attr not in ("now", "utcnow", "today", "sleep")


def test_nothing_outside_configurations_imports_configurations() -> None:
    source_root = Path(__file__).resolve().parents[2] / "src" / "smcsignal"
    for path in sorted(source_root.rglob("*.py")):
        if path.is_relative_to(CONFIGURATIONS_ROOT):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level > 0:
                    continue
                modules = [node.module] if node.module else []
            else:
                continue
            for module in modules:
                assert not module.startswith("smcsignal.configurations"), (
                    f"{path} imports configurations: the store is a leaf consumed only by callers"
                )


def test_configurations_is_a_single_module() -> None:
    names = sorted(path.name for path in CONFIGURATIONS_ROOT.rglob("*.py"))
    assert names == ["__init__.py", "configuration_store.py"]


def test_facade_exports_the_sanctioned_surface() -> None:
    import smcsignal
    import smcsignal.configurations as configurations_package

    assert configurations_package.__all__ == [
        "ConfigurationStore",
        "FileConfigurationStore",
        "MemoryConfigurationStore",
        "configuration_bytes",
        "load_configuration_bytes",
    ]
    assert smcsignal.__all__ == ["__version__"]
