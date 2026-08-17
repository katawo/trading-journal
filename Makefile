VENV ?= .venv
PYTHON ?= python3
APP := app.py
VENV_PYTHON := $(VENV)/bin/python
STREAMLIT := $(VENV_PYTHON) -m streamlit
DB_PATH ?= $(TRADING_JOURNAL_DB)
DB_PATH := $(if $(strip $(DB_PATH)),$(DB_PATH),data/trading_journal.db)

# Multi-user web deployment (see docs/multiuser_web_deploy.md). Override as needed:
#   make deploy-systemd DATA_DIR=/srv/journal SERVICE_USER=journal
DATA_DIR ?= /var/lib/trade-compass
SERVICE_USER ?= $(USER)
SYSTEMD_DIR ?= /etc/systemd/system
SUDO ?= sudo
COMPOSE := docker compose -f deploy/docker-compose.yml

.DEFAULT_GOAL := help

.PHONY: help venv setup run desktop bundle test check reset-db \
        deploy-systemd deploy-systemd-down deploy-docker deploy-docker-down \
        web-user web-token docker-user docker-token \
        docker-logs docker-shell docker-status \
        docker-restart docker-restart-web docker-restart-ingestion docker-restart-caddy

# Account/token creation for the systemd path (Docker uses `compose run` — see the guide).
NAME ?= $(USER_NAME)
EMAIL ?= $(USER_NAME)@example.com

help: ## Show available commands.
	@awk 'BEGIN {FS = ":.*##"} /^[a-zA-Z_-]+:.*##/ {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

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

deploy-systemd: venv ## Deploy the multi-user web app + ingestion API as systemd services (preferred). Reads deploy/.env.
	@test -f deploy/.env || { echo "Create deploy/.env from deploy/.env.example and set TRADING_JOURNAL_MULTIUSER_COOKIE_KEY (openssl rand -hex 32)."; exit 2; }
	@. ./deploy/.env; test -n "$$TRADING_JOURNAL_MULTIUSER_COOKIE_KEY" || { echo "Set TRADING_JOURNAL_MULTIUSER_COOKIE_KEY in deploy/.env."; exit 2; }
	$(VENV_PYTHON) -m pip install -e '.[multiuser,ingestion]'
	$(SUDO) mkdir -p "$(DATA_DIR)"
	$(SUDO) chown "$(SERVICE_USER)" "$(DATA_DIR)"
	@. ./deploy/.env; for unit in trade-compass-web trade-compass-ingestion; do \
		echo "Installing $$unit.service"; \
		sed -e 's#/opt/trade-compass#$(CURDIR)#g' \
		    -e 's#/var/lib/trade-compass#$(DATA_DIR)#g' \
		    -e 's#^User=.*#User=$(SERVICE_USER)#' \
		    -e "s#REPLACE_WITH_A_LONG_RANDOM_SECRET#$$TRADING_JOURNAL_MULTIUSER_COOKIE_KEY#" \
		    deploy/$$unit.service | $(SUDO) tee $(SYSTEMD_DIR)/$$unit.service >/dev/null; \
	done
	$(SUDO) systemctl daemon-reload
	$(SUDO) systemctl enable --now trade-compass-web trade-compass-ingestion
	@echo "Services started. Create accounts with: make web-user USER_NAME=alice"
	@echo "Then install Caddy and point deploy/Caddyfile at your domain (see docs/multiuser_web_deploy.md)."

deploy-systemd-down: ## Stop and disable the systemd web services (data is preserved).
	$(SUDO) systemctl disable --now trade-compass-web trade-compass-ingestion || true

deploy-docker: ## Deploy the multi-user web app + ingestion API + Caddy with Docker. Reads deploy/.env.
	@test -f deploy/.env || { echo "Create deploy/.env from deploy/.env.example and set TRADING_JOURNAL_MULTIUSER_COOKIE_KEY (openssl rand -hex 32)."; exit 2; }
	$(COMPOSE) --env-file deploy/.env up -d --build
	@echo "Started. Create accounts with: $(COMPOSE) run --rm web python scripts/add_web_user.py alice --name Alice --email a@example.com"

deploy-docker-down: ## Stop the Docker web deployment (data volume is preserved).
	$(COMPOSE) down

web-user: venv ## Create/update a web account (systemd path). Usage: make web-user USER_NAME=alice [NAME="Alice" EMAIL=a@x.com]
	@test -n "$(USER_NAME)" || { echo "Usage: make web-user USER_NAME=alice [NAME=\"Alice\" EMAIL=a@example.com]"; exit 2; }
	TRADING_JOURNAL_MULTIUSER_DATA_DIR="$(DATA_DIR)" $(VENV_PYTHON) scripts/add_web_user.py "$(USER_NAME)" --name "$(NAME)" --email "$(EMAIL)"

web-token: venv ## Issue an MT5 ingestion token for a user (systemd path). Usage: make web-token USER_NAME=alice
	@test -n "$(USER_NAME)" || { echo "Usage: make web-token USER_NAME=alice"; exit 2; }
	TRADING_JOURNAL_MULTIUSER_DATA_DIR="$(DATA_DIR)" $(VENV_PYTHON) scripts/add_ingestion_token.py "$(USER_NAME)"

docker-user: ## Create/update a web account (Docker path). Usage: make docker-user USER_NAME=alice [NAME="Alice" EMAIL=a@x.com]
	@test -f deploy/.env || { echo "Create deploy/.env from deploy/.env.example first"; exit 2; }
	@test -n "$(USER_NAME)" || { echo "Usage: make docker-user USER_NAME=alice [NAME=\"Alice\" EMAIL=a@example.com]"; exit 2; }
	$(COMPOSE) --env-file deploy/.env run --rm web \
		python scripts/add_web_user.py "$(USER_NAME)" \
		--name "$(NAME)" --email "$(EMAIL)"

docker-token: ## Issue an MT5 ingestion token for a user (Docker path). Usage: make docker-token USER_NAME=alice
	@test -f deploy/.env || { echo "Create deploy/.env from deploy/.env.example first"; exit 2; }
	@test -n "$(USER_NAME)" || { echo "Usage: make docker-token USER_NAME=alice"; exit 2; }
	$(COMPOSE) --env-file deploy/.env run --rm web \
		python scripts/add_ingestion_token.py "$(USER_NAME)"

docker-logs: ## Show real-time Docker logs. Usage: make docker-logs [SERVICE=web|ingestion|caddy] or [all]
	@SERVICE_FILTER=""; if [ -n "$(SERVICE)" ]; then SERVICE_FILTER="$(SERVICE)"; fi; \
	if [ -n "$$SERVICE_FILTER" ]; then \
		$(COMPOSE) --env-file deploy/.env logs -f $$SERVICE_FILTER; \
	else \
		$(COMPOSE) --env-file deploy/.env logs -f; \
	fi

docker-status: ## Show Docker container status and basic stats.
	@echo "Container status:"; \
	$(COMPOSE) --env-file deploy/.env ps; \
	echo ""; \
	echo "Resource usage:"; \
	docker stats --no-stream 2>/dev/null | grep -E '^CONTAINER|trade-compass' || echo "  (use 'docker stats' to monitor)"

docker-shell: ## Open a shell inside the web container. Usage: make docker-shell
	@test -f deploy/.env || { echo "Create deploy/.env from deploy/.env.example first"; exit 2; }
	$(COMPOSE) --env-file deploy/.env run --rm web /bin/bash

docker-restart: ## Restart all Docker services (web, ingestion, caddy).
	@test -f deploy/.env || { echo "Create deploy/.env from deploy/.env.example first"; exit 2; }
	$(COMPOSE) --env-file deploy/.env restart

docker-restart-web: ## Restart the web service (Streamlit app). Required after user changes.
	@test -f deploy/.env || { echo "Create deploy/.env from deploy/.env.example first"; exit 2; }
	$(COMPOSE) --env-file deploy/.env restart web

docker-restart-ingestion: ## Restart the ingestion API service (FastAPI).
	@test -f deploy/.env || { echo "Create deploy/.env from deploy/.env.example first"; exit 2; }
	$(COMPOSE) --env-file deploy/.env restart ingestion

docker-restart-caddy: ## Restart the Caddy reverse proxy. Required after Caddyfile changes.
	@test -f deploy/.env || { echo "Create deploy/.env from deploy/.env.example first"; exit 2; }
	$(COMPOSE) --env-file deploy/.env restart caddy
