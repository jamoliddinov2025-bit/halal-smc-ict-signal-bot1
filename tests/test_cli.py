"""Check the informational CLI without network access or trading behavior."""

import subprocess
import sys
from pathlib import Path

import pytest

from smcsignal import __version__
from smcsignal.cli import main


def test_default_command_reports_analysis_scope(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 0
    captured = capsys.readouterr()
    assert captured.out == (
        "Professional Halal SMC/ICT Spot Signal Bot\n"
        "Phase 11: deterministic Mitigation Block first-interaction analysis.\n"
        "Market data and analysis are available through the Python API.\n"
        "No signal engine, scoring, or trading.\n"
    )
    assert captured.err == ""


def test_version_option(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out == f"smcsignal {__version__}\n"


def test_help_option(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    output = " ".join(capsys.readouterr().out.split())
    assert "--version" in output
    assert "Phase 11 Mitigation Block analysis" in output
    assert "Phase 12 requires explicit approval" in output


@pytest.mark.parametrize("option", ["--live", "--trade", "--config"])
def test_unimplemented_options_are_rejected(
    option: str, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exc:
        main([option])
    assert exc.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "unrecognized arguments" in captured.err


def test_module_entrypoint_works_outside_checkout(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "smcsignal"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    assert "Phase 11: deterministic Mitigation Block first-interaction analysis." in result.stdout
    assert "No signal engine, scoring, or trading." in result.stdout
    assert result.stderr == ""
