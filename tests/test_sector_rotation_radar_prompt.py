"""Unit tests for the sector rotation radar prompt builder."""

from app.routers import sector_rotation_radar


class TestSectorRotationRadarPrompt:
    def test_uses_default_timeframes_when_missing(self) -> None:
        prompt = sector_rotation_radar._build_prompt_request(
            target_market="US",
            sectors="",
            timeframe="",
            bias="",
        )

        assert "- Target market: US" in prompt
        assert "- Sectors: All major sectors" in prompt
        assert "- Timeframe(s): next 1-2 weeks; next 1 month; next 3 months" in prompt
        assert "- Bias: No explicit bias" in prompt
