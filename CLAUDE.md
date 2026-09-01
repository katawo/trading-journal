# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

Trade Compass (package name `trade-compass`, module `trading_journal`): a local-first trading journal — a Streamlit app that runs as a source-development server or an optional multi-user web deployment (Docker or systemd, one SQLite database per user). Its only connection to MetaTrader 5 is read-only: an MQL5 EA exports completed positions (and, separately, live open-position snapshots) to CSV or pushes them to an HTTP ingestion endpoint; the app never sends orders and never stores an MT5 password. The retired desktop application is archived under `legacy/desktop/` and is outside normal maintenance scope.

## Build / run / test

```bash
make setup     # create .venv and install the app + dev deps (editable)
make run       # streamlit run app.py --server.runOnSave true
make test      # pytest -q; legacy/ is outside the configured test path
make check     # test, then `python -m compileall` on app.py and src/
make reset-db CONFIRM_RESET=yes   # delete the local dev database (data/trading_journal.db*)
```

Run a single test file or test:

```bash
.venv/bin/python -m pytest tests/test_mt5_import.py -q
.venv/bin/python -m pytest tests/test_mt5_import.py::test_imports_closed_position_and_waits_for_planned_risk -q
```

`TRADING_JOURNAL_DB` selects the database path for source development (defaults to `data/trading_journal.db`).

There is no `alembic` migration path wired up even though it's a listed dependency. In its place, `SQLiteJournalRepository.initialize()` (`infrastructure/sqlite_repository.py`) hand-rolls in-place migration for additive changes: after `Base.metadata.create_all()` (which only creates brand-new tables), it runs a series of `PRAGMA table_info(...)` + `ALTER TABLE ... ADD COLUMN` blocks that add any newly-declared *nullable* column to an existing table, preserving all existing rows/data. **When adding a new nullable column to an existing table, add its migration block there** rather than assuming a reset is required — grep that method for the existing per-table blocks (e.g. `post_trade_assessments`, `account_risk_policies`) and follow the same pattern. `_require_clean_framework_schema()` (called first, before `create_all()`) is the harder fallback: it only forces `make reset-db` for schema changes this simple migrator can't handle safely — a genuinely pre-framework database, or (in principle) a new *required* column with no default. Don't add a new column's name to that guard's `expected_columns` sets unless it truly can't be auto-migrated; doing so bypasses the migrator and forces an unnecessary reset (a real bug fixed in this session — see git history on `post_trade_assessment_revisions`).

No linter/formatter is configured (no ruff/black config) — match the surrounding file's style by hand.

### Optional multi-user web deployment

`make deploy-systemd` / `make deploy-docker` stand up the optional hosted deployment (see `docs/multiuser_web_deploy.md`): `TRADING_JOURNAL_MULTIUSER_MODE=1` switches the Streamlit app into a login-gated multi-tenant server, and `trading_journal.ingestion_api` (FastAPI, optional `ingestion` extra) gives the MT5 EA an HTTPS push target as an alternative to writing a local CSV. `make web-user`/`make web-token` (systemd) and `make docker-user`/`make docker-token` (Docker) create accounts and issue MT5 ingestion bearer tokens; `make docker-logs`/`docker-status`/`docker-shell`/`docker-restart*` are Docker operational helpers.

## Architecture

**Layout is DDD-ish under `src/trading_journal/`:**
- `domain/models.py` — pydantic models with no framework dependencies: `MT5PositionExport` (the closed-position CSV/ingestion contract, versioned via `schema_version`), `MT5LivePositionExport`/`MT5LiveSnapshotExport` (open-position snapshots, their own `schema_version`), `ImportedTradeView`, `ImportResult`.
- `domain/review_taxonomy.py` — the controlled post-trade mistake-tag vocabulary (`REVIEW_MISTAKES_BY_PILLAR`, one tuple per Psychology/Risk/System pillar) shared by the Framework application/presentation layers and the DB layer's legacy-code migration.
- `application/` — use-case services, framework-agnostic: `import_mt5.py` (closed-position import/idempotency logic), `live_positions.py` (`LivePositionImportService` — isolated live open-position monitoring; explicitly never writes to the post-trade journal), `dashboard.py` (`DashboardService`), `framework.py` (`FrameworkService` — the three-pillar review logic, ~1900 lines), `auto_sync.py` (`MT5AutoSyncService`, polls configured export paths for both closed and live snapshots), `mt5_paths.py` (locates MT5 `Common/Files` on native Windows and Wine/Linux), `reporting_time.py` / `display_time.py` (server/UTC/local timezone normalization for reports), `multiuser.py` (framework-agnostic helpers for multi-user mode: username validation, per-user DB path, ingestion-token hashing — shared by the Streamlit login UI and the FastAPI ingestion endpoint so neither depends on the other).
- `infrastructure/sqlite_repository.py` — the only persistence adapter (SQLAlchemy models + `SQLiteJournalRepository`), ~3400 lines. All tables (including `StrategyProfile`/`StrategySetup`, the per-account reusable-system definitions), view/DTO dataclasses, and query logic for the app live here; there is no separate ORM-model-vs-repository split beyond this one file.
- `presentation/` — Streamlit-facing helpers usable outside `app.py` directly: `framework.py` (renders the Framework page and sub-views — "Bearings" in the UI, see below; ~2300 lines), `ongoing.py` (renders the live-position "Ongoing" page), `i18n.py` (`tr()` translation helper + `LANGUAGES`, English source strings with a Vietnamese table, `install_streamlit_translations()`), `multiuser_auth.py` (the login gate for multi-user mode), `connection_recovery.py`, `global_alert_bubble.py`, `branding.py`, `browser_timezone.py`, `trade_tags.py`, `formatting.py`.
- `ingestion_api.py` — the optional FastAPI app for the multi-user HTTP ingestion path; imports and reuses `application/import_mt5.py` and `application/live_positions.py` rather than duplicating validation logic, and resolves the bearer token to a user before writing only into that user's own SQLite file.
- `legacy/desktop/` — unsupported historical desktop code. Ignore it in routine testing, compilation, packaging, documentation work, and refactors unless a task explicitly targets the archive.

**`app.py`** (~2100 lines) is the Streamlit entrypoint. It holds formatting/chart-styling helpers, the recovery UI shown when the database schema is incompatible, `repository()`/auto-sync-notice plumbing shared by every page, *and* — for historical reasons — the full render functions for several pages themselves (`render_dashboard`, `render_settings`, `render_mt5_account_settings`, `render_strategy_settings`, `render_strategy_analytics`), plus `main()`, which wires `st.navigation()` across `app_pages/*.py`. Newer pages (Ongoing, the three Bearings pages) instead put their real logic in a `presentation/` module and keep `app.py` untouched. **Don't grow `app.py` further for new pages** — follow the `presentation/<name>.py` + thin `app_pages/<name>.py` shim pattern (see `.claude/commands/new-page.md`) even though older pages don't yet follow it.

**Navigation naming**: the sidebar groups pages under "Workspace": Ongoing, Dashboard, then three Framework pages surfaced to users as **Bearings** — Review (`app_pages/bearings_review.py`), Monitor (`bearings_monitor.py`), Improve (`bearings_improve.py`) — followed by Settings and a read-only Guide page that renders `docs/three_pillar_framework_guide(.vi).md` verbatim. Code/docs/tests still say "Framework"; "Bearings" is UI-only naming, don't rename the underlying modules to match. `app_pages/analytics.py` (cross-account strategy comparison, `render_strategy_analytics`) exists and is fully wired but is deliberately commented out of `st.navigation()` in `app.py::main()` — check there before assuming it's reachable or dead code.

**MT5 bridge** (`mql5/TradingJournalSync.mq5`, `TradingJournalExporter.mq5`): a separate MQL5 EA (compiled the same way as the rest of `forex-ea` — see the parent `CLAUDE.md`) that writes `trading_journal/<login>_positions.csv` (closed positions, 60s safety refresh) and `<login>_open_positions.csv` (live snapshot, every 10s) under MT5 Common Files, or pushes the same payloads to the ingestion API. `MT5PositionExport.schema_version` gates what evidence a given export can carry (e.g. schema v5 adds entry SL/TP, initial risk, magic number, pre-trade balance). When bumping either schema, update the `.mq5` exporter and the matching `domain/models.py` model together. Unlike the internal Framework-schema additions described above, the core `trades` columns this gates (`expected_columns["trades"]` in `_require_clean_framework_schema()`) are enforced by the hard guard, not the soft column migrator, so bumping the MT5 export schema still means a database reset (`.claude/commands/schema-bump.md` walks through the closed-position case step by step) — unless you deliberately add the new column(s) to the `trades`/`ALTER TABLE` migration block instead and drop them from `expected_columns`.

**i18n**: all user-facing strings should go through `tr()` (`presentation/i18n.py`) with English as the literal dict key; add new strings to the `VI` table when adding UI text intended for both locales.

## Domain conventions to preserve

- **Read-only MT5**: the importer and sync EA accept only completed/live positions and never place/modify orders; don't add a write path back to MT5 without an explicit request.
- **Risk-policy compliance requires known risk; Dashboard/Monitor R is a separate, always-on normalization**: Dashboard R, Expectancy R, and the daily/weekly loss-limit R replay are `net P&L ÷ (funded capital × policy standard-risk %)` for every trade with a policy — that normalized 1R applies whether or not the trade's own risk is known. The *policy-compliance* badge (within-policy/over-policy/unavailable) is the one thing gated on established initial risk from a specific preset SL or a real-loss estimate (`abs(net P&L)` for a lossmaking trade with no calculable SL). Never infer *that* per-trade risk from a profitable outcome or an account-balance snapshot. Don't conflate the two — "no known risk" excludes a trade from policy-compliance evidence, not from Dashboard/Monitor R reporting.
- **Per-account currency, no aggregation/conversion**: dashboards and Risk policies are scoped to one MT5 account in that account's currency; don't add cross-account currency conversion or aggregation.
- **Hard-rule results are snapshotted**: a saved Framework assessment's Hard-rule `Clear`/`Fail` result must not be recomputed retroactively when Framework Rules change later — see `PostTradeAssessment`/`PostTradeAssessmentRevision` in `sqlite_repository.py`.
- **Corrections version, they don't overwrite**: Framework assessment edits create a new revision (`PostTradeAssessmentRevision`) and keep prior evidence auditable, rather than mutating the original row.
- **Logical trades are the reporting unit**: Dashboard P&L/balance/drawdown/streaks and completed-trade Risk monitoring (limit replay against time-effective policies) use mutable logical trades in final-close order. Regrouping recalculates derived history while imported MT5 member positions remain immutable and auditable. (`.claude/commands/framework-review.md` currently states the opposite — that monitoring uses raw positions, not logical trades; that predates the logical-trade replay work and is stale, flag it if you're relying on that file.)
- **Live positions never touch the post-trade journal**: `application/live_positions.py`/the Ongoing page are an isolated real-time view over MT5's live snapshot export; only fully closed positions ever become journal/Dashboard/Framework data.
- **Multi-user isolation**: hosted mode isolates tenants with one SQLite file per user and one ingestion token per user (`application/multiuser.py`). Treat changes to this networking or tenancy model as security-relevant.

## Quality approach (from README)

- **DDD**: domain models, use cases, and persistence adapters are separated under `src/trading_journal` (see Architecture above).
- **BDD**: executable behavior is described in `features/mt5_import.feature`.
- **TDD**: pytest covers the importer before the Streamlit adapter, including idempotency and failed-import safety.

## Docs worth reading before non-trivial changes

- `docs/three_pillar_framework_guide.md` (and `.vi.md`) — the single source of truth for the three-pillar (Psychology/Risk/Trading System) scoring and workflow rules; `README.md`'s "Trading framework" section is a summary of this.
- `docs/database-schema.puml` / `docs/review-state-machine.puml` — schema and Framework review-state diagrams.
- `docs/multiuser_web_deploy.md` — systemd vs. Docker deployment, account/token provisioning, operating notes for the optional web mode.
- `docs/mt5-import-scale-audit.md` — import-path scale/performance notes.

## Specialized agents, skills, and commands already configured here

- `.claude/skills/trading-app-engineer` — prefer it over ad hoc approaches for feature work, refactors, and architecture/design decisions here.
- `.claude/agents/` — `trading-domain-expert`, `trading-fullstack-developer`, `trading-qa-tester`, `trading-solution-architect`, scoped as their descriptions state (domain-correctness review, default implementation, testing/verification, and structural/architecture decisions respectively).
- `.claude/commands/` — `/check` (run `make check` and triage failures), `/framework-review` (diff review against the Domain conventions above — see the staleness note on logical-trade monitoring), `/new-page` (scaffold a page following the `presentation/` + thin-shim convention), `/schema-bump` (guided MT5 `schema_version` bump).
- `AGENTS.md` at the repo root carries an overlapping, more generic project-guidelines summary (structure, commands, style, commit/PR conventions) — this file is the more detailed and current source for architecture-specific guidance.
