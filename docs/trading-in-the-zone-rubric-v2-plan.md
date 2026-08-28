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
- Keep the Dashboard's Three-pillar monitor on its fixed 20-review window and Bearings → Monitor on its adjustable window. Add a concise v2 sample line showing current-rubric reviews collected and legacy reviews excluded; retain the pillar score cards, radar, hard-block/caution markers, and scope explanations.
- Update review-table score help and history badges to say whether a trade uses the legacy 13-criterion rubric or the Zone-aligned 12-criterion rubric instead of retaining hard-coded 13-criterion copy.
- Continue using escaped values for custom Dashboard HTML, stable Streamlit widget keys, native bordered containers/forms, sentence-case labels, and existing Material Symbols.

## Versioning and compatibility

- Add an explicit `rubric_version` to assessments and revisions with supported values `legacy_v1` and `zone_v2`. Existing rows are safely backfilled as `legacy_v1`; all new and corrected reviews use `zone_v2`.
- Make criterion validation and scoring version-aware. Legacy reviews retain their original 13-criterion scores and labels in history and trends.
- Correcting a legacy review preserves the original as a legacy revision, prefills unchanged Risk/System fields, and requires the four new Psychology grades before saving as v2.
- Monitor scores, readiness gates, roadmap sample counts, period reviews, and automated coaching use only v2 evidence. The UI explicitly shows how many legacy reviews were excluded, preventing old criteria from being treated as evidence for concepts they did not measure.
- Archive any active pre-v2 coaching focus during the additive migration with an audit explanation. Do not delete assessments or require a database reset.
- Update assessment/revision view types and repository save/read interfaces to expose the rubric version. Quick Risk Checks generate the current 12-criterion v2 shape.

## Test plan

- Verify both rubric weight sets total 100% and v2 contains exactly 12 criteria.
- Confirm v1 reviews retain their original pillar/process scores after upgrade.
- Test v2 validation, scoring, Quick Risk Check generation, and rejection of missing or removed criteria.
- Test legacy-to-v2 correction, revision preservation, shared-field prefilling, and unchanged hard-rule overrides.
- Confirm Monitor, readiness, roadmap gates, period reviews, and coaching exclude legacy evidence while history and trends retain it.
- Test the four new Psychology coaching paths, new mistake tags, System's four-component calculation, and movement of invalidation responsibility to Risk.
- Update UI and internationalization assertions for dynamic rubric totals, version badges, v2/legacy sample captions, and the fixed coaching panel.
- Preserve the Dashboard regression assertions introduced by `f14ce8b`: dense `dashboard-stat-label` markup remains present and Performance, Consistency, and Breakdowns remain always-visible sections rather than tabs.
- Run `make check`, then visually verify Dashboard and Bearings in light/dark themes and at desktop/mobile widths, paying particular attention to the fixed coaching panel and 20-review v2 sample message.

## Assumptions

- Risk Management remains otherwise unchanged.
- Existing journal data is preserved through additive migration; no reset or destructive rewrite is allowed.
- Legacy and v2 scores may appear together in historical trends but are visibly labeled because their rubrics differ.
- The recent dense Dashboard is the UI baseline; this work updates rubric-dependent content rather than redesigning performance analytics.
- The implementation remains post-trade and advisory; it does not place or block MT5 orders.
