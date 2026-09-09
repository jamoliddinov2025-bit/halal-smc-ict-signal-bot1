from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from smcsignal.analysis import (
    SetupAttributionAnalyzer,
    SetupAttributionConfig,
    SetupLabel,
    analyze_setup_attribution,
    load_setup_attribution_config,
)
from tests.setup_attribution.helpers import run, signal_frames

SRC = Path(__file__).resolve().parents[2] / "src" / "smcsignal" / "analysis"

# Modules whose decisions must never read setup-attribution context.
DECISION_PACKAGES = (
    "structure.py",
    "swings.py",
    "trend.py",
    "liquidity",
    "displacement",
    "fvg",
    "order_blocks",
    "premium_discount",
    "mss",
    "breaker_blocks",
    "mitigation_blocks",
    "ote",
    "mtf",
    "halal_filter",
    "setup_quality",
    "signal_eligibility",
    "signal_engine",
    "outcome_tracking",
    "indicators",
    "config.py",
    "models.py",
    "errors.py",
    "provenance.py",
)


def _sources() -> list[Path]:
    files: list[Path] = []
    for name in DECISION_PACKAGES:
        path = SRC / name
        if path.is_dir():
            files.extend(sorted(path.glob("*.py")))
        elif path.exists():
            files.append(path)
    return files


def test_attribution_frames_do_not_alter_signal_frames() -> None:
    frames = signal_frames()
    saved = deepcopy(frames)
    snapshots = run(frames=frames)
    assert frames == saved
    assert all(
        snapshot.upstream is original for snapshot, original in zip(snapshots, frames, strict=True)
    )


def test_signal_chain_is_untouched_by_attribution_consumption() -> None:
    frames = signal_frames()
    engine = SetupAttributionAnalyzer()
    for frame in frames:
        engine.update(frame)
    assert frames == signal_frames()
    assert engine.processed_count == len(frames)


def test_signal_statuses_are_unchanged_by_attribution() -> None:
    frames = signal_frames()
    before = tuple(frame.status for frame in frames)
    run(frames=frames)
    assert tuple(frame.status for frame in frames) == before


def test_no_decision_module_imports_attribution_context() -> None:
    for path in _sources():
        text = path.read_text(encoding="utf-8")
        assert "setup_attribution" not in text, (
            f"{path.relative_to(SRC)} must not read setup attribution"
        )
        assert "SetupAttribution" not in text, (
            f"{path.relative_to(SRC)} must not read setup attribution"
        )


def test_attribution_module_imports_no_new_dependencies() -> None:
    from smcsignal.analysis.setup_attribution import analyzer as attribution_analyzer

    text = Path(attribution_analyzer.__file__).read_text(encoding="utf-8")
    for forbidden in ("requests", "urllib", "http", "socket", "numpy", "pandas"):
        assert forbidden not in text


def test_analysis_package_exports_the_attribution_api() -> None:
    import smcsignal.analysis as analysis

    for name in (
        "SetupAttributionAnalyzer",
        "SetupAttributionConfig",
        "SetupAttribution",
        "SetupLabel",
        "analyze_setup_attribution",
        "load_setup_attribution_config",
    ):
        assert getattr(analysis, name) is not None
    assert analysis.SetupAttributionConfig() == SetupAttributionConfig()
    assert SetupLabel.MTF_BULLISH.value == "mtf_bullish"
    assert (
        load_setup_attribution_config(
            Path(__file__).resolve().parents[2] / "config" / "setup-attribution.example.toml"
        )
        == SetupAttributionConfig()
    )
    assert analyze_setup_attribution(()) == ()  # empty replays are legal
