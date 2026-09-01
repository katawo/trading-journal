# Trading Journal Web App — Product & Technical Specification

## 1. Purpose

Build a simple, local-first web application for manual trading journaling, risk management, and performance tracking.

The first version will **not integrate with MetaTrader 5** or any broker.

The app should help answer:

> Given my account size, trading system, risk model, historical expectancy, and execution quality, am I following a repeatable and risk-controlled process?

---

## 2. Primary Goal

The user enters account size manually, defines risk per trade, and reviews performance with evidence rather than a profit quota.

- App converts results into **R-multiples**
- App tracks:
  - Period P&L
  - Period R
  - Win rate
  - Expectancy
  - Profit factor
  - Drawdown
  - Rule adherence
  - Strategy/setup performance
  - Daily/weekly/monthly risk status

---

## 3. Core Design Principle

The application should follow this hierarchy:

```text
System Expectancy
    ↓
Trading Strategy
    ↓
Execution Quality
    ↓
Risk Management
    ↓
Individual Trades
```

Performance outcomes must never override the Trading System, execution quality, or Risk Management rules.

---

# 4. Scope

## V1 — Included

- Local web app
- Manual trade entry
- Local, read-only MetaTrader 5 trade import for selected accounts
- Local database
- Monthly profit target
- Account settings
- Risk-per-trade settings
- R-multiple calculations
- Daily/weekly/monthly risk limits
- Position-size planning
- Trade journal
- Strategy tagging
- Setup grading
- Psychology notes
- Rule violation tracking
- Daily review
- Weekly review
- Monthly review
- Performance dashboard
- Win rate
- Average winner
- Average loser
- Expectancy
- Profit factor
- Drawdown
- Strategy analytics
- Rule-adherence score
- Screenshot attachment support

## V1 — Excluded

- Broker API integration
- Automatic trade execution
- Automatic trade blocking
- AI trading advice
- Cloud hosting
- Multi-user accounts
- Mobile application
- News integration
- Economic calendar
- TradingView integration

These may be added later after the core journal has been validated.

---

# 5. Recommended Technology Stack

## Application Architecture

**Python modular monolith with Streamlit**

Reasons:

- Fast to build
- Python-native
- Easy forms
- Easy charts
- Good for local apps
- Simple deployment
- Minimal frontend development
- No local API server or background worker is required for V1

## Domain and Import Layer

**Python services with Pydantic validation**

Responsible for:

- Business rules
- Risk calculations
- Performance calculations
- Validation
- Database operations
- MT5 export-file validation and idempotent import processing

## Database

**SQLite with SQLAlchemy 2 and Alembic migrations**

Reasons:

- Local
- No database server required
- Single-file storage
- Easy backup
- Sufficient for a single-user journal
- WAL mode and foreign-key enforcement support safe local reads and writes
- Alembic migrations support upgrades as imported-trade data is added

## Libraries

```text
streamlit
pandas
plotly
sqlalchemy
alembic
pydantic
pillow
pytest
```

Use Python `Decimal` for monetary and R calculations, the standard-library `csv`, `hashlib`, and `zoneinfo` modules for import processing, and Streamlit AppTest with pytest for page-level tests. NumPy is not required in the initial release.

## MetaTrader 5 Exporter

Each selected MT5 terminal can run a resident, read-only MQL5 sync EA. It reads terminal history locally after trade-deal events and writes a versioned CSV snapshot to MT5's Common Files directory using a temporary file then rename. The app resolves this location locally across native Windows `%APPDATA%` and Linux Wine prefixes (`WINEPREFIX`, `~/.wine`, and `~/.mt5`), with an explicit environment override for non-standard installations. While the Dashboard is open, the Streamlit app checks the configured export path every 15 seconds and imports only a changed snapshot. The app does not use broker credentials, send orders, or communicate with a cloud service. The manually triggered exporter script remains a recovery fallback.

---

# 6. Suggested Project Structure

```text
trading-journal/
│
├── app.py
│
├── pyproject.toml
├── requirements.txt              # Optional simple end-user install path
│
├── README.md
│
├── data/
│   └── trading_journal.db
│
├── screenshots/
│
├── mql5/
│   ├── TradingJournalSync.mq5
│   └── TradingJournalExporter.mq5
│
├── migrations/
│
├── pages/
│   ├── dashboard.py
│   ├── new_trade.py
│   ├── journal.py
│   ├── risk_planner.py
│   ├── analytics.py
│   └── reviews.py
│
├── services/
│   ├── database.py
│   ├── mt5_importer.py
│   ├── risk_engine.py
│   ├── metrics_engine.py
│   └── review_engine.py
│
├── models/
│   ├── trade.py
│   ├── settings.py
│   ├── mt5_account.py
│   └── review.py
│
└── tests/
    ├── fixtures/
    └── test_mt5_importer.py
```

---

# 7. Main App Navigation

Recommended navigation:

```text
Dashboard
New Trade
Risk Planner
Analytics
Reviews
Settings
```

---

# 8. Screen 1 — Dashboard

The dashboard is the main command center.

## Period Summary

Display:

```text
Current Net P&L         $620
Current R              +6.2R

Trades                    17
Wins                       8
Losses                     9
Win Rate                 47.1%

Expectancy              +0.42R
Profit Factor             1.55
Current Drawdown         -1.3R
Max Drawdown             -3.4R

Rule Adherence             94%
```

## Dashboard sections

### Account

- Starting balance
- Current balance
- Reporting-period starting balance
- Reporting-period net P&L
- Reporting-period return %

### Performance

- Number of trades
- Win rate
- Average winner
- Average loser
- Average R
- Expectancy
- Profit factor

### Risk

- Current daily result
- Current weekly result
- Current monthly result
- Current drawdown
- Maximum drawdown
- Consecutive losses

### Process

- Rule adherence %
- A+ trades
- A trades
- B trades
- C trades
- Rule violations

---

# 9. Screen 2 — New Trade

Manual trade-entry form.

## Basic Trade Information

Fields:

- Trade date
- Entry time
- Exit time
- Symbol
- Direction
  - Long
  - Short
- Strategy
- Setup
- Trading session
  - Asia
  - London
  - New York
  - Other
- Market regime
  - Trending
  - Ranging
  - Breakout
  - High volatility
  - Low volatility
  - Other

## Price Information

- Entry price
- Stop-loss price
- Target price
- Exit price

## Risk Information

- Account balance at trade
- Risk %
- Planned risk $
- Stop distance
- Actual position size (optional reference field)
- Planned reward $
- Planned R:R

## Result

- Gross P&L
- Commission
- Fees
- Net P&L
- Result in R

## Trade Quality

Setup grade:

- A+
- A
- B
- C

Rule followed:

- Yes
- No
- Partial

## Psychology

Emotion before trade:

- Calm
- Confident
- Fearful
- FOMO
- Revenge
- Impatient
- Overconfident
- Hesitant
- Other

Emotion during trade:

- Calm
- Fear
- Greed
- Stress
- Impatience
- Other

## Journal Notes

- Entry reason
- Exit reason
- Mistakes
- Rule violations
- Lesson learned
- What was done well
- What should improve

## Screenshots

Optional:

- Before-entry screenshot
- After-entry screenshot
- Exit screenshot

---

# 10. Dashboard subview — Closed-trade detail

When the Dashboard uses the **Per trade** chart view, it displays a read-only table of completed positions. The table includes close time, position ID, symbol, P&L, result R, post-close drawdown, and balance when balance tracking is enabled.

MT5 execution values are stored as imported. Risk and strategy values are derived only from the journal-wide defaults; the current interface has no individual-trade annotations or overrides.

The Dashboard includes automatic changed-export detection and a manual **Sync MT5 now** action. They show the source account, created/updated/skipped/error counts, and any validation failures from the latest import run.

---

# 11. Screen 4 — Risk Planner

The Risk Planner is a standalone, stateless calculator. It does not create, save, prefill, or modify a journal trade.

## Inputs

- Account balance
- Risk %
- Entry price
- Stop-loss price
- Target price
- Instrument information if needed

The user supplies the instrument's cash value per point/tick or equivalent contract information when position size is required.

## Outputs

- Dollar risk
- Stop distance
- Position size
- Planned reward
- Planned R:R

Example:

```text
Balance             $10,000
Risk                  1.0%
Risk Amount            $100

Entry                1.08500
Stop                 1.08300
Target               1.09000

Planned Risk            1R
Planned Reward         2.5R
Planned R:R            1:2.5
```

---

# 12. Risk Management Framework

Initial defaults should be editable.

Recommended starting framework:

| Rule | Default |
|---|---:|
| Risk per trade | 0.5%–1.0% |
| Maximum simultaneous open risk | 2R |
| Daily loss limit | -2R |
| Weekly loss threshold | -4R |
| Monthly defensive threshold | -6R |
| Maximum risk on one idea | 1R |

These are defaults only. The user should configure them according to their actual trading system and historical drawdown.

---

# 13. Drawdown Protocol

The application should classify drawdown states.

## Normal

```text
0R to -2R
```

Action:

- Normal risk
- Normal execution

## Caution

```text
-2R to -4R
```

Action:

- Review recent trades
- Do not increase risk
- Focus on A/A+ setups

## Defensive

```text
-4R to -6R
```

Action:

- Reduce risk if configured
- Trade highest-quality setups only
- Review system vs execution errors

## Stop / Review

```text
Below -6R
```

Action:

- Pause normal execution
- Review:
  - Strategy performance
  - Market conditions
  - Rule violations
  - Psychology
  - Risk discipline

The app should provide warnings, not automatically place or block trades in V1.

---

# 14. R-Multiple System

R should be the primary normalized performance unit.

If planned risk is:

```text
$100 = 1R
```

Then:

```text
+$250 = +2.5R
-$100 = -1R
-$50  = -0.5R
+$80  = +0.8R
```

Formula:

```text
Result R = Net P&L / Planned Risk $
```

Example:

```text
Net P&L = $235
Planned Risk = $100

Result R = 235 / 100
         = +2.35R
```

---

# 16. Core Performance Calculations

## Win Rate

```text
Win Rate = Winning Trades / Total Closed Trades
```

---

## Loss Rate

```text
Loss Rate = Losing Trades / Total Closed Trades
```

---

## Average Winner

```text
Average Winner R =
Total Winning R / Number of Winning Trades
```

---

## Average Loser

```text
Average Loser R =
Total Losing R / Number of Losing Trades
```

Use absolute loss magnitude where appropriate for formulas.

---

## Expectancy

```text
Expectancy =
(Win Rate × Average Winner R)
-
(Loss Rate × Average Loss R)
```

Example:

```text
Win Rate = 45%
Average Winner = 2R
Loss Rate = 55%
Average Loss = 1R

Expectancy =
(0.45 × 2)
-
(0.55 × 1)

= +0.35R per trade
```

---

## Profit Factor

```text
Profit Factor =
Gross Profit / Absolute Gross Loss
```

---

## Average R

```text
Average R =
Total R / Total Trades
```

---

## Monthly Expected R

```text
Expected Monthly R =
Expectancy × Average Qualified Trades Per Month
```

Example:

```text
Expectancy = +0.42R
Average trades/month = 24

Expected Monthly R =
0.42 × 24
= +10.08R
```

---

# 17. Drawdown Calculations

Track:

- Current drawdown $
- Current drawdown %
- Current drawdown R
- Maximum drawdown $
- Maximum drawdown %
- Maximum drawdown R

Drawdown should be calculated from the equity curve.

Conceptually:

```text
Drawdown =
Current Equity
-
Previous Equity Peak
```

Maximum drawdown is the largest peak-to-trough decline.

---

# 18. Strategy Analytics

The app should compare performance by:

- Strategy
- Setup
- Symbol
- Session
- Day of week
- Hour of day
- Long vs short
- Setup grade
- Market regime
- Risk size
- Rule adherence

Example table:

| Strategy | Trades | Win % | Avg R | Expectancy | Total R |
|---|---:|---:|---:|---:|---:|
| Pullback | 31 | 58% | +0.67R | +0.67R | +20.8R |
| Breakout | 22 | 45% | +0.22R | +0.22R | +4.8R |
| Reversal | 17 | 35% | -0.16R | -0.16R | -2.7R |

---

# 19. Rule-Adherence System

Every trade should have two separate results:

```text
Financial Result
Execution Result
```

Example:

```text
Trade #152

Financial Result:
+2.1R

Execution Score:
60%

Violations:
- Entered before confirmation
- Risk exceeded plan
```

Another example:

```text
Trade #153

Financial Result:
-1R

Execution Score:
100%

Assessment:
Good trade.
All rules followed.
The setup simply lost.
```

This distinction prevents the user from treating every losing trade as a bad trade.

---

# 20. Execution Score

Suggested scoring model:

| Rule | Weight |
|---|---:|
| Correct setup | 20% |
| Correct entry | 15% |
| Correct stop | 15% |
| Correct position size | 15% |
| Correct target/exit process | 10% |
| Risk rule followed | 15% |
| No emotional/revenge behavior | 10% |

Total:

```text
100%
```

Suggested interpretation:

```text
90–100% = Excellent
80–89%  = Good
70–79%  = Needs attention
<70%    = Poor execution
```

---

# 21. Screen 5 — Analytics

Recommended sections:

## Equity Curve

Chart:

```text
Date → Account Equity
```

## Cumulative R Curve

```text
Date → Cumulative R
```

## Monthly Performance

- P&L $
- Total R
- Win rate
- Expectancy
- Profit factor
- Drawdown

## Strategy Breakdown

Performance by:

- Strategy
- Setup
- Symbol
- Session
- Grade
- Market regime

## Behavior Breakdown

Performance when:

- Rules followed
- Rules violated
- Calm
- FOMO
- Revenge
- Overconfident
- After a winning trade
- After a losing trade

---

# 22. Screen 6 — Reviews

Three review levels:

```text
Daily
Weekly
Monthly
```

---

## Daily Review

Fields:

- Date
- Number of trades
- Daily P&L
- Daily R
- Best trade
- Worst trade
- Best decision
- Biggest mistake
- Rule violations
- Emotional state
- Lesson
- Tomorrow's focus

---

## Weekly Review

Display:

- Weekly P&L
- Weekly R
- Trades
- Win rate
- Expectancy
- Profit factor
- Max drawdown
- Best setup
- Worst setup
- Rule adherence
- Main mistake pattern

Journal prompts:

- What worked?
- What did not work?
- Which setup performed best?
- Which mistake repeated?
- Did I respect risk limits?
- What should I focus on next week?

---

## Periodic Review

Display:

```text
Trades
Win Rate
Expectancy
Profit Factor
Max Drawdown

A+ Trades
A Trades
B Trades
C Trades

Rule Adherence
```

Review questions:

- Did I follow the system?
- Was performance consistent with the documented system evidence?
- Which setup generated most of the profit?
- Which setup created most losses?
- Which mistakes cost the most R?
- Did I overtrade?
- Did I increase risk emotionally?
- What should remain unchanged?
- What should change next month?
- What is the process goal for next month?

---

# 23. Settings Screen

Settings is the single configuration workspace. It has three tabs:

- **MT5 Accounts** — account currency and the journal-wide reporting time basis
- **MT5 Accounts** — approved accounts and their export file locations
- **Strategies** — reusable strategy profiles, the one journal-default strategy, and optional backtest evidence

## Account Settings

- Account deposit currency (used for that account's monetary reports)
- Journal reporting time basis: UTC, Server Timezone, or Local Timezone

## MT5 Account Settings

- Display name
- Exact MT5 account login
- Broker server
- Active / inactive import status
- Export file location

Only accounts explicitly registered here may be imported. The export currency must match that account's configured deposit currency. The journal performs no currency conversion or cross-account monetary aggregation.

## Risk Settings

Risk is configured per MT5 account in the account's **Risk policy**:

- Standard risk (1R) % for reporting
- Maximum risk per trade % for compliance
- Daily loss limit R
- Weekly loss threshold R
- Maximum open risk R

## Trading Settings

- Allowed symbols
- Strategies (managed in the **Strategies** tab)
- Setups
- Trading sessions
- Setup grades

## Behavioral Rules

Examples:

- Maximum trades per day
- No revenge trades
- No adding to losing trades
- No unauthorized setups
- Minimum setup grade
- Minimum planned R:R

---

# 24. Suggested Database Model

## settings

```text
id
reporting_time_basis
default_risk_percent
base_r_value
daily_loss_limit_r
weekly_loss_limit_r
monthly_loss_limit_r
max_open_risk_r
created_at
updated_at
```

---

## mt5_accounts

```text
id
display_name
mt5_login
broker_server
account_currency
export_file_path
active
created_at
updated_at
```

Each `mt5_login` is unique and must match an explicitly registered account. The broker server is retained as part of the exact export-source check.

---

## strategies

```text
id
name
description
active
created_at
```

---

## setups

```text
id
strategy_id
name
description
minimum_grade
active
created_at
```

---

## trades

```text
id
source                    # manual | mt5
mt5_account_id            # null for manual trades
mt5_position_id           # null for manual trades
source_updated_at
trade_date
entry_time
exit_time
symbol
direction
setup_id
session
market_regime

entry_price
stop_price
target_price
exit_price

account_balance
risk_percent
position_size
planned_rr

gross_pnl
commission
swap
fees
net_pnl

setup_grade
rule_followed
execution_score

emotion_before
emotion_during

entry_reason
exit_reason
mistakes
rule_violations
lesson
positive_notes

entry_screenshot
after_entry_screenshot
exit_screenshot

created_at
updated_at
```

---

## mt5_import_runs

```text
id
mt5_account_id
source_file_path
source_file_hash
exported_at
status                    # succeeded | failed
created_count
updated_count
skipped_count
error_count
error_summary
created_at
```

Imported records are unique by `(mt5_account_id, mt5_position_id)`. Re-importing updates MT5-owned execution data; strategy and R are derived from the journal-wide defaults.

---

## daily_reviews

```text
id
review_date
pnl
total_r
trade_count
best_trade_id
worst_trade_id
best_decision
biggest_mistake
rule_violations
emotional_state
lesson
next_focus
created_at
```

---

## weekly_reviews

```text
id
week_start
week_end
pnl
total_r
trade_count
win_rate
expectancy
profit_factor
drawdown
rule_adherence
best_setup
worst_setup
main_mistake
what_worked
what_failed
next_focus
created_at
```

---

## monthly_reviews

```text
id
year
month
target_pnl
actual_pnl
target_r
actual_r
trade_count
win_rate
expectancy
profit_factor
max_drawdown
rule_adherence
best_setup
worst_setup
major_mistakes
lessons
next_month_focus
created_at
```

---

# 25. Validation Rules

The app should validate:

- Risk % cannot be negative
- Planned risk cannot be zero when calculating R
- Exit cannot be entered before entry time
- Strategy must exist
- Setup should belong to the selected strategy
- Grade must be valid
- R calculation requires planned risk
- MT5 import file must use supported schema version `4`, include the MT5 server UTC offset, and match a registered `(login, broker server)` account
- MT5 import currency must match the configured account currency
- Imported position records must have a unique `(mt5_account_id, mt5_position_id)` identity
- Corrupt, incomplete, unknown-account, or currency-mismatched imports must make no database changes
- Imported execution/P&L fields are read-only; the current app has no individual-trade annotations or overrides
- Imported trades contribute to dollar P&L immediately, but require funded capital and an account Risk policy with a positive standard risk (1R) before contributing to R-based metrics

---

# 26. Risk Warnings

Examples:

## Daily Risk Warning

```text
Today's Result: -1.6R
Daily Limit: -2R

Status: CAUTION
```

## Weekly Warning

```text
Weekly Result: -4.2R
Weekly Threshold: -4R

Status: DEFENSIVE MODE
```

## Rule Warning

```text
Selected Setup Grade: C
Minimum Allowed Grade: A

Warning:
This trade does not meet the trading plan.
```

V1 should warn only.

It should not block trading activity outside the application.

---

# 28. Core Workflow

```text
1. Configure Trading Plan
        ↓
2. Configure Risk Rules
        ↓
3. Plan Trade
        ↓
4. Import or Enter Trade
        ↓
5. Record Result
        ↓
6. Calculate R
        ↓
7. Update Statistics
        ↓
8. Review Execution
        ↓
9. Identify Patterns
        ↓
10. Improve Process
```

---

# 29. Development Roadmap

## Phase 1 — Foundation

Build:

- Project structure
- SQLite database
- SQLAlchemy models and Alembic migrations
- Settings
- Strategy management
- Setup management

Success condition:

> User can save and reload all trading-plan settings.

---

## Phase 2 — Local MT5 Sync

Build:

- MT5 account whitelist and journal timezone/base-currency settings
- Resident read-only MQL5 sync EA for each selected local terminal, with a manual exporter-script fallback
- Versioned CSV exports in MT5 Common Files, written through a temporary file then moved into place
- Closed-position aggregation by MT5 position ID, including partial fills/closes, commission, swap, fees, and net P&L
- Hash-gated idempotent importer, import-run audit log, and automatic import status UI
- Read-only imported execution data with journal-wide risk and strategy defaults

Success condition:

> User can safely auto-import changed completed-trade snapshots from registered local MT5 accounts without sending or modifying any MT5 trade.

---

## Phase 3 — Manual Journal and Enrichment

Build:

- New Trade form for non-MT5 trades
- Trade table, detail page, edit, and delete flows
- Screenshot storage
- Journal enrichment for imported trades: planned risk, tags, grades, psychology, rules, and notes

Success condition:

> User can create manual trades and complete imported trades with journal/process data.

---

## Phase 4 — Risk Engine

Build:

- Dollar risk
- Risk %
- Standalone position-size helper
- Planned R:R
- Result R
- Daily/weekly/monthly risk state

Success condition:

> Every trade has consistent risk and R calculations.

---

## Phase 5 — Dashboard

Build:

- Current P&L
- Current R
- Trade count
- Win rate
- Average winner
- Average loser
- Expectancy
- Profit factor
- Drawdown
- Rule adherence

Success condition:

> Dashboard accurately reflects journal data.

---

## Phase 6 — Reviews

Build:

- Daily review
- Weekly review
- Monthly review

Success condition:

> User has a repeatable review process.

---

## Phase 7 — Analytics

Build:

- Strategy analytics
- Setup analytics
- Session analytics
- Symbol analytics
- Grade analytics
- Psychology analytics
- Rule-following analytics

Success condition:

> User can identify where profits and losses are coming from.

---

## Phase 8 — Risk Guardian

Build warnings for:

- Daily loss limit
- Weekly loss threshold
- Monthly drawdown
- Excessive trade risk
- Low-quality setup
- Rule violations
- Excessive trade count

Success condition:

> App detects risk-plan violations before or during journaling.

---

## Phase 9 — Performance Intelligence

Later version.

Examples:

```text
A+ setups: +14.2R
B setups: -2.5R
C setups: -5.1R
```

or:

```text
London expectancy: +0.61R
New York PM expectancy: -0.24R
```

The system can automatically highlight statistically meaningful patterns.

---

## Phase 10 — External Integrations

Only after the manual system is stable.

Possible additions:

- Broker APIs
- Economic calendar
- TradingView
- Telegram
- Cloud backup
- Mobile dashboard

---

# 30. V1 Definition of Done

V1 is complete when the user can:

1. Register an account with its deposit currency and choose the journal reporting time basis.
2. Define risk rules.
3. Define strategies and setups.
4. Whitelist local MT5 accounts and import their completed positions manually.
5. Manually log non-MT5 trades.
6. Attach trade screenshots.
7. Record psychology and mistakes.
8. Calculate net P&L.
9. Calculate R automatically when planned risk is present.
10. Calculate win rate.
11. Calculate expectancy.
12. Calculate profit factor.
13. Track drawdown.
14. Compare strategies and setups.
15. Track rule adherence.
16. Complete daily reviews.
17. Complete weekly reviews.
18. Complete monthly reviews.
19. Receive risk warnings.
20. Export or back up local journal data.

---

# 31. Suggested Initial Pages

Keep the first implementation small.

```text
1. Dashboard
2. Settings
3. New Trade
4. Risk Planner
5. Analytics
6. Reviews
7. Settings
```

---

# 32. Recommended MVP Build Order

```text
Settings
   ↓
Database
   ↓
MT5 Account Setup
   ↓
Automatic MT5 Sync
   ↓
Manual Trade Entry
   ↓
R Calculation
   ↓
Risk Engine
   ↓
Dashboard
   ↓
Reviews
   ↓
Analytics
   ↓
Risk Warnings
```

Do not start with advanced analytics.

The priority is:

> Correct data → correct calculations → useful dashboard → meaningful analysis.

---

# 33. Local MT5 Sync Architecture

MT5 is a local, read-only data source in Phase 2. It is not the foundation of the application and does not receive commands from it.

```text
Selected MT5 terminal
        ↓ read-only sync EA on trade events
MT5 Common Files CSV export
        ↓ changed-export detection while the app is open
Validated SQLite journal database
        ↓
Risk Engine → Analytics Engine → Dashboard
```

The exporter identifies its source by MT5 account login and broker server. The importer accepts only registered accounts and fully closed positions, groups related deals by MT5 position ID, and records every import outcome. Manual entry remains available for non-MT5 trades; it must not duplicate an imported position.

MT5 execution values remain read-only after import. Strategy is derived from the journal default; R is derived from the importing account's policy version and enters R-based metrics only after funded capital and standard risk (1R) are configured.

The journal and terminal must run on the same machine, or the journal must have local read access to the MT5 Common Files directory.

Future data-source compatibility:

```text
Manual Entry ─────┐
                  │
MT5 Sync export ──┼──→ Unified Trade Database
                  │
Broker API ───────┘
                         ↓
                    Risk Engine
                         ↓
                  Analytics Engine
                         ↓
                      Dashboard
```

This means the core journal, calculations, review system, and analytics remain independent from MT5. Broker APIs and cloud synchronization remain future work.

---

# 34. Product Principle

The tool should reinforce this rule:

> I execute my tested edge correctly, control risk, and review the results without turning a profit target into a trading instruction.

---

# 35. Recommended First Implementation Milestone

The first usable milestone should contain only:

```text
Settings
+
MT5 Account Setup
+
Local MT5 Sync
+
Account Risk policies and journal strategy defaults
+
Basic Dashboard (dollar P&L only until account standard risk is configured)
```

Once those components are reliable, add:

```text
Manual Trade Entry
+
R Calculation
+
Risk Planner
+
Reviews
+
Analytics
+
Risk Guardian
```

This prevents unnecessary complexity and makes the app useful very early in development.

---

# 36. Test Strategy

Use pytest for domain, database, and importer tests, plus Streamlit AppTest for important page flows.

## MT5 Sync Tests

- Import a valid completed MT5 position.
- Aggregate multiple entries, partial closes, commission, swap, fees, and net P&L into one journal trade.
- Reject an unknown account, broker-server mismatch, account-currency mismatch, invalid schema, corrupt CSV, or incomplete temporary export without changing the database.
- Re-import the same position safely and refresh MT5-owned execution data.
- Exclude open positions, pending orders, and non-trading balance/credit operations.
- Verify imported P&L is visible immediately and R/process metrics remain excluded until funded capital and an account Risk policy are configured.
- Verify all reporting-period grouping uses the selected UTC, Server Timezone, or Local Timezone basis.

## Regression Tests

- Verify manual trades and imported trades cannot share the same source identity.
- Verify imported execution values cannot be edited through the UI.
- Verify an earlier journal schema is rejected with an explicit reset instruction. The three-pillar release is greenfield and must not migrate or reinterpret old assessment data.
- Verify no importer path submits, edits, or blocks an MT5 trade.

---

# 37. Archived three-pillar implementation note

> **Non-normative historical note.** This section is retained only as release context. The current workflow, logical-trade model, scoring rules, hard-rule semantics, monitoring scope, and roadmap gates are defined exclusively in [the three-pillar operating guide](three_pillar_framework_guide.md). Do not use this section to implement or interpret the framework.

The three pillars remain the permanent framework:

- **Psychology** — did I execute with discipline?
- **Risk management** — did I control the loss and exposure?
- **Trading system** — did the completed trade follow a repeatable setup?

This is a **post-trade journal only**. MT5 is the execution terminal. The app never asks permission before a trade, never blocks an order, and never creates a retrospective “trade approved” record.

## 37.1 Workflow

1. MT5 exports a completed position to the local CSV.
2. The app imports that immutable position.
3. Open **Framework → Review trades**, filter the register by **Needs approval**, **Reviewed**, **Failed**, or **All**, then open a trade row.
4. Record or correct all 12 current three-pillar criteria as **Pass**, **Partial**, or **Fail**, then add reason tags, hard-rule events, a review note, and a corrective action when required.
5. Use the Dashboard, rolling Framework scorecards, saved period reviews, and Roadmap to spot repeated process failures.

Every imported closed position is visible in the review register. Within-policy automatic Risk evidence becomes an **Auto-review**; over-policy or unavailable evidence is **Needs approval**. The register also shows the attached **Risk limit** (the policy maximum) and **Actual risk** amount in the account currency. Actual risk uses the declared review value when present, otherwise a usable automatic Risk source; it is never silently replaced by the policy limit. A full post-trade review replaces an auto-approval. One review belongs to one MT5 position, so a review cannot be attached to a different account’s trade.

Schema-v5 MT5 exports provide entry SL/TP, calculated initial risk/reward where MT5 can calculate it, final recorded SL, entry magic number, exit reason, account-balance snapshots, and the server UTC offset used to preserve MT5 timestamps. The app stores each timestamp as UTC plus its exported offset, then groups reports by the journal-wide **UTC**, **Server Timezone**, or **Local Timezone** basis. It recognises two ordered Risk sources: **Specific preset SL** (MT5-calculated initial risk) and **Real-loss estimate** (`abs(net P&L)` for a losing trade without calculated initial risk). The app marks the evidence as within policy, over policy, or unavailable, with verified, inferred, or mixed confidence. It is advisory only: automatic MT5 evidence never creates a completed Risk, Psychology, System, Process, readiness, or roadmap score. Missing or ambiguous evidence remains unavailable, never neutral or failing.

## 37.2 What each review records

| Pillar | Explicit post-trade criteria | Trade-level weights |
|---|---|---|
| Psychology | Edge execution, risk acceptance, probability mindset, outcome independence & reset. | 35 / 25 / 20 / 20 |
| Risk management | Policy adherence, position-size accuracy, stop discipline, exposure & limit compliance. | 35 / 20 / 25 / 20 |
| Trading system | Setup validity, context alignment, entry fidelity, management/exit fidelity. | 30 / 25 / 20 / 25 |

Every criterion is rated **Pass = 100**, **Partial = 50**, or **Fail = 0**. Failed criteria require at least one reason tag; a Partial, Fail, or hard-rule event requires one corrective action. The raw process average is retained for diagnosis, but a configured hard-rule event sets **Process Quality = FAIL**. Outcomes are separately classified as Good/Bad Win, Loss, or Breakeven.

The review is independent of P&L: a losing rule-following trade may be good process, while a profitable rule-breaking trade is not.

The strategy profile is snapshotted with the review, so later strategy edits do not reinterpret historic System evidence. A correction creates a new review version and archives the prior version, including its strategy snapshot and all recorded evidence. The selected account’s active risk-policy version is attached to the review when available. `zone_v2` is the only supported rubric. A one-time, backup-protected migration converts earlier assessments and revisions by retaining the eight compatible Risk/System grades and assigning neutral `Partial` grades to the four new Psychology criteria. Incompatible earlier aggregate artifacts are removed.

## 37.3 Post-trade Risk Monitoring

Each account has immutable **Funded capital** and a versioned risk policy: standard risk (1R) for reporting, a separate maximum risk per trade for compliance, daily/weekly loss limits in R, drawdown limit, consecutive-loss limit, independent reset cadences for drawdown and losing streak, minimum R:R, and reference controls for open risk/correlation. Reset cadence supports Daily, Weekly, Monthly, and All time, with Daily as the default. Funded capital is acknowledged during creation, fixed for policy limits and historical drawdown, and never replaced by the dynamic live MT5 balance. A legacy account with no funded capital may initialize it once; after that the database rejects changes.

Replacing an active risk policy requires an explicit account-wide recalculation confirmation. The new active version becomes the analytical lens for all current derived Risk and R metrics across that account's complete trade history. Prior policy versions and policy IDs attached to imported/reviewed evidence remain audit history; saved assessment grades and revisions and saved weekly/monthly period reviews are not rewritten. Review-rule settings, roadmap evidence, and coaching focuses are owned by the account. Operational trades, groups, policies, reviews, rules, roadmap state, focuses, and analytics cannot cross accounts; only strategy definitions and controlled review-context vocabulary are reusable.

Risk monitoring includes **every completed logical trade**, not only reviewed trades:

- Balance drawdown and loss streak use aggregate logical-trade net P&L in final-close order. Each metric resets independently at its configured reporting-calendar boundary; the Dashboard retains lifetime records. A monitoring maximum-drawdown breach persists through recovery until that reset. A confirmed policy replacement recomputes the account's derived monitoring state from its complete logical-trade history under the new active thresholds, reset cadence, and saved server offset.
- R uses declared actual risk after a review, then Specific preset SL and Real-loss SL in that order. If neither exists, a saved review uses its attached standard 1R amount (funded capital × standard-risk %); an unreviewed position uses the current active policy standard 1R. Funded capital and account-balance snapshots never substitute for missing trade-level risk evidence.
- At 80% of a configured hard limit, the app shows `CAUTION`; at the limit, it shows `STOP`.

These statuses are retrospective monitoring signals for the next review or session, never live-order controls. The bridge contains completed positions only, so open-position risk and correlation cannot be verified automatically.

## 37.4 Scores, roadmap, and interpretation

Scores use a selectable rolling sample of **reviewed** `zone_v2` closed trades, including approved Auto-reviews and Manual reviews. These use neutral (`Partial`) Psychology/System defaults; Risk policy adherence is Pass only when automatic evidence is within policy. Needs-approval imports are excluded until approved or assessed with the current rubric. P&L and other outcome metrics never alter Psychology, Trading System, or Process evidence. Psychology, Risk, and Trading System scores and monitoring are all account-specific because each account represents an independent system. Readiness is the lowest complete pillar, never an average. A configured hard-rule event sets the affected pillar and readiness to **FAIL** for its active rolling sample. Repeated tagged critical breaches cap the affected numeric pillar score at 59 until a later weekly or monthly review records the response.

The three roadmap pillars progress in parallel through Define, Test, Execute, Measure, and Optimise. Level 3 requires 20 post-trade reviews, a score of at least 70, and no active hard failure. Level 4 requires 30 reviews, a score of at least 80, no active hard failure, and a saved period review. Auto-reviews and approved Auto-reviews advance a roadmap gate; needs-approval imports do not. Checklist completion needs a written evidence note, while reviewed evidence is required for maturity decisions. Saved weekly/monthly reviews snapshot the period-end scores, alerts, recurring issues, reflection, and one priority action.

Use the combined evidence diagnostically:

| Observation | Investigate |
|---|---|
| System evidence holds, Psychology weak | Execution behaviour or emotional trigger. |
| Psychology holds, Risk weak | Sizing, stop discipline, or loss controls. |
| Psychology and Risk hold, System weak | Setup definition, strategy evidence, or market regime. |

## 37.5 Data boundaries

Imported MT5 execution values are read-only. The user owns the post-trade journal evidence, but it never changes the imported position, sends a command to MT5, or claims that a review authorised a past trade.
