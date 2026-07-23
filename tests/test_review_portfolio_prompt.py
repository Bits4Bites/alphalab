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
        assert "recommendation" in prompt
        assert "role in the portfolio" in prompt
        assert "Yield Booster" in prompt
        assert "Defensive" in prompt
        assert "within the required Target Market" in prompt
        assert "approximate number of shares" in prompt
        assert "approximate cost" in prompt
        assert "estimated amounts, and in what order" in prompt

    def test_rebalance_review_defers_trade_calculations_to_backend(self) -> None:
        prompt = review_portfolio._REBALANCE_REVIEW_PROMPT_TEMPLATE.format(
            investor_context="Current Holdings:\nAAPL, 10, 100",
            scenario_context="- Scenario: (none provided)",
        )

        assert "Do not calculate exact trade quantities" in prompt
        assert "High-level transition priorities without exact quantities" in prompt
        assert "backend-generated trade plan" in prompt
        assert "approximate number of shares" not in prompt
        assert "approximate cost" not in prompt

    def test_rebalance_prompt_writer_only_builds_structured_target_prompt(self) -> None:
        prompt = review_portfolio._REBALANCE_PROMPT_TEMPLATE.format(
            market_name="Australia",
            market_code="AU",
            currency="AUD",
            risk_tolerance="Moderate",
            investment_goals="Capital growth",
            investment_horizon="Long-term",
            scenario="(not provided)",
            fractional_shares="not allowed",
            minimum_trade_amount="100",
            tax_context="Taxable account",
            snapshot_json='{"positions":[]}',
            review_content="# Review",
            schema_json='{"type":"object"}',
        )

        assert "Act only as a prompt writer" in prompt
        assert "Do not perform research, analysis, recommendations, or calculations" in prompt
        assert "Uses only securities listed in Australia" in prompt
        assert "Does not calculate current weights, target values, trade quantities" in prompt
        assert "return only JSON matching this schema" in prompt
        assert "Return ONLY the ready-to-execute prompt" in prompt
