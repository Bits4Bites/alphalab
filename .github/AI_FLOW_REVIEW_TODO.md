# AI Flow Architecture Review

This internal file tracks the architecture review of every AlphaLab flow that invokes an AI model.

Inventory baseline:

- 17 reviewable flows
- 38 configured AI tasks
- Authenticated feature flows and background AI jobs are both included

## Authenticated feature flows

- **Dashboard freestyle analysis**
  - [x] Reviewed
  - [x] Implemented
  - Status: Done
  - Tasks: `DASHBOARD_BUILD_PROMPT`, `DASHBOARD_ANALYZE`
  - Flow: validate request -> write research prompt -> run web-enabled analysis -> render result in the browser

- **Market Outlook**
  - [ ] Reviewed
  - [ ] Implemented
  - Tasks: `MARKET_OUTLOOK_BUILD_PROMPT`, `MARKET_OUTLOOK_ANALYZE`
  - Flow: resolve markets -> write research prompt -> run web-enabled analysis -> cache result

- **Analyze Ticker**
  - [ ] Reviewed
  - [ ] Implemented
  - Tasks: `ANALYZE_TICKER_BUILD_PROMPT`, `ANALYZE_TICKER_ANALYZE_QUICK`, `ANALYZE_TICKER_ANALYZE`
  - Flow: validate ticker -> fetch market metadata -> write research prompt -> run quick or comprehensive
    web-enabled analysis -> cache result

- **Compare Investments**
  - [ ] Reviewed
  - [ ] Implemented
  - Tasks: `COMPARE_INVESTMENTS_BUILD_PROMPT`, `COMPARE_INVESTMENTS_ANALYZE`,
    `COMPARE_INVESTMENTS_ANALYZE_SCENARIO`
  - Flow: validate candidates and quotes -> write research prompt -> run structured core research -> optionally repair
    invalid output -> optionally run and repair scenario research -> calculate rankings -> cache result

- **IPO / Listing Event Analyzer**
  - [ ] Reviewed
  - [ ] Implemented
  - Tasks: `IPO_ANALYZER_BUILD_PROMPT`, `IPO_ANALYZER_ANALYZE`
  - Flow: validate inputs -> optionally convert an uploaded prospectus -> write research prompt -> attach prospectus
    content -> run web-enabled analysis -> cache result -> delete uploaded document

- **Dividend Event**
  - [ ] Reviewed
  - [ ] Implemented
  - Tasks: `DIVIDEND_EVENT_BUILD_PROMPT`, `DIVIDEND_EVENT_ANALYZE`
  - Flow: validate event details -> write research prompt -> run web-enabled analysis -> cache result

- **Sector Rotation Radar**
  - [ ] Reviewed
  - [ ] Implemented
  - Tasks: `SECTOR_ROTATION_RADAR_BUILD_PROMPT`, `SECTOR_ROTATION_RADAR_ANALYZE`
  - Flow: validate market inputs -> write research prompt -> run web-enabled analysis -> cache result

- **IPO Scanner**
  - [ ] Reviewed
  - [ ] Implemented
  - Tasks: `IPO_SCANNER_VALIDATE_MARKET`, `IPO_SCANNER_BUILD_DISCOVERY_PROMPT`, `IPO_SCANNER_DISCOVER`,
    `IPO_SCANNER_BUILD_VERIFY_PROMPT`, `IPO_SCANNER_VERIFY`
  - Flow: validate input -> AI-validate market -> write discovery prompt -> discover candidates with web search ->
    parse candidates -> write verification prompt -> verify candidates with web search -> cache result

- **Draft Portfolio Intent**
  - [ ] Reviewed
  - [ ] Implemented
  - Task: `DRAFT_PORTFOLIO_INTENT`
  - Flow: validate optional preferences -> generate a structured draft or clarification questions -> validate response ->
    optionally repeat with clarification answers -> hand off the edited intent

- **Build Portfolio**
  - [ ] Reviewed
  - [ ] Implemented
  - Tasks: `BUILD_PORTFOLIO_BUILD_PROMPT`, `BUILD_PORTFOLIO_ANALYZE`
  - Flow: validate investor inputs -> write portfolio research prompt -> run web-enabled portfolio analysis -> cache result

- **Review Portfolio**
  - [ ] Reviewed
  - [ ] Implemented
  - Tasks: `REVIEW_PORTFOLIO_BUILD_PROMPT`, `REVIEW_PORTFOLIO_ANALYZE`
  - Flow: validate holdings and investor context -> write review prompt -> run web-enabled portfolio review -> cache result

- **Portfolio Rebalance Plan**
  - [ ] Reviewed
  - [ ] Implemented
  - Tasks: `REVIEW_PORTFOLIO_REBALANCE_BUILD_PROMPT`, `REVIEW_PORTFOLIO_REBALANCE_ANALYZE`
  - Flow: complete portfolio review -> fetch current quotes -> write allocation prompt -> generate structured target
    allocations -> validate proposed securities and prices -> calculate deterministic trades -> cache result

- **Portfolio Action Briefing**
  - [ ] Reviewed
  - [ ] Implemented
  - Tasks: `PORTFOLIO_ACTION_BRIEFING_BUILD_PROMPT`, `PORTFOLIO_ACTION_BRIEFING_ANALYZE`
  - Flow: validate holdings and watchlist -> fetch current quotes -> write research prompt -> run structured web research ->
    optionally repair invalid output without web search -> rank and size actions deterministically

- **Watchlist Monitor**
  - [ ] Reviewed
  - [ ] Implemented
  - Tasks: `WATCHLIST_MONITOR_BUILD_PROMPT`, `WATCHLIST_MONITOR_ANALYZE`
  - Flow: validate watchlist inputs -> write monitoring prompt -> run web-enabled analysis -> cache result

- **Earnings Catalyst Tracker**
  - [ ] Reviewed
  - [ ] Implemented
  - Tasks: `EARNINGS_CATALYST_TRACKER_BUILD_PROMPT`, `EARNINGS_CATALYST_TRACKER_ANALYZE`
  - Flow: validate ticker and event inputs -> write catalyst prompt -> run web-enabled analysis -> cache result

## Background dashboard-support flows

- **Dashboard sample prompt generation**
  - [x] Reviewed
  - [x] Implemented
  - Status: Done
  - Task: `DASHBOARD_GENERATE_SAMPLE_PROMPTS`
  - Flow: check cache freshness -> generate a structured prompt list -> parse and validate output -> cache prompts

- **Dashboard market news and actionable research**
  - [ ] Reviewed
  - [ ] Implemented
  - Tasks: `DASHBOARD_FETCH_MARKET_NEWS`, `DASHBOARD_GENERATE_ACTIONABLE_PROMPTS`,
    `DASHBOARD_EXECUTE_ACTIONABLE_PROMPT`
  - Flow: check cache freshness -> fetch web-enabled market news -> parse and cache news -> generate actionable
    prompts -> parse and cache prompts -> execute each prompt with web search -> cache each result

## Review criteria

For each flow, assess:

- Whether every AI stage is necessary or can be deterministic
- Whether prompt-writing and analysis responsibilities are correctly separated
- Model capability, reasoning level, web-search policy, latency, and cost
- Input validation, prompt-injection boundaries, and sensitive-data handling
- Structured-output contracts, validation, repair behavior, and failure handling
- Caching, retries, timeouts, cancellation, idempotency, and cleanup
- Test coverage and observability across stage boundaries

## Agreed cross-cutting requirements

- Treat all user inputs and all AI-model outputs as untrusted data.
- Validate AI outputs before using them as input to another model or application stage.
- Sanitize AI-generated HTML before rendering it in the browser or using it in a print view.
- Use POST rather than query-string GET requests for AI streaming flows.
- Keep the current reasoning-based search-context and tool-call limits unless a later review explicitly changes them.
- Prefer focused improvements over broad technical infrastructure changes during this review cycle.
