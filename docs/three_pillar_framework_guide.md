# Operating the three-pillar journal

> **This is the single source of truth for how Trade Compass applies the Psychology, Risk management, and Trading system framework.**
>
> The app renders this file on the **Guide** page. It is maintained with the current app labels and workflow.

The journal measures the *quality of a completed trade*, not only its P&L. It is deliberately post-trade and advisory: it never approves, blocks, changes, or sends an MT5 order.

## What the framework answers

| Pillar | Question | Scope in monitoring |
|---|---|---|
| Psychology | Did I execute myself correctly? | Selected MT5 account |
| Risk management | Did I protect capital and follow the account policy? | Selected MT5 account |
| Trading system | Did I execute a valid, documented setup? | Selected MT5 account (one independent system per account) |

A profitable trade can be a **Bad Win** when its process failed. A compliant losing trade can be a **Good Loss**. P&L and process quality are intentionally separate.

Trade cards and detail views also show factual direction and outcome tags: **Long** or **Short** comes from the imported MT5 direction, while **Profit**, **Loss**, or **Breakeven** comes from realized net P&L after costs. These labels do not change the independent process classification.

## Operating loop

```text
MT5 closes a position
        ↓
Import factual execution
        ↓
Optionally group scaled positions into one logical trade
        ↓
Review the closed trade against all three pillars
        ↓
Monitor the latest 10 to 100 approved Auto and Manual reviews
        ↓
Save a weekly or monthly reflection
        ↓
Trade Compass selects one evidence-backed coaching action and tracks it through a reviewed-trade sample
```

The journal starts at the completed trade. It does not claim to reconstruct every live decision, open exposure, or emotion from MT5 data.

## Coaching loop

Dashboard and **Bearings → Monitor** automatically maintain one post-trade coaching focus. The coach first addresses an active hard-rule failure, then recurring reviewed issues, then a weak pillar component, and finally missing reviewed evidence. Each focus gives one practical next-trade behavior, a target, and a reviewed-trade sample; it never approves, blocks, or changes an MT5 order.

An active experiment remains in place until its sample is ready for reflection. A new hard-rule safety focus can supersede it, with the previous focus retained in history. Quick Risk Checks and Deep Reviews both advance coaching progress; only a Deep Review can add detailed behavioural, issue, and context evidence.

## Logical trades and scaled positions

MT5 exports one completed **position** per row. The journal automatically maps each imported position to its own **logical trade**. A trading idea may use several scaled entries or exits, so **Bearings → Review** can later regroup compatible positions into one logical trade.

| Layer | What remains true |
|---|---|
| Imported position | Immutable MT5 execution fact: its position ID, timestamps, prices, volume, and P&L are never changed. |
| Logical trade | One position by default, or a user-created group of two or more positions. It receives one assessment and one process score. |
| Account risk monitoring | Continues to use the original chronological positions, so a group cannot hide a daily/weekly loss, drawdown, or loss-streak event. |

### Create and regroup a logical trade

1. In **Bearings → Review**, select two or more compatible logical trades. A selection may contain standalone trades, existing groups, or both; every selected trade moves with all of its positions.
2. Select **Group selected**, review the locked source-trade summary, and optionally add a label such as `London breakout scale-in`.
3. Confirm the merge. The journal creates a new logical-trade ID, then you can open the resulting trade and complete its one post-trade assessment.

Positions can be grouped only when they share the same MT5 account, symbol, direction, and imported risk-policy version. The generated label is based on symbol, direction, and first entry when no custom label is supplied. A group becomes one reviewable logical trade, and appears in the dashboard's **Per trade** analysis when its **last** member position closes.

The same row selection also supports **Quick review selected**. It can include single-position or grouped logical trades that are Awaiting approval or Require review. The confirmation lists the selected policy-evidence states before saving approved Auto reviews; a later Manual Review can replace any of them.

Logical-trade membership and labels are mutable. **Group selected** always merges whole logical trades into a new logical-trade ID; use **Manage positions** from any review for fine-grained position changes, or to split and disband positions. A membership change never alters an MT5 row. Instead, it supersedes each affected saved assessment, removes it from active pillar scores and roadmap evidence, and requires a new review. The superseded review keeps its original member-position snapshot and remains available in assessment history. A label-only change does not supersede an assessment.

### Group reporting and automatic risk

A logical trade counts once in dashboard logical-trade count, win rate, expectancy, strategy totals, and the **Per trade** analysis. These review analytics recalculate using the **current** grouping. Its net P&L is the sum of member P&L; the logical-trade date is the final member close. Expand the member-position detail during review to audit the individual MT5 rows.

Account balance, daily realized P&L, and account drawdown always use the immutable chronological MT5 positions. Regrouping therefore cannot rewrite account history or Risk-limit monitoring.

For a group, the automatic risk amount sums its per-position **specific preset SL** and **real-loss** estimates. An enabled **pre-trade-balance** fallback uses only the actual balance captured by MT5 immediately before the earliest entry and applies once to the logical trade, never once per member position. It is advisory and conservative; it never changes a missing MT5 SL. If MT5 could not establish the pre-entry balance, policy compliance is unavailable until the reviewer supplies a verified **Actual risk amount**.

## 1. Set up the evidence before reviewing

1. Add each MT5 account in **Settings → Account & risk** and set its funded capital when known.
2. Save an account **Risk policy** in **Settings → Account & risk**:
   - Standard risk (1R) for normalized reporting;
   - maximum risk per trade for compliance;
   - daily and weekly loss limits, maximum drawdown, maximum loss streak, and minimum R:R.
   - optionally enable **Use MT5 pre-trade balance as advisory no-SL risk evidence**. It defaults off and never uses funded capital or the current account balance as a substitute.
3. Create one or more **Strategies** in **Settings → Strategies**. Record the rules and available backtest evidence. A full review needs a selected strategy so the System score has evidence to assess.
4. In **Settings → Review rules**, choose which critical events are hard failures for new or corrected assessments. These settings affect journal scores and alerts only; they never control MT5.

Risk policies are versioned. A completed assessment retains the policy and strategy evidence that was attached when it was saved. Its effective hard-rule events are also snapshotted, so later configuration changes do not rewrite an earlier review.

## 2. What is and is not scored automatically

| Review status | What it means | Three-pillar score? | What to do |
|---|---|---:|---|
| Requires review | Automatic risk evidence is over policy or unavailable, and has not been approved yet. | No | Quick review to approve it in one click, or complete a full post-trade assessment. |
| Auto-reviewed | Automatic risk evidence is within policy and still awaiting your approval. | No | Approve it in one click, or complete a Deep Review instead. |
| Quick Risk Check | Automatic evidence you approved (tagged **Auto**). | Yes | Its normalized criterion grades enter the same rolling score as a Manual Review. |
| Deep Review | A full 13-criterion manual assessment (tagged **Manual**). | Yes | It adds detailed tags, corrective action, and optional setup/session/regime context. |

Automatic risk evidence enters the Psychology, Risk management, and Trading system rolling scores only after you approve it in one click as a **Quick Risk Check**. Approved Auto and Manual reviews have equal scoring weight, readiness, roadmap, and coaching status. A full **Deep Review** remains the only way to record behavioural/system notes, violations, hard-rule events, corrective actions, and optional context; it replaces an earlier Quick Risk Check for the same logical trade.

### Automatic risk evidence

| Source | Confidence | Interpretation |
|---|---|---|
| Specific preset SL | Verified | MT5-calculated initial risk was present in the export. |
| Real-loss estimate | Inferred | `abs(net P&L)` for a losing trade without a calculated initial risk. |
| Pre-trade-balance estimate | Conservative | The actual MT5 balance immediately before a profitable no-SL position opened, captured from the MT5 deal ledger. It is available only when enabled in the account Risk policy. |

The app compares the available amount with the account's maximum-risk policy and labels it within policy, over policy, or unavailable. Enter a verified **Actual risk amount** during review when the automatic amount is not the best evidence. It replaces the automatic amount for that logical trade's policy comparison, but it does **not** rewrite the immutable MT5-position chronology used for daily/weekly limits, drawdown, or loss-streak monitoring.

### Automatic limit monitoring and shutdown review

Daily loss, weekly loss, drawdown, and losing-streak limits are calculated from completed MT5 positions. When a position first reaches a limit, the app records a **Risk monitor reached** warning. That position is not automatically a failed trade: the journal cannot infer the trader's intention or what was known while an order was open.

For a later position whose entry timestamp is after an earlier completed position reached a limit, the app shows a **Shutdown review** candidate. It is a prompt to inspect the sequence, not a verdict. Select **Trading after hard shutdown** only when your post-trade review confirms that the entry broke your own stop rule and that hard rule is enabled in **Settings → Review rules** when the assessment is saved. Only that confirmed, enabled event changes the Hard-rule status to `FAIL`.

## 3. Complete one post-trade assessment

Open **Bearings → Review** and choose a logical trade from **Requires review**, **Auto-reviewed**, or **Reviewed**. Any automatic risk evidence can be accepted in one click, or you can rate all 13 criteria in a full assessment. A grouped logical trade contributes one review to the rolling sample, not one review per member position.

| Rating | Numeric value | Use it when |
|---|---:|---|
| Pass | 100 | The documented standard was met. |
| Partial | 50 | A meaningful deviation occurred, but the criterion was not wholly failed. |
| Fail | 0 | The documented standard was not met. |

The review must include a short post-trade note. Add at least one reason tag whenever any criterion is **Fail**. Add one specific corrective action whenever any criterion is **Partial** or **Fail**, or when a hard-rule event is recorded. This turns a score into a testable improvement rather than a label.

### Psychology criteria — 35% / 25% / 20% / 20%

| Criterion | Weight | Review question |
|---|---:|---|
| Rule adherence | 35% | Did I follow the documented behavioural and execution rules? |
| Impulse control | 25% | Did I avoid chasing, boredom entries, and revenge behaviour? |
| Emotional control | 20% | Did fear, greed, frustration, or FOMO change the decision? |
| Patience and discipline | 20% | Did I wait for the valid opportunity and execute it without improvising? |

### Risk management criteria — 35% / 20% / 25% / 20%

| Criterion | Weight | Review question |
|---|---:|---|
| Policy adherence | 35% | Was the trade compatible with the account Risk policy? |
| Position-size accuracy | 20% | Was position size appropriate for the intended risk? |
| Stop discipline | 25% | Was the stop/invalidation respected rather than widened or ignored? |
| Exposure-limit compliance | 20% | Were the applicable exposure controls respected? |

Open-risk and correlation controls are self-assessed because the closed-trade MT5 bridge cannot prove them automatically.

### Trading system criteria — 30% / 20% / 20% / 15% / 15%

| Criterion | Weight | Review question |
|---|---:|---|
| Setup validity | 30% | Was the chosen strategy setup actually present? |
| Context alignment | 20% | Did market, session, timeframe, and regime meet the strategy rules? |
| Entry fidelity | 20% | Did entry follow the documented trigger? |
| Invalidation fidelity | 15% | Was the invalidation/stop logic applied as documented? |
| Management and exit fidelity | 15% | Was trade management and exit consistent with the strategy? |

## 4. How a single trade is scored

Each pillar is the weighted sum of its criterion values. The raw **Process score** is the simple average of the three raw pillar scores:

```text
Pillar score  = Σ(criterion value × criterion weight)
Process score = (Psychology + Risk management + Trading system) / 3
```

The journal deliberately shows two separate results:

| Result | Rule |
|---|---|
| **Trade quality** | `Good` at 70 or above; `Needs improvement` below 70. |
| **Hard-rule status** | `Clear` unless the reviewer records an enabled hard-rule event; `Fail` when one applies. The effective event is snapshotted when saved. |
| **Classification** | A Good/Needs-improvement/Bad Win, Loss, or Breakeven. Any hard-rule failure is `Bad`, regardless of the raw score. |

This prevents a very low raw score from being presented as a Good trade merely because no hard rule was selected.

### Worked example: a good trade with one behavioural deviation

Assume every criterion is **Pass** except Psychology **Rule adherence**, which is **Partial**.

```text
Psychology = (50 × 35%) + (100 × 25%) + (100 × 20%) + (100 × 20%)
           = 17.5 + 25 + 20 + 20
           = 82.5

Risk management = 100
Trading system  = 100

Raw Process score = (82.5 + 100 + 100) / 3
                  = 94.17
```

This trade has **Good** quality and a **Clear** hard-rule status if no hard-rule event applies. Its P&L then determines whether it is shown as a Good Win, Good Loss, or Good Breakeven. The 94.17 does not mean the Partial rating is ignored: the corrective action and tag remain available for pattern analysis.

### Worked example: why a high average cannot hide a severe breach

Assume the same 94.17 raw score, but the trader records an enabled **Deliberately widened stop** hard-rule event.

```text
Raw Process score = 94.17     (retained as evidence)
Hard-rule status  = FAIL      (hard rule overrides the classification)
Classification     = Bad Win / Bad Loss / Bad Breakeven
Risk pillar        = hard-blocked in the rolling sample
```

If the trade made money, it is a **Bad Win**. If it lost money, it is a **Bad Loss**. The raw score remains visible so the review is auditable; it does not cancel the hard failure.

## 5. Hard rules and critical violations

The following events can be enabled as hard failures in **Settings → Review rules**:

| Event | Affected pillar(s) when enabled | Meaning |
|---|---|---|
| Oversized revenge trade | Psychology and Risk management | Emotional size increase or revenge behaviour. |
| Mandatory setup absent | Trading system | Trade was taken without a required setup. |
| Deliberately widened stop | Risk management | Risk was increased by moving the stop farther away. |
| Trading after hard shutdown | Risk management | Trade was taken after a configured stop condition. |

Hard rules do three things:

1. Set the individual trade's **Hard-rule status** to `FAIL` and its classification to `Bad`.
2. Mark the affected pillar as hard-blocked while that reviewed trade remains in the selected rolling window.
3. Prevent the readiness assessment from reporting `Ready`, even if its numeric score is high.

Reason tags also make recurring patterns visible. Psychology critical tags include revenge, emotional sizing, and failure to reset after a loss; Risk critical tags include daily/weekly/drawdown/exposure breaches and stop widening; the System critical tag is a mandatory setup absent. A tag is not automatically a hard rule unless its related hard-rule setting is enabled when the assessment is saved. In particular, automatic MT5 limit warnings and Shutdown review candidates never create a hard failure without a reviewer recording the enabled **Trading after hard shutdown** event. Changing Review rules later applies to new or corrected assessments; it does not revise historical classifications.

## 6. How rolling monitoring is calculated

In **Bearings → Monitor**, choose a rolling window from 10 to 100 trades (slider, step 5; the Dashboard's compact widget always shows a fixed 20-trade snapshot). Pillar scores, readiness, recurring issues, and coaching use the latest approved Auto and Manual reviews. Quick Risk Checks remain visible separately as selected-account risk-evidence coverage over the latest closed logical trades. A smaller reviewed window reaches the repeated-critical-violation Caution threshold sooner than a larger one, since that threshold is a fixed count, not a percentage of the window.

The Monitor also has an **Analysis period** (this month, last 90 days, all time, or custom). It changes descriptive charts only; it never changes a rolling score, readiness, or roadmap gate. Its Overview highlights the next evidence-led actions, while the Process & outcomes, Risk, and System & context views separate process quality from outcome, review coverage from policy evidence, and strategy/context observations from causal claims. Outcome comparisons use the same standard 1R convention as the Performance dashboard. Trades without a usable standard 1R are excluded from R charts and reported as missing evidence.

The Monitor computes a second set of period components from the reviewed window. These are not a simple average of the visible per-trade pillar scores; they are designed to reveal repeated behaviour and evidence quality.

### Psychology monitoring score

| Component | Weight | How it is measured |
|---|---:|---|
| Rule adherence | 35% | Average reviewed Rule adherence grade. |
| Impulse control | 25% | Average reviewed Impulse control grade. |
| Emotional control | 20% | Average reviewed Emotional control grade. |
| Post-loss discipline | 20% | The next reviewed trade after a loss on this account: its Impulse control grade, or 0 when tagged `post_loss_reset`. It is 100 when the sample has no eligible post-loss sequence. |

### Risk management monitoring score

| Component | Weight | How it is measured |
|---|---:|---|
| Policy adherence | 35% | Average reviewed Policy adherence grade. |
| Stop discipline | 25% | Average reviewed Stop discipline grade. |
| Limit compliance | 25% | 100 for a reviewed trade with no historical daily/weekly/drawdown/streak event; 0 when an event occurred. This affects the Risk monitoring component only; it does not automatically set the trade's Hard-rule status to `FAIL`. |
| Exposure control | 15% | Average reviewed Exposure-limit compliance grade. |

### Trading system monitoring score

| Component | Weight | How it is measured |
|---|---:|---|
| Setup validity | 20% | Average reviewed Setup validity grade. |
| Execution fidelity | 20% | Average of Entry, Invalidation, and Management/exit grades. |
| Context alignment | 15% | Average reviewed Context alignment grade. |
| Evidence quality | 20% | 100 when the attached strategy's backtest is marked verified; otherwise 0. |
| Edge evidence | 25% | 100 when the attached strategy's backtest is marked verified; otherwise 0. |

### Status, completeness, and readiness

- A pillar remains **Incomplete** until the selected window is full, even though the app can show an early numeric score from the reviews collected so far.
- A pillar is **Caution** when its repeated critical-violation count reaches the configured threshold. Its numeric score is capped at **59** until a later weekly or monthly period review is saved.
- A pillar is **Fail** when an active hard-rule failure exists in the selected window. Hard failure takes priority over caution and the numeric score.
- **Readiness** is the *lowest* complete pillar score, never the average. It is incomplete until all three pillars have a full selected window and measurable evidence. It is `FAIL` whenever any pillar has an active hard block.
- A score below **70** produces a developing-pillar warning. Risk caution/stop, active hard blocks, and overdue period reviews create retrospective alerts.

## 7. Use the weekly or monthly review to improve one thing

When a weekly or monthly review is due, save:

- a concise reflection on the completed period;
- the recurring tags or score weakness being addressed; and
- **one** priority corrective action for the next period.

The saved review snapshots the completed period's scores, alerts, recurring issues, and action. Later imports do not rewrite that saved period. A saved review after the latest repeated critical violation removes the 59-point caution cap; it does not erase an active hard block or change historic trade evidence.

Use the diagnosis rather than recent P&L to choose the action:

| Pattern | Interpretation | Example action |
|---|---|---|
| System strong, Psychology weak | The edge may be intact; execution is the problem. | Add a post-loss pause rule and assess it for the next 20 reviews. |
| Psychology and System strong, Risk weak | Decision quality is acceptable but capital protection is not. | Rehearse the sizing calculation and require a recorded risk amount for the next 10 trades. |
| Psychology and Risk strong, System weak | The process is disciplined but the setup/evidence needs work. | Freeze the strategy rules and collect or validate more backtest evidence before changing execution. |

Do not change a strategy solely because of a small recent P&L sample. Make one hypothesis, collect evidence, then keep or reject the change.

### Coaching focus

The Monitor can hold one active journal-wide **Coaching focus**. Trade Compass automatically selects a focus from reviewed evidence, then tracks 5, 10, or 20 newly reviewed trades before it is resolved with a reflection. All three pillars' focuses use the selected account's evidence, because each account represents an independent system. The app remains post-trade and advisory.

### Optional review context

Deep Reviews may record an optional strategy setup, session, and market regime from controlled lists in **Settings**. These labels make the Monitor's context tables comparable. They are descriptive: small samples are not proof that a setup or regime caused an outcome.

Context charts deliberately use Manual Reviews only because Quick Risk Checks do not contain optional context. Treat fewer than five Manual Reviews in a context bucket as directional evidence only, not a conclusion or a reason to change the trading system.

## 8. Improvement roadmap and gates

The three pillars progress in parallel:

| Level | Gate |
|---|---|
| Define | Rules and evidence are documented. |
| Test | Testing or practice evidence is documented. System testing requires the strategy's backtest to be marked verified. |
| Execute | 20 full reviews, score at least 70, and no active hard failure. |
| Measure | 30 full reviews, a saved weekly or monthly review for the latest completed period, a 30-review score of at least 80, and no active hard failure. |
| Optimize | A hypothesis, baseline, result, and keep/reject decision are recorded. |

The readiness roadmap progresses in parallel across the three pillars. **Psychology** evidence is behaviour-focused; **Risk management** evidence is account-specific policy and sizing evidence; **Trading system** evidence is strategy rules, examples, and backtest evidence.

Most roadmap items are detected automatically from data already saved elsewhere in the journal, and never need a manual click:

- Execute and Measure (all three pillars) complete themselves the moment their review-count/score/hard-failure/period-review conditions are met.
- Optimize (all three pillars) completes itself once a Coaching focus for that pillar has been resolved (completed or abandoned) with a reflection note.
- Risk's Define step completes itself once the account's Risk policy is saved.
- Trading system's Define, Test, and backtest steps complete themselves from the strategy bound to the selected account: a documented description, a setup with a documented example, and a backtest marked verified respectively.

Only the items with no equivalent structured data anywhere in the app stay self-certified — Psychology's Define and Test steps, and Risk's "risk-calculation or simulation evidence" Test step. Complete those in **Bearings → Improve** only when they can be explained and revisited.

## 9. Data limits

The post-trade MT5 bridge supplies completed positions only. It can retrospectively monitor realized R, daily/weekly limits, drawdown, loss streak, exported entry-stop information, and account-balance snapshots. A separate live snapshot feed shows current stop-based open risk and unprotected positions, but it is temporary operational state: it never becomes post-trade evidence, historical correlation proof, intratrade stop-adjustment history, mental-state evidence, planned intent, or a real original stop for a profitable no-SL export. Those limitations are why the journal combines automatic evidence with a deliberate human post-trade assessment.
