<overview>
AlphaLab is an AI-powered market research web app built with FastAPI and server-rendered Jinja templates. The conversation focused on expanding the app with new analysis features, improving the prompt-generation pattern used across AI workflows, refining the login/auth experience, and planning future stateless feature ideas. The overall approach was to keep the app stateless, use a consistent low-cost AI -> premium AI workflow, and improve UX with Bootstrap-based pages and streaming progress updates.
</overview>

<history>
1. The user asked for blog-writing help and later requested updates to the draft content.
   - Created and iterated on a blog draft based on an outline file.
   - This work was separate from the app feature work and did not affect the product code.

2. The user asked to evolve AlphaLab into a richer market-research web app.
   - Added and refined analysis features such as Analyze Ticker, Build Portfolio, Review Portfolio, Dividend Event, Market Outlook, and IPO/Listing Event.
   - Standardized the feature pattern so each flow uses a low-cost AI model to generate a prompt and a premium AI model to execute it.
   - Updated the sidebar/nav, added wait timers, and improved Markdown/KaTeX rendering for richer AI output.

3. The user requested code quality and repo-guidance improvements.
   - Added import-convention rules and fixed internal imports to follow them.
   - Disabled FastAPI docs/redoc/openapi because this is a web app rather than an API service.
   - Added or updated tests for ticker logic, AI utilities, and scheduling behavior.

4. The user asked for login/auth improvements.
   - Styled failed login/access-denied pages with Bootstrap 5.
   - Surfaced provider error reasons for invalid/expired OAuth codes.
   - Added better handling for provider-side cancellation errors, including LinkedIn's `error` callback parameters.
   - Forced re-authentication for GitHub and LinkedIn by sending provider-specific auth parameters.

5. The user asked for future feature suggestions.
   - Reviewed the current feature set and proposed additional stateless features.
   - Saved feature-plan documents for Sector Rotation Radar, Watchlist Monitor, and Earnings Catalyst Tracker.

6. The user requested a review of the current prompt-generation task design.
   - The review identified that several low-cost AI prompt templates could be misread as instructing the model to do the research/analysis itself.
   - The templates should be tightened so the low-cost model is clearly constrained to writing a prompt only, while the premium model performs the actual analysis.
</history>

<work_done>
Files updated or created:
- `app/routers/analyze_ticker.py` - added scenario-aware prompt support and strengthened prompt wording.
- `app/routers/build_portfolio.py` - prompt-template flow for portfolio construction.
- `app/routers/review_portfolio.py` - prompt-template refactor and added optional scenario stress-test support.
- `app/routers/dividend_event.py` - new feature workflow for dividend-event analysis.
- `app/routers/market_outlook.py` - new market-outlook workflow.
- `app/routers/ipo_analyzer.py` - new IPO/listing-event workflow.
- `app/templates/app_layout.html` - sidebar/nav updates and shared UI wiring.
- `app/templates/analyze_ticker.html`, `review_portfolio.html`, and related feature templates - UI updates, wait timer, markdown rendering.
- `app/routers/auth.py` - styled login errors, callback failure handling, access-denied page, provider-error handling.
- `app/services/oauth.py` - provider-specific OAuth parameters for forced re-authentication.
- `app/templates/login_error.html` - new Bootstrap-styled login error page.
- `tests/test_auth.py`, `tests/test_ai.py`, `tests/test_ticker.py`, `tests/test_scheduler.py`, and related tests - regression coverage for new logic.
- `.github/feature-plans/sector-rotation-radar.md`, `.github/feature-plans/watchlist-monitor.md`, `.github/feature-plans/earnings-catalyst-tracker.md` - saved future feature ideas.

Work completed:
- [x] Added and refined AI-driven analysis features.
- [x] Improved the login/auth UX and error handling.
- [x] Added stateless feature concepts for future expansion.
- [x] Verified the app with tests and linting.
- [ ] Tighten the low-cost AI prompt templates further so they explicitly limit the model to prompt drafting only.
</work_done>

<technical_details>
- The app uses a consistent two-step AI pattern across features: first a low-cost AI generates a prompt, then a premium AI executes the generated prompt.
- Prompt templates should explicitly tell the low-cost model to write only the input prompt for the premium model and not to perform the research, analysis, or final recommendation itself.
- The ticker analysis flow includes country-aware market-cap tiering, asset-type detection heuristics, and scenario-aware prompt expansion.
- The UI uses server-sent events (SSE) for streamed progress/results, plus shared JavaScript helpers for wait timers, Markdown rendering, and KaTeX math rendering.
- OAuth login is handled through provider-specific callbacks and provider-specific authorization parameters. GitHub now uses `prompt=login`, `allow_signup=false`, and `login=''`; LinkedIn uses `prompt=login`.
- The app is designed to remain stateless, with no server-side persistence required for the core features.
- FastAPI docs endpoints are disabled for this web app.
</technical_details>

<important_files>
- `app/routers/auth.py`
  - Central auth callback/login flow for OAuth and error handling.
  - Contains the styled error rendering for failed login and denied access cases.
- `app/services/oauth.py`
  - Defines the OAuth providers and provider-specific authorization parameters used to force re-authentication.
- `app/routers/analyze_ticker.py`
  - Core prompt builder and analysis flow for ticker analysis.
  - Includes scenario-aware prompt logic and asset-type-aware analysis context.
- `app/routers/review_portfolio.py`
  - Portfolio review prompt template and scenario stress-test flow.
- `app/templates/login_error.html`
  - Bootstrap-styled login failure/access-denied page shown to users.
- `app/templates/app_layout.html`
  - Shared sidebar/layout used across the app.
- `.github/feature-plans/*.md`
  - Saved feature-planning documents for future stateless features.
</important_files>

<next_steps>
Remaining work:
- Tighten the prompt-generation templates across the AI features so the low-cost model is clearly limited to writing the prompt and not performing the heavy analysis itself.
- Optionally implement one or more of the proposed future features such as Sector Rotation Radar, Watchlist Monitor, or Earnings Catalyst Tracker.

Immediate next steps:
- Review and update the shared prompt-generation template wording in the relevant routers.
- Add or refine tests around prompt-builder instructions if prompt wording changes are implemented.
</next_steps>

<checkpoint_title>Expanding AI and auth flows</checkpoint_title>
