# syntax=docker/dockerfile:1

# Container image for the optional multi-user web deployment.
#
# One image runs BOTH web-facing processes — the Streamlit app and the FastAPI
# ingestion endpoint — selected by the compose `command`.
#
# python:3.12-slim satisfies requires-python>=3.12 and is multi-arch, so this
# builds natively on an Oracle Cloud Ampere (arm64) VM as well as on x86_64.
FROM python:3.12-slim

# Streamlit + pandas want a couple of runtime libs; keep the layer minimal.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Leave pip caching enabled by default. The no-cache deployment target sets
# this build argument to 1 so pip also bypasses the BuildKit cache mount.
ARG PIP_NO_CACHE_DIR

# Install third-party dependencies from the project metadata before copying the
# application. The temporary package lets setuptools resolve the extras while
# keeping this download layer independent from ordinary source changes. Only
# install the web-relevant extras — never [dev] (test-only, and see the httpx
# pin note in deploy/README-docker.md).
COPY pyproject.toml ./
RUN --mount=type=cache,target=/root/.cache/pip,sharing=locked \
    mkdir -p src/trading_journal \
    && touch src/trading_journal/__init__.py \
    && pip install ".[multiuser,ingestion]" \
    && rm -rf src

COPY src ./src
# Replace the temporary package with the real application without resolving or
# downloading dependencies again.
RUN --mount=type=cache,target=/root/.cache/pip,sharing=locked \
    pip install --no-deps --force-reinstall .

COPY app.py ./
COPY app_pages ./app_pages
COPY docs ./docs
COPY .streamlit .streamlit
# Admin CLIs (add_web_user.py / add_ingestion_token.py) are run as one-off
# `docker compose run` commands, so they must be in the image too.
COPY scripts ./scripts

# Both services write per-user SQLite files under this one directory, mounted
# from a shared volume at runtime. Create it now and hand it to a non-root user
# so the named volume inherits that ownership on first creation.
ENV TRADING_JOURNAL_MULTIUSER_DATA_DIR=/data
RUN useradd --uid 10001 --create-home appuser \
    && mkdir -p /data \
    && chown appuser:appuser /data
VOLUME ["/data"]
USER appuser

# The compose file supplies the actual command (streamlit or uvicorn); this
# default just documents the primary one.
CMD ["python", "-m", "streamlit", "run", "app.py", \
     "--server.address", "0.0.0.0", "--server.port", "8501", "--server.headless", "true"]
