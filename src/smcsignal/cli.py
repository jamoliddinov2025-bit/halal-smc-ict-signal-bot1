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
            "Professional Halal SMC/ICT Spot Signal Bot — Phase 22 strategy-intelligence "
            "research reporting over the unchanged pipeline."
        ),
        epilog=(
            "Spot BUY_SIGNAL only. No SELL, SHORT, or trading. "
            "Stop after Phase 22. Phase 23 requires explicit approval."
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.parse_args(argv)

    print("Professional Halal SMC/ICT Spot Signal Bot")
    print(
        "Phase 22: deterministic strategy-intelligence research reporting over "
        "the unchanged pipeline."
    )
    print("Market data and analysis are available through the Python API.")
    print("Spot BUY_SIGNAL publications only; no SELL, SHORT, or trading.")
    return 0
