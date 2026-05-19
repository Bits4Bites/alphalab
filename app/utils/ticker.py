"""Stock ticker format conversion utilities."""

# Mapping from exchange code to Yahoo Finance suffix
EXCHANGE_TO_YFINANCE_SUFFIX: dict[str, str] = {
    "ASX": ".AX",
    "NYSE": "",
    "NASDAQ": "",
    "LSE": ".L",
    "TSX": ".TO",
    "HKG": ".HK",
    "HOSE": ".VN",
}


def to_yfinance_format(ticker: str) -> str | None:
    """Convert a ticker from EXCHANGE:SYMBOL format to Yahoo Finance format.

    Examples:
        ASX:CBA -> CBA.AX
        NYSE:AAPL -> AAPL
        HOSE:VNM -> VNM.VN

    Returns None if the exchange is not recognized.
    """
    if ":" not in ticker:
        return ticker

    exchange, symbol = ticker.split(":", maxsplit=1)
    exchange = exchange.upper().strip()
    symbol = symbol.upper().strip()

    suffix = EXCHANGE_TO_YFINANCE_SUFFIX.get(exchange)
    if suffix is None:
        return None

    return f"{symbol}{suffix}"
