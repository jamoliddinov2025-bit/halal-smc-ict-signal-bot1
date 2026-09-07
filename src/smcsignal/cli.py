"""Informational CLI for the project scaffold, not a signal engine."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from smcsignal import __version__


def main(argv: Sequence[str] | None = None) -> int:
    """Print scaffold status or standard help/version information."""
    parser = argparse.ArgumentParser(
        prog="smcsignal",
        description="Professional Halal SMC/ICT Spot Signal Bot — Phase 1 scaffold only.",
        epilog="No strategy logic is implemented. Phase 2 requires explicit approval.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.parse_args(argv)

    print("Professional Halal SMC/ICT Spot Signal Bot")
    print("Phase 1: project scaffold only.")
    print("No market data, signal generation, exchange connectivity, or order execution.")
    return 0
