"""Unit tests for the review portfolio prompt template."""

from app.routers import review_portfolio


class TestReviewPortfolioPrompt:
    def test_includes_scenario_stress_test_guidance(self) -> None:
        prompt = review_portfolio._PROMPT_TEMPLATE.format(
            investor_context="Current Holdings:\nAAPL 10 shares",
            scenario_context="- Scenario: rate hike shock",
        )

        assert "## Scenario stress test" in prompt
        assert "- Scenario: rate hike shock" in prompt
        assert "stress-test the portfolio under that scenario" in prompt
