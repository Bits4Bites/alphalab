<overview>
AlphaLab is an AI-powered market research web app built with FastAPI and server-rendered Jinja templates. The conversation focused on expanding the app with new analysis features, tightening the low-cost AI -> premium AI prompt-generation workflow, improving the login/auth experience, and adding a new stateless Sector Rotation Radar feature. The overall approach was to keep the app stateless, follow a consistent AI workflow, and improve clarity in both the UX and the prompt templates.
</overview>

<history>
1. The user asked for help creating and revising a blog post from an outline.
  - Created a draft based on the provided outline and then updated it to include a rainier, less-ideal weekend vibe in the opening section.
  - This work was separate from the product code and did not affect the app.

2. The user requested multiple product-feature enhancements for AlphaLab.
  - Added and refined analysis features including Analyze Ticker, Build Portfolio, Review Portfolio, Dividend Event, Market Outlook, IPO/Listing Event, and Sector Rotation Radar.
  - Extended Analyze Ticker and Review Portfolio with optional Scenario inputs so the AI can stress-test the asset or portfolio under a specified event.
  - Added a new Sector Rotation Radar feature with required Target Market, optional Sectors, optional Timeframe, and optional Bias.
  - Kept the experience stateless and aligned with the existing streamed-analysis pattern.

3. The user asked for workflow and repo-guidance improvements.
  - Added prompt-generation conventions so future AI features should use a low-cost AI to draft a premium-model prompt only, without performing the analysis itself.
  - Updated shared repository instructions to enforce that pattern for future features.
  - Kept FastAPI docs endpoints disabled for this web app and preserved the existing import conventions.

4. The user asked for login/auth improvements.
  - Styled failed login and access-denied pages with Bootstrap 5.
  - Improved handling for invalid or expired OAuth codes and surfaced the underlying error reason to the user.
  - Added protection for provider-side cancellation cases, including LinkedIn's callback `error` parameters.
  - Forced GitHub and LinkedIn re-authentication by sending provider-specific authorization parameters.

5. The user asked for future-feature planning and review.
  - Reviewed the current feature set and suggested additional stateless feature ideas.
  - Saved feature-plan documents for Sector Rotation Radar, Watchlist Monitor, and Earnings Catalyst Tracker.
  - Used those plans as implementation reference where appropriate.

6. The user asked for UI polish and clarity.
  - Updated the forms so required versus optional fields are clearly marked with classic indicators: red `*` for required and `(Optional)` for optional fields.
  - Removed the explanatory header text from forms and kept the field labels concise.
</history>

<work_done>
Files created or updated:
- `app/routers/analyze_ticker.py` - added scenario-aware prompt support and tightened prompt-writing instructions so the low-cost model only drafts the prompt.
- `app/routers/build_portfolio.py` - updated prompt-template wording for portfolio construction.
- `app/routers/review_portfolio.py` - added optional scenario stress-test support and strengthened prompt constraints.
- `app/routers/dividend_event.py` - updated prompt-generation wording for dividend-event analysis.
- `app/routers/market_outlook.py` - updated prompt-generation wording for market outlook analysis.
- `app/routers/ipo_analyzer.py` - updated prompt-generation wording for IPO/listing-event analysis.
- `app/routers/sector_rotation_radar.py` - new router for the Sector Rotation Radar feature with prompt generation and SSE streaming.
- `app/templates/sector_rotation_radar.html` - new UI for Sector Rotation Radar.
- `app/templates/app_layout.html` - added the new sidebar link for Sector Rotation Radar.
- `app/templates/analyze_ticker.html`, `build_portfolio.html`, `review_portfolio.html`, `dividend_event.html`, `ipo_analyzer.html`, `market_outlook.html`, `dashboard.html`, and `sector_rotation_radar.html` - updated form field labels to clearly indicate required vs optional inputs.
- `app/routers/auth.py` - improved login error rendering and callback failure handling.
- `app/services/oauth.py` - added provider-specific OAuth parameters for forced re-authentication.
- `app/templates/login_error.html` - Bootstrap-styled login failure/access-denied page.
- `ai_tasks.env` - added AI task configuration for Sector Rotation Radar.
- `.github/copilot-instructions.md` - documented the shared AI prompt-generation standard for future features.
- `tests/test_sector_rotation_radar_prompt.py` - added a regression test for the new feature prompt builder.

Work completed:
- [x] Added and refined AI-driven analysis features.
- [x] Added optional scenario handling to Analyze Ticker and Review Portfolio.
- [x] Implemented Sector Rotation Radar with required/optional input handling and default timeframes.
- [x] Improved login/auth UX and error handling.
- [x] Documented the shared prompt-generation standard for future features.
- [x] Verified the app with tests and linting.
</work_done>

<technical_details>
- The app uses a consistent two-step AI pattern across features: a low-cost AI drafts a prompt, then a premium AI executes that prompt.
- Prompt templates now explicitly tell the low-cost model to act only as a prompt writer and not to perform research, analysis, recommendations, or summarization itself.
- The output contract is a single self-contained prompt with no preamble, commentary, or analysis; the premium model receives it without additional context.
- Sector Rotation Radar uses a new router/template flow with required Target Market, optional Sectors, optional Timeframe, and optional Bias. If no timeframe is supplied, it defaults to next 1-2 weeks, next 1 month, and next 3 months.
- The UI uses server-sent events (SSE) for streamed progress/results, plus shared JavaScript helpers for wait timers, Markdown rendering, and KaTeX math rendering.
- OAuth login is handled through provider-specific callbacks and provider-specific authorization parameters. GitHub now uses `prompt=login`, `allow_signup=false`, and `login=''`; LinkedIn uses `prompt=login`.
- The app remains stateless by design, with no server-side persistence required for its core features.
- Validation was run successfully with Ruff and pytest; the latest test run reported 58 passed tests.
</technical_details>

<important_files>
- `app/routers/sector_rotation_radar.py`
  - New feature router for the Sector Rotation Radar flow.
  - Implements prompt building, default timeframe handling, and SSE streaming.
- `app/routers/analyze_ticker.py`
  - Core prompt builder and analysis flow for ticker analysis.
  - Includes scenario-aware prompt logic and the updated prompt-writing constraints.
- `app/routers/review_portfolio.py`
  - Portfolio review prompt template and scenario stress-test flow.
- `app/routers/auth.py`
  - Central auth callback/login flow for OAuth and login error handling.
- `app/services/oauth.py`
  - Defines provider-specific OAuth parameters used to force re-authentication.
- `app/templates/sector_rotation_radar.html`
  - New UI for the Sector Rotation Radar feature.
- `app/templates/app_layout.html`
  - Shared sidebar/layout used across the app, including the new feature entry.
- `.github/copilot-instructions.md`
  - Shared repository instructions that now encode the AI prompt-generation standard for future features.
- `ai_tasks.env`
  - AI task configuration for the new Sector Rotation Radar analysis flow.
</important_files>

<next_steps>
Remaining work:
- Optionally implement additional future stateless features such as Watchlist Monitor or Earnings Catalyst Tracker if requested.
- Continue applying the shared prompt-generation standard to any future AI-driven features.

Immediate next steps:
- If a new feature is requested, follow the same low-cost AI prompt drafting pattern and reuse the existing SSE/UI structure.
- If desired, refine the Sector Rotation Radar output further or add more advanced UI affordances.
</next_steps>

<checkpoint_title>Refining AI prompt workflows</checkpoint_title>
