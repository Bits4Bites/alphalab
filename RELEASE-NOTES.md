# AlphaLab release notes

## 2026-08-18 - v0.12.0

### Removed

- Remove(portfolio): Retire the standalone Portfolio Action Briefing feature.

### Fixed/Improvements

- Fix(UI): Draft Portfolio Intent wrongly displayed on the landing page.
- Patch(config): Change primary markets to AU,US,VN.
- Impr(dashboard): Harden AI planning, streamed requests, and result rendering.
- Impr(dashboard): Validate and normalize AI-generated sample prompts.
- Impr(dashboard): Batch and validate market news and actionable research.
- Impr(market-outlook): Streamline structured research and safe rendering.
- Impr(analyze-ticker): Harden structured quick and full research, sourcing, caching, and safe rendering.
- Impr(dividend-event): Add deterministic event metrics and validated source-backed analysis.
- Impr(portfolio-intent): Add validated neutral drafting, persistent browser state, and safe portfolio handoffs.
- Impr(build-portfolio): Add adaptive structured research, verified market data, and deterministic sizing.
- Impr(build-portfolio): Add focused prioritized action planning with deterministic transition sizing.
- Impr(review-portfolio): Add focused structured review and conditional evidence-backed rebalance planning.
- Impr(review-portfolio): Add prioritized NEW, ADD, HOLD, TRIM, and EXIT action plans.

## 2026-08-16 - v0.11.0

### Added/Refactoring/Deprecation

- Feat(AI): Add configurable low, medium, and high reasoning effort.
- Feat: Add new feature Draft Portfolio Intent.

### Fixed/Improvements

- Patch(AI): Disable web search by default.
- Impr(AI): Centralize task execution policies and optimize AI vendor and task model configurations.
- Fix(CodeQL): Fix CodeQL warnings.

## 2026-08-07 - v0.10.0

### Added/Refactoring/Deprecation

- Feat(action-briefing): Add stateless portfolio action briefings with prioritized actions, risks, and catalysts.

## 2026-07-23 - v0.9.0

### Added/Refactoring/Deprecation

- Feat(review portfolio): Add optional market-specific rebalance planner.
- Feat: Add market-validated stock and ETF comparison.

### Fixed/Improvements

- Patch(UI): Sync Review Portfolio form across other features.

## 2026-07-22 - v0.8.0

### Added/Refactoring/Deprecation

- Feat: Add optional prospectus PDF uploads to IPO event analysis.
- Feat: Add user-scoped local storage infrastructure.
- Feat: Cache Market Outlook inputs and results in user-scope storage.
- Feat: Cache Sector Rotation inputs and results in user-scope storage.
- Feat: Cache Analyze Ticker inputs and results in user-scope storage.
- Feat: Cache IPO event inputs and results in user-scope storage.
- Feat: Cache Dividend Event inputs and results in user-scope storage.
- Feat: Cache Build and Review Portfolio inputs and results in user-scope storage.
- Feat: Cache Watchlist Monitor inputs and results in user-scope storage.
- Feat: Cache Earnings Catalyst Tracker inputs and results in user-scope storage.

## 2026-06-29 - v0.7.1

### Fixed/Improvements

- Patch: Prompt update for Build Portfolio and Review Portfolio features.
- Patch: temperature value is now configurable per task.

## 2026-06-27 - v0.7.0

### Added/Refactoring/Deprecation

- Feat: New feature IPO scanner.

### Fixed/Improvements

- Patch: Make Redis check periodically.

## 2026-06-27 - v0.6.0

### Added/Refactoring/Deprecation

- Feat: New feature Sector Rotation Radar.
- Feat: New feature Watchlist Monitor.
- Feat: New feature Earnings Catalyst Tracker.

### Fixed/Improvements

- Impr(prompt): Make sure the low-cost AI model focuses on generating prompts.
- Patch(UI): Make Required/Optional fields more clear.

## 2026-06-26 - v0.5.1

### Fixed/Improvements

- Impr(UX): Better error message if login is denied.
- Impr(UX): Better error handling when login failed with invalid auth code.
- Impr(UX): Force reauthentication for every login.

## 2026-06-06 - v0.5.0

### Added/Refactoring/Deprecation

- Feat: IPO Analyzer.

### Fixed/Improvements

- Impr(analyze ticker): Improve Analyze Ticker feature with optional intent.
- Impr(analyze ticker): Improve Analyze Ticker feature with optional scenario.
- Patch(UI): Minor UI changes.
- Impr(review portfolio): Improve Review Portfolio feature with optional scenario.

## 2026-05-31 - v0.4.0

### Added/Refactoring/Deprecation

- Feat: Analyze a dividend event.
- Feat: Market outlook.

### Fixed/Improvements

- Fix: other fixes and improvements.

## 2026-05-26 - v0.3.1

### Fixed/Improvements

- Impr: Improve dashboard prompt.
- Impr: Improve portfolio building prompt.
- Impr: Improve portfolio review prompt.
- Impr(UI): Add a timer next to the progress bar.
- Impr(UI): Handle LaTeX in the generated markdown.
- Impr(UI): Update/Improve print view.

## 2026-05-25 - v0.3.0

### Fixed/Improvements

- Impr: Improve ticker analysis prompt.

## 2026-05-22 - v0.2.2

### Fixed/Improvements

- Impr: Fetch market news relevance with recency and locality constraint.

## 2026-05-21 - v0.2.1

### Fixed/Improvements

- Patch: Move app_version to AppSettings config.

## 2026-05-21 - v0.2.0

### Added/Refactoring/Deprecation

- Feat: Use AI to generate sample prompts for the Dashboard and store in an external data store.
- Feat: Fetch market news and present on Dashboard.
- Feat: Use AI to generate actionable ideas, and add to Dashboard.

## 2026-05-19 - v0.1.2

### Fixed/Improvements

- Impr(ui): make sidebar collapsible on mobile viewports.
- Impr(dashboard): display 4 random sample prompts from pool of 20.

## 2026-05-19 - v0.1.1

### Fixed/Improvements

- (Fix) Release workflow updates app version incorrectly.
- (Patch) Add disclaimer about the AI generated content.
- (Patch) Add app name/version info to bottom.
- (Patch) Add Terms of Service and Privacy Policy pages and links.

## 2026-05-19 - v0.1.0

### Added/Refactoring/Deprecation

- (Feat) Login with social network accounts: GitHub and LinkedIn.
- (Feat) Analyze Ticker.
- (Feat) Build Portfolio.
- (Feat) Review Portfolio.
