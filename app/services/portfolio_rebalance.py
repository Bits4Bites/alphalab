from __future__ import annotations

import datetime
import html
import json
import re
from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_DOWN, ROUND_FLOOR, ROUND_HALF_UP, Decimal, InvalidOperation
from typing import NoReturn

from pydantic import ValidationError

from app.schemas import portfolio_rebalance as rebalance_schemas
from app.services import portfolio_market_data

MAX_HOLDINGS = 20
MAX_MONEY = Decimal("1000000000000")
SHARE_QUANTUM = Decimal("0.000001")
MONEY_QUANTUM = Decimal("0.01")
_NUMBER_PATTERN = re.compile(r"^(?:0|[1-9]\d*)(?:\.\d{1,6})?$")
TAX_CONTEXTS = {
    "unknown": "Unknown / not specified",
    "taxable": "Taxable account",
    "tax-advantaged": "Tax-advantaged account",
}


class RebalanceError(ValueError):
    pass


class RebalanceInputError(RebalanceError):
    pass


class RebalanceRecommendationError(RebalanceError):
    pass


class RebalanceCalculationError(RebalanceError):
    pass


@dataclass(frozen=True)
class Holding:
    line_number: int
    ticker: str
    quantity: Decimal
    average_cost: Decimal | None


@dataclass(frozen=True)
class RebalanceSettings:
    available_cash: Decimal
    fractional_shares: bool
    minimum_trade_amount: Decimal
    tax_context: str


@dataclass(frozen=True)
class SnapshotPosition:
    holding: Holding
    quote: portfolio_market_data.MarketQuote
    market_value: Decimal
    weight_pct: Decimal


@dataclass(frozen=True)
class PortfolioSnapshot:
    positions: tuple[SnapshotPosition, ...]
    available_cash: Decimal
    holdings_value: Decimal
    total_value: Decimal


@dataclass(frozen=True)
class _SecurityState:
    ticker: str
    current_quantity: Decimal
    average_cost: Decimal | None
    price: Decimal
    current_value: Decimal
    current_weight_pct: Decimal
    target_weight_pct: Decimal
    target_value: Decimal
    role: str
    rationale: str


@dataclass(frozen=True)
class _TradeChoice:
    quantity: Decimal
    tracking_error: Decimal


def _parse_decimal(value: str, *, label: str, allow_zero: bool) -> Decimal:
    normalized = value.strip()
    if not _NUMBER_PATTERN.fullmatch(normalized):
        raise RebalanceInputError(f"{label} must be a non-negative number with up to 6 decimal places.")
    try:
        parsed = Decimal(normalized)
    except InvalidOperation as exc:
        raise RebalanceInputError(f"{label} is not a valid number.") from exc
    if not parsed.is_finite() or parsed > MAX_MONEY or (parsed == 0 and not allow_zero):
        qualifier = "greater than zero" if not allow_zero else f"no greater than {MAX_MONEY}"
        raise RebalanceInputError(f"{label} must be {qualifier}.")
    return parsed


def parse_holdings(value: str, market: portfolio_market_data.MarketDefinition) -> tuple[Holding, ...]:
    parsed: list[Holding] = []
    seen: dict[str, int] = {}

    for line_number, raw_line in enumerate(value.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        if len(parsed) >= MAX_HOLDINGS:
            raise RebalanceInputError(f"Portfolio analysis supports at most {MAX_HOLDINGS} holdings.")

        fields = [field.strip() for field in line.split(",")]
        if len(fields) not in {2, 3}:
            raise RebalanceInputError(
                f"Line {line_number}: use TICKER, QUANTITY, AVERAGE_COST (average cost is optional)."
            )

        raw_ticker, raw_quantity = fields[:2]
        if not raw_ticker:
            raise RebalanceInputError(f"Line {line_number}: ticker is required.")
        try:
            normalized_ticker = portfolio_market_data.normalize_symbol(raw_ticker, market)
        except portfolio_market_data.MarketSymbolError as exc:
            raise RebalanceInputError(f"Line {line_number}: {exc}") from exc

        if normalized_ticker in seen:
            raise RebalanceInputError(
                f"Line {line_number}: ticker {normalized_ticker} duplicates line {seen[normalized_ticker]}."
            )

        quantity = _parse_decimal(
            raw_quantity,
            label=f"Line {line_number} quantity",
            allow_zero=False,
        )
        average_cost = None
        if len(fields) == 3 and fields[2]:
            average_cost = _parse_decimal(
                fields[2],
                label=f"Line {line_number} average cost",
                allow_zero=True,
            )

        seen[normalized_ticker] = line_number
        parsed.append(
            Holding(
                line_number=line_number,
                ticker=normalized_ticker,
                quantity=quantity,
                average_cost=average_cost,
            )
        )

    if not parsed:
        raise RebalanceInputError("At least one holding is required in the format TICKER, QUANTITY, AVERAGE_COST.")
    return tuple(parsed)


def parse_settings(
    *,
    available_cash: str,
    fractional_shares: bool,
    minimum_trade_amount: str,
    tax_context: str,
) -> RebalanceSettings:
    cash = _parse_decimal(available_cash.strip() or "0", label="Available cash", allow_zero=True)
    minimum_trade = _parse_decimal(
        minimum_trade_amount.strip() or "0",
        label="Minimum trade amount",
        allow_zero=True,
    )
    normalized_tax_context = tax_context.strip().lower() or "unknown"
    if normalized_tax_context not in TAX_CONTEXTS:
        raise RebalanceInputError("Tax context is invalid.")
    return RebalanceSettings(
        available_cash=cash,
        fractional_shares=fractional_shares,
        minimum_trade_amount=minimum_trade,
        tax_context=normalized_tax_context,
    )


def build_snapshot(
    holdings: tuple[Holding, ...],
    quotes: dict[str, portfolio_market_data.MarketQuote],
    available_cash: Decimal,
) -> PortfolioSnapshot:
    position_values: list[tuple[Holding, portfolio_market_data.MarketQuote, Decimal]] = []
    for holding in holdings:
        quote = quotes.get(holding.ticker)
        if quote is None:
            raise RebalanceCalculationError(f"Market data is missing for {holding.ticker}.")
        position_values.append((holding, quote, holding.quantity * quote.price))

    holdings_value = sum((value for _, _, value in position_values), Decimal(0))
    total_value = holdings_value + available_cash
    if total_value <= 0:
        raise RebalanceCalculationError("Portfolio value must be greater than zero.")

    positions = tuple(
        SnapshotPosition(
            holding=holding,
            quote=quote,
            market_value=market_value,
            weight_pct=(market_value / total_value) * Decimal(100),
        )
        for holding, quote, market_value in position_values
    )
    return PortfolioSnapshot(
        positions=positions,
        available_cash=available_cash,
        holdings_value=holdings_value,
        total_value=total_value,
    )


def snapshot_prompt_data(snapshot: PortfolioSnapshot) -> dict[str, object]:
    return {
        "positions": [
            {
                "ticker": position.holding.ticker,
                "quantity": float(position.holding.quantity),
                "average_cost": (
                    float(position.holding.average_cost) if position.holding.average_cost is not None else None
                ),
                "current_price": float(position.quote.price),
                "market_value": float(position.market_value),
                "current_weight_pct": float(position.weight_pct),
            }
            for position in snapshot.positions
        ],
        "available_cash": float(snapshot.available_cash),
        "holdings_value": float(snapshot.holdings_value),
        "total_portfolio_value": float(snapshot.total_value),
    }


def _reject_json_constant(value: str) -> NoReturn:
    raise ValueError(f"Unsupported JSON constant: {value}")


def parse_recommendation(value: str) -> rebalance_schemas.TargetAllocationRecommendation:
    try:
        decoded = json.loads(value, parse_constant=_reject_json_constant)
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise RebalanceRecommendationError("The AI returned an invalid target-allocation response.") from exc

    try:
        return rebalance_schemas.TargetAllocationRecommendation.model_validate(decoded)
    except ValidationError as exc:
        raise RebalanceRecommendationError("The AI target allocation failed validation.") from exc


def normalize_recommendation(
    recommendation: rebalance_schemas.TargetAllocationRecommendation,
    market: portfolio_market_data.MarketDefinition,
) -> rebalance_schemas.TargetAllocationRecommendation:
    allocations: list[rebalance_schemas.TargetAllocation] = []
    seen: set[str] = set()

    for allocation in recommendation.allocations:
        if allocation.ticker == "CASH":
            normalized_ticker = "CASH"
        else:
            try:
                normalized_ticker = portfolio_market_data.normalize_symbol(allocation.ticker, market)
            except portfolio_market_data.MarketSymbolError as exc:
                raise RebalanceRecommendationError(
                    f"The AI recommended {allocation.ticker}, which does not match the selected market."
                ) from exc

        if normalized_ticker in seen:
            raise RebalanceRecommendationError(f"The AI returned duplicate target allocations for {normalized_ticker}.")
        seen.add(normalized_ticker)
        allocations.append(allocation.model_copy(update={"ticker": normalized_ticker}))

    return recommendation.model_copy(update={"allocations": allocations})


def recommendation_schema() -> dict[str, object]:
    return rebalance_schemas.TargetAllocationRecommendation.model_json_schema()


def recommended_security_tickers(
    recommendation: rebalance_schemas.TargetAllocationRecommendation,
) -> tuple[str, ...]:
    return tuple(allocation.ticker for allocation in recommendation.allocations if allocation.ticker != "CASH")


def _whole_choices(
    state: _SecurityState,
    *,
    sell: bool,
    minimum_trade_amount: Decimal,
) -> tuple[_TradeChoice, ...]:
    difference = state.current_value - state.target_value if sell else state.target_value - state.current_value
    ideal_quantity = max(Decimal(0), difference / state.price)
    lower = ideal_quantity.to_integral_value(rounding=ROUND_FLOOR)
    upper = ideal_quantity.to_integral_value(rounding=ROUND_CEILING)
    maximum = state.current_quantity.to_integral_value(rounding=ROUND_FLOOR) if sell else upper
    raw_quantities = {Decimal(0), min(lower, maximum), min(upper, maximum)}
    base_error = abs(state.current_value - state.target_value)
    choices: list[_TradeChoice] = []

    for quantity in raw_quantities:
        if quantity < 0:
            continue
        trade_value = quantity * state.price
        if quantity and trade_value < minimum_trade_amount:
            continue
        resulting_value = (
            (state.current_quantity - quantity) * state.price
            if sell
            else (state.current_quantity + quantity) * state.price
        )
        tracking_error = abs(resulting_value - state.target_value)
        if tracking_error <= base_error:
            choices.append(_TradeChoice(quantity=quantity, tracking_error=tracking_error))

    return tuple(sorted(choices, key=lambda choice: (choice.quantity, choice.tracking_error)))


def _choose_best(choices: tuple[_TradeChoice, ...]) -> _TradeChoice:
    return min(choices, key=lambda choice: (choice.tracking_error, choice.quantity))


def _select_whole_trades(
    states: list[_SecurityState],
    settings: RebalanceSettings,
    target_cash: Decimal,
) -> tuple[dict[str, Decimal], dict[str, Decimal], list[str]]:
    warnings: list[str] = []
    sell_choices: dict[str, tuple[_TradeChoice, ...]] = {}
    selected_sells: dict[str, _TradeChoice] = {}

    for state in states:
        if state.current_value <= state.target_value:
            continue
        choices = _whole_choices(state, sell=True, minimum_trade_amount=settings.minimum_trade_amount)
        sell_choices[state.ticker] = choices
        selected_sells[state.ticker] = _choose_best(choices)

    cash_after_sells = settings.available_cash + sum(
        selected_sells[ticker].quantity * next(state.price for state in states if state.ticker == ticker)
        for ticker in selected_sells
    )

    while cash_after_sells < target_cash:
        current_cash_error = abs(cash_after_sells - target_cash)
        upgrades: list[tuple[Decimal, str, _TradeChoice, Decimal]] = []
        for state in states:
            selected = selected_sells.get(state.ticker)
            if selected is None:
                continue
            for candidate in sell_choices[state.ticker]:
                if candidate.quantity <= selected.quantity:
                    continue
                extra_cash = (candidate.quantity - selected.quantity) * state.price
                error_increase = candidate.tracking_error - selected.tracking_error
                new_cash_error = abs(cash_after_sells + extra_cash - target_cash)
                total_error_change = error_increase + new_cash_error - current_cash_error
                if total_error_change < 0:
                    upgrades.append((total_error_change, state.ticker, candidate, extra_cash))

        if not upgrades:
            break
        _, ticker_value, candidate, extra_cash = min(
            upgrades,
            key=lambda item: (item[0], item[2].tracking_error, item[1]),
        )
        selected_sells[ticker_value] = candidate
        cash_after_sells += extra_cash

    selected_buys: dict[str, _TradeChoice] = {}
    buy_choices: dict[str, tuple[_TradeChoice, ...]] = {}
    for state in states:
        if state.current_value >= state.target_value:
            continue
        choices = _whole_choices(state, sell=False, minimum_trade_amount=settings.minimum_trade_amount)
        buy_choices[state.ticker] = choices
        selected_buys[state.ticker] = _choose_best(choices)

    buy_budget = max(Decimal(0), cash_after_sells - target_cash)

    def total_buy_cost() -> Decimal:
        return sum(
            selected_buys[ticker_value].quantity * next(state.price for state in states if state.ticker == ticker_value)
            for ticker_value in selected_buys
        )

    while total_buy_cost() > buy_budget:
        downgrades: list[tuple[Decimal, str, _TradeChoice]] = []
        for state in states:
            selected = selected_buys.get(state.ticker)
            if selected is None:
                continue
            cheaper = [choice for choice in buy_choices[state.ticker] if choice.quantity < selected.quantity]
            if not cheaper:
                continue
            candidate = max(cheaper, key=lambda choice: choice.quantity)
            cash_saved = (selected.quantity - candidate.quantity) * state.price
            error_increase = candidate.tracking_error - selected.tracking_error
            score = error_increase / cash_saved if cash_saved else Decimal("Infinity")
            downgrades.append((score, state.ticker, candidate))

        if not downgrades:
            raise RebalanceCalculationError("Whole-share trades could not be funded without overspending.")
        _, ticker_value, candidate = min(downgrades, key=lambda item: (item[0], item[1]))
        selected_buys[ticker_value] = candidate

    sells = {ticker_value: choice.quantity for ticker_value, choice in selected_sells.items()}
    buys = {ticker_value: choice.quantity for ticker_value, choice in selected_buys.items()}
    constrained = [
        state.ticker
        for state in states
        if state.current_value != state.target_value
        and sells.get(state.ticker, Decimal(0)) == 0
        and buys.get(state.ticker, Decimal(0)) == 0
    ]
    if constrained:
        warnings.append(
            "Whole-share or minimum-trade constraints left these positions unchanged: "
            + ", ".join(sorted(constrained))
            + "."
        )
    warnings.append("Whole-share quantities were selected to reduce tracking error without overspending.")
    return sells, buys, warnings


def _select_fractional_trades(
    states: list[_SecurityState],
    settings: RebalanceSettings,
    target_cash: Decimal,
) -> tuple[dict[str, Decimal], dict[str, Decimal], list[str]]:
    warnings: list[str] = []
    sells: dict[str, Decimal] = {}
    buys: dict[str, Decimal] = {}
    skipped: list[str] = []

    for state in states:
        difference = state.target_value - state.current_value
        if difference < 0:
            quantity = min(
                state.current_quantity,
                (abs(difference) / state.price).quantize(SHARE_QUANTUM, rounding=ROUND_HALF_UP),
            )
            if quantity * state.price < settings.minimum_trade_amount:
                quantity = Decimal(0)
                skipped.append(state.ticker)
            sells[state.ticker] = quantity
        elif difference > 0:
            quantity = (difference / state.price).quantize(SHARE_QUANTUM, rounding=ROUND_DOWN)
            if quantity * state.price < settings.minimum_trade_amount:
                quantity = Decimal(0)
                skipped.append(state.ticker)
            buys[state.ticker] = quantity

    cash_after_sells = settings.available_cash + sum(
        quantity * next(state.price for state in states if state.ticker == ticker_value)
        for ticker_value, quantity in sells.items()
    )
    buy_budget = max(Decimal(0), cash_after_sells - target_cash)
    total_buy_cost = sum(
        quantity * next(state.price for state in states if state.ticker == ticker_value)
        for ticker_value, quantity in buys.items()
    )
    if total_buy_cost > buy_budget and total_buy_cost > 0:
        desired_buys = buys
        buys = {ticker_value: Decimal(0) for ticker_value in desired_buys}
        remaining_budget = buy_budget
        candidates: list[tuple[Decimal, Decimal, _SecurityState, Decimal]] = []

        for state in states:
            quantity = desired_buys.get(state.ticker)
            if not quantity:
                continue
            cost = quantity * state.price
            base_error = abs(state.current_value - state.target_value)
            resulting_error = abs((state.current_quantity + quantity) * state.price - state.target_value)
            benefit = base_error - resulting_error
            if benefit > 0:
                candidates.append((benefit / cost, benefit, state, quantity))

        limited: list[str] = []
        for _, _, state, desired_quantity in sorted(
            candidates,
            key=lambda item: (-item[0], -item[1], item[2].ticker),
        ):
            desired_cost = desired_quantity * state.price
            if desired_cost <= remaining_budget:
                selected_quantity = desired_quantity
            else:
                selected_quantity = min(
                    desired_quantity,
                    (remaining_budget / state.price).quantize(SHARE_QUANTUM, rounding=ROUND_DOWN),
                )

            selected_cost = selected_quantity * state.price
            resulting_error = abs((state.current_quantity + selected_quantity) * state.price - state.target_value)
            base_error = abs(state.current_value - state.target_value)
            if selected_quantity and selected_cost >= settings.minimum_trade_amount and resulting_error < base_error:
                buys[state.ticker] = selected_quantity
                remaining_budget -= selected_cost
            else:
                limited.append(state.ticker)

        if limited:
            warnings.append(
                "Available cash and minimum-trade constraints limited purchases for: "
                + ", ".join(sorted(limited))
                + "."
            )

    if skipped:
        warnings.append(
            "Minimum-trade constraints skipped or reduced trades for: " + ", ".join(sorted(set(skipped))) + "."
        )
    warnings.append("Fractional-share quantities were rounded down to 6 decimal places where needed.")
    return sells, buys, warnings


def _tracking_error(
    states: list[_SecurityState],
    sells: dict[str, Decimal],
    buys: dict[str, Decimal],
    available_cash: Decimal,
    target_cash: Decimal,
) -> Decimal:
    cash_after = available_cash
    security_error = Decimal(0)

    for state in states:
        sell_quantity = sells.get(state.ticker, Decimal(0))
        buy_quantity = buys.get(state.ticker, Decimal(0))
        resulting_quantity = state.current_quantity - sell_quantity + buy_quantity
        if resulting_quantity < 0:
            return Decimal("Infinity")
        cash_after += (sell_quantity - buy_quantity) * state.price
        security_error += abs(resulting_quantity * state.price - state.target_value)

    if cash_after < 0:
        return Decimal("Infinity")
    return security_error + abs(cash_after - target_cash)


def _omit_non_improving_trades(
    states: list[_SecurityState],
    sells: dict[str, Decimal],
    buys: dict[str, Decimal],
    available_cash: Decimal,
    target_cash: Decimal,
) -> tuple[dict[str, Decimal], dict[str, Decimal], list[str]]:
    baseline_error = _tracking_error(states, {}, {}, available_cash, target_cash)
    selected_sells = dict(sells)
    selected_buys = dict(buys)
    selected_error = _tracking_error(
        states,
        selected_sells,
        selected_buys,
        available_cash,
        target_cash,
    )
    active = {
        ticker_value
        for ticker_value in set(selected_sells) | set(selected_buys)
        if selected_sells.get(ticker_value, Decimal(0)) or selected_buys.get(ticker_value, Decimal(0))
    }
    removed: list[str] = []

    while active and selected_error >= baseline_error:
        alternatives: list[tuple[Decimal, str, dict[str, Decimal], dict[str, Decimal]]] = []
        for ticker_value in active:
            candidate_sells = dict(selected_sells)
            candidate_buys = dict(selected_buys)
            candidate_sells[ticker_value] = Decimal(0)
            candidate_buys[ticker_value] = Decimal(0)
            alternatives.append(
                (
                    _tracking_error(
                        states,
                        candidate_sells,
                        candidate_buys,
                        available_cash,
                        target_cash,
                    ),
                    ticker_value,
                    candidate_sells,
                    candidate_buys,
                )
            )

        selected_error, removed_ticker, selected_sells, selected_buys = min(
            alternatives,
            key=lambda item: (item[0], item[1]),
        )
        active.remove(removed_ticker)
        removed.append(removed_ticker)

    warnings: list[str] = []
    if removed:
        warnings.append(
            "Trades that did not improve total portfolio tracking error were omitted: "
            + ", ".join(sorted(removed))
            + "."
        )
    return selected_sells, selected_buys, warnings


def _to_float(value: Decimal) -> float:
    return float(value)


def calculate_plan(
    snapshot: PortfolioSnapshot,
    recommendation: rebalance_schemas.TargetAllocationRecommendation,
    quotes: dict[str, portfolio_market_data.MarketQuote],
    market: portfolio_market_data.MarketDefinition,
    settings: RebalanceSettings,
) -> rebalance_schemas.RebalancePlan:
    allocations = {allocation.ticker: allocation for allocation in recommendation.allocations}
    target_cash_weight = Decimal(str(allocations["CASH"].target_weight_pct)) if "CASH" in allocations else Decimal(0)
    target_cash = snapshot.total_value * target_cash_weight / Decimal(100)

    current_by_ticker = {position.holding.ticker: position for position in snapshot.positions}
    ordered_tickers = [ticker_value for ticker_value in allocations if ticker_value != "CASH"]
    ordered_tickers.extend(ticker_value for ticker_value in current_by_ticker if ticker_value not in allocations)

    states: list[_SecurityState] = []
    for ticker_value in ordered_tickers:
        current = current_by_ticker.get(ticker_value)
        quote = quotes.get(ticker_value)
        if quote is None:
            raise RebalanceCalculationError(f"Market data is missing for {ticker_value}.")
        allocation = allocations.get(ticker_value)
        current_quantity = current.holding.quantity if current else Decimal(0)
        current_value = current.market_value if current else Decimal(0)
        current_weight = current.weight_pct if current else Decimal(0)
        target_weight = Decimal(str(allocation.target_weight_pct)) if allocation else Decimal(0)
        states.append(
            _SecurityState(
                ticker=ticker_value,
                current_quantity=current_quantity,
                average_cost=current.holding.average_cost if current else None,
                price=quote.price,
                current_value=current_value,
                current_weight_pct=current_weight,
                target_weight_pct=target_weight,
                target_value=snapshot.total_value * target_weight / Decimal(100),
                role=allocation.role if allocation else "Exit",
                rationale=allocation.rationale if allocation else "Not included in the proposed target allocation.",
            )
        )

    if settings.fractional_shares:
        sells, buys, warnings = _select_fractional_trades(states, settings, target_cash)
    else:
        sells, buys, warnings = _select_whole_trades(states, settings, target_cash)
    sells, buys, tracking_warnings = _omit_non_improving_trades(
        states,
        sells,
        buys,
        settings.available_cash,
        target_cash,
    )
    warnings.extend(tracking_warnings)

    sell_proceeds = sum(sells.get(state.ticker, Decimal(0)) * state.price for state in states)
    buy_cost = sum(buys.get(state.ticker, Decimal(0)) * state.price for state in states)
    cash_after = settings.available_cash + sell_proceeds - buy_cost
    if cash_after < 0:
        raise RebalanceCalculationError("Calculated trades would overspend available cash.")

    current_positions = [
        rebalance_schemas.CurrentPosition(
            ticker=position.holding.ticker,
            quantity=_to_float(position.holding.quantity),
            average_cost=(
                _to_float(position.holding.average_cost) if position.holding.average_cost is not None else None
            ),
            current_price=_to_float(position.quote.price),
            market_value=_to_float(position.market_value),
            current_weight_pct=_to_float(position.weight_pct),
        )
        for position in snapshot.positions
    ]

    proposed_positions: list[rebalance_schemas.ProposedPosition] = []
    trades: list[rebalance_schemas.RebalanceTrade] = []
    largest_after = Decimal(0)
    for state in states:
        sell_quantity = sells.get(state.ticker, Decimal(0))
        buy_quantity = buys.get(state.ticker, Decimal(0))
        resulting_quantity = state.current_quantity - sell_quantity + buy_quantity
        if resulting_quantity < 0:
            raise RebalanceCalculationError(f"Calculated trades would oversell {state.ticker}.")
        resulting_value = resulting_quantity * state.price
        resulting_weight = resulting_value / snapshot.total_value * Decimal(100)
        largest_after = max(largest_after, resulting_weight)

        if sell_quantity:
            action = "SELL" if state.target_weight_pct == 0 else "TRIM"
            trade_quantity = sell_quantity
        elif buy_quantity:
            action = "BUY"
            trade_quantity = buy_quantity
        else:
            action = "HOLD"
            trade_quantity = Decimal(0)

        proposed_positions.append(
            rebalance_schemas.ProposedPosition(
                ticker=state.ticker,
                target_weight_pct=_to_float(state.target_weight_pct),
                resulting_quantity=_to_float(resulting_quantity),
                resulting_value=_to_float(resulting_value),
                resulting_weight_pct=_to_float(resulting_weight),
                role=state.role,
                rationale=state.rationale,
            )
        )
        trades.append(
            rebalance_schemas.RebalanceTrade(
                ticker=state.ticker,
                action=action,
                current_quantity=_to_float(state.current_quantity),
                trade_quantity=_to_float(trade_quantity),
                resulting_quantity=_to_float(resulting_quantity),
                current_price=_to_float(state.price),
                estimated_trade_value=_to_float(trade_quantity * state.price),
                current_weight_pct=_to_float(state.current_weight_pct),
                target_weight_pct=_to_float(state.target_weight_pct),
                resulting_weight_pct=_to_float(resulting_weight),
            )
        )

    cash_weight_after = cash_after / snapshot.total_value * Decimal(100)
    proposed_positions.append(
        rebalance_schemas.ProposedPosition(
            ticker="CASH",
            target_weight_pct=_to_float(target_cash_weight),
            resulting_quantity=None,
            resulting_value=_to_float(cash_after),
            resulting_weight_pct=_to_float(cash_weight_after),
            role=allocations["CASH"].role if "CASH" in allocations else "Liquidity",
            rationale=(
                allocations["CASH"].rationale
                if "CASH" in allocations
                else "Residual cash after applying trade-size and share constraints."
            ),
        )
    )

    resulting_total = sum(Decimal(str(position.resulting_value)) for position in proposed_positions)
    if abs(resulting_total - snapshot.total_value) > MONEY_QUANTUM:
        raise RebalanceCalculationError("Calculated trades did not preserve portfolio value.")

    if cash_after + MONEY_QUANTUM < target_cash:
        warnings.append(
            "Trade constraints left cash below the target cash allocation; review the suggested trades before acting."
        )
    if any(position.holding.average_cost is not None for position in snapshot.positions):
        warnings.append("Average costs inform context only; tax lots and exact tax liabilities are not calculated.")
    warnings.extend(
        [
            "Prices are delayed snapshots and may differ from executable market prices.",
            "Fees, bid-ask spreads, slippage, and taxes are excluded from the calculations.",
        ]
    )

    largest_before = max((position.weight_pct for position in snapshot.positions), default=Decimal(0))
    market_data_at = max(quote.retrieved_at for quote in quotes.values())
    trades.sort(key=lambda trade: ({"SELL": 0, "TRIM": 1, "BUY": 2, "HOLD": 3}[trade.action], trade.ticker))

    return rebalance_schemas.RebalancePlan(
        generated_at=datetime.datetime.now(datetime.UTC),
        market_data_at=market_data_at,
        market_data_source="Yahoo Finance",
        market=market.code,
        market_name=market.name,
        currency=market.currency,
        tax_context=settings.tax_context,
        fractional_shares=settings.fractional_shares,
        minimum_trade_amount=_to_float(settings.minimum_trade_amount),
        total_portfolio_value=_to_float(snapshot.total_value),
        cash_before=_to_float(settings.available_cash),
        target_cash=_to_float(target_cash),
        cash_after=_to_float(cash_after),
        largest_position_before_pct=_to_float(largest_before),
        largest_position_after_pct=_to_float(largest_after),
        strategy_summary=recommendation.strategy_summary,
        current_positions=current_positions,
        proposed_positions=proposed_positions,
        trades=trades,
        risks=recommendation.risks,
        execution_guidance=recommendation.execution_guidance,
        tax_considerations=recommendation.tax_considerations,
        warnings=list(dict.fromkeys(warnings)),
    )


def _safe_text(value: str) -> str:
    return html.escape(value, quote=False).replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def _money(value: float, currency: str) -> str:
    return f"{currency} {value:,.2f}"


def _quantity(value: float) -> str:
    return f"{value:,.6f}".rstrip("0").rstrip(".")


def render_plan_markdown(plan: rebalance_schemas.RebalancePlan) -> str:
    lines = [
        "# Rebalance Plan",
        "",
        (
            f"Market: **{_safe_text(plan.market_name)}** | Currency: **{plan.currency}** | "
            f"Market data retrieved: **{plan.market_data_at.isoformat()}**"
        ),
        "",
        _safe_text(plan.strategy_summary),
        "",
        "## Portfolio Snapshot",
        "",
        "| Measure | Before | Proposed result |",
        "|---|---:|---:|",
        (f"| Cash | {_money(plan.cash_before, plan.currency)} | {_money(plan.cash_after, plan.currency)} |"),
        (f"| Largest position | {plan.largest_position_before_pct:.2f}% | {plan.largest_position_after_pct:.2f}% |"),
        f"| Total portfolio value | {_money(plan.total_portfolio_value, plan.currency)} | "
        f"{_money(plan.total_portfolio_value, plan.currency)} |",
        "",
        "## Proposed Trades",
        "",
        "| Action | Ticker | Current qty | Trade qty | Est. value | Current | Target | Result |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for trade in plan.trades:
        lines.append(
            f"| {trade.action} | {trade.ticker} | {_quantity(trade.current_quantity)} | "
            f"{_quantity(trade.trade_quantity)} | {_money(trade.estimated_trade_value, plan.currency)} | "
            f"{trade.current_weight_pct:.2f}% | {trade.target_weight_pct:.2f}% | "
            f"{trade.resulting_weight_pct:.2f}% |"
        )

    lines.extend(
        [
            "",
            "## Proposed Allocation",
            "",
            "| Ticker | Target | Result | Role | Rationale |",
            "|---|---:|---:|---|---|",
        ]
    )
    for position in plan.proposed_positions:
        lines.append(
            f"| {position.ticker} | {position.target_weight_pct:.2f}% | "
            f"{position.resulting_weight_pct:.2f}% | {_safe_text(position.role)} | "
            f"{_safe_text(position.rationale)} |"
        )

    sections = (
        ("Key Risks", plan.risks),
        ("Execution Guidance", plan.execution_guidance),
        ("Tax Considerations", plan.tax_considerations),
        ("Important Warnings", plan.warnings),
    )
    for heading, items in sections:
        if not items:
            continue
        lines.extend(["", f"## {heading}", ""])
        lines.extend(f"- {_safe_text(item)}" for item in items)

    lines.extend(
        [
            "",
            (
                "**Decision-support only:** Verify live prices, account rules, fees, taxes, and order details "
                "before making any trade."
            ),
        ]
    )
    return "\n".join(lines)


def cache_payload(
    content: str,
    plan: rebalance_schemas.RebalancePlan,
) -> dict[str, object]:
    return rebalance_schemas.RebalanceCachePayload(content=content, plan=plan).model_dump(mode="json")


def is_valid_cache_payload(value: object) -> bool:
    try:
        rebalance_schemas.RebalanceCachePayload.model_validate(value)
    except ValidationError:
        return False
    return True
