"""Informational CLI; market data fetching is an explicit Python API operation."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from smcsignal import __version__


def main(argv: Sequence[str] | None = None) -> int:
    """Print project status or standard help/version information."""
    parser = argparse.ArgumentParser(
        prog="smcsignal",
        description="Professional Halal SMC/ICT Spot Signal Bot — Phase 2 market data foundation.",
        epilog="No strategy logic is implemented. Phase 3 requires explicit approval.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.parse_args(argv)

    print("Professional Halal SMC/ICT Spot Signal Bot")
    print("Phase 2: market data foundation.")
    print("CSV replay and Binance public OHLCV are available through the Python data API.")
    print("No strategy logic, signals, or trading.")
    return 0
