# Trading Journal

Local-first desktop journal with a read-only MetaTrader 5 import path. The desktop release runs the existing Streamlit interface only on your computer and never exposes its local server to the network.

## Run locally

```bash
make setup
make run
```

`make run` keeps Streamlit running and automatically reruns the app when source files are saved. Use `make test` for tests and `make check` for tests plus compilation.

For the normal local desktop experience, run:

```bash
make desktop
```

It starts the loopback-only journal, a background MT5 sync worker, and opens the interface in your default browser. Build a portable bundle for the current operating system with `make bundle`. See [the desktop guide](docs/desktop_app.md) for installation, backups, and release details.

Set `TRADING_JOURNAL_DB` to choose a database location during source development; it defaults to `data/trading_journal.db`. The desktop bundle stores its database in the operating system's user-data directory so application updates cannot overwrite it.
To intentionally erase all local accounts, imports, settings, strategies, and framework evidence in source development, run `make reset-db CONFIRM_RESET=yes`. Restart the app afterwards to create a clean database. In the desktop application, use **Settings → Reset local database**, type `RESET`, and the desktop supervisor will restart a clean journal while preserving MT5 export files and logs. The three-pillar framework is greenfield: databases from an earlier schema must be reset before they can be opened; the app does not migrate or reinterpret old reviews.

## Import MT5 history

1. Copy `mql5/TradingJournalSync.mq5` to the MT5 `Experts` directory and compile it in MetaEditor. The current EA writes schema v5 with entry SL/TP, initial calculated risk, MT5 magic number, close metadata, account-balance snapshots, the real pre-entry balance when MT5 can establish it, and the MT5 server UTC offset. Reset an older journal database before importing v5 data.
2. Attach it once to any chart in the terminal you trade from. It writes `trading_journal/<MT5-login>_positions.csv` under MT5 Common Files after trade-deal events, plus a 60-second safety refresh.
3. In **Settings → MT5 Accounts**, approve each account using its exact login, broker server, and deposit currency. Choose one journal-wide reporting clock: **Server Timezone** (the MT5 clock preserved in every export), **UTC**, or the computer's **Local Timezone**. Monetary reports always use the selected account's currency—accounts are never converted or aggregated. Leave the custom export-path field blank to use the matching account-specific default. The app detects native Windows `%APPDATA%` and Linux Wine locations (including `WINEPREFIX`, `~/.wine`, and `~/.mt5`); its detected source is shown in the advanced export-location panel. If your terminal is elsewhere, start the app with `TRADING_JOURNAL_MT5_COMMON_FILES` set to its `Terminal/Common/Files` directory.
4. In the desktop application, the background worker checks the configured export every five seconds and imports a changed snapshot automatically, even while Settings or Guide is open. In source development, keep **Dashboard** or **Framework** open for the built-in 15-second auto-sync.

The sync EA and importer accept completed positions only and never send orders. Re-importing refreshes the MT5-owned execution data. The journal never stores an MT5 password. `mql5/TradingJournalExporter.mq5` remains available for one-off manual exports.

The current release is desktop-only: it does not upload CSV files, journal data, reviews, or account credentials to Streamlit Community Cloud. The hosted app cannot read a computer's MT5 folders; remote sharing can be added later as a separately secured sync feature.

## Risk policy and reports

Each MT5 account has one versioned **Risk policy**. Its **Standard risk (1R)** normalizes dashboard R for that account; its **Maximum risk per trade** is a separate compliance limit used by Risk reviews and alerts. A policy change applies to later imports while already-attached policy versions keep historical R and compliance context auditable. The current dashboard does not provide individual-trade editing.

The **Dashboard** reports one selected MT5 account at a time, in that account's currency; it does not convert or aggregate balances across accounts. **Funded capital** is optional during account setup and can be adjusted later. It is the fixed basis for the historical balance curve, drawdown, and monetary Risk-policy amounts; it does not replace the latest live MT5 balance. The dashboard provides period filters, headline performance metrics, and a **Daily** or **Per trade** chart view. Daily realized P&L, balance, and drawdown always use chronological raw MT5 positions. The per-trade view uses mutable logical trades: every raw position starts as one logical trade, while compatible scaled positions can be grouped, split, or regrouped from Framework → Review trades. This affects logical-trade analytics only; it never rewrites account cash-flow history or Risk-limit monitoring. A membership change supersedes affected assessments for audit and requires a fresh review. Trades without an account Risk policy or funded capital remain visible but are excluded from R metrics.

## Strategies and backtests

Use **Settings → Strategies** to maintain each strategy’s description and optional backtest period, sample size, win rate, expectancy, net R, and notes. Select one saved profile as the journal default; every imported trade inherits it dynamically. Optionally add comma-separated MT5 magic numbers to map EA trades to a strategy automatically. The default uses a stable ID, so a profile can be renamed without breaking the journal assignment. The dashboard shows the live result beside that strategy’s backtest context; backtest data is informational and never changes live P&L or R.

## Trading framework

**Framework** is a post-trade process journal around read-only MT5 history. A complete assessment rates 13 Psychology, Risk, and Trading System criteria as **Pass / Partial / Fail**, records failure tags and hard-rule events, then produces raw pillar scores, a trade-quality label, and an explicit Hard-rule `Clear` or `Fail` result. A hard-rule failure cannot be hidden by a high average; very low raw scores are shown as **Needs improvement** rather than Good, and profitable hard-rule failures are **Bad Wins**. It recognises **Specific preset SL** (verified MT5-calculated initial risk), **Real-loss estimate** (`abs(net P&L)` for a loss without calculable initial risk), and an opt-in **Pre-trade-balance estimate** for a profitable no-SL trade when MT5 captured the actual balance immediately before entry. The option defaults off in each Risk policy and is advisory only: it never creates a completed Risk, Psychology, System, Process, roadmap, or readiness score. **Funded capital** is fixed for policy limits and drawdown; it is never used as a per-trade balance fallback. No pre-trade approval, session, or trade-linking workflow exists. Corrections create an auditable new assessment version and retain prior evidence.

`STOP` and `CAUTION` are retrospective monitoring signals, never MT5 controls. An automatically reached daily, weekly, drawdown, or loss-streak limit is a warning, not a Hard-rule failure; a later entry is only a Shutdown review candidate until the reviewer confirms the enabled **Trading after hard shutdown** rule. The effective hard-rule result is snapshotted with a saved assessment, so later Framework Rules changes do not rewrite history. Framework monitoring adds rolling 20/30/50 scorecards, readiness as the weakest complete pillar, recurring issue tags, quality/outcome distribution, and saved weekly/monthly reviews with one priority action. Maximum open risk is reference-only because the bridge exports completed positions. Psychology and Trading System scores are trader-wide; Risk monitoring and its roadmap gate remain account-specific. The [three-pillar operating guide](docs/three_pillar_framework_guide.md) is the single source of truth for the framework's workflow and scoring rules.

## Quality approach

- **DDD:** domain models, use cases, and persistence adapters are separated under `src/trading_journal`.
- **BDD:** executable behavior is described in `features/mt5_import.feature`.
- **TDD:** pytest covers the importer before the Streamlit adapter, including idempotency and failed-import safety.
