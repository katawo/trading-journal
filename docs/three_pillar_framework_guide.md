# Three-Pillar Trading Framework Guide

## Purpose

This journal is a **post-trade development system**. It helps a trader separate three questions that profit and loss alone cannot answer:

| Pillar | Question | Objective |
|---|---|---|
| Psychology | Can I execute my plan consistently? | Identify and correct behaviour that breaks the process. |
| Risk management | Can I survive adverse outcomes? | Keep loss, drawdown, and exposure within defined limits. |
| Trading system | Is the completed trade part of a repeatable edge? | Prove that decisions followed a documented, testable setup. |

The framework is advisory and post-trade only. MT5 remains the execution terminal: the journal never approves, blocks, changes, or submits an order.

> A profitable rule-breaking trade can be poor process. A losing rule-following trade can be good process.

## Framework loop

```text
Define rules → Import closed trade → Review evidence → Measure → Improve one thing → Repeat
```

The three pillars develop in parallel. Do not wait to “finish” Psychology before defining Risk or the Trading System.

## What the journal records

### Immutable MT5 evidence

The MT5 export supplies the completed position: time, symbol, direction, prices, volume, P&L, and—where the exporter can provide it—initial SL/TP risk information, magic number, exit reason, and account balance. Imported trade facts are read-only.

### Trader-owned post-trade evidence

One review belongs to one completed position. A review records:

- chosen documented strategy and whether the setup was valid;
- actual risk when known;
- whether the stop was widened;
- impulse, revenge, and emotional-size breaches;
- review note and, when needed, a corrective action.

Changing a saved review creates a new version and retains the prior version. The strategy evidence is snapshotted with the review, so later edits cannot reinterpret historic System evidence.

### Risk policy and funded capital

Each MT5 account has:

- **Funded capital** — the fixed capital base used for policy limits and historical drawdown calculations. It is not the live MT5 balance.
- **Risk policy** — standard risk (1R) for reporting, a separate maximum risk per trade for compliance, daily and weekly R limits, maximum drawdown, maximum consecutive losses, minimum R:R, and reference controls for open risk/correlation.

Saving a policy creates a version. Reviews and schema-v2/v3 imports retain their policy reference; changing funded capital recalculates monetary policy limits, historical Risk scores, and monitoring.

## How to build and use the framework

### 1. Define the Trading System

Create one reusable strategy in **Settings → Strategies**. At minimum, write:

- market and trading session;
- context/regime and timeframe;
- setup, entry trigger, invalidation/stop, and target/exit;
- no-trade conditions;
- valid and invalid examples.

Add backtest evidence when available. For the journal’s System-evidence component, an eligible strategy needs a description, a backtest start and end date, at least 100 trades, and positive expectancy in R after costs. This is evidence of documentation and testing; it is not a promise of future performance.

### 2. Define Risk management

For each account, set **Funded capital** in **Settings → MT5 Accounts**, then save a **Risk policy** in **Framework → Risk policy**.

Start with explicit rules such as:

- standard risk (1R) for reporting and maximum risk per trade for compliance;
- maximum daily and weekly loss in R;
- maximum drawdown and consecutive-loss count;
- no stop widening merely to avoid a loss;
- position-sizing rule and no emotional size increase.

Use the account currency consistently. The Review register shows both the **Risk limit** (funded capital × maximum-risk percentage) and **Actual risk** so the compliance comparison is visible for each position. Dashboard R uses funded capital × the policy's standard-risk percentage.

### 3. Define Psychology rules

Write a short, observable set of behaviour rules. The current review supports these concrete breach signals:

- impulse entry or management;
- revenge trade;
- emotionally increased size;
- corrective action after a breach.

Keep the rule set small enough to review honestly. For example: “After a loss, wait for the next fully documented setup; do not increase size to recover.”

### 4. Build the roadmap evidence

Open **Framework → Roadmap**. Each completed item requires a written evidence note; a checkbox alone is not proof.

| Level | Psychology | Risk management | Trading system |
|---|---|---|---|
| 1 — Define | Name triggers and behaviour rules. | Define policy and stop/daily-stop rules. | Document rules and valid/invalid examples. |
| 2 — Test | Track behaviour and recurring patterns in structured practice. | Test 20 risk calculations or simulated trades. | Record 100+ backtest trades with positive expectancy after costs. |
| 3 — Execute | Collect 20 reviewed trades without a critical behaviour breach. | Collect 20 reviewed trades without a critical risk breach. | Collect 20 reviewed trades using valid setups. |
| 4 — Measure | Maintain 30 reviewed trades. | Maintain 30 reviewed trades with Risk metrics. | Maintain 30 reviewed trades with live metrics. |
| 5 — Optimise | Test one behavioural improvement hypothesis. | Test one isolated policy improvement. | Test one isolated system change. |

Psychology and Trading System evidence is trader-wide. Risk evidence is scoped to the selected account.

### 5. Import completed trades and review them

When MT5 exports a closed position, it appears in **Framework → Review closed trades**. Filter the register by:

- **Needs review** — no full assessment exists;
- **Auto-reviewed** — automatic Risk evidence exists, but no full three-pillar review;
- **Reviewed** — a complete post-trade assessment is saved;
- **All** — every imported closed position.

Select a row to open the review modal. Review the execution against the system—not against whether P&L was positive. Then record Risk and Psychology evidence and save the review.

## Per-trade scoring

Only a saved post-trade review creates a full Psychology, Risk, System, and Process score. Scores are deterministic percentages; they do not use trade P&L as a quality input.

### Psychology score

| Component | Weight | Pass condition |
|---|---:|---|
| Behaviour control | 70% | No impulse, revenge, or emotional-size breach recorded. |
| Corrective action | 30% | No breach exists, or a corrective action is recorded. |

Formula:

```text
Psychology = (behaviour control × 70%) + (corrective action × 30%)
```

Each component is either 100 or 0. Therefore:

- no recorded breach = **100%**;
- breach with a corrective action = **30%**;
- breach without a corrective action = **0%**.

A revenge or emotional-size breach is a **Psychology hard block**. An impulse breach lowers the score but is not currently a hard block by itself.

### Risk management score

| Component | Weight | Pass condition |
|---|---:|---|
| Risk size compliance | 70% | Chosen actual Risk is positive and no greater than the attached policy limit. |
| Stop discipline | 30% | Stop was not widened. |

```text
Risk = (risk size compliance × 70%) + (stop discipline × 30%)
```

Each component is either 100 or 0:

| Risk size | Stop widened | Risk score |
|---|---|---:|
| Within policy | No | 100% |
| Over policy | No | 30% |
| Within policy | Yes | 70% |
| Over policy | Yes | 0% |

Widening the stop is a **Risk hard block**. The selected account also has a Risk hard block while its monitoring state is `STOP`.

#### Actual-Risk order

The journal selects one Risk amount in this order:

1. Declared actual risk saved in the review.
2. **Specific preset SL** — MT5-calculated initial risk from the export.
3. **Real-loss SL** — `abs(net P&L)` for a losing trade without calculated initial risk. This is an assumption, not a recorded MT5 stop.
4. **Live-account-balance SL** — current positive MT5 balance for a profitable trade with no recorded entry SL. This is a conservative dynamic assumption, not a recorded MT5 stop.
5. Standard 1R amount, but only as a fallback for Risk monitoring or a saved review that has no usable source. It is not shown as Actual risk in the Review register.

The first three automatic sources can create a Risk-only automatic review once funded capital and a Risk policy are configured. Within policy is 100%; over policy is 0%. Auto-reviews never create Psychology, System, or Process scores, and never advance the roadmap.

### Trading System score

| Component | Weight | Pass condition |
|---|---:|---|
| Valid documented setup | 70% | The reviewer confirms that the trade followed the documented setup. |
| Strategy evidence | 30% | Strategy meets the eligible backtest/documentation requirements. |

```text
Trading System = (valid setup × 70%) + (strategy evidence × 30%)
```

Each component is 100 or 0. An invalid reviewed setup is a **Trading System hard block**, regardless of whether the trade made money.

### Process score

The Process score is displayed only for a saved full review:

```text
Process = (Psychology + Risk management + Trading System) / 3
```

It is a summary, not a gate. A hard block remains visible even if the arithmetic Process score is high.

## Monitoring and scorecards

### Pillar scorecards

The scorecards average the latest 20 **reviewed** trades:

- Psychology: trader-wide;
- Trading System: trader-wide;
- Risk management: selected account.

Needs-review and Risk-only Auto-reviewed trades do not enter those averages. If there are no full reviews, the pillar score remains unavailable rather than inventing a neutral score.

A pillar is shown as **Review needed** when a hard block exists in the latest reviewed sample. A high average never clears that block.

### Account Risk monitoring

Risk monitoring uses every imported closed position in close-time order. It calculates:

- daily R and weekly R using the reporting timezone;
- current and maximum peak-to-trough drawdown from funded capital;
- current consecutive-loss streak.

```text
R = net P&L / selected Risk amount
```

The monitoring state compares the highest utilization of daily loss, weekly loss, maximum drawdown, and loss-streak limits:

| State | Condition |
|---|---|
| `CLEAR` | All monitored limits are below 80%. |
| `CAUTION` | At least one monitored limit is at least 80%, but none is at the limit. |
| `STOP` | At least one monitored limit is reached or exceeded. |

These are retrospective signals for the next review or session. Because the bridge contains closed positions only, it cannot automatically verify open-position risk or correlated exposure.

## Interpreting the evidence

Use the three scores to choose the next investigation, not to justify changing everything at once.

| Pattern | First place to investigate |
|---|---|
| System and Risk hold; Psychology weak | Emotional trigger, impulse, revenge, or routine failure. |
| Psychology and System hold; Risk weak | Sizing, stop discipline, or loss-limit use. |
| Psychology and Risk hold; System weak | Setup rules, market regime, strategy evidence, or execution of the documented setup. |
| All three weak | Return to Define and Test; do not optimise details yet. |
| All three hold but short-term P&L is weak | Avoid a strategy change based on a small sample; compare the sample with the tested distribution. |

## Build principles for future changes

Keep the framework reliable as the app evolves:

1. **Keep imported execution facts immutable.** Journal annotations must never rewrite MT5 data.
2. **Keep policy, review, and strategy evidence versioned or snapshotted.** Historical evidence must remain explainable.
3. **Derive scores from stored facts.** Do not store a score as an independent source of truth.
4. **Show source and uncertainty.** Distinguish declared Actual risk, MT5-calculated risk, Real-loss assumptions, and Live-account-balance assumptions.
5. **Separate hard blocks from averages.** An unsafe behaviour must not disappear inside a good composite score.
6. **Require evidence for roadmap progress.** A completed checklist item needs a written note and, at execution/measurement levels, reviewed-trade evidence.
7. **Change one variable at a time.** Record a hypothesis, test it on a meaningful sample, then keep, reject, or continue testing the change.

This framework is decision support and a learning record, not financial advice or a guarantee of trading results.
