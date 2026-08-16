"""Create or update a multi-user web-mode account (admin-only, no self-serve signup).

Usage:
    .venv/bin/python scripts/add_web_user.py USERNAME --name "Display Name" --email you@example.com

Prompts for a password (not echoed), hashes it, and writes/updates the entry
in the streamlit-authenticator credentials file used by multi-user web mode
(see src/trading_journal/presentation/multiuser_auth.py). That user's own
SQLite database is created automatically on their first successful login.
"""

from __future__ import annotations

import argparse
import getpass
from pathlib import Path
import sys

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from trading_journal.application.multiuser import is_valid_username, user_database_path, users_config_path  # noqa: E402


def _load_config(path: Path) -> dict:
    if not path.is_file():
        return {"credentials": {"usernames": {}}}
    with path.open("r", encoding="utf-8") as handle:
        config = yaml.safe_load(handle) or {}
    config.setdefault("credentials", {}).setdefault("usernames", {})
    return config


def _save_config(path: Path, config: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(config, handle, default_flow_style=False)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("username", help="Lowercase letters, digits, hyphen, underscore; 1-32 chars.")
    parser.add_argument("--name", required=True, help="Display name shown in the app.")
    parser.add_argument("--email", required=True, help="Not used to send anything today; kept for the credentials schema.")
    arguments = parser.parse_args(argv)

    if not is_valid_username(arguments.username):
        print(f"Invalid username: {arguments.username!r} (use lowercase letters, digits, '-', '_', 1-32 chars)", file=sys.stderr)
        return 2

    password = getpass.getpass("New password: ")
    confirm = getpass.getpass("Confirm password: ")
    if password != confirm:
        print("Passwords did not match.", file=sys.stderr)
        return 2
    if len(password) < 8:
        print("Password must be at least 8 characters.", file=sys.stderr)
        return 2

    import streamlit_authenticator as stauth

    config_path = users_config_path()
    config = _load_config(config_path)
    config["credentials"]["usernames"][arguments.username] = {
        "name": arguments.name,
        "email": arguments.email,
        "password": stauth.Hasher.hash(password),
    }
    _save_config(config_path, config)

    print(f"Saved credentials for {arguments.username!r} to {config_path}")
    print(f"Their database will be created at {user_database_path(arguments.username)} on first login.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
