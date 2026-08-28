# Dashboard: dense review workspace

**Status:** Implemented 2026-08-29. Supersedes the first dense-stat-grid layout from 2026-08-28.

## Intent

The Dashboard is a professional review workspace, not a collection of metric cards. It keeps account outcomes, historical risk, analytical drivers, and three-pillar process evidence distinct while making them easy to scan on one page.

## Layout

The page uses three primary review regions beneath a compact account header; the middle region uses separate history and analysis surfaces so its charts remain readable:

1. **Account and trade outcomes** — a six-value account strip followed by three columns for Profit, Loss, and a legend-free percentage Outcome mix. Win rate, payoff ratio, Expectancy R, and R coverage form a 2×2 outcome grid; day and streak statistics form one compact footer row.
2. **Performance history and analysis** — one synchronized chart aligns the balance/cumulative-P&L line, negative drawdown area, and daily/per-trade P&L bars. An always-visible analysis surface places breakdown and profit/loss concentration charts side by side, followed by a compact breakdown table. Closed-trade detail appears only in Per-trade view.
3. **Process and risk** — operational risk/readiness values sit beside horizontal Psychology, Risk management, and Trading system score bars. Outcome profitability never determines these process scores.

The floating coaching focus remains available without occupying document flow. On narrow screens, the three-column overview and two-column analysis layouts stack using Streamlit's native responsive columns.

## Chart choices

- Outcome mix: 100% stacked horizontal bar for proportions.
- Performance history: shared-axis line, filled negative area, and diverging bars for level, drawdown, and periodic results.
- Direction/symbol breakdown: diverging horizontal bars for signed comparison.
- Concentration: paired Pareto bars and cumulative-share lines so profit and loss remain visible together.
- Pillars: horizontal 0–100 bars for accurate comparison; status color and text preserve incomplete, caution-capped, and hard-blocked states. The previous three-axis radar is retained only on the dedicated Bearings monitor.

The daily-result-range chart is removed because its three values already appear in the outcome statistics and do not require another plot.

## Data and behavior

`DashboardService` and `DashboardReport` remain unchanged. Daily remains the default history view. Per trade changes the history grain and reveals the logical-trade detail table; the Daily view stays focused on account history and aggregate drivers.

## Verification

Automated tests cover chart traces, semantic colors, shared axes, empty concentration sides, pillar status encoding, controls, and the full Streamlit dashboard. Final verification uses `make check` plus light/dark and narrow-width visual inspection when the app is running.
