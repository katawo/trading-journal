# Framework & Journal Domain Reference

Use this for post-trade journal/review applications built around a scoring or assessment framework (e.g. a multi-pillar review such as Psychology/Risk/Trading System), as opposed to an execution or backtesting platform. The correctness concerns here are about auditability and data integrity of *assessments*, not about order/fill mechanics — treat both this file and `references/trading-engineering.md` as potentially relevant to the same repo, covering different halves of the domain.

Always check for a repo-specific source-of-truth doc for the actual scoring/workflow rules (e.g. a "framework guide" under `docs/`) before implementing — this reference covers the general *shape* of these correctness concerns, not any one repo's specific rubric.

## Snapshot Immutability for Rule-Based Results

If an assessment records a rule-based outcome (e.g. a hard-rule "Clear"/"Fail" result) at the time it was made, that recorded outcome must not be silently recomputed if the underlying rules change later. A user's historical assessment should reflect what the rules said *then*, not what they say now.

- Store the evaluated result itself, not just the inputs plus a pointer to "current rules."
- If rules change, new assessments use the new rules; old assessments keep their original recorded result unless there's an explicit, intentional re-evaluation feature — and even then, that should likely produce a new revision (see below) rather than overwrite history.

## Corrections Version, They Don't Overwrite

When a user edits or corrects a previously saved assessment, prefer creating a new revision that references the original rather than mutating the original row in place.

- This keeps prior evidence/reasoning auditable — useful for understanding how someone's self-assessment or reasoning changed, not just the final number.
- When implementing, check whether the repo has a `*Revision` pattern already established (a base assessment table plus a revisions table) and follow that shape rather than introducing ad hoc edit-in-place logic.
- Queries that show "the current view" of an assessment should resolve to the latest revision; queries about history should be able to walk all revisions.

## R-Multiples Require Known Risk

If the app computes R-multiples (P&L expressed as a multiple of risked amount), a trade should only be included in that metric once its initial risk is actually established — never inferred from the outcome.

Typical valid sources of "known risk," roughly in order of reliability:
1. A specific preset stop-loss recorded at entry.
2. For a loss-making trade with no recorded SL: the realized loss itself is a defensible real-loss estimate of risk (the trade lost exactly what it risked, at minimum).
3. An opt-in estimate from a captured pre-trade account balance, used only when that data was actually captured at the time (not backfilled or guessed).

What NOT to do: never derive "risk" from a profitable trade's outcome (there's no way to know what was actually risked just because it won), and never silently default an unknown-risk trade to some placeholder R value — exclude it from R-based metrics instead, and make that exclusion visible rather than silent.

## Logical Trades vs. Raw Positions

Distinguish two layers if the app supports scaling in/out or manual trade grouping:

- **Raw positions**: the immutable, broker-sourced record of what actually happened (fills/positions from the import). Account-level metrics that must reconcile against real account history — daily P&L, balance curve, drawdown, any risk-limit monitoring — should always be computed from raw positions, never from user-editable groupings.
- **Logical trades**: a user-facing, potentially mutable view that groups/splits/regroups raw positions into what the trader considers "one trade" (e.g. multiple scale-in fills treated as a single position for review purposes).

Keep these layers structurally separate. A user regrouping logical trades for review purposes must never change what the account-level, audit-sensitive metrics report — if it does, that's a correctness bug worth flagging even if no one explicitly asked about it.

## Per-Account, Per-Currency Scoping

If accounts are tracked in their own currency, be deliberate about whether the app aggregates or converts across accounts:

- Many journal apps intentionally scope dashboards, risk policies, and metrics to a single account, in that account's native currency, specifically to avoid the complexity and inaccuracy of FX conversion for this purpose.
- Don't add cross-account aggregation or currency conversion as an incidental side effect of an unrelated feature. If a request seems to require it, flag that it's a scope expansion beyond "one account, native currency" rather than implementing it quietly.

## Testing Priorities Specific to This Domain

Beyond the general trading-math tests in `references/trading-engineering.md`, prioritize tests for:

- Rule-result snapshot behavior: saved hard-rule outcomes don't change when rules are edited afterward.
- Revision creation vs. mutation: editing an assessment produces a new revision; the original remains queryable.
- R-multiple inclusion/exclusion: trades with unknown risk are excluded from R metrics; each of the valid known-risk sources is covered by its own test case; a profitable trade with no SL and no captured pre-trade balance is excluded, not estimated.
- Logical-trade regrouping does not alter raw-position-derived metrics (daily P&L, drawdown, risk-limit checks) — a good regression test groups/splits a set of raw positions several ways and asserts those metrics are unchanged.
