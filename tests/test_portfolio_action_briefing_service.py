import datetime
import json
from decimal import Decimal

import pytest

from app.schemas import portfolio_action_briefing as briefing_schemas
from app.services import portfolio_action_briefing, portfolio_market_data, portfolio_rebalance


def _market() -> portfolio_market_data.MarketDefinition:
    market = portfolio_market_data.resolve_market("US")
    assert market is not None
    return market


def test_parse_watchlist_deduplicates_holdings_and_symbols() -> None:
    assert portfolio_action_briefing.parse_watchlist(
        "MSFT, aapl; MSFT",
        _market(),
        holding_tickers={"AAPL"},
    ) == ("MSFT",)


@pytest.mark.parametrize(
    ("horizon", "event_date", "included"),
    [
        ("today", datetime.date(2026, 8, 7), True),
        ("today", datetime.date(2026, 8, 8), False),
        ("7", datetime.date(2026, 8, 14), True),
        ("14", datetime.date(2026, 8, 21), True),
        ("30", datetime.date(2026, 9, 6), True),
        ("90", datetime.date(2026, 11, 5), True),
    ],
)
def test_build_result_applies_action_horizon(
    horizon: str,
    event_date: datetime.date,
    included: bool,
) -> None:
    market = _market()
    retrieved_at = datetime.datetime(2026, 8, 7, 1, 0, tzinfo=datetime.UTC)
    holdings = (portfolio_rebalance.Holding(1, "AAPL", Decimal("1"), None),)
    quotes = {"AAPL": portfolio_market_data.MarketQuote("AAPL", "AAPL", Decimal("100"), "USD", "NMS", retrieved_at)}
    research = briefing_schemas.BriefingResearch(
        as_of=retrieved_at,
        headline="Monitor the portfolio.",
        overall_stance="Neutral",
        confidence="medium",
        actions=[
            briefing_schemas.ResearchAction(
                ticker="AAPL",
                action="WATCH",
                urgency="this_week",
                impact="medium",
                confidence="medium",
                rationale="Monitor.",
                sizing_pct=None,
                source_ids=["s1"],
            )
        ],
        risks=["Market risk"],
        upcoming_events=[
            briefing_schemas.ResearchEvent(
                date=event_date,
                ticker="AAPL",
                title="Portfolio event",
                description="Potentially relevant event.",
                source_ids=["s1"],
            )
        ],
        sources=[
            briefing_schemas.ResearchSource(
                id="s1",
                title="Source",
                publisher="Publisher",
                url="https://example.com",
                published_at=None,
            )
        ],
        warnings=[],
    )

    result = portfolio_action_briefing.build_result(
        research,
        holdings=holdings,
        quotes=quotes,
        market=market,
        horizon=horizon,
        available_cash=Decimal("0"),
        now=datetime.datetime(2026, 8, 7, 12, 0, tzinfo=datetime.UTC),
    )

    assert bool(result.upcoming_events) is included


def test_build_result_uses_exposure_in_priority_and_filters_events() -> None:
    market = _market()
    holdings = (
        portfolio_rebalance.Holding(1, "AAPL", Decimal("10"), None),
        portfolio_rebalance.Holding(2, "MSFT", Decimal("1"), None),
    )
    retrieved_at = datetime.datetime(2026, 8, 7, 1, 0, tzinfo=datetime.UTC)
    quotes = {
        "AAPL": portfolio_market_data.MarketQuote("AAPL", "AAPL", Decimal("100"), "USD", "NMS", retrieved_at),
        "MSFT": portfolio_market_data.MarketQuote("MSFT", "MSFT", Decimal("100"), "USD", "NMS", retrieved_at),
    }
    source = briefing_schemas.ResearchSource(
        id="s1",
        title="Source",
        publisher="Publisher",
        url="https://example.com",
        published_at=datetime.date(2026, 8, 7),
    )
    research = briefing_schemas.BriefingResearch(
        as_of=retrieved_at,
        headline="Review the concentrated position.",
        overall_stance="Cautious",
        confidence="high",
        actions=[
            briefing_schemas.ResearchAction(
                ticker="MSFT",
                action="HOLD",
                urgency="today",
                impact="high",
                confidence="high",
                rationale="Lower exposure.",
                sizing_pct=None,
                source_ids=["s1"],
            ),
            briefing_schemas.ResearchAction(
                ticker="AAPL",
                action="HOLD",
                urgency="this_week",
                impact="high",
                confidence="high",
                rationale="High exposure.",
                sizing_pct=None,
                source_ids=["s1"],
            ),
        ],
        risks=["Concentration"],
        upcoming_events=[
            briefing_schemas.ResearchEvent(
                date=datetime.date(2026, 8, 10),
                ticker="AAPL",
                title="Included",
                description="Within horizon",
                source_ids=["s1"],
            ),
            briefing_schemas.ResearchEvent(
                date=datetime.date(2026, 9, 10),
                ticker="MSFT",
                title="Excluded",
                description="Outside horizon",
                source_ids=["s1"],
            ),
        ],
        sources=[source],
        warnings=[],
    )

    result = portfolio_action_briefing.build_result(
        research,
        holdings=holdings,
        quotes=quotes,
        market=market,
        horizon="7",
        available_cash=Decimal("0"),
        now=datetime.datetime(2026, 8, 7, 2, 0, tzinfo=datetime.UTC),
    )

    assert [action.ticker for action in result.actions] == ["AAPL", "MSFT"]
    assert [event.title for event in result.upcoming_events] == ["Included"]
    assert result.warnings == ["1 event(s) outside the selected horizon were omitted."]


def test_research_requires_source_references() -> None:
    with pytest.raises(ValueError, match="unknown source"):
        briefing_schemas.BriefingResearch(
            as_of=datetime.datetime.now(datetime.UTC),
            headline="Headline",
            overall_stance="Neutral",
            confidence="medium",
            actions=[
                briefing_schemas.ResearchAction(
                    ticker="AAPL",
                    action="WATCH",
                    urgency="this_week",
                    impact="medium",
                    confidence="medium",
                    rationale="Monitor.",
                    sizing_pct=None,
                    source_ids=["missing"],
                )
            ],
            risks=["Risk"],
            upcoming_events=[],
            sources=[
                briefing_schemas.ResearchSource(
                    id="s1",
                    title="Source",
                    publisher="Publisher",
                    url="https://example.com",
                    published_at=None,
                )
            ],
            warnings=[],
        )


def test_parse_research_normalizes_safe_cross_field_defects() -> None:
    research = {
        "as_of": "2026-08-07T03:05:00Z",
        "headline": "Review current portfolio risks.",
        "overall_stance": "Cautious",
        "confidence": "high",
        "actions": [
            {
                "ticker": " aapl ",
                "action": "hold",
                "urgency": "this_week",
                "impact": "high",
                "confidence": "high",
                "rationale": "Maintain the position.",
                "sizing_pct": 25,
                "source_ids": ["s1", "missing", "s1"],
            },
            {
                "ticker": "NVDA",
                "action": "BUY",
                "urgency": "this_week",
                "impact": "medium",
                "confidence": "medium",
                "rationale": "Outside submitted scope.",
                "sizing_pct": 10,
                "source_ids": ["s1"],
            },
        ],
        "risks": ["Concentration risk."],
        "upcoming_events": [
            {
                "date": "2026-08-10",
                "ticker": "NVDA",
                "title": "Out-of-scope event",
                "description": "Must be omitted.",
                "source_ids": ["s1"],
            }
        ],
        "sources": [
            {
                "id": "s1",
                "title": "Source",
                "publisher": "Publisher",
                "url": "https://example.com",
                "published_at": None,
            },
            {
                "id": "s1",
                "title": "Duplicate",
                "publisher": "Publisher",
                "url": "https://example.com/duplicate",
                "published_at": None,
            },
        ],
        "warnings": [],
    }

    parsed = portfolio_action_briefing.parse_research(
        json.dumps(research),
        allowed_tickers={"AAPL"},
        holding_tickers={"AAPL"},
    )

    assert len(parsed.sources) == 1
    assert len(parsed.actions) == 1
    assert parsed.actions[0].ticker == "AAPL"
    assert parsed.actions[0].action == "HOLD"
    assert parsed.actions[0].sizing_pct is None
    assert parsed.actions[0].source_ids == ["s1"]
    assert parsed.upcoming_events == []


def test_build_result_scales_buy_actions_to_available_cash() -> None:
    market = _market()
    retrieved_at = datetime.datetime(2026, 8, 7, 1, 0, tzinfo=datetime.UTC)
    holdings = (portfolio_rebalance.Holding(1, "AAPL", Decimal("1"), None),)
    quotes = {
        ticker: portfolio_market_data.MarketQuote(ticker, ticker, Decimal("100"), "USD", "NMS", retrieved_at)
        for ticker in ("AAPL", "MSFT", "NVDA")
    }
    research = briefing_schemas.BriefingResearch(
        as_of=retrieved_at,
        headline="Deploy cash selectively.",
        overall_stance="Neutral",
        confidence="medium",
        actions=[
            briefing_schemas.ResearchAction(
                ticker=ticker,
                action="BUY",
                urgency="this_week",
                impact="medium",
                confidence="medium",
                rationale="Candidate.",
                sizing_pct=75,
                source_ids=["s1"],
            )
            for ticker in ("MSFT", "NVDA")
        ],
        risks=["Deployment risk"],
        upcoming_events=[],
        sources=[
            briefing_schemas.ResearchSource(
                id="s1",
                title="Source",
                publisher="Publisher",
                url="https://example.com",
                published_at=None,
            )
        ],
        warnings=[],
    )

    result = portfolio_action_briefing.build_result(
        research,
        holdings=holdings,
        quotes=quotes,
        market=market,
        horizon="7",
        available_cash=Decimal("1000"),
        now=datetime.datetime(2026, 8, 7, 2, 0, tzinfo=datetime.UTC),
    )

    assert sum(action.estimated_value or 0 for action in result.actions) == 1000
    assert "Buy sizing was proportionally reduced to stay within available cash." in result.warnings
