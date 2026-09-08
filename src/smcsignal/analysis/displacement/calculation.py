"""Exact finite-decimal gates and explicitly rounded descriptive ratios.

No price/ATR gate uses rounded division. Local contexts do not inherit ambient
precision, rounding, or traps. Resource bounds fail explicitly, never round an
otherwise exact comparison into a different classification.
"""

from __future__ import annotations

from decimal import (
    MAX_EMAX,
    MIN_EMIN,
    ROUND_HALF_EVEN,
    Context,
    Decimal,
    DecimalException,
    DivisionByZero,
    Inexact,
    InvalidOperation,
    Overflow,
    Underflow,
    localcontext,
)

from smcsignal.analysis.errors import AnalysisInputError

RATIO_PRECISION = 50
MAX_EXACT_DIGITS = 4096


def _digits(value: Decimal) -> tuple[int, int]:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise AnalysisInputError("displacement arithmetic requires finite Decimal values")
    if not value:
        return 1, 0
    _, digits, exponent = value.as_tuple()
    assert isinstance(exponent, int)
    end = len(digits)
    while digits[end - 1] == 0:
        end -= 1
        exponent += 1
    return end, exponent


def _context(precision: int, *, exact: bool = True) -> Context:
    if precision > MAX_EXACT_DIGITS:
        raise AnalysisInputError("displacement arithmetic exceeds the 4096-digit exact span limit")
    context = Context(
        prec=max(precision, 1),
        rounding=ROUND_HALF_EVEN,
        Emin=MIN_EMIN,
        Emax=MAX_EMAX,
        capitals=1,
        clamp=0,
        traps=[InvalidOperation, DivisionByZero, Overflow, Underflow],
    )
    context.traps[Inexact] = exact
    return context


def exact_sum(values: tuple[Decimal, ...]) -> Decimal:
    parts = [_digits(value) for value in values if value]
    if not parts:
        return Decimal(0)
    low = min(exponent for _, exponent in parts)
    high = max(digits + exponent for digits, exponent in parts)
    precision = high - low + len(str(len(parts))) + 1
    try:
        with localcontext(_context(precision)):
            return sum(values, Decimal(0))
    except DecimalException as exc:
        raise AnalysisInputError(
            "exact displacement arithmetic is outside the Decimal range"
        ) from exc


def difference(left: Decimal, right: Decimal) -> Decimal:
    return exact_sum((left, right.copy_negate()))


def product(left: Decimal, right: Decimal) -> Decimal:
    precision = _digits(left)[0] + _digits(right)[0]
    try:
        with localcontext(_context(precision)):
            return left * right
    except DecimalException as exc:
        raise AnalysisInputError(
            "exact displacement arithmetic is outside the Decimal range"
        ) from exc


def ratio(numerator: Decimal, denominator: Decimal) -> Decimal:
    """Descriptive Decimal ratio, 50 significant digits, half-even; never a gate."""
    _digits(numerator)
    _digits(denominator)
    if denominator <= 0:
        raise AnalysisInputError("ratio denominator must be positive")
    try:
        with localcontext(_context(RATIO_PRECISION, exact=False)):
            result = numerator / denominator
        if not result.is_finite() or (numerator != 0 and result == 0):
            raise AnalysisInputError("descriptive ratio is outside the supported Decimal range")
        return result
    except DecimalException as exc:
        raise AnalysisInputError(
            "descriptive ratio is outside the supported Decimal range"
        ) from exc


def true_range(high: Decimal, low: Decimal, previous_close: Decimal) -> Decimal:
    return max(
        difference(high, low),
        difference(high, previous_close).copy_abs(),
        difference(low, previous_close).copy_abs(),
    )
