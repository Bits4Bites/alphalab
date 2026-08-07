from __future__ import annotations

import asyncio
import datetime
import logging
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

import yfinance

from app.utils import ticker

logger = logging.getLogger(__name__)

MAX_QUOTE_SYMBOLS = 20
DEFAULT_QUOTE_TIMEOUT_SECONDS = 20.0
_SYMBOL_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9.-]{0,19}$")


class MarketConfigurationError(ValueError):
    pass


class MarketSymbolError(ValueError):
    pass


class MarketDataError(RuntimeError):
    pass


@dataclass(frozen=True)
class MarketDefinition:
    code: str
    name: str
    currency: str
    ticker_exchange: str
    symbol_suffix: str
    aliases: frozenset[str]
    input_exchanges: frozenset[str]
    quote_exchanges: frozenset[str]
    time_zone: str

    @property
    def label(self) -> str:
        return f"{self.name} ({self.currency})"


@dataclass(frozen=True)
class MarketQuote:
    ticker: str
    yahoo_symbol: str
    price: Decimal
    currency: str
    exchange: str
    retrieved_at: datetime.datetime
    asset_type: str = "stock"
    display_name: str = ""


_MARKETS = (
    MarketDefinition(
        code="US",
        name="United States",
        currency="USD",
        ticker_exchange="NASDAQ",
        symbol_suffix="",
        aliases=frozenset({"US", "USA", "UNITED STATES", "UNITED STATES OF AMERICA"}),
        input_exchanges=frozenset({"US", "NYSE", "NASDAQ", "AMEX"}),
        quote_exchanges=frozenset({"NMS", "NYQ", "NGM", "NCM", "ASE", "PCX", "BTS", "NASDAQ", "NYSE"}),
        time_zone="America/New_York",
    ),
    MarketDefinition(
        code="AU",
        name="Australia",
        currency="AUD",
        ticker_exchange="ASX",
        symbol_suffix=".AX",
        aliases=frozenset({"AU", "AUS", "AUSTRALIA", "ASX"}),
        input_exchanges=frozenset({"AU", "ASX"}),
        quote_exchanges=frozenset({"ASX"}),
        time_zone="Australia/Sydney",
    ),
)


def resolve_market(value: str) -> MarketDefinition | None:
    normalized = value.strip().upper()
    return next((market for market in _MARKETS if normalized in market.aliases), None)


def configured_markets(configured: set[str] | None) -> tuple[MarketDefinition, ...]:
    requested = configured or {market.code for market in _MARKETS}
    resolved_codes: set[str] = set()
    unsupported: list[str] = []

    for value in requested:
        market = resolve_market(value)
        if market is None:
            unsupported.append(value)
        else:
            resolved_codes.add(market.code)

    if unsupported:
        logger.warning(
            "Ignoring primary markets unsupported by market-data features: %s",
            ", ".join(sorted(str(value) for value in unsupported)),
        )
    if not resolved_codes:
        raise MarketConfigurationError("At least one supported primary market (US or AU) is required.")
    return tuple(market for market in _MARKETS if market.code in resolved_codes)


def resolve_configured_market(value: str, configured: set[str] | None) -> MarketDefinition:
    market = resolve_market(value)
    if market is None:
        raise MarketSymbolError(f"Target Market '{value}' is not supported.")

    available_codes = {definition.code for definition in configured_markets(configured)}
    if market.code not in available_codes:
        raise MarketSymbolError(f"Target Market '{value}' is not enabled.")
    return market


def normalize_symbol(value: str, market: MarketDefinition) -> str:
    normalized = value.strip().upper()
    if not normalized:
        raise MarketSymbolError("Ticker symbol is required.")

    if ":" in normalized:
        parts = normalized.split(":")
        if len(parts) != 2:
            raise MarketSymbolError(f"Ticker '{value}' has an invalid exchange prefix.")
        exchange, normalized = (part.strip() for part in parts)
        if exchange not in market.input_exchanges:
            raise MarketSymbolError(f"Ticker '{value}' does not match the {market.name} market.")

    known_suffixes = {definition.symbol_suffix for definition in _MARKETS if definition.symbol_suffix}
    matching_suffix = next((suffix for suffix in known_suffixes if normalized.endswith(suffix)), None)
    if matching_suffix:
        if matching_suffix != market.symbol_suffix:
            raise MarketSymbolError(f"Ticker '{value}' does not match the {market.name} market.")
        normalized = normalized[: -len(matching_suffix)]

    if normalized == "CASH" or not _SYMBOL_PATTERN.fullmatch(normalized):
        raise MarketSymbolError(f"Ticker '{value}' is not a valid market symbol.")
    return normalized


def to_yfinance_symbol(symbol: str, market: MarketDefinition) -> str:
    converted = ticker.to_yfinance_format(f"{market.ticker_exchange}:{symbol}")
    if converted is None:
        raise MarketConfigurationError(f"No Yahoo Finance mapping is configured for {market.name}.")
    return converted


def _positive_decimal(value: object, symbol: str) -> Decimal:
    if isinstance(value, bool):
        raise MarketDataError(f"No usable market price was returned for {symbol}.")
    try:
        price = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise MarketDataError(f"No usable market price was returned for {symbol}.") from exc
    if not price.is_finite() or price <= 0:
        raise MarketDataError(f"No usable market price was returned for {symbol}.")
    return price


def _fetch_quote_sync(symbol: str, market: MarketDefinition) -> MarketQuote:
    yahoo_symbol = to_yfinance_symbol(symbol, market)
    try:
        info = yfinance.Ticker(yahoo_symbol).info
    except (yfinance.exceptions.YFException, OSError, TypeError, ValueError) as exc:
        logger.warning("Failed to retrieve quote for %s: %s", yahoo_symbol, exc)
        raise MarketDataError(f"Market data is unavailable for {symbol}.") from exc

    if not isinstance(info, dict):
        raise MarketDataError(f"Market data is unavailable for {symbol}.")

    quote_type = str(info.get("quoteType", "")).upper()
    if quote_type not in {"EQUITY", "ETF"}:
        raise MarketDataError(f"Ticker {symbol} is not a supported stock or ETF.")

    currency = str(info.get("currency", "")).upper()
    if currency != market.currency:
        raise MarketDataError(
            f"Ticker {symbol} uses {currency or 'an unknown currency'}, not the required {market.currency}."
        )

    exchange = str(info.get("exchange", "")).upper()
    if exchange not in market.quote_exchanges:
        raise MarketDataError(f"Ticker {symbol} is not listed in the selected {market.name} market.")

    raw_price = info.get("currentPrice") or info.get("regularMarketPrice") or info.get("previousClose")
    return MarketQuote(
        ticker=symbol,
        yahoo_symbol=yahoo_symbol,
        price=_positive_decimal(raw_price, symbol),
        currency=currency,
        exchange=exchange,
        retrieved_at=datetime.datetime.now(datetime.UTC),
        asset_type="etf" if quote_type == "ETF" else "stock",
        display_name=str(info.get("longName") or info.get("shortName") or symbol),
    )


async def fetch_quotes(
    symbols: list[str] | tuple[str, ...],
    market: MarketDefinition,
    *,
    timeout_seconds: float = DEFAULT_QUOTE_TIMEOUT_SECONDS,
) -> dict[str, MarketQuote]:
    unique_symbols = tuple(dict.fromkeys(symbols))
    if not unique_symbols:
        return {}
    if len(unique_symbols) > MAX_QUOTE_SYMBOLS:
        raise MarketDataError(f"Market data supports at most {MAX_QUOTE_SYMBOLS} symbols per request.")
    if timeout_seconds <= 0:
        raise ValueError("Quote timeout must be greater than zero.")

    semaphore = asyncio.Semaphore(5)

    async def fetch_one(symbol: str) -> tuple[MarketQuote | None, str | None]:
        async with semaphore:
            try:
                quote = await asyncio.wait_for(
                    asyncio.to_thread(_fetch_quote_sync, symbol, market),
                    timeout=timeout_seconds,
                )
            except TimeoutError:
                return None, f"Market data timed out for {symbol}."
            except MarketDataError as exc:
                return None, str(exc)
            return quote, None

    results = await asyncio.gather(*(fetch_one(symbol) for symbol in unique_symbols))
    errors = [error for _, error in results if error]
    if errors:
        raise MarketDataError(" ".join(errors))
    return {quote.ticker: quote for quote, _ in results if quote is not None}
