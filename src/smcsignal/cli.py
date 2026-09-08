"""Informational CLI; market data and analysis are explicit Python API operations."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from smcsignal import __version__


def main(argv: Sequence[str] | None = None) -> int:
    """Print project status or standard help/version information."""
    parser = argparse.ArgumentParser(
        prog="smcsignal",
        description=("Professional Halal SMC/ICT Spot Signal Bot — Phase 5 displacement analysis."),
        epilog="No signal engine is implemented. Phase 6 requires explicit approval.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.parse_args(argv)

    print("Professional Halal SMC/ICT Spot Signal Bot")
    print("Phase 5: objective displacement analysis.")
    print("Market data and analysis are available through the Python API.")
    print("No signal engine, scoring, or trading.")
    return 0
