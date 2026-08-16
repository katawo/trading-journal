"""Framework-agnostic helpers for optional multi-user mode: one SQLite file per user.

Shared by both the Streamlit app (presentation/multiuser_auth.py, the login
UI) and the FastAPI ingestion endpoint (the /ingest HTTP push target for the
MT5 EA) - neither depends on the other for this. See
/home/thang/.claude/plans/which-free-server-platform-whimsical-sky.md for the
full design.
"""

from __future__ import annotations

import hashlib
import os
import re
import secrets
from pathlib import Path

MULTIUSER_MODE_ENVIRONMENT_KEY = "TRADING_JOURNAL_MULTIUSER_MODE"
MULTIUSER_DATA_DIRECTORY_ENVIRONMENT_KEY = "TRADING_JOURNAL_MULTIUSER_DATA_DIR"
_USERNAME_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9_-]{0,31})$")


def is_multiuser_mode(environment: dict[str, str] | None = None) -> bool:
    env = os.environ if environment is None else environment
    return env.get(MULTIUSER_MODE_ENVIRONMENT_KEY) == "1"


def multiuser_data_directory(environment: dict[str, str] | None = None) -> Path:
    """Root directory holding the credentials file, ingestion tokens, and every user's own database."""

    env = os.environ if environment is None else environment
    configured = env.get(MULTIUSER_DATA_DIRECTORY_ENVIRONMENT_KEY, "").strip()
    return Path(configured).expanduser() if configured else Path("data/multiuser")


def users_config_path(environment: dict[str, str] | None = None) -> Path:
    return multiuser_data_directory(environment) / "users.yaml"


def ingestion_tokens_path(environment: dict[str, str] | None = None) -> Path:
    return multiuser_data_directory(environment) / "ingestion_tokens.yaml"


def is_valid_username(username: str) -> bool:
    """Filesystem-safe: lowercase letters, digits, hyphen, underscore; 1-32 chars."""

    return bool(_USERNAME_PATTERN.match(username))


def user_database_path(username: str, environment: dict[str, str] | None = None) -> Path:
    if not is_valid_username(username):
        raise ValueError(f"Invalid username: {username!r}")
    return multiuser_data_directory(environment) / "users" / username / "trading_journal.db"


def generate_ingestion_token() -> str:
    return secrets.token_urlsafe(32)


def hash_ingestion_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def resolve_username_for_token(token: str, environment: dict[str, str] | None = None) -> str | None:
    """Which user a bearer token from the MT5 EA belongs to, or None if unknown."""

    path = ingestion_tokens_path(environment)
    if not path.is_file():
        return None
    import yaml

    with path.open("r", encoding="utf-8") as handle:
        config = yaml.safe_load(handle) or {}
    return config.get("tokens", {}).get(hash_ingestion_token(token))
