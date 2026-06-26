"""Unit tests for the watchlist monitor prompt builder."""

from app.routers import watchlist_monitor


class TestWatchlistMonitorPrompt:
    def test_uses_default_target_market_and_focus_when_missing(self) -> None:
        prompt = watchlist_monitor._build_prompt_request(
            tickers="AAPL, MSFT",
            target_market="",
            focus="",
        )

        assert "- Tickers: AAPL, MSFT" in prompt
        assert "- Target market: US" in prompt
        assert "- Focus: General watchlist review" in prompt
