import json
import logging
import time

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from sse_starlette.sse import EventSourceResponse

from app import config, dependencies, templating
from app.utils import ai

router = APIRouter(tags=["ipo_scanner"])
TEMPLATE = "ipo_scanner.html"
logger = logging.getLogger(__name__)

_CACHE_TTL_SECONDS = 60 * 60  # 1 hour
_CACHE_KEY_SUFFIX = "ipo_scanner:result"

# In-memory fallback used when Redis is not available (e.g. local dev without a
# Redis server). Per-process and non-persistent, but enough to keep a user's last
# scan result between page views. Maps cache key -> {..., "expires_at": float}.
_MEMORY_CACHE: dict[str, dict] = {}


def _user_cache_key(user: dict) -> str:
    """Build a per-user Redis cache key so users never see each other's results."""
    provider = ((user or {}).get("provider") or "unknown").strip() or "unknown"
    sub = ((user or {}).get("sub") or "anon").strip() or "anon"
    return f"{config.datastore_settings.key_prefix}{_CACHE_KEY_SUFFIX}:{provider}:{sub}"


async def _get_cached_result(user: dict) -> dict | None:
    """Return the last cached scan result for the user, or None if unavailable."""
    settings = config.datastore_settings
    key = _user_cache_key(user)

    if settings.redis_enabled and settings.redis_client:
        try:
            cached = await settings.redis_client.get(key)
            if cached:
                data = json.loads(cached)
                if data.get("content"):
                    return data
        except Exception as exc:
            logger.warning("Failed to read IPO scanner cache from Redis: %s", exc)

    # In-memory fallback.
    entry = _MEMORY_CACHE.get(key)
    if entry:
        if entry.get("expires_at", 0) < time.time():
            _MEMORY_CACHE.pop(key, None)
        elif entry.get("content"):
            return {k: v for k, v in entry.items() if k != "expires_at"}
    return None


async def _set_cached_result(user: dict, *, target_market: str, content: str) -> None:
    """Cache the final scan result for the user with a TTL."""
    settings = config.datastore_settings
    key = _user_cache_key(user)
    now = time.time()
    data = {"target_market": target_market, "content": content, "generated_at": now}

    if settings.redis_enabled and settings.redis_client:
        try:
            await settings.redis_client.set(key, json.dumps(data), ex=_CACHE_TTL_SECONDS)
            return
        except Exception as exc:
            logger.warning("Failed to write IPO scanner cache to Redis: %s", exc)

    # In-memory fallback.
    _MEMORY_CACHE[key] = {**data, "expires_at": now + _CACHE_TTL_SECONDS}


# ---------------------------------------------------------------------------
# Step 0 - Validation: low-cost AI checks whether the target market is a real,
# recognized market / region / exchange before any expensive premium calls.
# ---------------------------------------------------------------------------
_VALIDATE_MARKET_PROMPT_TEMPLATE = (
    "You are a financial markets expert. Determine whether the text below names a real, recognized\n"
    "stock market, country, region, or securities exchange where IPOs / new listings can occur.\n"
    "\n"
    "## Input\n"
    "- Target market: {target_market}\n"
    "\n"
    "## Instructions\n"
    "- Decide if this is a valid, recognized market/region/exchange for IPO events.\n"
    "- Accept countries (e.g. United States, Australia), regions (e.g. Europe, Southeast Asia, Global),\n"
    "  and specific exchanges (e.g. NYSE, NASDAQ, ASX, HKEX, LSE).\n"
    "- Reject gibberish, random characters, non-markets (e.g. a person's name, a food), or anything\n"
    "  that does not correspond to a real place or exchange where companies list shares.\n"
    "- Do NOT browse or research; rely on your own knowledge.\n"
    "\n"
    "## Output format\n"
    "Return ONLY a single valid JSON object - no markdown, no code fences, no commentary:\n"
    "{{\n"
    '  "valid": true | false,\n'
    '  "normalized_market": "<canonical market/exchange name, or null>",\n'
    '  "reason": "<short reason, required when valid is false>"\n'
    "}}"
)


# ---------------------------------------------------------------------------
# Step 1 - Discovery: low-cost AI writes a LENIENT prompt that asks the premium
# model to cast a wide net and surface up to 20 candidate upcoming IPO events.
# No strict filtering here - the goal is recall, not precision.
# ---------------------------------------------------------------------------
_DISCOVERY_PROMPT_TEMPLATE = (
    "You are an expert IPO research assistant and prompt engineer.\n"
    "\n"
    "Your task is to write a detailed, ready-to-execute prompt that instructs a premium AI model\n"
    "to DISCOVER upcoming IPO / new listing events for a market and return them as structured JSON\n"
    "that a downstream program will parse.\n"
    "\n"
    "## Prompt-writing role and constraints\n"
    "- You are only drafting the prompt for the premium AI model. Do not perform the scan yourself.\n"
    "- Do not browse, research, summarize, or recommend anything in your own response.\n"
    "- Return only one self-contained prompt that the premium model can execute without additional context.\n"
    "\n"
    "## IPO discovery request\n"
    "- Target market: {target_market}\n"
    "\n"
    "## Prompt-writing instructions\n"
    "Write a prompt that tells the premium model to:\n"
    "1. Use its web search capability to actively search for upcoming IPO / new listing events in the\n"
    "   target market. Most exchanges publish an official 'upcoming floats / upcoming listings / IPO\n"
    "   calendar' page (for example, the ASX 'upcoming floats and listings' page for Australia). Search\n"
    "   those official exchange pages, recent financial news, and broker IPO calendars.\n"
    "2. Cast a WIDE net: return up to 20 candidate events. This is a DISCOVERY pass, so be inclusive -\n"
    "   include an event even if some details are missing, tentative, or only approximately known.\n"
    "3. Do NOT discard a candidate just because its listing date is vague, its ticker is unknown, or\n"
    "   the offer details are incomplete. Capture whatever is known and use null for the rest.\n"
    "4. Do NOT analyze, rate, recommend, or give an opinion - capture factual information only.\n"
    "5. Only exclude an entry if you cannot identify a real company plausibly heading toward a listing\n"
    "   in this market. If official sources show events, do NOT return an empty list.\n"
    "\n"
    "## Required JSON output schema\n"
    "The prompt must instruct the premium model to return ONLY a single valid JSON object with this exact shape:\n"
    "{{\n"
    '  "target_market": "<the market that was scanned>",\n'
    '  "candidates": [\n'
    "    {{\n"
    '      "company_name": "<string, REQUIRED>",\n'
    '      "exchange": "<exchange it is expected to list on, or null>",\n'
    '      "ticker": "<expected ticker symbol, or null>",\n'
    '      "expected_listing_date": "<best-known listing date or period, or null>",\n'
    '      "public_offer_available": "yes" | "no" | "unknown",\n'
    '      "price_range": "<offer price or price range, or null>",\n'
    '      "source_url": "<URL of a source for this candidate, or null>",\n'
    '      "notes": "<any other known detail, or null>"\n'
    "    }}\n"
    "  ]\n"
    "}}\n"
    "\n"
    "## Output format rules the prompt must enforce on the premium model\n"
    "- Return ONLY the JSON object - no markdown, no code fences, no preamble, and no commentary\n"
    '- Include at most 20 entries in the "candidates" array\n'
    "- company_name is the only required field; use null (not empty strings) for anything unknown\n"
    "- Prefer official exchange and company sources over rumors, but include credible reported events\n"
    "\n"
    "## Output format\n"
    "Return ONLY the ready-to-execute prompt. No preamble, no explanation, no commentary, and no analysis.\n"
    "The prompt must be self-contained, the premium model will receive it with no other context.\n"
    "The premium model is NOT to include any suggested follow-up questions."
)


# ---------------------------------------------------------------------------
# Step 2 - Verification: low-cost AI writes verification INSTRUCTIONS. The
# candidate list and the strict output schema are appended deterministically so
# the schema cannot drift. The premium model re-checks each candidate against
# official sources and tags it verified / unconfirmed / rejected.
# ---------------------------------------------------------------------------
_VERIFY_PROMPT_TEMPLATE = (
    "You are an expert IPO research assistant and prompt engineer.\n"
    "\n"
    "Your task is to write a detailed, ready-to-execute prompt that instructs a premium AI model\n"
    "to VERIFY a provided list of candidate upcoming IPO events against official sources and return\n"
    "the verified results as structured JSON that a downstream program will parse.\n"
    "\n"
    "## Prompt-writing role and constraints\n"
    "- You are only drafting the verification instructions for the premium AI model. Do not perform\n"
    "  the verification yourself, and do not browse or research anything in your own response.\n"
    "- The downstream program will append the candidate list and the exact JSON output schema AFTER\n"
    "  your instructions, so do NOT invent your own schema or candidate data.\n"
    "- Return only the instruction text (no candidate data, no schema, no JSON).\n"
    "\n"
    "## Verification request\n"
    "- Target market: {target_market}\n"
    "- Number of candidate events to verify: {candidate_count}\n"
    "\n"
    "## Verification instructions the premium model must follow\n"
    "Write instructions that tell the premium model to, for EACH candidate provided below:\n"
    "1. Use its web search capability to confirm the event against official sources (the listing\n"
    "   exchange's upcoming-listings page, the company's investor-relations page, or an official\n"
    "   exchange / regulator announcement).\n"
    "2. Fill in and correct the minimum required fields from those official sources:\n"
    "   - company_name\n"
    "   - exchange the company will list on, AND the upcoming ticker symbol\n"
    "   - expected_listing_date specific to the day and month level (e.g. 2027-03-15 or 15 Mar 2027);\n"
    '     a vague value such as "late 2027", "Q1 2027", "H2 2026", "mid-2027", or a year only is\n'
    "     NOT acceptable as a verified date\n"
    "   - if there is a public offer: the offer_open_date and offer_close_date, plus any conditions\n"
    "   - source_url: a link to the official announcement or exchange upcoming-listings page\n"
    "3. Set verification_status for each candidate:\n"
    '   - "verified": all minimum required fields confirmed from an official source\n'
    '   - "unconfirmed": the event appears real but one or more minimum fields could not be confirmed\n'
    "     from an official source (still return it, filling whatever is known and null for the rest)\n"
    '   - "rejected": the event could not be substantiated at all, or is not a real upcoming listing\n'
    "4. Add a short verification_notes explaining what was confirmed or what is missing.\n"
    "5. Report factual information only - do NOT analyze, rate, recommend, or give an opinion.\n"
    "6. Do NOT drop a candidate to an empty result just because optional fields (sector, description,\n"
    "   price_range, offer_size) are unknown - set those to null and keep the event.\n"
    "\n"
    "## Output format\n"
    "Return ONLY the ready-to-execute verification instruction text. No preamble, no explanation, no\n"
    "candidate data, and no JSON schema (those are appended separately). The premium model is NOT to\n"
    "include any suggested follow-up questions."
)


# The strict final schema, appended deterministically to the executable verify prompt.
_FINAL_OUTPUT_SCHEMA = (
    "## Required JSON output schema\n"
    "Return ONLY a single valid JSON object with this exact shape - no markdown, no code fences,\n"
    "no preamble, and no commentary:\n"
    "{\n"
    '  "target_market": "<the market that was scanned>",\n'
    '  "ipos": [\n'
    "    {\n"
    '      "company_name": "<string, REQUIRED>",\n'
    '      "description": "<brief factual company info, or null>",\n'
    '      "sector": "<string or null>",\n'
    '      "exchange": "<exchange the company will list on, REQUIRED>",\n'
    '      "ticker": "<upcoming listed ticker symbol, REQUIRED>",\n'
    '      "expected_listing_date": "<specific calendar date at day/month level, REQUIRED>",\n'
    '      "public_offer_available": "yes" | "no",\n'
    '      "offer_open_date": "<application open date, REQUIRED if public offer, else null>",\n'
    '      "offer_close_date": "<application close date, REQUIRED if public offer, else null>",\n'
    '      "offer_conditions": "<special conditions, restrictions, or eligibility, or null>",\n'
    '      "price_range": "<offer price or price range, or null>",\n'
    '      "offer_size": "<total offer size or shares offered, or null>",\n'
    '      "source_url": "<URL of the official announcement or exchange listing page, REQUIRED>",\n'
    '      "verification_status": "verified" | "unconfirmed" | "rejected",\n'
    '      "verification_notes": "<short note on what was confirmed or what is missing>"\n'
    "    }\n"
    "  ]\n"
    "}\n"
    "\n"
    "## Output rules\n"
    "- Include one entry per candidate you were given, in the same order where possible\n"
    '- Set verification_status to "verified", "unconfirmed", or "rejected" for every entry\n'
    "- For verified entries, exchange, ticker, a day/month-specific expected_listing_date, and an\n"
    "  official source_url MUST be present\n"
    "- Use null (not empty strings) for any unknown optional value\n"
    "- Keep the output purely informational, with no analysis, scoring, or investment advice"
)


def _build_discovery_prompt_request(*, target_market: str) -> str:
    resolved_target_market = (target_market or "").strip()
    return _DISCOVERY_PROMPT_TEMPLATE.format(target_market=resolved_target_market)


def _build_verify_prompt_request(*, target_market: str, candidate_count: int) -> str:
    resolved_target_market = (target_market or "").strip()
    return _VERIFY_PROMPT_TEMPLATE.format(
        target_market=resolved_target_market,
        candidate_count=candidate_count,
    )


def _extract_json(text: str) -> str:
    """Return the first balanced {...} object found in text, stripping code fences."""
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        newline = cleaned.find("\n")
        if newline != -1:
            cleaned = cleaned[newline + 1 :]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()

    start = cleaned.find("{")
    if start == -1:
        return ""
    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(cleaned)):
        ch = cleaned[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return cleaned[start : i + 1]
    return ""


def _parse_candidates(discovery_output: str) -> list[dict]:
    """Parse the discovery JSON and return its candidate list (best effort)."""
    payload = _extract_json(discovery_output)
    if not payload:
        return []
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return []
    candidates = data.get("candidates")
    if not isinstance(candidates, list):
        return []
    return [c for c in candidates if isinstance(c, dict) and c.get("company_name")]


def _build_executable_verify_prompt(*, verify_instructions: str, candidates: list[dict]) -> str:
    candidates_json = json.dumps({"candidates": candidates}, ensure_ascii=False, indent=2)
    return (
        f"{verify_instructions.strip()}\n\n## Candidate events to verify\n{candidates_json}\n\n{_FINAL_OUTPUT_SCHEMA}"
    )


def _build_validate_market_request(*, target_market: str) -> str:
    resolved_target_market = (target_market or "").strip()
    return _VALIDATE_MARKET_PROMPT_TEMPLATE.format(target_market=resolved_target_market)


def _parse_validation(validation_output: str) -> dict | None:
    """Parse the market-validation JSON. Returns None if it cannot be parsed."""
    payload = _extract_json(validation_output)
    if not payload:
        return None
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or "valid" not in data:
        return None
    return data


def _count_usable_ipos(verify_output: str) -> int | None:
    """Count non-rejected IPO entries in the verify output.

    Returns None when the output cannot be parsed (caller should not stop early in
    that case), or an int count of entries whose verification_status is not 'rejected'.
    """
    payload = _extract_json(verify_output)
    if not payload:
        return None
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return None
    ipos = data.get("ipos")
    if not isinstance(ipos, list):
        return None
    usable = 0
    for ipo in ipos:
        if not isinstance(ipo, dict):
            continue
        status = str(ipo.get("verification_status") or "").strip().lower()
        if status == "rejected":
            continue
        usable += 1
    return usable


@router.get("/ipo-scanner", response_class=HTMLResponse)
async def ipo_scanner_page(
    request: Request,
    user: dict = Depends(dependencies.get_current_user),
) -> HTMLResponse:
    cached_result = await _get_cached_result(user)
    return templating.templates.TemplateResponse(request, TEMPLATE, {"user": user, "cached_result": cached_result})


@router.get("/ipo-scanner/stream")
async def ipo_scanner_stream(
    request: Request,
    target_market: str = Query(...),
    user: dict = Depends(dependencies.get_current_user),
) -> EventSourceResponse:
    async def event_generator():
        def progress(step: int, total: int, message: str):
            return json.dumps({"type": "progress", "step": step, "total": total, "message": message})

        def error(message: str):
            return json.dumps({"type": "error", "message": message})

        def result(content: str):
            return json.dumps({"type": "result", "content": content})

        total_steps = 7

        yield {"data": progress(1, total_steps, "Preparing IPO scan request...")}

        cleaned_target_market = (target_market or "").strip()
        if not cleaned_target_market:
            yield {"data": error("Target market is required.")}
            return

        # --- Step 0: low-cost AI validates the target market ---
        yield {"data": progress(2, total_steps, "Validating target market...")}
        validate_client = config.ai_task_settings.get_ai_client("IPO_SCANNER_VALIDATE_MARKET")
        if not validate_client:
            yield {"data": error("AI task 'IPO_SCANNER_VALIDATE_MARKET' is not configured.")}
            return
        validate_task = config.ai_task_settings.tasks.get("IPO_SCANNER_VALIDATE_MARKET")
        validate_request = _build_validate_market_request(target_market=cleaned_target_market)
        validate_result = await ai.execute_prompt(
            validate_client, validate_task.model, validate_request, temperature=validate_task.temperature
        )
        if not validate_result.success:
            yield {"data": error(f"Failed to validate target market: {validate_result.error}")}
            return
        validation = _parse_validation(validate_result.completion)
        # Only stop when the model explicitly reports the market as invalid; if the
        # response cannot be parsed we proceed rather than block a valid market.
        if validation is not None and validation.get("valid") is False:
            reason = (validation.get("reason") or "").strip()
            message = f"'{cleaned_target_market}' is not a recognized market or exchange."
            if reason:
                message = f"{message} {reason}"
            yield {"data": error(message)}
            return

        # --- Step 1: low-cost AI builds the discovery prompt ---
        yield {"data": progress(3, total_steps, "Generating discovery prompt...")}
        build_discovery_client = config.ai_task_settings.get_ai_client("IPO_SCANNER_BUILD_DISCOVERY_PROMPT")
        if not build_discovery_client:
            yield {"data": error("AI task 'IPO_SCANNER_BUILD_DISCOVERY_PROMPT' is not configured.")}
            return
        build_discovery_task = config.ai_task_settings.tasks.get("IPO_SCANNER_BUILD_DISCOVERY_PROMPT")
        discovery_prompt_request = _build_discovery_prompt_request(target_market=cleaned_target_market)
        discovery_prompt_result = await ai.execute_prompt(
            build_discovery_client,
            build_discovery_task.model,
            discovery_prompt_request,
            temperature=build_discovery_task.temperature,
        )
        if not discovery_prompt_result.success:
            yield {"data": error(f"Failed to generate discovery prompt: {discovery_prompt_result.error}")}
            return

        # --- Step 2: premium AI discovers candidate IPOs (wide net) ---
        yield {"data": progress(4, total_steps, "Discovering upcoming IPO candidates...")}
        discover_client = config.ai_task_settings.get_ai_client("IPO_SCANNER_DISCOVER")
        if not discover_client:
            yield {"data": error("AI task 'IPO_SCANNER_DISCOVER' is not configured.")}
            return
        discover_task = config.ai_task_settings.tasks.get("IPO_SCANNER_DISCOVER")
        discover_result = await ai.execute_prompt(
            discover_client,
            discover_task.model,
            discovery_prompt_result.completion,
            temperature=discover_task.temperature,
        )
        if not discover_result.success:
            yield {"data": error(f"Failed to discover IPOs: {discover_result.error}")}
            return

        candidates = _parse_candidates(discover_result.completion)
        if not candidates:
            # Stop early: discovery surfaced no candidate events.
            yield {"data": error(f"No upcoming IPO events were found for '{cleaned_target_market}'.")}
            return

        # --- Step 3: low-cost AI builds the verification prompt ---
        yield {"data": progress(5, total_steps, f"Preparing to verify {len(candidates)} candidate(s)...")}
        build_verify_client = config.ai_task_settings.get_ai_client("IPO_SCANNER_BUILD_VERIFY_PROMPT")
        if not build_verify_client:
            yield {"data": error("AI task 'IPO_SCANNER_BUILD_VERIFY_PROMPT' is not configured.")}
            return
        build_verify_task = config.ai_task_settings.tasks.get("IPO_SCANNER_BUILD_VERIFY_PROMPT")
        verify_prompt_request = _build_verify_prompt_request(
            target_market=cleaned_target_market, candidate_count=len(candidates)
        )
        verify_prompt_result = await ai.execute_prompt(
            build_verify_client,
            build_verify_task.model,
            verify_prompt_request,
            temperature=build_verify_task.temperature,
        )
        if not verify_prompt_result.success:
            yield {"data": error(f"Failed to generate verification prompt: {verify_prompt_result.error}")}
            return

        # --- Step 4: premium AI verifies the candidates against official sources ---
        yield {"data": progress(6, total_steps, "Verifying candidates against official sources...")}
        verify_client = config.ai_task_settings.get_ai_client("IPO_SCANNER_VERIFY")
        if not verify_client:
            yield {"data": error("AI task 'IPO_SCANNER_VERIFY' is not configured.")}
            return
        verify_task = config.ai_task_settings.tasks.get("IPO_SCANNER_VERIFY")
        executable_verify_prompt = _build_executable_verify_prompt(
            verify_instructions=verify_prompt_result.completion, candidates=candidates
        )
        verify_result = await ai.execute_prompt(
            verify_client, verify_task.model, executable_verify_prompt, temperature=verify_task.temperature
        )
        if not verify_result.success:
            yield {"data": error(f"Failed to verify IPOs: {verify_result.error}")}
            return

        # Stop early if verification produced no usable (non-rejected) events.
        usable_count = _count_usable_ipos(verify_result.completion)
        if usable_count == 0:
            yield {"data": error(f"No upcoming IPO events could be verified for '{cleaned_target_market}'.")}
            return

        yield {"data": progress(7, total_steps, "Scan complete!")}
        await _set_cached_result(user, target_market=cleaned_target_market, content=verify_result.completion)
        yield {"data": result(verify_result.completion)}

    return EventSourceResponse(event_generator())
