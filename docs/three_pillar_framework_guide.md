# Operating the three-pillar journal

> **This is the single source of truth for how the Trading Journal applies the Psychology, Risk Management, and Trading System framework.**
>
> The app renders this file on the **Guide** page. If an older design note or screen label differs from this guide, this guide wins.

The journal measures the *quality of a completed trade*, not only its P&L. It is deliberately post-trade and advisory: it never approves, blocks, changes, or sends an MT5 order.

## What the framework answers

| Pillar | Question | Scope in monitoring |
|---|---|---|
| Psychology | Did I execute myself correctly? | Trader-wide across active accounts |
| Risk Management | Did I protect capital and follow the account policy? | Selected MT5 account |
| Trading System | Did I execute a valid, documented setup? | Trader-wide across active accounts |

A profitable trade can be a **Bad Win** when its process failed. A compliant losing trade can be a **Good Loss**. P&L and process quality are intentionally separate.

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
Monitor the latest 20, 30, or 50 full reviews
        ↓
Save a weekly or monthly reflection
        ↓
Choose one corrective action and test it
```

The journal starts at the completed trade. It does not claim to reconstruct every live decision, open exposure, or emotion from MT5 data.

## Logical trades and scaled positions

MT5 exports one completed **position** per row. The journal automatically maps each imported position to its own **logical trade**. A trading idea may use several scaled entries or exits, so **Framework → Review trades** can later regroup compatible positions into one logical trade.

| Layer | What remains true |
|---|---|
| Imported position | Immutable MT5 execution fact: its position ID, timestamps, prices, volume, and P&L are never changed. |
| Logical trade | One position by default, or a user-created group of two or more positions. It receives one assessment and one process score. |
| Account Risk monitoring | Continues to use the original chronological positions, so a group cannot hide a daily/weekly loss, drawdown, or loss-streak event. |

### Create and regroup a logical trade

1. In **Framework → Review trades**, select two or more compatible single-position logical trades.
2. Select **Create logical trade** and optionally add a label, such as `London breakout scale-in`.
3. Save the group, then open the resulting logical trade and complete its one post-trade assessment.

Positions can be grouped only when they share the same MT5 account, symbol, direction, and imported Risk-policy version. The generated label is based on symbol, direction, and first entry when no custom label is supplied. A group becomes one reviewable logical trade, and appears in the dashboard's **Per trade** analysis, when its **last** member position closes.

Logical-trade membership and labels are mutable. Use **Manage positions** from any review to add, remove, split, merge, or disband positions. A membership change never alters an MT5 row. Instead, it supersedes each affected saved assessment, removes it from active pillar scores and roadmap evidence, and requires a new review. The superseded review keeps its original member-position snapshot and remains available in assessment history. A label-only change does not supersede an assessment.

### Group reporting and automatic risk

A logical trade counts once in dashboard logical-trade count, win rate, expectancy, strategy totals, and the **Per trade** analysis. These review analytics recalculate using the **current** grouping. Its net P&L is the sum of member P&L; the logical-trade date is the final member close. Expand the member-position detail during review to audit the individual MT5 rows.

Account balance, daily realized P&L, and account drawdown always use the immutable chronological MT5 positions. Regrouping therefore cannot rewrite account history or Risk-limit monitoring.

For a group, the automatic Risk amount sums its per-position **specific preset SL** and **real-loss** estimates. A **live-account-balance** fallback is account-level: it applies once to the entire logical trade, never once per member position, and takes precedence over lower per-position amounts. The evidence is `verified` only when every member has a specific preset SL; every live-balance or mixed estimate remains advisory. If any member has no usable source, policy compliance is unavailable until the reviewer supplies a verified **Actual risk amount**.

## 1. Set up the evidence before reviewing

1. Add each MT5 account in **Settings → MT5 Accounts** and set its funded capital when known.
2. Save an account **Risk policy** in **Framework → Risk policy**:
   - Standard risk (1R) for normalized reporting;
   - maximum risk per trade for compliance;
   - daily and weekly loss limits, maximum drawdown, maximum loss streak, and minimum R:R.
3. Create one or more **Strategies** in **Settings → Strategies**. Record the rules and available backtest evidence. A full review needs a selected strategy so the System score has evidence to assess.
4. In **Framework → Framework rules**, choose which critical events are hard failures for new or corrected assessments. These settings affect journal scores and alerts only; they never control MT5.

Risk policies are versioned. A completed assessment retains the policy and strategy evidence that was attached when it was saved. Its effective hard-rule events are also snapshotted, so later configuration changes do not rewrite an earlier review.

## 2. What is and is not scored automatically

| State shown in Review trades | What it means | Three-pillar score? | What to do |
|---|---|---:|---|
| Needs review | The logical trade has no usable automatic risk evidence and no saved assessment. | No | Open it and complete a post-trade assessment. |
| Automatic evidence | The importer found a usable risk estimate. | No | Use it as evidence, then complete a human assessment if the trade should count. |
| Reviewed | All 13 criteria, the strategy, notes, and required follow-up were saved. | Yes | It contributes to the rolling scores and roadmap. |

Automatic MT5 evidence is helpful, but it cannot assess Psychology or System execution. It never creates a completed Risk, Psychology, System, Process, readiness, or roadmap score on its own.

### Automatic risk evidence

| Source | Confidence | Interpretation |
|---|---|---|
| Specific preset SL | Verified | MT5-calculated initial risk was present in the export. |
| Real-loss estimate | Inferred | `abs(net P&L)` for a losing trade without a calculated initial risk. |
| Live-account-balance estimate | Conservative | The latest schema-v4 MT5 balance for a profitable trade without an entry stop. It is a conservative fallback, not proof of the original stop. |

The app compares the available amount with the account's maximum-risk policy and labels it within policy, over policy, or unavailable. Enter a verified **Actual risk amount** during review when the automatic amount is not the best evidence. It replaces the automatic amount for that logical trade's policy comparison, but it does **not** rewrite the immutable MT5-position chronology used for daily/weekly limits, drawdown, or loss-streak monitoring.

### Automatic limit monitoring and shutdown review

Daily loss, weekly loss, drawdown, and losing-streak limits are calculated from completed MT5 positions. When a position first reaches a limit, the app records a **Risk monitor reached** warning. That position is not automatically a failed trade: the journal cannot infer the trader's intention or what was known while an order was open.

For a later position whose entry timestamp is after an earlier completed position reached a limit, the app shows a **Shutdown review** candidate. It is a prompt to inspect the sequence, not a verdict. Select **Trading after hard shutdown** only when your post-trade review confirms that the entry broke your own stop rule and that hard rule is enabled in **Framework rules** when the assessment is saved. Only that confirmed, enabled event changes the Hard-rule status to `FAIL`.

## 3. Complete one post-trade assessment

Open **Framework → Review trades**, choose a logical trade from **Needs review**, **Automatic evidence**, or **Reviewed**, and rate all 13 criteria once. A grouped logical trade contributes one review to the rolling sample, not one review per member position.

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

### Risk Management criteria — 35% / 20% / 25% / 20%

| Criterion | Weight | Review question |
|---|---:|---|
| Policy adherence | 35% | Was the trade compatible with the account Risk policy? |
| Position-size accuracy | 20% | Was position size appropriate for the intended risk? |
| Stop discipline | 25% | Was the stop/invalidation respected rather than widened or ignored? |
| Exposure-limit compliance | 20% | Were the applicable exposure controls respected? |

Open-risk and correlation controls are self-assessed because the closed-trade MT5 bridge cannot prove them automatically.

### Trading System criteria — 30% / 20% / 20% / 15% / 15%

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
Process score = (Psychology + Risk Management + Trading System) / 3
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

Risk Management = 100
Trading System  = 100

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

The following events can be enabled as hard failures in **Framework rules**:

| Event | Affected pillar(s) when enabled | Meaning |
|---|---|---|
| Oversized revenge trade | Psychology and Risk Management | Emotional size increase or revenge behaviour. |
| Mandatory setup absent | Trading System | Trade was taken without a required setup. |
| Deliberately widened stop | Risk Management | Risk was increased by moving the stop farther away. |
| Trading after hard shutdown | Risk Management | Trade was taken after a configured stop condition. |

Hard rules do three things:

1. Set the individual trade's **Hard-rule status** to `FAIL` and its classification to `Bad`.
2. Mark the affected pillar as hard-blocked while that reviewed trade remains in the selected rolling window.
3. Prevent the readiness assessment from reporting `Ready`, even if its numeric score is high.

Reason tags also make recurring patterns visible. Psychology critical tags include revenge, emotional sizing, and failure to reset after a loss; Risk critical tags include daily/weekly/drawdown/exposure breaches and stop widening; the System critical tag is a mandatory setup absent. A tag is not automatically a hard rule unless its related hard-rule setting is enabled when the assessment is saved. In particular, automatic MT5 limit warnings and Shutdown review candidates never create a hard failure without a reviewer recording the enabled **Trading after hard shutdown** event. Changing Framework rules later applies to new or corrected assessments; it does not revise historical classifications.

## 6. How rolling monitoring is calculated

In **Framework → Monitor**, choose a 20-, 30-, or 50-trade window. Only fully **Reviewed** trades enter the window. Automatic evidence and unreviewed imports are counted separately and do not improve a score.

The Monitor computes a second set of period components from the reviewed window. These are not a simple average of the visible per-trade pillar scores; they are designed to reveal repeated behaviour and evidence quality.

### Psychology monitoring score

| Component | Weight | How it is measured |
|---|---:|---|
| Rule adherence | 35% | Average reviewed Rule adherence grade. |
| Impulse control | 25% | Average reviewed Impulse control grade. |
| Emotional control | 20% | Average reviewed Emotional control grade. |
| Post-loss discipline | 20% | The next reviewed trade after a loss across all active accounts: its Impulse control grade, or 0 when tagged `post_loss_reset`. It is 100 when the sample has no eligible post-loss sequence. |

### Risk Management monitoring score

| Component | Weight | How it is measured |
|---|---:|---|
| Policy adherence | 35% | Average reviewed Policy adherence grade. |
| Stop discipline | 25% | Average reviewed Stop discipline grade. |
| Limit compliance | 25% | 100 for a reviewed trade with no historical daily/weekly/drawdown/streak event; 0 when an event occurred. This affects the Risk monitoring component only; it does not automatically set the trade's Hard-rule status to `FAIL`. |
| Exposure control | 15% | Average reviewed Exposure-limit compliance grade. |

### Trading System monitoring score

| Component | Weight | How it is measured |
|---|---:|---|
| Setup validity | 20% | Average reviewed Setup validity grade. |
| Execution fidelity | 20% | Average of Entry, Invalidation, and Management/exit grades. |
| Context alignment | 15% | Average reviewed Context alignment grade. |
| Evidence quality | 20% | 100 when the attached strategy has a description, backtest dates, and at least 100 backtest trades; 50 when it is documented but below 100; otherwise 0. |
| Edge evidence | 25% | 100 for at least 100 backtest trades with positive expectancy after costs; 50 for at least 50 with positive expectancy; otherwise 0. |

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

## 8. Roadmap gates

The three pillars progress in parallel:

| Level | Gate |
|---|---|
| Define | Rules and evidence are documented. |
| Test | Testing or practice evidence is documented. System testing requires 100+ backtest trades with positive expectancy after costs. |
| Execute | 20 full reviews, score at least 70, and no active hard failure. |
| Measure | 30 full reviews, a saved weekly or monthly review for the latest completed period, a 30-review score of at least 80, and no active hard failure. |
| Optimize | A hypothesis, baseline, result, and keep/reject decision are recorded. |

**Psychology** roadmap evidence is behaviour-focused; **Risk** evidence is account-specific policy and sizing evidence; **System** evidence is strategy rules, examples, and backtest evidence. Complete the evidence in **Framework → Roadmap** only when it can be explained and revisited.

## 9. Data limits

The current MT5 bridge supplies completed positions only. It can retrospectively monitor realized R, daily/weekly limits, drawdown, loss streak, exported entry-stop information, and account-balance snapshots. It cannot prove historical open risk, correlated exposure, every intratrade stop adjustment, mental state, planned intent, or a real original stop for a profitable no-SL export. Those limitations are why the journal combines automatic evidence with a deliberate human post-trade assessment.
