# Trading in the Zone–aligned three-pillar rubric v2

Status: implemented on 2026-08-28.

## Summary

Reframe Psychology around probabilistic execution, keep Risk Management mechanically focused, and reduce Trading System from five to four criteria. Deep Review becomes a 12-criterion assessment: 4 Psychology + 4 Risk + 4 System. Integrate the new rubric into the current dense Dashboard without changing its outcome-analytics contract or visual hierarchy.

## Implementation changes

### Psychology rubric

| Criterion | Weight | Review intent |
|---|---:|---|
| Edge execution | 35% | Execute a documented edge without hesitation, chasing, or improvisation. |
| Risk acceptance | 25% | Genuinely accept the predefined loss before entry so fear or hope does not alter execution. |
| Probability mindset | 20% | Treat the trade as one uncertain event in a series rather than requiring a prediction. |
| Outcome independence and reset | 20% | Judge process separately from P&L and reset before the next decision. |

### Risk Management rubric

Keep the existing four Risk Management criteria. Clarify that Stop discipline owns both predefined invalidation and compliance with the stop.

### Trading System rubric

| Criterion | Weight |
|---|---:|
| Setup validity | 30% |
| Context alignment | 25% |
| Entry fidelity | 20% |
| Management/exit fidelity | 25% |

Remove System's `invalidation_fidelity`; Risk Management already measures that behavior through Stop discipline.

### Monitoring, coaching, and documentation

- Align rolling Psychology components directly with the four new criteria and their weights.
- Reduce the System Monitor to four components: Setup validity 25%, Context alignment 20%, Execution fidelity 25%, and Edge evidence 30%. Execution fidelity combines Entry and Management/exit; Edge evidence replaces the two currently duplicated backtest-evidence components.
- Add book-aligned coaching tags and actions for certainty-seeking, unaccepted risk, hesitation on a valid edge, and outcome attachment. Retain existing historical tags and all current hard-rule behavior.
- Update the Psychology roadmap to require a written probability mindset, a pre-trade risk-acceptance routine, fixed-sample execution practice, and outcome-independent review evidence.
- Rewrite Deep Review prompts, help text, coaching explanations, English/Vietnamese translations, and framework documentation using paraphrased concepts rather than book quotations.

### Dashboard and Streamlit UI alignment

- Preserve the dense Dashboard introduced by commit `f14ce8b`: compact escaped label/value grids, profit/loss and best/worst splits, and always-visible Performance, Consistency, and Breakdowns sections remain unchanged.
- Keep `DashboardService` and `DashboardReport` untouched. P&L, expectancy, drawdown, concentration, and breakdown analytics remain outcome evidence and must not be blended with rubric-v2 process scores.
- Retain the current page order: fixed collapsed coaching focus, Performance dashboard, then the compact Three-pillar monitor. Do not reintroduce metric cards or Statistics tabs into the outcome-analytics sections.
- Update the existing fixed coaching-focus panel in place with the new Psychology actions and v2-only progress. Preserve its responsive desktop/mobile positioning and dialog-based edit/resolve interactions.
- Keep the Dashboard's Three-pillar monitor on its fixed 20-review window and Bearings → Monitor on its adjustable window. Show the current-rubric sample collected; retain the pillar score cards, radar, hard-block/caution markers, and scope explanations.
- Use the Zone-aligned 12-criterion label consistently in review history and score help.
- Continue using escaped values for custom Dashboard HTML, stable Streamlit widget keys, native bordered containers/forms, sentence-case labels, and existing Material Symbols.

## Versioning and compatibility

- Keep an explicit `rubric_version` on assessments and revisions, accepting only `zone_v2` after migration.
- Back up the SQLite database, then convert earlier assessments and revisions automatically. Retain the eight compatible Risk/System grades, set the four new Psychology grades to neutral `Partial`, and discard the removed invalidation grade.
- Remove incompatible earlier period reviews, coaching focuses, and Psychology roadmap evidence so no v1 scoring path remains active.
- Monitor scores, readiness gates, roadmap sample counts, period reviews, and automated coaching use only v2 evidence.
- Update assessment/revision view types and repository save/read interfaces to expose the rubric version. Quick Risk Checks generate the current 12-criterion v2 shape.

## Test plan

- Verify the v2 rubric weights total 100% and v2 contains exactly 12 criteria.
- Confirm earlier assessments and revisions convert to the v2 shape with compatible fields retained and new Psychology fields neutral.
- Test v2 validation, scoring, Quick Risk Check generation, and rejection of missing or removed criteria.
- Confirm migration creates one backup, is idempotent, and removes incompatible derived artifacts.
- Confirm Monitor, readiness, roadmap gates, period reviews, and coaching use only v2 evidence.
- Test the four new Psychology coaching paths, new mistake tags, System's four-component calculation, and movement of invalidation responsibility to Risk.
- Update UI and internationalization assertions for dynamic rubric totals, v2 sample captions, and the fixed coaching panel.
- Preserve the Dashboard regression assertions introduced by `f14ce8b`: dense `dashboard-stat-label` markup remains present and Performance, Consistency, and Breakdowns remain always-visible sections rather than tabs.
- Run `make check`, then visually verify Dashboard and Bearings in light/dark themes and at desktop/mobile widths, paying particular attention to the fixed coaching panel and 20-review v2 sample message.

## Assumptions

- Risk Management remains otherwise unchanged.
- Imported trades and compatible assessment evidence are preserved without a database reset. Incompatible v1 aggregate artifacts are removed after a backup, as described above.
- Runtime analytics and historical trends use `zone_v2` only after the one-time migration; legacy scores are not retained as an active or parallel scoring path.
- The recent dense Dashboard is the UI baseline; this work updates rubric-dependent content rather than redesigning performance analytics.
- The implementation remains post-trade and advisory; it does not place or block MT5 orders.
