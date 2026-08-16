# AI Flow Architecture Review

This internal file tracks the remaining architecture reviews for AlphaLab flows that invoke an AI model.

Remaining inventory:

- 11 reviewable flows
- 25 configured AI tasks used by these flows
- All remaining items are authenticated feature flows

## Remaining flows

1. **Compare Investments**
   - [ ] Reviewed
   - [ ] Implemented
   - Tasks: `COMPARE_INVESTMENTS_BUILD_PROMPT`, `COMPARE_INVESTMENTS_ANALYZE`,
     `COMPARE_INVESTMENTS_ANALYZE_SCENARIO`
   - Flow: validate candidates and quotes -> write research prompt -> run structured core research -> optionally repair
     invalid output -> optionally run and repair scenario research -> calculate rankings -> cache result

2. **IPO / Listing Event Analyzer**
   - [ ] Reviewed
   - [ ] Implemented
   - Tasks: `IPO_ANALYZER_BUILD_PROMPT`, `IPO_ANALYZER_ANALYZE`
   - Flow: validate inputs -> optionally convert an uploaded prospectus -> write research prompt -> attach prospectus
     content -> run web-enabled analysis -> cache result -> delete uploaded document

3. **Sector Rotation Radar**
   - [ ] Reviewed
   - [ ] Implemented
   - Tasks: `SECTOR_ROTATION_RADAR_BUILD_PROMPT`, `SECTOR_ROTATION_RADAR_ANALYZE`
   - Flow: validate market inputs -> write research prompt -> run web-enabled analysis -> cache result

4. **IPO Scanner**
   - [ ] Reviewed
   - [ ] Implemented
   - Tasks: `IPO_SCANNER_VALIDATE_MARKET`, `IPO_SCANNER_BUILD_DISCOVERY_PROMPT`, `IPO_SCANNER_DISCOVER`,
     `IPO_SCANNER_BUILD_VERIFY_PROMPT`, `IPO_SCANNER_VERIFY`
   - Flow: validate input -> AI-validate market -> write discovery prompt -> discover candidates with web search ->
     parse candidates -> write verification prompt -> verify candidates with web search -> cache result

5. **Draft Portfolio Intent**
   - [x] Reviewed
   - [x] Implemented
   - Status: Done
   - Task: `DRAFT_PORTFOLIO_INTENT`
   - Flow: validate and persist optional preferences -> deterministically clarify an empty request or generate one
     neutral structured draft -> optionally run one clarification follow-up -> validate and persist output -> choose
     Build or Review handoff with compatible fields

6. **Build Portfolio**
   - [ ] Reviewed
   - [ ] Implemented
   - Tasks: `BUILD_PORTFOLIO_BUILD_PROMPT`, `BUILD_PORTFOLIO_ANALYZE`
   - Flow: validate investor inputs -> write portfolio research prompt -> run web-enabled portfolio analysis -> cache result

7. **Review Portfolio**
    - [ ] Reviewed
    - [ ] Implemented
    - Tasks: `REVIEW_PORTFOLIO_BUILD_PROMPT`, `REVIEW_PORTFOLIO_ANALYZE`
    - Flow: validate holdings and investor context -> write review prompt -> run web-enabled portfolio review -> cache result

8. **Portfolio Rebalance Plan**
    - [ ] Reviewed
    - [ ] Implemented
    - Tasks: `REVIEW_PORTFOLIO_REBALANCE_BUILD_PROMPT`, `REVIEW_PORTFOLIO_REBALANCE_ANALYZE`
    - Flow: complete portfolio review -> fetch current quotes -> write allocation prompt -> generate structured target
      allocations -> validate proposed securities and prices -> calculate deterministic trades -> cache result

9. **Portfolio Action Briefing**
    - [ ] Reviewed
    - [ ] Implemented
    - Tasks: `PORTFOLIO_ACTION_BRIEFING_BUILD_PROMPT`, `PORTFOLIO_ACTION_BRIEFING_ANALYZE`
    - Flow: validate holdings and watchlist -> fetch current quotes -> write research prompt -> run structured web research ->
      optionally repair invalid output without web search -> rank and size actions deterministically

10. **Watchlist Monitor**
    - [ ] Reviewed
    - [ ] Implemented
    - Tasks: `WATCHLIST_MONITOR_BUILD_PROMPT`, `WATCHLIST_MONITOR_ANALYZE`
    - Flow: validate watchlist inputs -> write monitoring prompt -> run web-enabled analysis -> cache result

11. **Earnings Catalyst Tracker**
    - [ ] Reviewed
    - [ ] Implemented
    - Tasks: `EARNINGS_CATALYST_TRACKER_BUILD_PROMPT`, `EARNINGS_CATALYST_TRACKER_ANALYZE`
    - Flow: validate ticker and event inputs -> write catalyst prompt -> run web-enabled analysis -> cache result

## Review criteria

For each flow, assess:

- Whether AI stages should be introduced, removed, combined, reordered, or replaced with deterministic logic to
  improve the workflow
- Whether prompt-writing and analysis responsibilities are correctly separated
- Model capability, reasoning level, web-search policy, latency, and cost
- Input validation, prompt-injection boundaries, and sensitive-data handling
- Structured-output contracts, validation, repair behavior, and failure handling
- Caching, retries, timeouts, cancellation, idempotency, and cleanup
- Test coverage and observability across stage boundaries

## Agreed cross-cutting requirements

- Treat all user inputs and all AI-model outputs as untrusted data.
- Validate AI outputs before using them as input to another model or application stage.
- New AI stages may be introduced when they materially improve quality, safety, reliability, or efficiency; use the
  lowest-cost suitable model and account for added latency and trust boundaries.
- Sanitize AI-generated HTML before rendering it in the browser or using it in a print view.
- Use POST rather than query-string GET requests for AI streaming flows.
- Keep the current reasoning-based search-context and tool-call limits unless a later review explicitly changes them.
- Prefer focused improvements over broad technical infrastructure changes during this review cycle.
