from __future__ import annotations

import datetime
import json
import logging
import re
from decimal import ROUND_DOWN, Decimal
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from app.schemas import portfolio_action_briefing as briefing_schemas
from app.services import portfolio_market_data, portfolio_rebalance

MAX_WATCHLIST = 20
_WATCHLIST_SPLIT_PATTERN = re.compile(r"[\s,;]+")
_URGENCY_SCORES = {"today": 3, "this_week": 2, "this_month": 1, "this_quarter": 0}
_IMPACT_SCORES = {"high": 3, "medium": 2, "low": 1}
_CONFIDENCE_SCORES = {"high": 2, "medium": 1, "low": 0}
logger = logging.getLogger(__name__)


class BriefingError(ValueError):
    pass


class BriefingResearchError(BriefingError):
    def __init__(self, message: str, *, validation_issues: tuple[str, ...] = ()) -> None:
        super().__init__(message)
        self.validation_issues = validation_issues


def parse_watchlist(
    value: str,
    market: portfolio_market_data.MarketDefinition,
    *,
    holding_tickers: set[str],
) -> tuple[str, ...]:
    raw_symbols = [symbol for symbol in _WATCHLIST_SPLIT_PATTERN.split(value.strip()) if symbol]
    if len(raw_symbols) > MAX_WATCHLIST:
        raise BriefingError(f"Watchlist supports at most {MAX_WATCHLIST} tickers.")

    normalized: list[str] = []
    seen = set(holding_tickers)
    for raw_symbol in raw_symbols:
        try:
            symbol = portfolio_market_data.normalize_symbol(raw_symbol, market)
        except portfolio_market_data.MarketSymbolError as exc:
            raise BriefingError(str(exc)) from exc
        if symbol not in seen:
            normalized.append(symbol)
            seen.add(symbol)
    return tuple(normalized)


def _normalize_research_payload(
    decoded: object,
    *,
    allowed_tickers: set[str] | None,
    holding_tickers: set[str] | None,
) -> object:
    if not isinstance(decoded, dict):
        return decoded

    normalized = dict(decoded)
    raw_sources = normalized.get("sources")
    known_source_ids: set[str] = set()
    sources: list[object] = []
    if isinstance(raw_sources, list):
        for source in raw_sources:
            if not isinstance(source, dict):
                sources.append(source)
                continue
            source_id = source.get("id")
            if isinstance(source_id, str) and source_id in known_source_ids:
                continue
            if isinstance(source_id, str):
                known_source_ids.add(source_id)
            sources.append(source)
        normalized["sources"] = sources

    raw_actions = normalized.get("actions")
    actions: list[object] = []
    if isinstance(raw_actions, list):
        for raw_action in raw_actions:
            if not isinstance(raw_action, dict):
                actions.append(raw_action)
                continue
            action = dict(raw_action)
            ticker = action.get("ticker")
            if isinstance(ticker, str):
                ticker = ticker.strip().upper()
                action["ticker"] = ticker
            if allowed_tickers is not None and ticker not in allowed_tickers:
                continue

            action_name = action.get("action")
            if isinstance(action_name, str):
                action_name = action_name.strip().upper()
                action["action"] = action_name
            if holding_tickers is not None:
                if ticker not in holding_tickers and action_name in {"SELL", "TRIM", "HOLD"}:
                    action_name = "WATCH"
                    action["action"] = action_name
                elif ticker in holding_tickers and action_name == "BUY":
                    action_name = "HOLD"
                    action["action"] = action_name

            if action_name in {"HOLD", "WATCH"}:
                action["sizing_pct"] = None
            elif action_name in {"BUY", "SELL", "TRIM"} and action.get("sizing_pct") is None:
                action["action"] = "HOLD" if ticker in (holding_tickers or set()) else "WATCH"
                action["sizing_pct"] = None

            source_ids = action.get("source_ids")
            if isinstance(source_ids, list):
                action["source_ids"] = list(
                    dict.fromkeys(
                        source_id
                        for source_id in source_ids
                        if isinstance(source_id, str) and source_id in known_source_ids
                    )
                )
                if not action["source_ids"]:
                    continue
            actions.append(action)
        normalized["actions"] = actions

    raw_events = normalized.get("upcoming_events")
    events: list[object] = []
    if isinstance(raw_events, list):
        for raw_event in raw_events:
            if not isinstance(raw_event, dict):
                events.append(raw_event)
                continue
            event = dict(raw_event)
            ticker = event.get("ticker")
            if isinstance(ticker, str):
                ticker = ticker.strip().upper()
                event["ticker"] = ticker
            if allowed_tickers is not None and ticker is not None and ticker not in allowed_tickers:
                continue
            source_ids = event.get("source_ids")
            if isinstance(source_ids, list):
                event["source_ids"] = list(
                    dict.fromkeys(
                        source_id
                        for source_id in source_ids
                        if isinstance(source_id, str) and source_id in known_source_ids
                    )
                )
                if not event["source_ids"]:
                    continue
            events.append(event)
        normalized["upcoming_events"] = events

    return normalized


def parse_research(
    value: str,
    *,
    allowed_tickers: set[str] | None = None,
    holding_tickers: set[str] | None = None,
) -> briefing_schemas.BriefingResearch:
    try:
        decoded = json.loads(value, parse_constant=lambda constant: (_ for _ in ()).throw(ValueError(constant)))
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        issues = ("response: invalid JSON",)
        logger.warning("Invalid portfolio action briefing research: %s", issues[0])
        raise BriefingResearchError(
            "The AI returned an invalid action briefing.",
            validation_issues=issues,
        ) from exc

    try:
        normalized = _normalize_research_payload(
            decoded,
            allowed_tickers=allowed_tickers,
            holding_tickers=holding_tickers,
        )
        return briefing_schemas.BriefingResearch.model_validate(normalized)
    except ValidationError as exc:
        issues = tuple(
            f"{'.'.join(str(part) for part in error['loc']) or 'response'}: {error['msg']}"
            for error in exc.errors(include_url=False)
        )[:12]
        logger.warning("Invalid portfolio action briefing research: %s", "; ".join(issues))
        raise BriefingResearchError(
            "The AI returned an invalid action briefing.",
            validation_issues=issues,
        ) from exc


def validate_research_scope(
    research: briefing_schemas.BriefingResearch,
    *,
    allowed_tickers: set[str],
) -> briefing_schemas.BriefingResearch:
    returned_tickers = {action.ticker for action in research.actions}
    event_tickers = {item.ticker for item in research.upcoming_events if item.ticker is not None}
    unknown_tickers = (returned_tickers | event_tickers) - allowed_tickers
    if unknown_tickers:
        issue = f"actions and events contain unsubmitted tickers: {', '.join(sorted(unknown_tickers))}"
        logger.warning("Invalid portfolio action briefing scope: %s", issue)
        raise BriefingResearchError(
            "The AI returned actions or events for securities outside the submitted portfolio and watchlist.",
            validation_issues=(issue,),
        )
    return research


def research_schema() -> dict[str, object]:
    return briefing_schemas.BriefingResearch.model_json_schema(mode="serialization")


def quote_prompt_data(
    quotes: dict[str, portfolio_market_data.MarketQuote],
) -> list[dict[str, object]]:
    return [
        {
            "ticker": quote.ticker,
            "name": quote.display_name or quote.ticker,
            "asset_type": quote.asset_type,
            "price": float(quote.price),
            "currency": quote.currency,
            "retrieved_at": quote.retrieved_at.isoformat(),
        }
        for quote in quotes.values()
    ]


def _exposure_score(
    ticker: str,
    *,
    holding_values: dict[str, Decimal],
    holdings_value: Decimal,
) -> int:
    if ticker not in holding_values or holdings_value <= 0:
        return 0
    weight_pct = holding_values[ticker] / holdings_value * Decimal("100")
    return 2 if weight_pct >= Decimal("15") else 1


def _suggested_trade(
    action: briefing_schemas.ResearchAction,
    *,
    holdings: dict[str, portfolio_rebalance.Holding],
    quotes: dict[str, portfolio_market_data.MarketQuote],
    available_cash: Decimal,
    sizing_pct: Decimal | None,
) -> tuple[float | None, float | None]:
    if sizing_pct is None or action.ticker not in quotes:
        return None, None

    quote = quotes[action.ticker]
    sizing_ratio = sizing_pct / Decimal("100")
    if action.action == "BUY":
        trade_value = available_cash * sizing_ratio
        if trade_value <= 0:
            return None, None
        quantity = trade_value / quote.price
    elif action.action in {"SELL", "TRIM"} and action.ticker in holdings:
        quantity = holdings[action.ticker].quantity * sizing_ratio
        trade_value = quantity * quote.price
    else:
        return None, None

    quantity = quantity.quantize(Decimal("0.000001"), rounding=ROUND_DOWN)
    trade_value = trade_value.quantize(Decimal("0.01"), rounding=ROUND_DOWN)
    return float(quantity), float(trade_value)


def build_result(
    research: briefing_schemas.BriefingResearch,
    *,
    holdings: tuple[portfolio_rebalance.Holding, ...],
    quotes: dict[str, portfolio_market_data.MarketQuote],
    market: portfolio_market_data.MarketDefinition,
    horizon: str,
    available_cash: Decimal,
    now: datetime.datetime | None = None,
) -> briefing_schemas.BriefingResult:
    generated_at = now or datetime.datetime.now(datetime.UTC)
    holdings_by_ticker = {holding.ticker: holding for holding in holdings}
    holding_values = {holding.ticker: holding.quantity * quotes[holding.ticker].price for holding in holdings}
    holdings_value = sum(holding_values.values(), Decimal("0"))
    portfolio_value = holdings_value + available_cash

    ranked_actions: list[tuple[int, briefing_schemas.ResearchAction]] = []
    for action in research.actions:
        score = (
            _URGENCY_SCORES[action.urgency]
            + _IMPACT_SCORES[action.impact]
            + _CONFIDENCE_SCORES[action.confidence]
            + _exposure_score(
                action.ticker,
                holding_values=holding_values,
                holdings_value=holdings_value,
            )
        )
        ranked_actions.append((score, action))
    ranked_actions.sort(key=lambda item: (-item[0], item[1].ticker, item[1].action))

    buy_sizing_total = sum(
        (Decimal(str(action.sizing_pct)) for _, action in ranked_actions if action.action == "BUY"),
        Decimal("0"),
    )
    buy_sizing_factor = min(Decimal("1"), Decimal("100") / buy_sizing_total) if buy_sizing_total else Decimal("1")

    actions = []
    for priority, (_, action) in enumerate(ranked_actions, start=1):
        sizing_pct = Decimal(str(action.sizing_pct)) if action.sizing_pct is not None else None
        if sizing_pct is not None and action.action == "BUY":
            sizing_pct *= buy_sizing_factor
        quantity, estimated_value = _suggested_trade(
            action,
            holdings=holdings_by_ticker,
            quotes=quotes,
            available_cash=available_cash,
            sizing_pct=sizing_pct,
        )
        actions.append(
            briefing_schemas.BriefingAction(
                priority=priority,
                ticker=action.ticker,
                action=action.action,
                urgency=action.urgency,
                rationale=action.rationale,
                suggested_quantity=quantity,
                estimated_value=estimated_value,
            )
        )

    cutoff_days = {"today": 0, "7": 7, "14": 14, "30": 30, "90": 90}[horizon]
    start_date = generated_at.astimezone(ZoneInfo(market.time_zone)).date()
    cutoff_date = start_date + datetime.timedelta(days=cutoff_days)
    events = [
        briefing_schemas.BriefingEvent(
            date=event.date,
            ticker=event.ticker,
            title=event.title,
            description=event.description,
        )
        for event in research.upcoming_events
        if start_date <= event.date <= cutoff_date
    ]
    warnings = list(research.warnings)
    if buy_sizing_total > 100:
        warnings.append("Buy sizing was proportionally reduced to stay within available cash.")
    omitted_events = len(research.upcoming_events) - len(events)
    if omitted_events:
        warnings.append(f"{omitted_events} event(s) outside the selected horizon were omitted.")

    market_data_at = min(quote.retrieved_at for quote in quotes.values())
    priority_count = sum(1 for score, _ in ranked_actions if score >= 8)
    return briefing_schemas.BriefingResult(
        generated_at=generated_at,
        market_data_at=market_data_at,
        market=market.code,
        market_name=market.name,
        currency=market.currency,
        horizon=horizon,
        summary=briefing_schemas.BriefingSummary(
            headline=research.headline,
            portfolio_value=float(portfolio_value),
            cash_available=float(available_cash),
            priority_actions_count=priority_count,
            overall_stance=research.overall_stance,
            confidence=research.confidence,
        ),
        actions=actions,
        risks=research.risks,
        upcoming_events=events,
        sources=[
            briefing_schemas.BriefingSource(
                title=source.title,
                publisher=source.publisher,
                url=source.url,
                published_at=source.published_at,
            )
            for source in research.sources
        ],
        warnings=warnings[:10],
    )


def is_valid_result(value: object) -> bool:
    try:
        briefing_schemas.BriefingResult.model_validate(value)
    except (TypeError, ValueError, ValidationError):
        return False
    return True
