# Repository Guidelines

## Project Structure & Module Organization

Trade Compass is a local-first Python 3.12+ Streamlit trading journal. Keep core logic in `src/trading_journal/`: `domain/` contains models and business rules, `application/` contains use cases and orchestration, `infrastructure/` contains SQLite persistence, and `presentation/` contains UI-facing helpers. Streamlit pages live in `app_pages/`; `app.py` is the entry point. Tests are in `tests/`, operational scripts are in `scripts/`, documentation is in `docs/`, and the MT5 exporters are in `mql5/`. Retired desktop code is archived under `legacy/desktop/` and is outside normal maintenance scope.

## Build, Test, and Development Commands

- `make setup` creates `.venv` and installs the application plus test dependencies.
- `make run` starts Streamlit with reload-on-save for source development.
- `make test` runs fast maintained behavior tests plus a Streamlit smoke render.
- `make test-bdd` runs scenarios explicitly organized as Given/When/Then.
- `make test-web` runs the slower Streamlit interaction regression scenarios.
- `make test-browser` runs optional real-browser behavior when Chromium is available.
- `make check` runs the fast test gate, then compiles `app.py` and `src/`.

Set `TRADING_JOURNAL_DB` to use a non-default development database. Never run `make reset-db CONFIRM_RESET=yes` unless intentionally deleting local journal data.

## Coding Style & Naming Conventions

Use four-space indentation, type hints where they clarify interfaces, and standard Python naming: `snake_case` for modules, functions, and variables; `PascalCase` for classes; `UPPER_SNAKE_CASE` for constants. Keep domain policy independent of Streamlit and SQLite details; route UI actions through application services. Name tests as `test_<behavior>.py` and test functions as `test_<expected_outcome>()`. Match the existing direct, readable style; no formatter or linter is currently configured.

## Testing Guidelines

Add or update pytest coverage for every behavior change, especially imports, persistence, risk calculations, and framework scoring. Express scenarios as `given / when / then` in the test name or arrange the body under those comments when it improves clarity. Test observable behavior instead of source text or historical compatibility. Keep tests deterministic by using temporary databases and explicit timestamps. Run `make check` before opening a pull request; run `make test-web` for Streamlit interaction changes. Do not include `legacy/` in tests, compilation, packaging, or maintenance work.

## Commit & Pull Request Guidelines

Use short imperative commit subjects. Existing history commonly uses `fix:`, `feat(<area>):`, or plain imperative summaries; follow that pattern and keep each commit focused. Pull requests should describe the user-visible change and technical approach, link the relevant issue when available, list commands run, and include screenshots for Streamlit UI changes. Call out database-schema, MT5-export, or reset-data implications explicitly.
