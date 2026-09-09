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
            "Professional Halal SMC/ICT Spot Signal Bot — Phase 18 outcome "
            "tracking and analytics over spot signals."
        ),
        epilog=(
            "Spot BUY_SIGNAL only. No SELL, SHORT, or trading. Phase 19 requires explicit approval."
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.parse_args(argv)

    print("Professional Halal SMC/ICT Spot Signal Bot")
    print("Phase 18: outcome tracking and analytics over spot signals.")
    print("Market data and analysis are available through the Python API.")
    print("Spot BUY_SIGNAL publications only; no SELL, SHORT, or trading.")
    return 0
