from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from smcsignal.analysis import IndicatorAnalyzer
from tests.indicators.helpers import SMALL, displacement_frames, run

SRC = Path(__file__).resolve().parents[2] / "src" / "smcsignal" / "analysis"

# Modules whose decisions must never read indicator context.
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


def test_indicator_frames_do_not_alter_displacement_frames() -> None:
    frames = displacement_frames()
    saved = deepcopy(frames)
    run(frames=frames)
    assert frames == saved
    snapshots = run(frames=frames)
    assert all(
        snapshot.upstream is original for snapshot, original in zip(snapshots, frames, strict=True)
    )


def test_displacement_chain_is_untouched_by_indicator_consumption() -> None:
    frames = displacement_frames()
    engine = IndicatorAnalyzer(SMALL)
    for frame in frames:
        engine.update(frame)
    assert frames == displacement_frames()
    assert engine.processed_count == len(frames)


def test_no_decision_module_imports_indicator_context() -> None:
    for path in _sources():
        text = path.read_text(encoding="utf-8")
        assert "indicators" not in text, f"{path.relative_to(SRC)} must not read indicators"


def test_indicator_module_imports_no_new_dependencies() -> None:
    from smcsignal.analysis.indicators import analyzer as indicators_analyzer

    text = Path(indicators_analyzer.__file__).read_text(encoding="utf-8")
    for forbidden in ("requests", "urllib", "http", "socket", "numpy", "pandas"):
        assert forbidden not in text
