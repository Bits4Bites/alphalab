# AI Flow Architecture Review

This internal file tracks the remaining architecture reviews for AlphaLab flows that invoke an AI model.

Remaining inventory:

- 6 reviewable flows
- 16 configured AI tasks used by these flows
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

5. **Watchlist Monitor**
    - [ ] Reviewed
    - [ ] Implemented
    - Tasks: `WATCHLIST_MONITOR_BUILD_PROMPT`, `WATCHLIST_MONITOR_ANALYZE`
    - Flow: validate watchlist inputs -> write monitoring prompt -> run web-enabled analysis -> cache result

6. **Earnings Catalyst Tracker**
    - [ ] Reviewed
    - [ ] Implemented
    - Tasks: `EARNINGS_CATALYST_TRACKER_BUILD_PROMPT`, `EARNINGS_CATALYST_TRACKER_ANALYZE`
    - Flow: validate ticker and event inputs -> write catalyst prompt -> run web-enabled analysis -> cache result

## Review criteria

For each flow, assess:

- Expected output quality first. Cost and latency are secondary considerations and must not justify a design that is
  expected to produce lower-quality output.
- Whether AI stages should be introduced, removed, combined, reordered, or replaced with deterministic logic to
  improve the workflow
- Whether each AI stage has one focused objective. Prefer separate focused stages when combining distinct goals could
  overload a model or reduce output quality, even when separation requires additional AI calls.
- Whether prompt-writing and analysis responsibilities are correctly separated
- Model capability, reasoning level, and web-search policy; compare latency and cost only after quality requirements
  are satisfied
- Input validation, prompt-injection boundaries, and sensitive-data handling
- Structured-output contracts, validation, repair behavior, and failure handling
- Caching, retries, timeouts, cancellation, idempotency, and cleanup
- Test coverage and observability across stage boundaries

## Agreed cross-cutting requirements

- Treat all user inputs and all AI-model outputs as untrusted data.
- Validate AI outputs before using them as input to another model or application stage.
- Quality over cost is the governing rule for AI-flow architecture and model selection.
- Do not treat fewer AI calls as an optimization goal. Introduce, retain, or split AI stages when focused tasks are
  expected to materially improve output quality, safety, or reliability.
- Use the lowest-cost model or design only when the alternatives are expected to provide materially equivalent output
  quality, safety, and reliability. Account for added latency and trust boundaries without sacrificing quality merely
  to reduce cost.
- Sanitize AI-generated HTML before rendering it in the browser or using it in a print view.
- Use POST rather than query-string GET requests for AI streaming flows.
- Keep the current reasoning-based search-context and tool-call limits unless a later review explicitly changes them.
- Prefer focused improvements over broad technical infrastructure changes during this review cycle.
