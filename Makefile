VENV ?= .venv
PYTHON ?= python3
APP := app.py
VENV_PYTHON := $(VENV)/bin/python
STREAMLIT := $(VENV_PYTHON) -m streamlit

.DEFAULT_GOAL := help

.PHONY: help venv setup run test check

help: ## Show available commands.
	@awk 'BEGIN {FS = ":.*##"} /^[a-zA-Z_-]+:.*##/ {printf "\033[36m%-8s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

venv: ## Create the local Python virtual environment if needed.
	@test -x $(VENV_PYTHON) || $(PYTHON) -m venv $(VENV)

setup: venv ## Install the application and development dependencies.
	$(VENV_PYTHON) -m pip install -e '.[dev]'

run: venv ## Start Streamlit with automatic reruns when source files are saved.
	$(STREAMLIT) run $(APP) --server.runOnSave true

test: venv ## Run the automated test suite.
	$(VENV_PYTHON) -m pytest -q

check: test ## Compile the application after the tests pass.
	$(VENV_PYTHON) -m compileall -q $(APP) src
