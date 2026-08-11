# Operating the three-pillar journal

The canonical model and formulas are in [Three-Pillar-Trading-Framework.md](Three-Pillar-Trading-Framework.md). This guide explains how the app applies it.

## Workflow

```text
MT5 closes a position → Import factual execution → Assess → Monitor a rolling sample → Save weekly/monthly review → Improve one action
```

The journal is post-trade and advisory. It does not approve, block, change, or send an MT5 order.

## Set up

1. Add an MT5 account and its funded capital in **Settings**.
2. Save an account **Risk policy**: Standard risk (1R), maximum per-trade risk, daily/weekly limits, drawdown, loss streak, and minimum R:R.
3. Create a **Strategy** with its rules and available backtest evidence.
4. In **Framework → Framework rules**, decide which critical events are hard failures. These affect journal status only.

Psychology and Trading System monitoring is trader-wide. Risk monitoring and Risk roadmap evidence are account-specific.

## Review a closed trade

Open **Framework → Review trades**, filter by status, then open a row.

Rate all 13 criteria as:

| Rating | Value | Meaning |
|---|---:|---|
| Pass | 100 | The documented standard was met. |
| Partial | 50 | A meaningful deviation exists but the trade was not a complete failure. |
| Fail | 0 | The documented standard was not met. |

Add reason tags to identify recurring causes. A corrective action is required for any Partial, Fail, or hard-rule event.

The app calculates the three weighted raw pillar scores and their average. A hard-rule event does not erase the raw data, but it sets **Process Quality = FAIL**. A profitable failed trade is a **Bad Win**; a compliant losing trade is a **Good Loss**.

## Automatic Risk evidence

MT5 imports may expose one of these risk sources:

| Source | Confidence | Meaning |
|---|---|---|
| Specific preset SL | Verified | MT5-calculated initial risk. |
| Real-loss estimate | Inferred | `abs(net P&L)` for a loss without calculated initial risk. |
| Live-account-balance estimate | Conservative | Current MT5 balance for a profitable no-SL export. |

The app can mark the evidence within policy, over policy, or unavailable. This is not a completed Risk score and never advances the framework, because Psychology and System execution still require a human assessment.

## Monitor and review periods

**Framework → Monitor** shows rolling 20, 30, or 50-review pillar scores, readiness, active alerts, issue frequency, execution trend, and process-quality distribution.

Readiness is the lowest complete pillar score, never an average that can hide a weak area.

Save a weekly or monthly period review when it is due. The saved review snapshots the completed period's scores, alerts, recurring issues, reflection, and one priority corrective action; later trades do not rewrite it.

- One hard failure in the active rolling window marks the affected pillar `FAIL`.
- Repeated critical breaches cap the numeric pillar score at 59 until a period review is completed.
- Alerts remain retrospective and advisory.

## Roadmap gates

| Level | Gate |
|---|---|
| Define | Rules and evidence are documented. |
| Test | Testing evidence is documented. |
| Execute | 20 full reviews, score ≥70, no active hard failure. |
| Measure | 30 full reviews, score ≥80, no active hard failure, and a saved period review. |
| Optimize | A hypothesis, baseline, result, and keep/reject decision are recorded. |

Use the weak pillar or the most frequent tagged issue to select one corrective action. Do not change the strategy solely because of a small recent P&L sample.

## Data limits

The current MT5 bridge supplies closed positions. It can retrospectively monitor realized R, daily/weekly limits, drawdown, and loss streak. It cannot prove historical open risk, correlated exposure, every intratrade stop adjustment, mental state, or planned intent. Those items are deliberately human-assessed or shown as unavailable.
