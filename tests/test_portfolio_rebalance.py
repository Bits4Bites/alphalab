import datetime
import json
import random
import types
from decimal import Decimal

import pytest

from app.schemas import portfolio_rebalance as rebalance_schemas
from app.services import portfolio_market_data, portfolio_rebalance


def _market(code: str = "US") -> portfolio_market_data.MarketDefinition:
    market = portfolio_market_data.resolve_market(code)
    assert market is not None
    return market


def _quote(ticker: str, price: str, market_code: str = "US") -> portfolio_market_data.MarketQuote:
    market = _market(market_code)
    return portfolio_market_data.MarketQuote(
        ticker=ticker,
        yahoo_symbol=portfolio_market_data.to_yfinance_symbol(ticker, market),
        price=Decimal(price),
        currency=market.currency,
        exchange=next(iter(market.quote_exchanges)),
        retrieved_at=datetime.datetime(2026, 7, 23, 1, 2, tzinfo=datetime.UTC),
    )


def _recommendation(
    allocations: list[dict[str, object]],
) -> rebalance_schemas.TargetAllocationRecommendation:
    return rebalance_schemas.TargetAllocationRecommendation.model_validate(
        {
            "strategy_summary": "Reduce concentration while retaining core exposure.",
            "allocations": allocations,
            "risks": ["Prices can move before execution."],
            "execution_guidance": ["Use limit orders where appropriate."],
            "tax_considerations": ["Review tax lots before selling."],
        }
    )


def test_configured_markets_are_canonical_and_ordered(caplog: pytest.LogCaptureFixture) -> None:
    markets = portfolio_market_data.configured_markets({"Australia", "USA", "LSE"})

    assert [market.code for market in markets] == ["US", "AU"]
    assert [market.currency for market in markets] == ["USD", "AUD"]
    assert "Ignoring primary markets unsupported" in caplog.text


def test_configured_markets_requires_at_least_one_supported_market() -> None:
    with pytest.raises(portfolio_market_data.MarketConfigurationError, match="at least one supported"):
        portfolio_market_data.configured_markets({"LSE"})


def test_parse_holdings_normalizes_market_symbols() -> None:
    holdings = portfolio_rebalance.parse_holdings(
        "CBA.AX, 100, 92.10\n\nBHP, 25",
        _market("AU"),
    )

    assert [(holding.ticker, holding.quantity, holding.average_cost) for holding in holdings] == [
        ("CBA", Decimal("100"), Decimal("92.10")),
        ("BHP", Decimal("25"), None),
    ]


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("CBA 100", "Line 1: use TICKER"),
        ("CBA, nope", "Line 1 quantity"),
        ("CBA, 1\nCBA.AX, 2", "duplicates line 1"),
        ("NASDAQ:AAPL, 1", "does not match the Australia market"),
    ],
)
def test_parse_holdings_returns_line_specific_errors(value: str, message: str) -> None:
    with pytest.raises(portfolio_rebalance.RebalanceInputError, match=message):
        portfolio_rebalance.parse_holdings(value, _market("AU"))


def test_parse_recommendation_rejects_non_finite_json() -> None:
    response = json.dumps(
        {
            "strategy_summary": "Summary",
            "allocations": [
                {
                    "ticker": "AAPL",
                    "target_weight_pct": 100,
                    "role": "Core",
                    "rationale": "Quality exposure",
                }
            ],
            "risks": ["Risk"],
            "execution_guidance": ["Guidance"],
            "tax_considerations": [],
        }
    ).replace("100", "NaN", 1)

    with pytest.raises(portfolio_rebalance.RebalanceRecommendationError, match="invalid"):
        portfolio_rebalance.parse_recommendation(response)


def test_normalize_recommendation_rejects_canonical_duplicates() -> None:
    recommendation = _recommendation(
        [
            {"ticker": "CBA", "target_weight_pct": 50, "role": "Core", "rationale": "Bank exposure"},
            {"ticker": "CBA.AX", "target_weight_pct": 50, "role": "Core", "rationale": "Duplicate"},
        ]
    )

    with pytest.raises(portfolio_rebalance.RebalanceRecommendationError, match="duplicate"):
        portfolio_rebalance.normalize_recommendation(recommendation, _market("AU"))


def test_calculate_plan_preserves_value_and_cash_target() -> None:
    market = _market()
    holdings = portfolio_rebalance.parse_holdings("AAA, 10, 80\nBBB, 10", market)
    quotes = {"AAA": _quote("AAA", "100"), "BBB": _quote("BBB", "50")}
    settings = portfolio_rebalance.parse_settings(
        available_cash="500",
        fractional_shares=False,
        minimum_trade_amount="0",
        tax_context="taxable",
    )
    snapshot = portfolio_rebalance.build_snapshot(holdings, quotes, settings.available_cash)
    recommendation = _recommendation(
        [
            {"ticker": "AAA", "target_weight_pct": 25, "role": "Core", "rationale": "Reduce concentration"},
            {"ticker": "BBB", "target_weight_pct": 25, "role": "Defensive", "rationale": "Retain exposure"},
            {"ticker": "CASH", "target_weight_pct": 50, "role": "Liquidity", "rationale": "Hold reserve"},
        ]
    )

    plan = portfolio_rebalance.calculate_plan(snapshot, recommendation, quotes, market, settings)

    assert plan.total_portfolio_value == 2000
    assert plan.cash_after == 1000
    assert plan.largest_position_before_pct == 50
    assert plan.largest_position_after_pct == 25
    assert sum(position.resulting_value for position in plan.proposed_positions) == pytest.approx(2000)
    trades = {trade.ticker: trade for trade in plan.trades}
    assert trades["AAA"].action == "TRIM"
    assert trades["AAA"].trade_quantity == 5
    assert trades["BBB"].action == "HOLD"


def test_whole_share_plan_does_not_take_trade_that_worsens_tracking_error() -> None:
    market = _market()
    holdings = portfolio_rebalance.parse_holdings("AAA, 1", market)
    quotes = {"AAA": _quote("AAA", "100")}
    settings = portfolio_rebalance.parse_settings(
        available_cash="0",
        fractional_shares=False,
        minimum_trade_amount="0",
        tax_context="unknown",
    )
    snapshot = portfolio_rebalance.build_snapshot(holdings, quotes, settings.available_cash)
    recommendation = _recommendation(
        [
            {"ticker": "AAA", "target_weight_pct": 60, "role": "Core", "rationale": "Retain majority exposure"},
            {"ticker": "CASH", "target_weight_pct": 40, "role": "Liquidity", "rationale": "Build reserve"},
        ]
    )

    plan = portfolio_rebalance.calculate_plan(snapshot, recommendation, quotes, market, settings)

    assert plan.trades[0].action == "HOLD"
    assert plan.trades[0].resulting_quantity == 1
    assert plan.cash_after == 0
    assert any("cash below the target" in warning for warning in plan.warnings)


def test_whole_share_plan_omits_sell_when_minimum_blocks_offsetting_buy() -> None:
    market = _market()
    holdings = portfolio_rebalance.parse_holdings("AAA, 3\nBBB, 20", market)
    quotes = {"AAA": _quote("AAA", "100"), "BBB": _quote("BBB", "10")}
    settings = portfolio_rebalance.parse_settings(
        available_cash="0",
        fractional_shares=False,
        minimum_trade_amount="70",
        tax_context="unknown",
    )
    snapshot = portfolio_rebalance.build_snapshot(holdings, quotes, settings.available_cash)
    recommendation = _recommendation(
        [
            {"ticker": "AAA", "target_weight_pct": 48, "role": "Core", "rationale": "Reduce slightly"},
            {"ticker": "BBB", "target_weight_pct": 52, "role": "Core", "rationale": "Increase slightly"},
        ]
    )

    plan = portfolio_rebalance.calculate_plan(snapshot, recommendation, quotes, market, settings)

    assert {trade.ticker: trade.action for trade in plan.trades} == {"AAA": "HOLD", "BBB": "HOLD"}
    assert plan.cash_after == 0
    assert any("tracking error were omitted" in warning for warning in plan.warnings)


def test_fractional_plan_funds_feasible_subset_instead_of_dropping_all_buys() -> None:
    market = _market()
    holdings = portfolio_rebalance.parse_holdings("AAA, 1.5", market)
    quotes = {
        "AAA": _quote("AAA", "100"),
        "BBB": _quote("BBB", "10"),
        "CCC": _quote("CCC", "10"),
    }
    settings = portfolio_rebalance.parse_settings(
        available_cash="100",
        fractional_shares=True,
        minimum_trade_amount="60",
        tax_context="unknown",
    )
    snapshot = portfolio_rebalance.build_snapshot(holdings, quotes, settings.available_cash)
    recommendation = _recommendation(
        [
            {"ticker": "AAA", "target_weight_pct": 40, "role": "Core", "rationale": "Reduce"},
            {"ticker": "BBB", "target_weight_pct": 30, "role": "Diversifier", "rationale": "Add"},
            {"ticker": "CCC", "target_weight_pct": 30, "role": "Diversifier", "rationale": "Add"},
        ]
    )

    plan = portfolio_rebalance.calculate_plan(snapshot, recommendation, quotes, market, settings)

    trades = {trade.ticker: trade for trade in plan.trades}
    assert trades["BBB"].action == "BUY"
    assert trades["BBB"].estimated_trade_value == 75
    assert trades["CCC"].action == "HOLD"
    assert plan.cash_after == 25
    assert any("limited purchases for: CCC" in warning for warning in plan.warnings)


@pytest.mark.parametrize("fractional_shares", [False, True])
def test_generated_plans_never_worsen_total_tracking_error(fractional_shares: bool) -> None:
    generator = random.Random(8675309)
    market = _market()

    for _ in range(100):
        quantities = {"AAA": generator.randint(1, 20), "BBB": generator.randint(1, 20)}
        prices = {"AAA": generator.randint(5, 200), "BBB": generator.randint(5, 200)}
        available_cash = generator.randint(0, 500)
        first_weight = generator.randint(1, 80)
        second_weight = generator.randint(1, 99 - first_weight)
        cash_weight = 100 - first_weight - second_weight
        target_weights = {"AAA": first_weight, "BBB": second_weight, "CASH": cash_weight}

        holdings = portfolio_rebalance.parse_holdings(
            f"AAA, {quantities['AAA']}\nBBB, {quantities['BBB']}",
            market,
        )
        quotes = {
            ticker: _quote(ticker, str(price))
            for ticker, price in prices.items()
        }
        settings = portfolio_rebalance.parse_settings(
            available_cash=str(available_cash),
            fractional_shares=fractional_shares,
            minimum_trade_amount=str(generator.randint(0, 150)),
            tax_context="unknown",
        )
        snapshot = portfolio_rebalance.build_snapshot(holdings, quotes, settings.available_cash)
        allocations = [
            {
                "ticker": ticker,
                "target_weight_pct": weight,
                "role": "Target",
                "rationale": "Generated invariant case",
            }
            for ticker, weight in target_weights.items()
            if weight
        ]
        plan = portfolio_rebalance.calculate_plan(
            snapshot,
            _recommendation(allocations),
            quotes,
            market,
            settings,
        )

        target_values = {
            ticker: snapshot.total_value * Decimal(weight) / Decimal(100)
            for ticker, weight in target_weights.items()
        }
        baseline_error = sum(
            abs(Decimal(quantities[ticker] * prices[ticker]) - target_values[ticker])
            for ticker in quantities
        ) + abs(Decimal(available_cash) - target_values["CASH"])
        resulting_values = {
            position.ticker: Decimal(str(position.resulting_value))
            for position in plan.proposed_positions
        }
        resulting_error = sum(
            abs(resulting_values[ticker] - target_values[ticker])
            for ticker in quantities
        ) + abs(resulting_values["CASH"] - target_values["CASH"])

        assert resulting_error <= baseline_error + Decimal("0.000001")
        assert plan.cash_after >= 0
        assert sum(position.resulting_value for position in plan.proposed_positions) == pytest.approx(
            plan.total_portfolio_value
        )


def test_market_quote_requires_matching_market_and_currency(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        portfolio_market_data.yfinance,
        "Ticker",
        lambda _symbol: types.SimpleNamespace(
            info={
                "quoteType": "EQUITY",
                "currency": "USD",
                "exchange": "NMS",
                "currentPrice": 190.25,
            }
        ),
    )

    quote = portfolio_market_data._fetch_quote_sync("AAPL", _market("US"))
    assert quote.price == Decimal("190.25")

    with pytest.raises(portfolio_market_data.MarketDataError, match="not the required AUD"):
        portfolio_market_data._fetch_quote_sync("AAPL", _market("AU"))
