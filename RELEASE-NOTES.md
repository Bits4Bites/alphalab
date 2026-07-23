# AlphaLab release notes

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
