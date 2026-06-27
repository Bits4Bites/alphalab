"""Unit tests for the earnings catalyst tracker prompt builder."""

from app.routers import earnings_catalyst_tracker


class TestEarningsCatalystTrackerPrompt:
    def test_uses_default_target_market_and_event_focus_when_missing(self) -> None:
        prompt = earnings_catalyst_tracker._build_prompt_request(
            tickers="AAPL, MSFT",
            target_market="",
            event_focus="",
        )

        assert "- Tickers: AAPL, MSFT" in prompt
        assert "- Target market: US" in prompt
        assert "- Event focus: All upcoming earnings catalysts" in prompt
