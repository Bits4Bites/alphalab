import copy
import datetime
import json
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.schemas import investment_comparison as comparison_schemas
from app.services import investment_comparison, portfolio_market_data


def _market(code: str = "US") -> portfolio_market_data.MarketDefinition:
    market = portfolio_market_data.resolve_market(code)
    assert market is not None
    return market


def _quote(
    ticker: str,
    *,
    price: str = "100",
    asset_type: str = "stock",
) -> portfolio_market_data.MarketQuote:
    return portfolio_market_data.MarketQuote(
        ticker=ticker,
        yahoo_symbol=ticker,
        price=Decimal(price),
        currency="USD",
        exchange="NMS",
        retrieved_at=datetime.datetime(2020, 1, 2, 8, 0, tzinfo=datetime.UTC),
        asset_type=asset_type,
        display_name=f"{ticker} Investment",
    )


def _metrics(*, etf: bool, source_id: str) -> list[dict[str, object]]:
    metrics: list[dict[str, object]] = []
    for key in comparison_schemas.METRIC_KEYS:
        if key == "cost" and not etf:
            metrics.append(
                {
                    "key": key,
                    "label": "Fund expense ratio",
                    "display_value": "N/A",
                    "applicability": "not_applicable",
                    "as_of": None,
                    "source_ids": [],
                    "note": "Not applicable to an operating company.",
                }
            )
        else:
            metrics.append(
                {
                    "key": key,
                    "label": key.replace("_", " ").title(),
                    "display_value": "12.3",
                    "applicability": "applicable",
                    "as_of": "2020-01-02",
                    "source_ids": [source_id],
                    "note": "Comparable observation.",
                }
            )
    return metrics


def _candidate(
    ticker: str,
    *,
    asset_type: str,
    scores: tuple[int, int, int, int, int, int],
    confidence: str = "high",
    source_id: str,
) -> dict[str, object]:
    return {
        "ticker": ticker,
        "name": f"AI name for {ticker}",
        "asset_type": asset_type,
        "categories": [
            {
                "category": category,
                "score": score,
                "confidence": confidence,
                "summary": f"{category} assessment",
                "evidence": [f"{ticker} evidence for {category}"],
                "source_ids": [source_id],
            }
            for category, score in zip(comparison_schemas.CATEGORY_NAMES, scores, strict=True)
        ],
        "metrics": _metrics(etf=asset_type == "etf", source_id=source_id),
        "profile_suitability": [
            {
                "profile": profile,
                "rating": "fit",
                "summary": f"{ticker} is a considered fit for {profile} investors.",
            }
            for profile in comparison_schemas.PROFILE_NAMES
        ],
        "scenario": {
            "impact": "mixed",
            "resilience_score": 65,
            "summary": "The scenario creates both opportunities and risks.",
            "key_drivers": ["Demand sensitivity"],
            "source_ids": [source_id],
        },
        "strengths": ["Strong market position", "Healthy demand"],
        "risks": ["Valuation risk", "Macro sensitivity"],
        "catalysts": ["Upcoming earnings update"],
    }


def _research_data() -> dict[str, object]:
    return {
        "as_of": "2020-01-02T08:30:00+00:00",
        "methodology_summary": "Candidates were scored against fixed peer-relative category anchors.",
        "candidates": [
            _candidate(
                "AAPL",
                asset_type="stock",
                scores=(80, 90, 70, 60, 75, 65),
                confidence="medium",
                source_id="S1",
            ),
            _candidate(
                "QQQ",
                asset_type="etf",
                scores=(70, 80, 75, 85, 60, 80),
                source_id="S2",
            ),
        ],
        "sources": [
            {
                "id": "S1",
                "title": "Apple source",
                "publisher": "Example Publisher",
                "url": "https://example.com/apple",
                "published_at": "2020-01-01",
            },
            {
                "id": "S2",
                "title": "QQQ source",
                "publisher": "Example Publisher",
                "url": "https://example.com/qqq",
                "published_at": "2020-01-01",
            },
        ],
        "caveats": ["Market conditions can change after the stated as-of time."],
    }


def _research() -> comparison_schemas.ComparisonResearch:
    return comparison_schemas.ComparisonResearch.model_validate(_research_data())


def _core_research_data() -> dict[str, object]:
    data = copy.deepcopy(_research_data())
    for candidate in data["candidates"]:
        candidate.pop("scenario")
    return data


def _core_research() -> comparison_schemas.CoreComparisonResearch:
    return comparison_schemas.CoreComparisonResearch.model_validate(_core_research_data())


def _scenario_research_data(
    *,
    scenario: str = "Recession",
    assessed: bool = True,
) -> dict[str, object]:
    research = _research_data()
    candidates = []
    for candidate in research["candidates"]:
        assessment = copy.deepcopy(candidate["scenario"])
        if not assessed:
            assessment = {
                "impact": "not_assessed",
                "resilience_score": None,
                "summary": "No stress scenario was assessed.",
                "key_drivers": [],
                "source_ids": [],
            }
        candidates.append(
            {
                "ticker": candidate["ticker"],
                "scenario": assessment,
            }
        )
    return {
        "scenario": scenario,
        "as_of": research["as_of"],
        "candidates": candidates,
        "sources": research["sources"],
        "caveats": ["Scenario outcomes depend on the assumptions supplied."],
    }


def test_parse_tickers_normalizes_and_preserves_order() -> None:
    assert investment_comparison.parse_tickers(" cba.ax, BHP ", _market("AU")) == ("CBA", "BHP")
    assert investment_comparison.parse_tickers(
        "MSFT, AMZN, ORCL, GOOGL, AAPL",
        _market("US"),
    ) == ("MSFT", "AMZN", "ORCL", "GOOGL", "AAPL")


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("AAPL", "between 2 and 5"),
        ("AAPL,MSFT,NVDA,AMZN,GOOG,META", "between 2 and 5"),
        ("AAPL,AAPL", "listed more than once"),
        ("ASX:CBA,AAPL", "does not match"),
    ],
)
def test_parse_tickers_rejects_invalid_candidate_sets(value: str, message: str) -> None:
    with pytest.raises(investment_comparison.ComparisonInputError, match=message):
        investment_comparison.parse_tickers(value, _market())


def test_research_schema_requires_all_categories_metrics_and_profiles() -> None:
    data = _research_data()
    data["candidates"][0]["categories"].pop()

    with pytest.raises(ValidationError, match="categories"):
        comparison_schemas.ComparisonResearch.model_validate(data)


def test_core_research_contract_excludes_scenario_assessments() -> None:
    data = _core_research_data()
    comparison_schemas.CoreComparisonResearch.model_validate(data)
    data["candidates"][0]["scenario"] = _research_data()["candidates"][0]["scenario"]

    with pytest.raises(ValidationError, match="scenario"):
        comparison_schemas.CoreComparisonResearch.model_validate(data)


def test_ai_research_schemas_omit_unsupported_uri_format() -> None:
    for schema in (
        investment_comparison.core_research_schema(),
        investment_comparison.scenario_research_schema(),
    ):
        url_schema = schema["$defs"]["ResearchSource"]["properties"]["url"]
        assert url_schema == {
            "minLength": 1,
            "title": "Url",
            "type": "string",
        }
    category_sources = investment_comparison.core_research_schema()["$defs"]["CategoryAssessment"]["properties"][
        "source_ids"
    ]
    assert category_sources["minItems"] == 1
    assert category_sources["maxItems"] == 6


def test_research_source_still_rejects_non_http_url() -> None:
    source = comparison_schemas.ResearchSource.model_validate(
        {
            "id": "S1",
            "title": "Valid source",
            "publisher": "Example Publisher",
            "url": "https://example.com/research",
            "published_at": "2020-01-01",
        }
    )
    assert str(source.url) == "https://example.com/research"

    with pytest.raises(ValidationError, match="URL"):
        comparison_schemas.ResearchSource.model_validate(
            {
                "id": "S1",
                "title": "Invalid source",
                "publisher": "Example Publisher",
                "url": "javascript:alert(1)",
                "published_at": "2020-01-01",
            }
        )


def test_research_schema_rejects_unknown_source_reference() -> None:
    data = _research_data()
    data["candidates"][0]["categories"][0]["source_ids"] = ["missing"]

    with pytest.raises(ValidationError, match="unknown source"):
        comparison_schemas.ComparisonResearch.model_validate(data)


def test_parse_research_rejects_invalid_json() -> None:
    with pytest.raises(investment_comparison.ComparisonResearchError, match="invalid"):
        investment_comparison.parse_research("```json\n{}\n```")


def test_parse_core_research_logs_sanitized_validation_issues(
    caplog: pytest.LogCaptureFixture,
) -> None:
    data = _core_research_data()
    data["sources"][0]["url"] = "javascript:private-value"

    with caplog.at_level(
        "WARNING",
        logger="app.services.investment_comparison",
    ):
        with pytest.raises(
            investment_comparison.ComparisonResearchError,
            match="structured validation",
        ) as error:
            investment_comparison.parse_core_research(json.dumps(data))

    assert error.value.repairable is True
    assert error.value.validation_issues
    assert "sources.0.url" in caplog.text
    assert "URL scheme should be 'http' or 'https'" in caplog.text
    assert "private-value" not in caplog.text


def test_validate_research_requires_exact_tickers_and_asset_types() -> None:
    quotes = {"AAPL": _quote("AAPL"), "QQQ": _quote("QQQ", asset_type="etf")}

    validated = investment_comparison.validate_research(
        _research(),
        tickers=("AAPL", "QQQ"),
        quotes=quotes,
        market=_market(),
        scenario="Recession",
    )
    assert [candidate.name for candidate in validated.candidates] == [
        "AAPL Investment",
        "QQQ Investment",
    ]

    with pytest.raises(investment_comparison.ComparisonResearchError, match="exact requested"):
        investment_comparison.validate_research(
            _research(),
            tickers=("AAPL", "MSFT"),
            quotes={"AAPL": quotes["AAPL"], "MSFT": _quote("MSFT")},
            market=_market(),
            scenario="Recession",
        )

    mismatched_quotes = {"AAPL": _quote("AAPL", asset_type="etf"), "QQQ": quotes["QQQ"]}
    with pytest.raises(investment_comparison.ComparisonResearchError, match="asset type"):
        investment_comparison.validate_research(
            _research(),
            tickers=("AAPL", "QQQ"),
            quotes=mismatched_quotes,
            market=_market(),
            scenario="Recession",
        )


def test_validate_core_research_safely_removes_unknown_source_references(
    caplog: pytest.LogCaptureFixture,
) -> None:
    data = _core_research_data()
    first_candidate = data["candidates"][0]
    first_candidate["categories"][0]["source_ids"] = ["missing"]
    first_candidate["categories"][1]["source_ids"] = ["S1", "missing"]
    first_candidate["metrics"][0]["source_ids"] = ["missing"]
    core = comparison_schemas.CoreComparisonResearch.model_validate(data)
    quotes = {"AAPL": _quote("AAPL"), "QQQ": _quote("QQQ", asset_type="etf")}

    with caplog.at_level(
        "WARNING",
        logger="app.services.investment_comparison",
    ):
        validated = investment_comparison.validate_core_research(
            core,
            tickers=("AAPL", "QQQ"),
            quotes=quotes,
            market=_market(),
        )

    candidate = validated.candidates[0]
    assert candidate.categories[0].source_ids == []
    assert candidate.categories[0].confidence == "low"
    assert candidate.categories[0].score == 50
    assert candidate.categories[0].summary.startswith("Returned source unavailable; backend normalized this category")
    assert candidate.categories[1].source_ids == ["S1"]
    assert candidate.metrics[0].source_ids == []
    assert candidate.metrics[0].applicability == "unavailable"
    assert candidate.metrics[0].display_value == "Unavailable"
    assert "Removed 3 unresolved source reference(s)" in caplog.text

    combined = investment_comparison.combine_research(validated)
    result = investment_comparison.build_result(
        combined,
        tickers=("AAPL", "QQQ"),
        quotes=quotes,
        market=_market(),
        scenario="",
    )
    assert investment_comparison.is_valid_cache_payload(investment_comparison.cache_payload(result))


def test_validate_core_research_rejects_multiple_unsourced_categories() -> None:
    data = _core_research_data()
    data["candidates"][0]["categories"][0]["source_ids"] = ["missing-1"]
    data["candidates"][0]["categories"][1]["source_ids"] = ["missing-2"]
    core = comparison_schemas.CoreComparisonResearch.model_validate(data)

    with pytest.raises(
        investment_comparison.ComparisonResearchError,
        match="insufficient source coverage",
    ) as error:
        investment_comparison.validate_core_research(
            core,
            tickers=("AAPL", "QQQ"),
            quotes={
                "AAPL": _quote("AAPL"),
                "QQQ": _quote("QQQ", asset_type="etf"),
            },
            market=_market(),
        )

    assert error.value.repairable is True
    assert "2 categories without returned sources" in error.value.validation_issues[0]


def test_build_result_calculates_rankings_winners_and_profile_neutral_scores() -> None:
    quotes = {
        "AAPL": _quote("AAPL", price="210"),
        "QQQ": _quote("QQQ", price="520", asset_type="etf"),
    }
    research = investment_comparison.validate_research(
        _research(),
        tickers=("AAPL", "QQQ"),
        quotes=quotes,
        market=_market(),
        scenario="Recession",
    )

    result = investment_comparison.build_result(
        research,
        tickers=("AAPL", "QQQ"),
        quotes=quotes,
        market=_market(),
        scenario="Recession",
    )

    assert [(entry.ticker, entry.rank, entry.weighted_score) for entry in result.rankings] == [
        ("QQQ", 1, 75.0),
        ("AAPL", 2, 74.5),
    ]
    assert result.rankings[0].confidence == "high"
    assert result.rankings[1].confidence == "medium"
    valuation_winner = next(winner for winner in result.category_winners if winner.category == "valuation")
    assert valuation_winner.tickers == ["AAPL"]
    assert result.scenario == "Recession"
    assert [candidate.ticker for candidate in result.candidates] == ["QQQ", "AAPL"]


def test_build_result_assigns_shared_rank_for_equal_weighted_scores() -> None:
    data = _research_data()
    data["candidates"][1]["categories"] = data["candidates"][0]["categories"]
    research = comparison_schemas.ComparisonResearch.model_validate(data)
    quotes = {"AAPL": _quote("AAPL"), "QQQ": _quote("QQQ", asset_type="etf")}
    research = investment_comparison.validate_research(
        research,
        tickers=("AAPL", "QQQ"),
        quotes=quotes,
        market=_market(),
        scenario="Recession",
    )

    result = investment_comparison.build_result(
        research,
        tickers=("AAPL", "QQQ"),
        quotes=quotes,
        market=_market(),
        scenario="Recession",
    )

    assert [entry.rank for entry in result.rankings] == [1, 1]
    assert [entry.ticker for entry in result.rankings] == ["AAPL", "QQQ"]


def test_validate_research_requires_scenario_assessment_to_match_request() -> None:
    quotes = {"AAPL": _quote("AAPL"), "QQQ": _quote("QQQ", asset_type="etf")}

    with pytest.raises(investment_comparison.ComparisonResearchError, match="scenario assessments"):
        investment_comparison.validate_research(
            _research(),
            tickers=("AAPL", "QQQ"),
            quotes=quotes,
            market=_market(),
            scenario="",
        )

    data = _research_data()
    for candidate in data["candidates"]:
        candidate["scenario"] = {
            "impact": "not_assessed",
            "resilience_score": None,
            "summary": "No stress scenario was requested.",
            "key_drivers": [],
            "source_ids": [],
        }
    unassessed = comparison_schemas.ComparisonResearch.model_validate(data)
    with pytest.raises(investment_comparison.ComparisonResearchError, match="scenario assessments"):
        investment_comparison.validate_research(
            unassessed,
            tickers=("AAPL", "QQQ"),
            quotes=quotes,
            market=_market(),
            scenario="Recession",
        )

    validated = investment_comparison.validate_research(
        unassessed,
        tickers=("AAPL", "QQQ"),
        quotes=quotes,
        market=_market(),
        scenario="",
    )
    result = investment_comparison.build_result(
        validated,
        tickers=("AAPL", "QQQ"),
        quotes=quotes,
        market=_market(),
        scenario="",
    )

    assert result.scenario == ""
    assert investment_comparison.is_valid_cache_payload(investment_comparison.cache_payload(result))


def test_scenario_research_is_validated_and_merged_after_core_research() -> None:
    quotes = {"AAPL": _quote("AAPL"), "QQQ": _quote("QQQ", asset_type="etf")}
    core = investment_comparison.validate_core_research(
        _core_research(),
        tickers=("AAPL", "QQQ"),
        quotes=quotes,
        market=_market(),
    )
    scenario = comparison_schemas.ComparisonScenarioResearch.model_validate(_scenario_research_data())
    scenario = investment_comparison.validate_scenario_research(
        scenario,
        tickers=("AAPL", "QQQ"),
        scenario="Recession",
    )

    combined = investment_comparison.combine_research(core, scenario)

    assert [candidate.scenario.impact for candidate in combined.candidates] == [
        "mixed",
        "mixed",
    ]
    assert combined.as_of == scenario.as_of
    assert len(combined.sources) == 2
    assert combined.caveats[-1] == "Scenario outcomes depend on the assumptions supplied."


def test_scenario_research_rejects_unassessed_or_mismatched_scenario() -> None:
    unassessed = comparison_schemas.ComparisonScenarioResearch.model_validate(_scenario_research_data(assessed=False))
    with pytest.raises(investment_comparison.ComparisonResearchError, match="scenario assessments"):
        investment_comparison.validate_scenario_research(
            unassessed,
            tickers=("AAPL", "QQQ"),
            scenario="Recession",
        )

    mismatched = comparison_schemas.ComparisonScenarioResearch.model_validate(
        _scenario_research_data(scenario="Rate shock")
    )
    with pytest.raises(investment_comparison.ComparisonResearchError, match="does not match"):
        investment_comparison.validate_scenario_research(
            mismatched,
            tickers=("AAPL", "QQQ"),
            scenario="Recession",
        )


def test_build_result_does_not_create_false_tie_from_display_rounding() -> None:
    data = _research_data()
    baseline_scores = (70, 70, 70, 70, 70, 70)
    near_scores = (69, 70, 71, 70, 70, 70)
    for candidate, scores in zip(
        data["candidates"],
        (baseline_scores, near_scores),
        strict=True,
    ):
        for assessment, score in zip(candidate["categories"], scores, strict=True):
            assessment["score"] = score

    research = comparison_schemas.ComparisonResearch.model_validate(data)
    quotes = {"AAPL": _quote("AAPL"), "QQQ": _quote("QQQ", asset_type="etf")}
    research = investment_comparison.validate_research(
        research,
        tickers=("AAPL", "QQQ"),
        quotes=quotes,
        market=_market(),
        scenario="Recession",
    )
    result = investment_comparison.build_result(
        research,
        tickers=("AAPL", "QQQ"),
        quotes=quotes,
        market=_market(),
        scenario="Recession",
    )

    assert [(entry.ticker, entry.rank, entry.weighted_score) for entry in result.rankings] == [
        ("AAPL", 1, 70.0),
        ("QQQ", 2, 69.95),
    ]


def test_comparison_cache_payload_validates_round_trip() -> None:
    quotes = {"AAPL": _quote("AAPL"), "QQQ": _quote("QQQ", asset_type="etf")}
    research = investment_comparison.validate_research(
        _research(),
        tickers=("AAPL", "QQQ"),
        quotes=quotes,
        market=_market(),
        scenario="Recession",
    )
    result = investment_comparison.build_result(
        research,
        tickers=("AAPL", "QQQ"),
        quotes=quotes,
        market=_market(),
        scenario="Recession",
    )
    payload = investment_comparison.cache_payload(result)

    assert investment_comparison.is_valid_cache_payload(payload) is True
    assert investment_comparison.is_valid_cache_payload(json.loads(json.dumps(payload))) is True
    assert investment_comparison.is_valid_cache_payload({"result": {"market": "US"}}) is False

    corrupted = json.loads(json.dumps(payload))
    corrupted["result"]["rankings"][0]["weighted_score"] = 1
    assert investment_comparison.is_valid_cache_payload(corrupted) is False

    mismatched_scenario = json.loads(json.dumps(payload))
    mismatched_scenario["result"]["scenario"] = ""
    assert investment_comparison.is_valid_cache_payload(mismatched_scenario) is False

    excessively_unsourced = json.loads(json.dumps(payload))
    for category in excessively_unsourced["result"]["candidates"][0]["categories"][:2]:
        category["source_ids"] = []
        category["confidence"] = "low"
        category["score"] = 50
    assert investment_comparison.is_valid_cache_payload(excessively_unsourced) is False
