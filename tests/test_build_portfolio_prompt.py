"""Unit tests for the Build Portfolio prompt template."""

from app.routers import build_portfolio


class TestBuildPortfolioPrompt:
    def _render(self) -> str:
        return build_portfolio._PROMPT_TEMPLATE.format(
            investor_profile="Risk Tolerance: aggressive",
            existing_holdings_instruction="",
        )

    def test_requires_summary_table(self) -> None:
        prompt = self._render()

        assert "portfolio summary table" in prompt
        assert "ticker, approximate allocation %" in prompt

    def test_summary_table_minimum_columns(self) -> None:
        prompt = self._render()

        assert "approximate number of shares" in prompt
        assert "approximate cost" in prompt
        assert "role in the portfolio" in prompt
        assert "Yield Booster" in prompt
        assert "Defensive" in prompt
