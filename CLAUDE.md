# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

A local-first trading journal: a Streamlit web app that also ships as a desktop app (Windows/Linux), with a read-only import path from MetaTrader 5 (MQL5). It never sends orders and never stores an MT5 password — the MT5 side is a small EA that exports completed positions to CSV; the app only reads that CSV. This is a sibling project inside the larger `forex-ea` MQL5 EA collection, but it is an independent Python/Streamlit codebase (its own `pyproject.toml`, venv, tests).

## Build / run / test

```bash
make setup     # create .venv and install the app + dev deps (editable)
make run       # streamlit run app.py --server.runOnSave true
make desktop   # local desktop experience: loopback-only server + background MT5 sync worker
make bundle    # build a portable PyInstaller desktop bundle for the current OS
make test      # pytest -q
make check     # test, then `python -m compileall` on app.py and src/
make reset-db CONFIRM_RESET=yes   # delete the local dev database (data/trading_journal.db*)
```

Run a single test file or test:

```bash
.venv/bin/python -m pytest tests/test_mt5_import.py -q
.venv/bin/python -m pytest tests/test_mt5_import.py::test_imports_closed_position_and_waits_for_planned_risk -q
```

`TRADING_JOURNAL_DB` selects the database path for source development (defaults to `data/trading_journal.db`). The desktop bundle instead stores its database under the OS user-data directory (see `desktop_data_directory()` in `src/trading_journal/desktop.py`) so app updates can't overwrite it.

There is no `alembic` migration path wired up even though it's a listed dependency — the schema is greenfield. A breaking schema change means bumping the MT5 export schema version and telling users to reset their local database (see "Domain conventions" below); don't add migrations to preserve old data unless asked.

## Architecture

**Layout is DDD-ish under `src/trading_journal/`:**
- `domain/models.py` — pydantic models with no framework dependencies: `MT5PositionExport` (the CSV import contract, versioned via `schema_version`), `ImportedTradeView`, `ImportResult`.
- `application/` — use-case services, framework-agnostic: `import_mt5.py` (import/idempotency logic), `dashboard.py` (`DashboardService`), `framework.py` (`FrameworkService` — the three-pillar review logic), `auto_sync.py` (`MT5AutoSyncService`, polls configured export paths), `mt5_paths.py` (locates MT5 `Common/Files` on native Windows and Wine/Linux), `reporting_time.py` / `display_time.py` (server/UTC/local timezone normalization for reports).
- `infrastructure/sqlite_repository.py` — the only persistence adapter (SQLAlchemy models + `SQLiteJournalRepository`), ~2500 lines. All tables, view/DTO dataclasses, and query logic for the app live here; there is no separate ORM-model-vs-repository split beyond this one file.
- `presentation/` — Streamlit-facing helpers usable outside `app.py` directly: `framework.py` (renders the Framework page and its sub-views), `i18n.py` (`tr()` translation helper + `LANGUAGES`, English source strings with a Vietnamese table, `install_streamlit_translations()`), `global_alert_bubble.py`, `desktop_reset_restart.py`.
- `desktop.py` — the desktop runtime: resolves per-OS data directories, supervises the Streamlit server + MT5 sync worker as child processes, an instance lock, a JSON-file-based sync-status/control channel between the worker and the Streamlit UI, and the PyInstaller/pywebview entrypoints. Read this file before changing anything about desktop startup, shutdown, or the reset flow — it's a small state machine, not just a launcher script.

**`app.py`** (~1100 lines) is the Streamlit entrypoint: formatting helpers (currency/number/signed-value formatting used throughout the UI), chart styling, the recovery UI shown when the database schema is incompatible, and `main()`, which wires `st.navigation()` across `app_pages/*.py`. Each file under `app_pages/` is a thin `st.Page` shim that calls into `presentation/` or `app.py` render functions — put real page logic in `presentation/` or a new module, not in `app_pages/`.

**MT5 bridge** (`mql5/TradingJournalSync.mq5`, `TradingJournalExporter.mq5`): a separate MQL5 EA (compiled the same way as the rest of `forex-ea` — see the parent `CLAUDE.md`) that writes `trading_journal/<login>_positions.csv` under MT5 Common Files after trade-deal events. `MT5PositionExport.schema_version` gates what evidence a given export can carry (e.g. schema v5 adds entry SL/TP, initial risk, magic number, pre-trade balance). When bumping the schema, update both the `.mq5` exporter and `domain/models.py` together, and expect a database reset (no migration path — see above).

**i18n**: all user-facing strings should go through `tr()` (`presentation/i18n.py`) with English as the literal dict key; add new strings to the `VI` table when adding UI text intended for both locales.

## Domain conventions to preserve

- **Read-only MT5**: the importer and sync EA accept only completed positions and never place/modify orders; don't add a write path back to MT5 without an explicit request.
- **R multiples require known risk**: a trade is only included in R metrics once its initial risk is established — via a specific preset SL, a real-loss estimate (`abs(net P&L)` for a lossmaking trade with no calculable SL), or an opt-in pre-trade-balance estimate (profitable, no-SL, only when MT5 captured the actual pre-entry balance). Never infer risk from the trade's outcome.
- **Per-account currency, no aggregation/conversion**: dashboards and Risk policies are scoped to one MT5 account in that account's currency; don't add cross-account currency conversion or aggregation.
- **Hard-rule results are snapshotted**: a saved Framework assessment's Hard-rule `Clear`/`Fail` result must not be recomputed retroactively when Framework Rules change later — see `PostTradeAssessment`/`PostTradeAssessmentRevision` in `sqlite_repository.py`.
- **Corrections version, they don't overwrite**: Framework assessment edits create a new revision (`PostTradeAssessmentRevision`) and keep prior evidence auditable, rather than mutating the original row.
- **Logical trades vs. raw positions**: daily P&L/balance/drawdown always use raw chronological MT5 positions; only the per-trade view uses mutable "logical trades" (grouped/split/regrouped scaled positions). Don't let logical-trade edits touch account cash-flow history or Risk-limit monitoring.
- **Desktop is loopback-only**: the desktop server binds `127.0.0.1` and is never exposed to the network; the hosted/Community-Cloud build cannot read local MT5 folders. Don't change desktop networking without flagging it as a security-relevant change.

## Quality approach (from README)

- **DDD**: domain models, use cases, and persistence adapters are separated under `src/trading_journal` (see Architecture above).
- **BDD**: executable behavior is described in `features/mt5_import.feature`.
- **TDD**: pytest covers the importer before the Streamlit adapter, including idempotency and failed-import safety.

## Docs worth reading before non-trivial Framework changes

- `docs/three_pillar_framework_guide.md` (and `.vi.md`) — the single source of truth for the three-pillar (Psychology/Risk/Trading System) scoring and workflow rules; `README.md`'s "Trading framework" section is a summary of this.
- `docs/database-schema.puml` — schema diagram.
- `docs/desktop_app.md` — desktop install/backup/release details.

## Specialized skills already configured here

This repo has a `.claude/skills/trading-app-engineer` skill tailored to this codebase (Python/Streamlit trading journal engineering) — prefer it over ad hoc approaches for feature work, refactors, and architecture/design decisions here.
