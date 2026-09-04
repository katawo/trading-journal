# Trade Compass

Survival · Consistency · Discipline — a post-trade assessment app focused on trader mental health through three-pillar reviews of Psychology, Risk Management, and Trading System performance.

## Run locally

```bash
make setup
make run
```

`make run` keeps Streamlit running and automatically reruns the app when source files are saved. `make test` runs the fast maintained behavior suite plus a Streamlit smoke render; `make test-bdd` selects explicit Given/When/Then scenarios; `make test-web` runs the fuller Streamlit interaction regression suite, and `make test-browser` runs the optional Chromium scenario. Use `make check` for the fast suite plus compilation.

Set `TRADING_JOURNAL_DB` to choose a database location during source development; it defaults to `data/trading_journal.db`.
To intentionally erase all local accounts, imports, settings, strategies, and framework evidence, run `make reset-db CONFIRM_RESET=yes`. Restart the app afterwards to create a clean database. The three-pillar framework is greenfield: databases from an earlier schema must be reset before they can be opened; the app does not migrate or reinterpret old reviews.

The retired native desktop launcher and its historical tests, build scripts, workflow, and documentation are archived under `legacy/desktop/`. They are excluded from normal development, testing, packaging, and releases.

## Import MT5 history

1. Copy `mql5/TradingJournalSync.mq5` to the MT5 `Experts` directory and compile it in MetaEditor. The current EA writes schema v5 with entry SL/TP, initial calculated risk, MT5 magic number, close metadata, account-balance snapshots, the real pre-entry balance when MT5 can establish it, and the MT5 server UTC offset. Reset an older journal database before importing v5 data.
2. Attach it once to any chart in the terminal you trade from. It writes completed positions to `trading_journal/<MT5-login>_positions.csv` after trade-deal events with a 60-second safety refresh, and writes temporary open-position snapshots to `<MT5-login>_open_positions.csv` every 10 seconds.
3. In **Settings → MT5 Accounts**, approve each account using its exact login, broker server, and deposit currency. Choose one journal-wide reporting clock: **Server Timezone** (the MT5 clock preserved in every export), **UTC**, or the computer's **Local Timezone**. Monetary reports always use the selected account's currency—accounts are never converted or aggregated. Leave the custom export-path field blank to use the matching account-specific default. The app detects native Windows `%APPDATA%` and Linux Wine locations (including `WINEPREFIX`, `~/.wine`, and `~/.mt5`); its detected source is shown in the advanced export-location panel. If your terminal is elsewhere, start the app with `TRADING_JOURNAL_MT5_COMMON_FILES` set to its `Terminal/Common/Files` directory.
4. During local source development, the app checks configured exports every five seconds and imports changed snapshots automatically. Ongoing rerenders its operational status, metrics, and positions every five seconds while that page is open.

The sync EA never sends orders. It exports every currently open position, regardless of symbol or magic number, to the Ongoing workspace; only fully closed positions feed permanent post-trade evidence. Concurrent positions with the same symbol and direction can be grouped manually into one live logical trade. That grouping survives disposable snapshots, remains pending through partial closes, and enters review and reporting as one trade only after every member is present in completed history. Pending orders are not synchronized. A disconnected terminal does not publish a false-fresh live snapshot. The journal never stores an MT5 password. `mql5/TradingJournalExporter.mq5` remains available for one-off manual exports.

The local app does not upload CSV files, journal data, reviews, or account credentials. A hosted app cannot read a computer's MT5 folders; use the separately secured ingestion service described in `docs/multiuser_web_deploy.md` for remote MT5 synchronization.

## Risk policy and reports

Each MT5 account has one versioned **Risk policy**. Its **Standard risk (1R)** normalizes dashboard R for that account; its **Maximum risk per trade** is a separate compliance limit used by Risk reviews and alerts. Monitoring replays logical-trade entries and final closes in UTC order through the current active policy across the complete account history. Replacing that policy requires confirmation. Imported and reviewed policy IDs remain audit evidence and do not select the current analytical policy. The current dashboard does not provide individual-trade editing.

The **Dashboard** reports the all-time record for one selected MT5 account, in that account's currency; it does not convert or aggregate balances across accounts. **Funded capital** is required and immutable after account creation. It is the fixed basis for the lifetime balance curve, drawdown, and monetary Risk-policy amounts; it does not replace the latest live MT5 balance. Every Dashboard metric and both the **Daily** and **Per trade** chart views use mutable logical trades: every raw position starts as one logical trade unless it was assigned to a live logical trade in Ongoing, while compatible completed positions can still be grouped, split, or regrouped from Bearings → Review. A logical trade contributes its aggregate P&L at its final member close, so regrouping recalculates Dashboard and Risk-monitoring history. A membership change supersedes affected assessments for audit and requires a fresh review. Replacing an active Risk policy requires confirmation and recalculates current derived Risk/R analytics across the complete history of that account; saved assessments and period-review snapshots remain unchanged.

## Strategies and backtests

Use **Settings → Strategies** to maintain reusable system definitions and their optional backtest period, sample size, win rate, expectancy, net R, and notes. Each MT5 account is bound to one saved strategy when it is created, so imported trades, reviews, the Dashboard, and Bearings always use that account’s system. The binding can still be changed in Settings until the account has imported trades, after which it locks. A strategy can be deliberately shared by multiple accounts. Use **Analytics** to compare accounts that share a strategy; monetary results remain separate by currency. Backtest data is informational and never changes live P&L or R.

## Trading framework

**Framework** is a post-trade process journal around read-only MT5 history. A complete assessment rates 12 Psychology, Risk, and Trading System criteria as **Pass / Partial / Fail**, records failure tags and hard-rule events, then produces raw pillar scores, a trade-quality label, and an explicit Hard-rule `Clear` or `Fail` result. A hard-rule failure cannot be hidden by a high average; very low raw scores are shown as **Needs improvement** rather than Good, and profitable hard-rule failures are **Bad Wins**. It recognises **Specific preset SL** (verified MT5-calculated initial risk) and **Real-loss estimate** (`abs(net P&L)` for a loss without calculable initial risk). **Funded capital** is fixed for policy limits and drawdown; it is never used as per-trade risk evidence. No pre-trade approval or session workflow exists. Corrections overwrite the current assessment so only the latest save is retained.

`STOP` and `CAUTION` are retrospective monitoring signals, never MT5 controls. An automatically reached daily, weekly, drawdown, or loss-streak limit is a warning, not a Hard-rule failure; a later entry is only a Shutdown review candidate until the reviewer confirms the enabled **Trading after hard shutdown** rule. Drawdown and losing streak have independent Daily, Weekly, Monthly, or All-time reset cadences in the versioned Risk policy; both default to Daily. A maximum-drawdown breach remains active through recovery until its configured period resets. Threshold-only policy changes preserve accumulated state but reevaluate each limit from the new policy's save time. The effective hard-rule result is snapshotted with a saved assessment, so later Framework Rules changes do not rewrite history. Framework monitoring adds rolling 20/30/50 scorecards, readiness as the weakest complete pillar, recurring issue tags, quality/outcome distribution, and saved weekly/monthly reviews with one priority action. Maximum open risk is reference-only because the bridge exports completed positions. Psychology, Risk, and Trading System scores and their roadmap gates are all account-specific. The [three-pillar operating guide](docs/three_pillar_framework_guide.md) is the single source of truth for the framework's workflow and scoring rules.

## Quality approach

- **DDD:** domain models, use cases, and persistence adapters are separated under `src/trading_journal`.
- **BDD:** executable behavior is described in `features/mt5_import.feature`.
- **TDD:** pytest covers the importer before the Streamlit adapter, including idempotency and failed-import safety.
