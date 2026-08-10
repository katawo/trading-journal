# Trading Journal

Local-first Streamlit trading journal with a read-only MetaTrader 5 import path.

## Run locally

```bash
make setup
make run
```

`make run` keeps Streamlit running and automatically reruns the app when source files are saved. Use `make test` for tests and `make check` for tests plus compilation.

Set `TRADING_JOURNAL_DB` to choose a database location; it defaults to `data/trading_journal.db`.

## Import MT5 history

1. Copy `mql5/TradingJournalSync.mq5` to the MT5 `Experts` directory and compile it in MetaEditor.
2. Attach it once to any chart in the terminal you trade from. It writes `trading_journal/<MT5-login>_positions.csv` under MT5 Common Files after trade-deal events, plus a 60-second safety refresh.
3. In **Settings → General**, configure the journal's base currency, then approve the account in **Settings → MT5 Accounts** using its exact login and broker server. Leave the custom export-path field blank to use the matching account-specific default. The app detects native Windows `%APPDATA%` and Linux Wine locations (including `WINEPREFIX`, `~/.wine`, and `~/.mt5`); its detected source is shown in the advanced export-location panel. If your terminal is elsewhere, start the app with `TRADING_JOURNAL_MT5_COMMON_FILES` set to its `Terminal/Common/Files` directory.
4. Keep **Dashboard** open. The app checks the configured export every 15 seconds and imports a changed snapshot automatically.

The sync EA and importer accept completed positions only and never send orders. Re-importing refreshes the MT5-owned execution data. The journal never stores an MT5 password. `mql5/TradingJournalExporter.mq5` remains available for one-off manual exports.

## Risk baseline and reports

In **Settings**, you can enable a journal-wide default planned risk (1R). It applies dynamically to every imported trade, and changing it recalculates every R value. The current dashboard does not provide individual-trade editing.

The **Dashboard** provides period filters, headline performance metrics, and a **Daily** or **Per trade** chart view. The per-trade view is the read-only closed-trade detail, showing each completed position's P&L and post-close drawdown; it is not floating or intra-trade MT5 drawdown. Its target progress is calculated against the monthly target for every calendar month in the selected period. Enable **Track balance growth and percentage drawdown** in Settings and enter the balance immediately before your first imported trade to add balance growth and percentage drawdown. Trades without a journal risk baseline remain visible but are excluded from R metrics.

## Strategies and backtests

Use **Settings → Strategies** to maintain each strategy’s description and optional backtest period, sample size, win rate, expectancy, net R, and notes. Select one saved profile as the journal default; every imported trade inherits it dynamically. The default uses a stable ID, so a profile can be renamed without breaking the journal assignment. The dashboard shows the live result beside that strategy’s backtest context; backtest data is informational and never changes live P&L or R.

## Quality approach

- **DDD:** domain models, use cases, and persistence adapters are separated under `src/trading_journal`.
- **BDD:** executable behavior is described in `features/mt5_import.feature`.
- **TDD:** pytest covers the importer before the Streamlit adapter, including idempotency and failed-import safety.
