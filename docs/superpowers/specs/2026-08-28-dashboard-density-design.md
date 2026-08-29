# Dashboard: dense review workspace

**Status:** Implemented 2026-08-29. Supersedes the first dense-stat-grid layout from 2026-08-28.

## Intent

The Dashboard is a professional review workspace, not a collection of metric cards. It keeps account outcomes, historical risk, analytical drivers, and three-pillar process evidence distinct while making them easy to scan on one page.

## Layout

The page follows the review sequence from account state through outcomes and historical evidence to process readiness:

1. **Account & risk snapshot** — Capital, Performance, Daily-close Drawdown, and Quality keep funded capital, realized results, drawdown, Total R, and Profit factor together. Daily-close drawdown is stable and does not change with the history-chart grain.
2. **Trade outcomes** — Profit, Loss, and Edge quality summarize the all-time closed logical-trade record. A compact Consistency profile groups activity, daily efficiency, and streak risk. Direction edge compares Long and Short through Trade profile and Edge results matrices without a redundant chart or Long-minus-Short column.
3. **Performance history** — one synchronized chart aligns the balance/cumulative-P&L line, negative drawdown area, and daily/per-trade P&L bars. Daily remains the default; Per trade also reveals closed-logical-trade detail.
4. **Concentration** — paired profit/loss Pareto views identify where outcomes are concentrated by trade or symbol without claiming causation or system quality.
5. **Process & risk** — operational risk/readiness values sit beside horizontal Psychology, Risk management, and Trading system score bars. Outcome profitability never determines these process scores. A compact question-mark popover explains the boundary.

The floating coaching focus remains available without occupying document flow. On narrow screens, Streamlit columns stack and the two Direction edge matrices stack vertically while retaining Long/Short comparison columns.

## Chart choices

- Performance history: shared-axis line, filled negative area, and diverging bars for level, drawdown, and periodic results.
- Direction edge: compact Long/Short matrices for trade counts, outcome rates, P&L, R metrics, and Profit factor.
- Concentration: paired Pareto bars and cumulative-share lines so profit and loss remain visible together.
- Pillars: horizontal 0–100 bars for accurate comparison; status color and text preserve incomplete, caution-capped, and hard-blocked states. The previous three-axis radar is retained only on the dedicated Bearings monitor.

The daily-result-range chart is removed because its three values already appear in the outcome statistics and do not require another plot.

## Data and behavior

`DashboardService` and `DashboardReport` remain unchanged. Daily remains the default history view. Per trade changes the history grain and reveals the logical-trade detail table; the Daily view stays focused on account history and aggregate drivers. Account snapshot drawdown always uses daily-close values.

## Verification

Automated tests cover chart traces, direction statistics, drawdown formatting, semantic colors, shared axes, empty concentration sides, pillar status encoding, controls, section order, and the full Streamlit dashboard. Final verification uses `make check` plus light/dark and narrow-width visual inspection when the app is running.
