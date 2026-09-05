# Monitor: dashboard-density restyle

**Status:** Implemented 2026-09-05.

## Intent

The Bearings Monitor page (`_render_monitor` and its helpers in `src/trading_journal/presentation/framework.py`) predated the density/card conventions already established on the Dashboard (`2026-08-28-dashboard-density-design.md`) and the Ongoing workspace: loose `st.metric` widgets with no card boundary, plain markdown sub-headings instead of the shared `dashboard-stat-column-head` style, and risk-evidence-coverage counters split across the top of the page and the Risk tab. This restyles Monitor to the same visual language, reusing existing CSS classes and the `_render_framework_stat_grid` helper rather than inventing new ones.

Mockup (before/after toggle, approved as-is after a density pass): artifact `https://claude.ai/code/artifact/9e7ea0a3-d67a-47ef-bb26-7fbf6e78012e`.

## Layout

1. **Monitoring controls** — rolling-sample slider, analysis-period control, and the scope/threshold captions now sit inside one bordered card (`st.container(border=True)`), matching Ongoing's "Exposure snapshot" and Dashboard's "Account & risk snapshot" cards.
2. **Coaching focus** — unchanged; already a bordered card.
3. **Pillar scores & readiness** — new bordered card combining what used to be three separate loose blocks: the "Overall readiness" metric, the three pillar `st.metric` cards (`_render_score_cards`), the radar chart, and the "What drives the current scores" component table, the last two now side by side in two columns. Pillar/readiness values stay real `st.metric` widgets (not custom HTML) because `test_monitor_tab_shows_early_estimate_not_incomplete_for_a_partial_sample` and `test_monitor_tab_explains_why_a_pillar_is_capped` assert on them via `app.metric`; only the individual `border=True` on each metric was dropped to avoid a nested double border inside the new card.
4. **Evidence-led actions** — the same `_render_monitor_insights` content, now wrapped in a bordered card instead of floating between sections.
5. **Risk tab / Risk snapshot** — the risk-evidence-coverage counters (Risk checks/pending/over-policy/unavailable), previously a loose `st.metric` row above the tabs, now live together with Daily R/Weekly R/Max drawdown/Loss streak in one "Risk snapshot" card inside the Risk tab, rendered through `_render_framework_stat_grid` (the same boxed-tile component `render_framework_dashboard` already uses for Dashboard's "Process & risk" preview). Review evidence lifecycle / Policy evidence charts keep their bar charts but their sub-labels switch to the shared `dashboard-stat-column-head` HTML style instead of plain markdown `#####` headings.

No change to Process & outcomes or System & context tab content.

## Supporting cleanup

- `_render_framework_stat_grid` now omits a stat tile's note `<div>` entirely when the note is empty, instead of rendering a blank line — needed because several of the new Risk-snapshot tiles (Loss streak, Risk pending, etc.) have no note text.
- `_daily_r_metric` renamed to `_r_metric`: its logic was never daily-specific (it just classifies a signed R value into Gain/Loss/Flat), and Monitor's new Risk snapshot reuses it for Weekly R as well as Daily R.
- The `tone_by_delta_color` dict inlined in `render_framework_dashboard` became a module-level `_TONE_BY_DELTA_COLOR` constant, shared with the new Risk-snapshot card instead of being redefined per call site.

## Data and behavior

No changes to `FrameworkService`, `MonitorAnalysisReport`, `RiskSnapshot`, or `RiskEvidenceCoverage`. Presentation-only.

## Verification

`make check` (367 passed) plus the `web`-marked AppTest suite (`pytest -m web`, 52 passed, includes both Monitor tests and all Dashboard tests). Verified visually in a running app against a seeded dev database (12 closed/reviewed trades, one capped pillar) in light theme: controls card, Pillar scores & readiness card (radar + drivers side by side), Evidence-led actions card, and the Risk tab's consolidated Risk snapshot tile grid.
