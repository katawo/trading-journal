# Dashboard: dense analytics layout (replacing metric cards)

**Status:** Approved 2026-08-28. Visual mockup: see the "Dashboard Density Preview" artifact reviewed during design.

## Problem

`render_dashboard` / `_render_dashboard_statistics` (`app.py`) build the Dashboard page almost entirely from `st.metric` "cards" (`_render_dashboard_metric` → `render_accent_metric`, a colored-left-border box per number), 4-6 per row, repeated across a headline row, a "Logical-trade quality" row, and — behind `st.tabs` (Performance / Consistency / Breakdowns) — two more rows each. Cards take a lot of vertical space per number and hide most of the Statistics content behind tab clicks, which reads as consumer/card-dashboard style rather than a professional analytics report.

## Scope

Presentation-layer only, confined to `app.py`. `DashboardService` / `DashboardReport` (`application/dashboard.py`) are untouched — no data contract change, no new metrics, no test changes needed there (existing tests only exercise `DashboardService.build_report`, never the Streamlit widgets).

Not touched: the two side-by-side charts (balance/equity curve, drawdown), the daily/per-trade P&L bar chart, the "Closed-trade detail" table, and the Concentration (80/20) section — all already chart/table-based, not card-based.

`render_accent_metric` in `presentation/formatting.py` and its other caller (`ongoing.py`) are untouched — Ongoing keeps today's card look. The Dashboard gets its own new rendering helper(s) in `app.py`.

## Design

**Dense stat grid.** A new helper renders `label → value` pairs as a CSS grid (`auto-fill`, wraps responsively) instead of `st.metric` cards: no borders/backgrounds, small single-line rows, tight gaps. Tone (positive/negative/warning/info/neutral) is computed exactly as today via the existing `_signed_metric_tone` / `_profit_factor_metric_tone` / `_risk_metric_tone` / `_r_coverage_metric_tone` / `_streak_metric_tone` helpers, but instead of driving a card's border color it colors the value text, reusing the same `--st-green-color` / `--st-red-color` / `--st-orange-color` / `--st-blue-color` / `--st-gray-color` CSS variables already defined in `apply_application_style()`. The one metric with a "delta" today (Profitable days' rate) becomes inline text, e.g. `24/39 (61.5%)`.

**Sections replace tabs.** The Statistics `st.tabs` (Performance / Consistency / Breakdowns) become three always-visible stacked sections (`st.container(border=True)` each, matching the current tab-panel framing) — no click required to compare them. Breakdowns keeps its existing chart + `st.dataframe` table (already dense), just no longer gated behind a tab.

**Profit/loss split where metrics naturally pair.** Where a section's metrics have a real profit-side/loss-side counterpart, split them into two columns (mirroring the existing Concentration section's profit/loss column pattern) instead of one flat grid:

- **Performance**: a Profit column (Gross profit, Average win, Wins) and a Loss column (Gross loss, Average loss, Losses); metrics with no side (Payoff ratio, Expectancy R, Breakevens, R coverage) stay in one shared grid row underneath.
- **Consistency**: a Best column (Best day, Longest win streak) and a Worst column (Worst day, Longest loss streak); the rest (Active trading days, Profitable days, Average day, Recovery factor, Current streak) stay in one shared grid row above.

Headline row (Account balance / growth / Realized P&L / Account drawdown) and the "Logical-trade quality" row (Total R / Win rate / Profit factor / Expectancy / Worst day) stay as single flowing dense grids — none of those metrics have a profit/loss counterpart.

## Verification

UI-only, data contract unchanged: verify by running the app (`make run`) and visually checking the Dashboard in both light and dark theme with a real account's data, per this repo's UI-testing convention. No automated test changes.
