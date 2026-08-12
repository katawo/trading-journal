VENV ?= .venv
PYTHON ?= python3
APP := app.py
VENV_PYTHON := $(VENV)/bin/python
STREAMLIT := $(VENV_PYTHON) -m streamlit
DB_PATH ?= $(TRADING_JOURNAL_DB)
DB_PATH := $(if $(strip $(DB_PATH)),$(DB_PATH),data/trading_journal.db)

.DEFAULT_GOAL := help

.PHONY: help venv setup run desktop bundle test check reset-db

help: ## Show available commands.
	@awk 'BEGIN {FS = ":.*##"} /^[a-zA-Z_-]+:.*##/ {printf "\033[36m%-8s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

venv: ## Create the local Python virtual environment if needed.
	@test -x $(VENV_PYTHON) || $(PYTHON) -m venv $(VENV)

setup: venv ## Install the application and development dependencies.
	$(VENV_PYTHON) -m pip install -e '.[dev]'

run: venv ## Start Streamlit with automatic reruns when source files are saved.
	$(STREAMLIT) run $(APP) --server.runOnSave true

desktop: venv ## Start the local desktop launcher, MT5 sync worker, and desktop UI.
	$(VENV_PYTHON) -m pip install -e '.[desktop]'
	$(VENV_PYTHON) -m trading_journal.desktop

bundle: venv ## Build a portable desktop bundle for the current operating system.
	$(VENV_PYTHON) -m pip install -e '.[desktop]'
	$(VENV_PYTHON) scripts/build_desktop.py

test: venv ## Run the automated test suite.
	$(VENV_PYTHON) -m pytest -q

check: test ## Compile the application after the tests pass.
	$(VENV_PYTHON) -m compileall -q $(APP) src

reset-db: ## Delete all local journal data. Requires CONFIRM_RESET=yes.
	@test "$(CONFIRM_RESET)" = "yes" || { echo "Refusing to reset. Run: make reset-db CONFIRM_RESET=yes"; exit 2; }
	@test "$(DB_PATH)" != "/" && test "$(DB_PATH)" != "." && test "$(DB_PATH)" != "" || { echo "Unsafe database path: $(DB_PATH)"; exit 2; }
	rm -f "$(DB_PATH)" "$(DB_PATH)-wal" "$(DB_PATH)-shm"
	@echo "Removed journal database: $(DB_PATH)"
