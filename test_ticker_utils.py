import pytest

from ticker_utils import normalize_ticker_for_yfinance


@pytest.mark.parametrize(
    "original, expected",
    [
        ("BRK.B", "BRK-B"),
        ("bf.a", "BF-A"),
        ("HEI.C", "HEI-C"),
    ],
)
def test_share_class_suffixes_are_hyphenated(original, expected):
    assert normalize_ticker_for_yfinance(original) == expected


def test_london_exchange_suffix_remains_unchanged():
    assert normalize_ticker_for_yfinance("GLEN.L") == "GLEN.L"


def test_tokyo_numeric_ticker_remains_unchanged():
    assert normalize_ticker_for_yfinance("7203.T") == "7203.T"
