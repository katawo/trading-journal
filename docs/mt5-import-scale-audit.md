# MT5 trade-history scale audit

**Status:** code-reading audit only — no benchmark has been run yet, no numbers below are measured. This documents where the import/query paths look like they don't scale with trade count, so a future pass can decide what (if anything) is worth fixing.

## Why this matters now

The app's multi-user web mode (`TRADING_JOURNAL_MULTIUSER_MODE=1`, see `src/trading_journal/application/multiuser.py`) gives every logged-in user their own SQLite database (`src/trading_journal/presentation/multiuser_auth.py`). That doesn't change how any single account's import/query code behaves — it just means a per-account scaling problem that was tolerable for one operator is now multiplied across every deployed user, each with their own growing MT5 trade history.

A realistic active MT5 account (5-20 trades/day) over 1-3 years lands around **2,000-15,000 closed positions**. Nothing in the repo currently documents an expected row count, and there are no performance tests or large synthetic fixtures (`tests/` fixtures are all single-digit to low-dozens of rows).

## Hotspots found

### 1. Import upsert is N+1, not bulk

`SQLiteJournalRepository.upsert_mt5_positions` (`src/trading_journal/infrastructure/sqlite_repository.py:2826-2911`) loops over every position in the import (`for position in positions:`, line 2853) and, per row:
- runs one `SELECT` to check for an existing `Trade` by `mt5_position_id` (line 2854)
- on a new row, inserts a `LogicalTrade` and immediately `session.flush()`es it before inserting the `Trade` (lines 2887-2904)

There's no bulk-fetch of existing `mt5_position_id`s into a dict up front, and no `executemany`/bulk insert. A 10,000-row export means ~10,000 individual `SELECT`s plus a flush per new row, all inside one transaction (`with self._sessions.begin()`, line 2840).

### 2. Auto-sync re-processes the whole file on every change, not just new rows

`MT5AutoSyncService.sync_configured_exports` (`src/trading_journal/application/auto_sync.py:35-90`) has a cheap short-circuit: if the file's `(mtime_ns, size)` fingerprint is unchanged since the last import, it skips entirely (lines 58-65). But once the file *has* changed — which is the normal case every time MT5 appends newly closed trades — it reads the entire file (`path.read_bytes()`, line 66), and `import_bytes` re-parses and re-validates **every row**, then re-runs the full N+1 upsert loop from #1 against the whole history (existing rows just take the cheaper `UPDATE` branch, but they're still one `SELECT` + one `UPDATE` each). There's no byte-offset or last-seen-position-id tracking to process only the newly appended rows.

### 3. Dashboard/framework services load full account history, filter in Python

- `DashboardService.build_report` (`src/trading_journal/application/dashboard.py:125-137`) calls `list_trade_performance(account_id)` and `list_account_balance_movements(account_id)` with no date bound at the SQL level, then filters to `[start_date, end_date]` in a Python list comprehension (lines 136-137) after the full history is already materialized.
- `FrameworkService._account_trade_scores` (`src/trading_journal/application/framework.py`, ~line 978) loads the full closed-trade history per account via `list_closed_trades_for_review(account_id)` plus all assessments/positions/risk-events, cached only in-memory (`self._account_score_cache`) — no windowing at the query level even though most callers only need a rolling window (`window: int = 20`).
- `FrameworkService._trader_trade_process_scores` (~line 965) makes this multiplicative: it re-runs the full per-account load/score for **every** MT5 account belonging to the trader (`for account in self._repository.list_mt5_accounts() for score in self.trade_process_scores(account.id)`), triggered by public entry points like `pillar_scores()` and `focus_progress()`.

### 4. Trade list has no pagination

`SQLiteJournalRepository.list_trades` (`src/trading_journal/infrastructure/sqlite_repository.py:2679-2698`) has no `LIMIT`/`OFFSET` — it loads every trade across every account, ordered by `exit_time desc`, then builds a `TradeListItem` per row. The live "Closed-trade detail" table in `app.py` (~lines 1400-1414) similarly builds an unbounded in-memory `pandas.DataFrame` from an in-memory `per_trade` analytics structure and renders it via plain `st.dataframe()` — no pagination or virtualization.

## What's *not* yet known

- Actual wall-clock cost of each hotspot at realistic scale (2,000-15,000 rows) — nothing has been measured.
- Whether SQLite's own query planner/caching makes the per-row `SELECT` in #1 cheap enough in practice to not matter (indexed lookup on `mt5_position_id` vs. genuine N+1 overhead from ORM session churn).
- Whether Streamlit's `st.dataframe()` rendering cost (#4) is actually the bottleneck for a user, versus the query/DataFrame-build step before it.

## Suggested next step

Build a small benchmark harness (synthetic MT5 CSV export generator at 1k/5k/15k/30k rows, reusing the fixture pattern in `tests/test_mt5_import.py`'s `write_export`/`V5_HEADER`) to actually time each hotspot before deciding what to fix. Until that's run, treat the above as suspects, not confirmed bottlenecks.

If/when a fix is warranted, likely candidates in priority order:
1. Bulk-fetch existing `mt5_position_id → Trade` mappings before the upsert loop instead of one `SELECT` per row (#1) — this also speeds up #2 for free.
2. Track a byte-offset or last-imported `position_id` in auto-sync so only newly appended rows are parsed/upserted (#2).
3. Push `start_date`/`end_date` filtering into the SQL query for `build_report` and framework scoring instead of loading the full account history and filtering in Python (#3).
4. Add `LIMIT`/`OFFSET` (or a rolling window) to `list_trades()` and paginate the trades UI (#4).
