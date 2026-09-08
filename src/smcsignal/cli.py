"""Informational CLI; market data and analysis are explicit Python API operations."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from smcsignal import __version__


def main(argv: Sequence[str] | None = None) -> int:
    """Print project status or standard help/version information."""
    parser = argparse.ArgumentParser(
        prog="smcsignal",
        description=(
            "Professional Halal SMC/ICT Spot Signal Bot — Phase 9 Market Structure Shift analysis."
        ),
        epilog="No signal engine is implemented. Phase 10 requires explicit approval.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.parse_args(argv)

    print("Professional Halal SMC/ICT Spot Signal Bot")
    print("Phase 9: deterministic Market Structure Shift analysis.")
    print("Market data and analysis are available through the Python API.")
    print("No signal engine, scoring, or trading.")
    return 0
