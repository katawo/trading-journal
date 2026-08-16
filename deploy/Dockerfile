# Container image for the optional multi-user web deployment.
#
# One image runs BOTH web-facing processes — the Streamlit app and the FastAPI
# ingestion endpoint — selected by the compose `command`. The desktop app is NOT
# containerised; it stays a PyInstaller bundle (see docs/desktop_app.md).
#
# python:3.12-slim satisfies requires-python>=3.12 and is multi-arch, so this
# builds natively on an Oracle Cloud Ampere (arm64) VM as well as on x86_64.
FROM python:3.12-slim

# Streamlit + pandas want a couple of runtime libs; keep the layer minimal.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install dependencies first (better layer caching), then the app itself. Only
# the web-relevant extras — never [desktop] (PyInstaller/pywebview) or [dev]
# (test-only, and see the httpx pin note in deploy/README-docker.md).
COPY pyproject.toml ./
COPY src ./src
COPY app.py ./
COPY app_pages ./app_pages
COPY docs ./docs
# Admin CLIs (add_web_user.py / add_ingestion_token.py) are run as one-off
# `docker compose run` commands, so they must be in the image too.
COPY scripts ./scripts
RUN pip install --no-cache-dir ".[multiuser,ingestion]"

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
