"""Unit tests for app.utils.ticker module."""

import pytest

from app.utils.ticker import EXCHANGE_TO_YFINANCE_SUFFIX, to_yfinance_format


class TestToYfinanceFormat:
    """Tests for to_yfinance_format()."""

    @pytest.mark.parametrize(
        "input_ticker,expected",
        [
            ("ASX:CBA", "CBA.AX"),
            ("NYSE:AAPL", "AAPL"),
            ("NASDAQ:MSFT", "MSFT"),
            ("LSE:BARC", "BARC.L"),
            ("TSX:RY", "RY.TO"),
            ("HKG:0005", "0005.HK"),
            ("HOSE:VNM", "VNM.VN"),
        ],
    )
    def test_known_exchanges(self, input_ticker: str, expected: str) -> None:
        assert to_yfinance_format(input_ticker) == expected

    def test_unknown_exchange_returns_none(self) -> None:
        assert to_yfinance_format("UNKNOWN:XYZ") is None

    def test_no_colon_returns_ticker_as_is(self) -> None:
        assert to_yfinance_format("AAPL") == "AAPL"
        assert to_yfinance_format("CBA.AX") == "CBA.AX"

    def test_case_insensitive_exchange(self) -> None:
        assert to_yfinance_format("asx:cba") == "CBA.AX"
        assert to_yfinance_format("Nyse:aapl") == "AAPL"

    def test_whitespace_stripped(self) -> None:
        assert to_yfinance_format("ASX : CBA ") == "CBA.AX"
        assert to_yfinance_format(" NYSE : AAPL") == "AAPL"

    def test_multiple_colons_splits_on_first(self) -> None:
        # Only splits on first colon
        result = to_yfinance_format("ASX:AB:CD")
        assert result == "AB:CD.AX"

    def test_empty_symbol(self) -> None:
        # Edge case: exchange present but symbol empty
        result = to_yfinance_format("ASX:")
        assert result == ".AX"

    def test_empty_string_no_colon(self) -> None:
        assert to_yfinance_format("") == ""


class TestExchangeMapping:
    """Verify the exchange mapping dict is well-formed."""

    def test_all_suffixes_start_with_dot_or_empty(self) -> None:
        for exchange, suffix in EXCHANGE_TO_YFINANCE_SUFFIX.items():
            assert suffix == "" or suffix.startswith("."), f"{exchange} has invalid suffix: {suffix}"

    def test_keys_are_uppercase(self) -> None:
        for exchange in EXCHANGE_TO_YFINANCE_SUFFIX:
            assert exchange == exchange.upper(), f"Key {exchange} should be uppercase"
