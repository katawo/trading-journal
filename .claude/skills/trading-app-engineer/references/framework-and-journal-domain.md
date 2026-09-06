# Framework & Journal Domain Reference

Use this for post-trade journal/review applications built around a scoring or assessment framework (e.g. a multi-pillar review such as Psychology/Risk/Trading System), as opposed to an execution or backtesting platform. The correctness concerns here are about auditability and data integrity of *assessments*, not about order/fill mechanics — treat both this file and `references/trading-engineering.md` as potentially relevant to the same repo, covering different halves of the domain.

Always check for a repo-specific source-of-truth doc for the actual scoring/workflow rules (e.g. a "framework guide" under `docs/`) before implementing — this reference covers the general *shape* of these correctness concerns, not any one repo's specific rubric.

## Snapshot Immutability for Rule-Based Results

If an assessment records a rule-based outcome (e.g. a hard-rule "Clear"/"Fail" result) at the time it was made, that recorded outcome must not be silently recomputed if the underlying rules change later. A user's historical assessment should reflect what the rules said *then*, not what they say now.

- Store the evaluated result itself, not just the inputs plus a pointer to "current rules."
- If rules change, new assessments use the new rules; old assessments keep their original recorded result unless an explicit, intentional re-evaluation feature is added.

## Assessment Corrections and Regrouping

This journal keeps one active `PostTradeAssessment` per logical trade. Editing or correcting a review overwrites that active row; it does not create per-edit revision history.

- Supersession is reserved for regrouping that changes logical-trade membership. It stamps `superseded_at` and `superseded_reason` on the old assessment.
- Current queries use the single active row. Regrouping history remains queryable through `list_superseded_post_trade_assessments_for_trade`.
- Do not introduce a revision table or turn ordinary review edits into supersession without an explicit product change.

## Policy Evidence and Reporting R Use Different Risk Conventions

Policy-compliance evidence requires known per-trade risk and must never infer risk from the outcome.

Typical valid sources of "known risk," roughly in order of reliability:
1. A specific preset stop-loss recorded at entry.
2. For a loss-making trade with no recorded SL: the realized loss itself is a defensible real-loss estimate of risk (the trade lost exactly what it risked, at minimum).
3. An opt-in estimate from a captured pre-trade account balance, used only when that data was actually captured at the time (not backfilled or guessed).

What NOT to do for compliance evidence: never derive "risk" from a profitable trade's outcome, and never silently claim that an unknown-risk trade was within policy.

Dashboard and Monitor outcome R, plus daily/weekly risk replay, deliberately use the account policy's standard-risk amount as 1R for every logical trade. That reporting convention is distinct from evidence that proves the trade's actual risk was known and policy-compliant.

## Logical Trades vs. Raw Positions

Distinguish two layers if the app supports scaling in/out or manual trade grouping:

- **Raw positions**: the immutable, broker-sourced members imported from MT5. They remain auditable and are never rewritten by grouping.
- **Logical trades**: the mutable reporting unit that groups/splits/regroups raw positions into what the trader considers "one trade" (e.g. multiple scale-in fills treated as a single position for review purposes).

Keep these layers structurally separate. Dashboard P&L, balance, drawdown, and risk-limit monitoring intentionally replay logical trades in final-close order. Regrouping may therefore change period assignment and path-dependent metrics while leaving imported member positions and total monetary P&L intact.

## Per-Account, Per-Currency Scoping

If accounts are tracked in their own currency, be deliberate about whether the app aggregates or converts across accounts:

- Many journal apps intentionally scope dashboards, risk policies, and metrics to a single account, in that account's native currency, specifically to avoid the complexity and inaccuracy of FX conversion for this purpose.
- Don't add cross-account aggregation or currency conversion as an incidental side effect of an unrelated feature. If a request seems to require it, flag that it's a scope expansion beyond "one account, native currency" rather than implementing it quietly.

## Testing Priorities Specific to This Domain

Beyond the general trading-math tests in `references/trading-engineering.md`, prioritize tests for:

- Rule-result snapshot behavior: saved hard-rule outcomes don't change when rules are edited afterward.
- Correction vs. supersession: ordinary edits overwrite one active assessment; regrouping supersedes the old assessment and keeps that regrouping history queryable.
- Metric-specific R behavior: known-risk sources gate policy-compliance evidence, while Dashboard/Monitor and daily/weekly replay consistently use policy-standard 1R even when per-trade risk is unknown.
- Logical-trade regrouping: grouping/splitting assigns the combined P&L to the logical trade's final close and correctly recomputes daily P&L, balance, drawdown, and risk-limit output without mutating imported positions.
