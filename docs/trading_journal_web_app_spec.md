# Trading Journal Web App — Product & Technical Specification

## 1. Purpose

Build a simple, local-first web application for manual trading journaling, risk management, performance tracking, and monthly goal planning.

The first version will **not integrate with MetaTrader 5** or any broker.

The app should help answer:

> Given my account size, trading system, risk model, historical expectancy, execution quality, and number of valid opportunities, is my monthly target realistic — and am I following the process required to achieve it?

The app must **not encourage overtrading or increasing risk simply because the monthly profit target has not yet been reached**.

---

## 2. Primary Goal

Example monthly target:

- Monthly target: **$1,000**
- User enters account size manually
- User defines risk per trade
- App converts results into **R-multiples**
- App tracks:
  - Monthly P&L
  - Monthly R
  - Win rate
  - Expectancy
  - Profit factor
  - Drawdown
  - Rule adherence
  - Strategy/setup performance
  - Daily/weekly/monthly risk status

The monthly target is a **planning objective**, not a trading quota.

---

## 3. Core Design Principle

The application should follow this hierarchy:

```text
Monthly Goal
    ↓
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

It must avoid this behavior:

```text
Behind Monthly Target
    ↓
Trade More
    ↓
Increase Position Size
    ↓
Take Lower-Quality Setups
    ↓
Increase Drawdown
```

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

Each selected MT5 terminal runs a manually triggered MQL5 exporter script. The script reads terminal history locally and writes a versioned CSV file to MT5's Common Files directory. The Streamlit app imports that file; it does not use broker credentials, send orders, or communicate with a cloud service.

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
Journal
New Trade
Risk Planner
Analytics
Reviews
Settings
```

---

# 8. Screen 1 — Dashboard

The dashboard is the main command center.

## Monthly Summary

Display:

```text
Monthly Target        $1,000
Current Net P&L         $620
Current R              +6.2R
Target R               +10R

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
- Monthly starting balance
- Monthly net P&L
- Monthly return %

### Goal

- Monthly target $
- Current result $
- Target R
- Current R
- Progress %

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

## Important UX rule

Do **not** display:

> You need $380 more today.

Instead display:

```text
Monthly Target Progress: 62%

Current Expectancy: +0.42R/trade
Average Qualified Trades/Month: 24
Expected Monthly R: +10.08R
```

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

# 10. Screen 3 — Journal

Table of all trades.

Recommended columns:

| Field |
|---|
| Date |
| Symbol |
| Direction |
| Strategy |
| Setup |
| Grade |
| Risk $ |
| Risk % |
| Planned R:R |
| Net P&L |
| Result R |
| Rule followed |
| Emotion |
| Notes |

Filters:

- Date range
- Symbol
- Strategy
- Setup
- Grade
- Session
- Long/Short
- Win/Loss
- Rule followed
- Market regime

Clicking a trade should open its complete detail page.

For MT5-imported trades, execution values and P&L are read-only. The user can add or edit strategy/setup tags, grade, rule checks, psychology, notes, screenshots, and planned risk.

The Journal includes a manual **Import MT5 Trades** action that shows the source account, created/updated/skipped/error counts, and any validation failures from the latest import run.

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

# 15. Monthly Goal Engine

The monthly goal is tracked in dollars and R.

Example:

```text
Monthly Target = $1,000
1R = $100
Target R = 10R
```

Formula:

```text
Target R = Monthly Target / Base Risk Amount
```

The app should display:

- Monthly target $
- Current P&L $
- Monthly target R
- Current R
- Monthly progress %
- Current expectancy
- Average monthly trade count
- Expected monthly R

The app must never recommend increasing risk solely because the monthly target is behind schedule.

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

## Monthly Review

Display:

```text
Target
Actual
Difference

Target R
Actual R

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
- Was the target statistically realistic?
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

## Account Settings

- Journal base currency
- Journal reporting timezone
- Monthly target

## MT5 Account Settings

- Display name
- Exact MT5 account login
- Broker server
- Active / inactive import status
- Export file location

Only accounts explicitly registered here may be imported. An account whose MT5 deposit currency differs from the journal base currency is rejected; V1 does not perform currency conversion.

## Risk Settings

- Default risk %
- Base 1R value
- Daily loss limit R
- Weekly loss threshold R
- Monthly loss threshold R
- Maximum open risk R

## Trading Settings

- Allowed symbols
- Strategies
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
journal_base_currency
reporting_timezone
monthly_target
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

Each `(mt5_login, broker_server)` pair is unique and must match an explicitly registered account.

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
journal_completed_at      # set when planned risk is recorded for an imported trade
trade_date
entry_time
exit_time
symbol
direction
strategy_id
setup_id
session
market_regime

entry_price
stop_price
target_price
exit_price

account_balance
risk_percent
planned_risk_amount
position_size
planned_rr

gross_pnl
commission
swap
fees
net_pnl
result_r

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

Imported records are unique by `(mt5_account_id, mt5_position_id)`. Re-importing updates MT5-owned execution data while preserving journal annotations.

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
- Monthly target should not affect position sizing
- Position sizing must use defined risk, not remaining monthly target
- MT5 import file must match the expected schema version and a registered `(login, broker server)` account
- MT5 import currency must match the journal base currency
- Imported position records must have a unique `(mt5_account_id, mt5_position_id)` identity
- Corrupt, incomplete, unknown-account, or currency-mismatched imports must make no database changes
- Imported execution/P&L fields are read-only; only journal annotations and planned risk may be edited
- Imported trades contribute to dollar P&L immediately, but require positive planned risk before contributing to R, expectancy, target-R, or process metrics

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

# 27. Monthly Target Philosophy

The app should evaluate whether the target is statistically supported.

Example:

```text
Monthly Target: $1,000
Base Risk: $100
Required: +10R

Historical Expectancy:
+0.40R/trade

Average Qualified Trades:
25/month

Expected:
25 × 0.40R = +10R
```

This means the target is broadly aligned with historical expectancy.

If:

```text
Expectancy = +0.20R
Trades/month = 15

Expected Monthly R = +3R
```

then a +10R target would be statistically aggressive.

The application should communicate that without encouraging increased risk.

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

## Phase 2 — Local MT5 Import

Build:

- MT5 account whitelist and journal timezone/base-currency settings
- Manually triggered MQL5 exporter script for each selected local terminal
- Versioned CSV exports in MT5 Common Files, written through a temporary file then moved into place
- Closed-position aggregation by MT5 position ID, including partial fills/closes, commission, swap, fees, and net P&L
- Idempotent importer, import-run audit log, and import-result UI
- Read-only imported execution data with editable journal annotations

Success condition:

> User can manually export and safely import completed trades from registered local MT5 accounts without sending or modifying any MT5 trade.

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

- Monthly target
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

1. Set journal base currency, reporting timezone, and monthly target.
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
14. View monthly target progress.
15. Compare strategies and setups.
16. Track rule adherence.
17. Complete daily reviews.
18. Complete weekly reviews.
19. Complete monthly reviews.
20. Receive risk warnings.
21. Export or back up local journal data.

---

# 31. Suggested Initial Pages

Keep the first implementation small.

```text
1. Dashboard
2. Journal / MT5 Import
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
MT5 Import
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

# 33. Local MT5 Import Architecture

MT5 is a local, read-only data source in Phase 2. It is not the foundation of the application and does not receive commands from it.

```text
Selected MT5 terminal
        ↓ manual exporter script
MT5 Common Files CSV export
        ↓ manual import action
Validated SQLite journal database
        ↓
Risk Engine → Analytics Engine → Dashboard
```

The exporter identifies its source by MT5 account login and broker server. The importer accepts only registered accounts and fully closed positions, groups related deals by MT5 position ID, and records every import outcome. Manual entry remains available for non-MT5 trades; it must not duplicate an imported position.

MT5 execution values remain read-only after import. Journal annotations are editable, and the trade enters R-based metrics only once the user supplies planned risk.

The journal and terminal must run on the same machine, or the journal must have local read access to the MT5 Common Files directory.

Future data-source compatibility:

```text
Manual Entry ─────┐
                  │
MT5 Import ───────┼──→ Unified Trade Database
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

> I do not trade to hit my monthly target. I execute my tested edge correctly, control risk, and allow the monthly result to emerge from the process.

The monthly target is useful for planning.

The process determines whether the target is realistically achievable.

---

# 35. Recommended First Implementation Milestone

The first usable milestone should contain only:

```text
Settings
+
MT5 Account Setup
+
Local MT5 Import
+
Journal Enrichment
+
Basic Dashboard (dollar P&L only until planned risk is recorded)
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

## MT5 Import Tests

- Import a valid completed MT5 position.
- Aggregate multiple entries, partial closes, commission, swap, fees, and net P&L into one journal trade.
- Reject an unknown account, broker-server mismatch, base-currency mismatch, invalid schema, corrupt CSV, or incomplete temporary export without changing the database.
- Re-import the same position safely; refresh MT5-owned data while preserving journal annotations.
- Exclude open positions, pending orders, and non-trading balance/credit operations.
- Verify imported P&L is visible immediately and R/process metrics remain excluded until planned risk is recorded.
- Verify all reporting-period grouping uses the configured journal timezone.

## Regression Tests

- Verify manual trades and imported trades cannot share the same source identity.
- Verify imported execution values cannot be edited through the UI.
- Verify migration upgrade paths preserve existing journal data and create a recoverable database backup before schema changes.
- Verify no importer path submits, edits, or blocks an MT5 trade.
