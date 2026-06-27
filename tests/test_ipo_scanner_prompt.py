"""Unit tests for the IPO scanner multi-step prompt builders and parsing helpers."""

import json

from app.routers import ipo_scanner


class TestValidateMarketPrompt:
    def test_includes_target_market_and_json_contract(self) -> None:
        prompt = ipo_scanner._build_validate_market_request(target_market="Atlantis")

        assert "- Target market: Atlantis" in prompt
        assert '"valid"' in prompt
        assert '"normalized_market"' in prompt
        assert '"reason"' in prompt


class TestParseValidation:
    def test_parses_valid_true(self) -> None:
        data = ipo_scanner._parse_validation('{"valid": true, "normalized_market": "Australia"}')

        assert data is not None
        assert data["valid"] is True

    def test_parses_valid_false_with_prose(self) -> None:
        data = ipo_scanner._parse_validation('Result: {"valid": false, "reason": "gibberish"} done')

        assert data is not None
        assert data["valid"] is False
        assert data["reason"] == "gibberish"

    def test_returns_none_when_unparseable(self) -> None:
        assert ipo_scanner._parse_validation("not json") is None
        assert ipo_scanner._parse_validation('{"foo": "bar"}') is None


class TestCountUsableIpos:
    def test_counts_non_rejected_entries(self) -> None:
        payload = json.dumps(
            {
                "ipos": [
                    {"company_name": "A", "verification_status": "verified"},
                    {"company_name": "B", "verification_status": "unconfirmed"},
                    {"company_name": "C", "verification_status": "rejected"},
                ]
            }
        )

        assert ipo_scanner._count_usable_ipos(payload) == 2

    def test_returns_zero_for_empty_or_all_rejected(self) -> None:
        assert ipo_scanner._count_usable_ipos('{"ipos": []}') == 0
        assert ipo_scanner._count_usable_ipos('{"ipos": [{"verification_status": "rejected"}]}') == 0

    def test_returns_none_when_unparseable(self) -> None:
        assert ipo_scanner._count_usable_ipos("not json") is None
        assert ipo_scanner._count_usable_ipos('{"no_ipos": true}') is None


class TestDiscoveryPrompt:
    def test_includes_target_market(self) -> None:
        prompt = ipo_scanner._build_discovery_prompt_request(target_market="Australia")

        assert "- Target market: Australia" in prompt

    def test_is_lenient_wide_net_up_to_twenty(self) -> None:
        prompt = ipo_scanner._build_discovery_prompt_request(target_market="US")

        assert "up to 20 candidate events" in prompt
        assert "DISCOVERY pass" in prompt
        assert "do NOT return an empty list" in prompt
        assert "Do NOT analyze" in prompt

    def test_requests_structured_candidate_json(self) -> None:
        prompt = ipo_scanner._build_discovery_prompt_request(target_market="US")

        assert "valid JSON object" in prompt
        assert '"candidates"' in prompt
        assert '"company_name"' in prompt
        assert '"source_url"' in prompt
        assert '"price_range"' in prompt


class TestVerifyPrompt:
    def test_includes_market_and_candidate_count(self) -> None:
        prompt = ipo_scanner._build_verify_prompt_request(target_market="Australia", candidate_count=7)

        assert "- Target market: Australia" in prompt
        assert "Number of candidate events to verify: 7" in prompt

    def test_describes_verification_statuses(self) -> None:
        prompt = ipo_scanner._build_verify_prompt_request(target_market="US", candidate_count=3)

        assert "verified" in prompt
        assert "unconfirmed" in prompt
        assert "rejected" in prompt
        assert "day and month" in prompt
        assert "late 2027" in prompt

    def test_instructions_exclude_schema_and_candidates(self) -> None:
        # The low-cost model writes instructions only; the schema/candidates are appended later.
        prompt = ipo_scanner._build_verify_prompt_request(target_market="US", candidate_count=3)

        assert "appended" in prompt


class TestParseCandidates:
    def test_parses_plain_json(self) -> None:
        raw = json.dumps({"candidates": [{"company_name": "Acme"}, {"company_name": "Globex"}]})

        candidates = ipo_scanner._parse_candidates(raw)

        assert [c["company_name"] for c in candidates] == ["Acme", "Globex"]

    def test_parses_json_wrapped_in_prose(self) -> None:
        raw = 'Here are the results: {"candidates":[{"company_name":"Acme"}]} hope that helps'

        candidates = ipo_scanner._parse_candidates(raw)

        assert len(candidates) == 1
        assert candidates[0]["company_name"] == "Acme"

    def test_drops_entries_without_company_name(self) -> None:
        raw = json.dumps({"candidates": [{"company_name": "Acme"}, {"ticker": "X"}, {"company_name": ""}]})

        candidates = ipo_scanner._parse_candidates(raw)

        assert len(candidates) == 1

    def test_returns_empty_for_invalid_json(self) -> None:
        assert ipo_scanner._parse_candidates("not json at all") == []
        assert ipo_scanner._parse_candidates("") == []


class TestExecutableVerifyPrompt:
    def test_appends_candidates_and_schema(self) -> None:
        candidates = [{"company_name": "Acme", "exchange": "ASX"}]

        prompt = ipo_scanner._build_executable_verify_prompt(verify_instructions="VERIFY THESE", candidates=candidates)

        assert "VERIFY THESE" in prompt
        assert "Candidate events to verify" in prompt
        assert "Acme" in prompt
        assert "Required JSON output schema" in prompt
        assert '"verification_status"' in prompt
        assert '"ipos"' in prompt
