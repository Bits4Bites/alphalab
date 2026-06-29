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

    def test_requires_summary_table_with_minimum_columns(self) -> None:
        prompt = review_portfolio._PROMPT_TEMPLATE.format(
            investor_context="Current Holdings:\nAAPL 50%, MSFT 50%",
            scenario_context="- Scenario: (none provided)",
        )

        assert "summary table" in prompt
        assert "ticker, approximate allocation %" in prompt
        assert "approximate number of shares" in prompt
        assert "approximate cost" in prompt
        assert "role in the portfolio" in prompt
        assert "Yield Booster" in prompt
        assert "Defensive" in prompt
