import pytest

from smcsignal.analysis import AnalysisConfigurationError
from smcsignal.analysis.mtf import timeframe_seconds
from smcsignal.analysis.mtf.timeframes import require_higher_multiple


@pytest.mark.parametrize(
    "timeframe,seconds",
    [
        ("1s", 1),
        ("1m", 60),
        ("3m", 180),
        ("5m", 300),
        ("15m", 900),
        ("30m", 1800),
        ("1h", 3600),
        ("2h", 7200),
        ("4h", 14400),
        ("6h", 21600),
        ("8h", 28800),
        ("12h", 43200),
        ("1d", 86400),
        ("3d", 259200),
        ("1w", 604800),
    ],
)
def test_fixed_supported_intervals_have_exact_utc_durations(timeframe, seconds):
    assert timeframe_seconds(timeframe) == seconds


@pytest.mark.parametrize("timeframe", ["1M", "15M", "2d", "", "1h "])
def test_month_and_unknown_intervals_are_rejected(timeframe):
    with pytest.raises(AnalysisConfigurationError):
        timeframe_seconds(timeframe)


@pytest.mark.parametrize(
    "primary,higher",
    [("15m", "1h"), ("15m", "4h"), ("5m", "15m"), ("1h", "4h"), ("1d", "1w")],
)
def test_strict_higher_integer_multiples_are_accepted(primary, higher):
    require_higher_multiple(primary, higher)


@pytest.mark.parametrize(
    "primary,higher",
    [
        ("15m", "15m"),
        ("1h", "15m"),
        ("4h", "1h"),
        ("1w", "3d"),
        ("3d", "1w"),
        ("1h", "1h"),
    ],
)
def test_equal_shorter_or_non_multiple_pairs_are_rejected(primary, higher):
    with pytest.raises(AnalysisConfigurationError):
        require_higher_multiple(primary, higher)


def test_30m_is_not_an_integer_multiple_of_4h():
    assert 14400 % 1800 == 0
    # 4h is a multiple of 30m; the rejected case above is the reverse direction.
    require_higher_multiple("30m", "4h")
    with pytest.raises(AnalysisConfigurationError):
        require_higher_multiple("4h", "30m")
