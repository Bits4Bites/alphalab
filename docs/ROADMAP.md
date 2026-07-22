# AlphaLab Roadmap

Last updated: 2026-07-22

This roadmap prioritizes features that improve investment decisions, research continuity, and actionable outcomes.
AlphaLab should remain simple and server-rendered where practical, using browser storage for small user-owned data and
temporary server-side caching for generated analyses.

Priority is relative within each section. Business features take precedence over standalone technical work unless a
technical item is required to deliver a business feature safely or reliably.

## Business Features

### High Priority

#### Compare Investments

Help investors choose between two to five stocks or ETFs using a consistent scorecard.

**MVP**

- Compare valuation, financial quality, growth, momentum, catalysts, and risks.
- Include scenario sensitivity and suitability for different investor profiles.
- Present a side-by-side summary table, category winners, and a balanced overall ranking.
- Persist comparison inputs locally and cache the latest successful comparison per user.

#### Filing and Earnings Report Analyzer

Extend source-based document research beyond IPO prospectuses.

**MVP**

- Accept annual reports, quarterly filings, earnings releases, and earnings transcripts.
- Extract key financial changes, guidance, management commentary, risks, and inconsistencies.
- Cite the relevant page or document section for material findings.
- Reuse the existing temporary upload and cleanup lifecycle.

#### Portfolio Rebalance Planner

Turn portfolio analysis into an executable adjustment plan.

**MVP**

- Compare current and proposed allocations.
- Produce specific buy, sell, trim, and cash-deployment actions.
- Estimate trade amounts and show concentration changes before and after rebalancing.
- Include tax and execution considerations without presenting the output as financial advice.

### Medium Priority

#### Investment Thesis Tracker

Create continuity between one-off analyses without requiring server-side account data.

**MVP**

- Store a thesis per ticker in user-scoped browser storage.
- Capture rationale, catalysts, risks, invalidation conditions, target, and review date.
- Refresh the thesis against current information and highlight what changed.
- Allow users to archive or remove local thesis records.

#### Unified Catalyst Calendar

Connect earnings, dividend, IPO, and watchlist workflows into one event view.

**MVP**

- Show upcoming events over 30, 60, and 90 days.
- Rank events by urgency and likely portfolio relevance.
- Provide event-specific watch items and links into existing analysis features.
- Support CSV and ICS export.

**Dependency**

- Select a reliable structured data source before implementation; AI web research alone should not be the system of
  record for event dates.

### Low Priority

#### Real-Time Alerts and Multi-Device Sync

Defer alerts, notification subscriptions, and synchronized research until persistent storage and background jobs are
part of the product direction. These capabilities require durable user data, scheduled processing, delivery channels,
and additional privacy and operational controls.

## Technical Improvements

### High Priority

#### Shared Analysis Page Client Module

- Extract repeated form persistence, corruption warnings, cached-result restoration, and SSE handling from templates.
- Keep feature-specific schemas and rendering configuration declarative.
- Reduce duplicated JavaScript and inconsistent behavior across analysis pages.

#### Structured AI Result Contracts

- Define validated JSON result shapes alongside Markdown output.
- Use structured data for comparison tables, portfolio actions, exports, and deterministic rendering.
- Preserve Markdown as the human-readable narrative layer.

### Medium Priority

#### Shared Backend Analysis Workflow

- Centralize request normalization and common SSE progress, error, and result events.
- Standardize successful-result caching and timestamp handling.
- Keep prompt construction and feature-specific business rules in their router or service modules.

### Low Priority

#### Cache and AI Task Observability

- Track cache hits, misses, invalid-entry eviction, and Redis failures.
- Measure AI task latency and failure rates by feature and model.
- Add operational diagnostics without storing sensitive prompts or user inputs.

## Recommended Sequence

1. Compare Investments, including the minimum structured result contract needed for reliable comparisons.
2. Filing and Earnings Report Analyzer.
3. Portfolio Rebalance Planner.
4. Investment Thesis Tracker.
5. Unified Catalyst Calendar after selecting a trustworthy event-data source.
6. Real-time alerts and multi-device sync only after approving persistent storage and background processing.

Technical improvements should be delivered incrementally with the business feature that first needs them rather than as
large standalone rewrites.

## Summary

| Area | Priority | Initiative | Primary Outcome |
|---|---|---|---|
| Business | High | Compare Investments | Faster, evidence-based selection between competing investments |
| Business | High | Filing and Earnings Report Analyzer | Deeper source-backed company research |
| Business | High | Portfolio Rebalance Planner | Concrete portfolio adjustment and execution plan |
| Business | Medium | Investment Thesis Tracker | Research continuity and repeat engagement |
| Business | Medium | Unified Catalyst Calendar | One prioritized view of upcoming market events |
| Business | Low | Real-Time Alerts and Multi-Device Sync | Automated engagement after durable infrastructure exists |
| Technical | High | Shared Analysis Page Client Module | Less duplicated UI and persistence logic |
| Technical | High | Structured AI Result Contracts | Reliable tables, comparisons, actions, and exports |
| Technical | Medium | Shared Backend Analysis Workflow | Consistent request, SSE, and caching behavior |
| Technical | Low | Cache and AI Task Observability | Better operational visibility and diagnosis |
