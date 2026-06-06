"""Unit tests for the analyze_ticker prompt builder."""

from app.routers import analyze_ticker


class TestBuildAnalysisPrompt:
    def test_includes_scenario_stress_test_guidance(self) -> None:
        info = {
            "longName": "Apple Inc.",
            "shortName": "Apple",
            "quoteType": "EQUITY",
            "sector": "Technology",
            "industry": "Consumer Electronics",
            "fullExchangeName": "NASDAQ",
            "marketCap": 2_500_000_000_000,
            "country": "United States",
        }

        prompt = analyze_ticker._build_analysis_prompt(
            ticker="AAPL",
            info=info,
            quick_mode=True,
            intent="Should I buy?",
            scenario="rate hike shock",
        )

        assert "- Intent:     Should I buy?" in prompt
        assert "- Scenario:   rate hike shock" in prompt
        assert "stress-test section" in prompt
