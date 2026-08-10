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

1. Copy `mql5/TradingJournalExporter.mq5` to the MT5 Scripts directory and compile it in MetaEditor.
2. Run it in each approved MT5 terminal. It writes `trading_journal/positions.csv` under MT5 Common Files.
3. In the app, configure the journal's base currency and approve the account by its exact login and broker server.
4. Paste the local Common Files CSV path in **MT5 Import** and import it.

The importer accepts completed positions only, never sends orders, and preserves journal annotations when an MT5 position is re-imported.

## Risk baseline and reports

In **Settings**, you can enable a journal-wide default planned risk (1R). It applies dynamically to every trade without a trade-level override; changing it recalculates inherited R values without affecting overrides. In **Journal**, enable **Override the journal risk baseline** only for trades that need a different risk amount.

The **Dashboard** provides period filters, headline performance metrics, and a **Daily** or **Per trade** chart view. The per-trade view shows each completed position's P&L and its post-close drawdown; it is not floating or intra-trade MT5 drawdown. Its target progress is calculated against the monthly target for every calendar month in the selected period. Enable **Track balance growth and percentage drawdown** in Settings and enter the balance immediately before your first imported trade to add balance growth and percentage drawdown. Trades without either a baseline or an override remain visible but are excluded from R metrics.

## Strategies and backtests

Use **Strategies** to maintain each strategy’s description and optional backtest period, sample size, win rate, expectancy, net R, and notes. Select one saved profile as the journal default; all untagged trades inherit it dynamically. A trade-level strategy override always takes priority. Saved-profile assignments use stable IDs, so a profile can be renamed without breaking its linked trades or default. The dashboard shows the live result beside that strategy’s backtest context; backtest data is informational and never changes live P&L or R.

## Quality approach

- **DDD:** domain models, use cases, and persistence adapters are separated under `src/trading_journal`.
- **BDD:** executable behavior is described in `features/mt5_import.feature`.
- **TDD:** pytest covers the importer before the Streamlit adapter, including idempotency and failed-import safety.
