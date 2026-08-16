"""Issue an MT5 EA ingestion bearer token for a multi-user web-mode account.

Usage:
    .venv/bin/python scripts/add_ingestion_token.py USERNAME

Prints the plaintext token exactly once - paste it into the EA's ApiToken
input. Only its hash is stored (in ingestion_tokens.yaml, alongside
users.yaml), so it cannot be recovered later; re-run this script to issue a
new one if it's lost (the old token then still resolves, add a revoke step
here if that's ever needed).
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from trading_journal.application.multiuser import (  # noqa: E402
    generate_ingestion_token,
    hash_ingestion_token,
    ingestion_tokens_path,
    is_valid_username,
    user_database_path,
    users_config_path,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("username", help="Must already exist - see scripts/add_web_user.py.")
    arguments = parser.parse_args(argv)

    if not is_valid_username(arguments.username):
        print(f"Invalid username: {arguments.username!r}", file=sys.stderr)
        return 2

    users_path = users_config_path()
    if users_path.is_file():
        with users_path.open("r", encoding="utf-8") as handle:
            users = (yaml.safe_load(handle) or {}).get("credentials", {}).get("usernames", {})
        if arguments.username not in users:
            print(f"Warning: {arguments.username!r} has no login account yet (see scripts/add_web_user.py).", file=sys.stderr)

    tokens_path = ingestion_tokens_path()
    if tokens_path.is_file():
        with tokens_path.open("r", encoding="utf-8") as handle:
            config = yaml.safe_load(handle) or {}
    else:
        config = {}
    config.setdefault("tokens", {})

    token = generate_ingestion_token()
    config["tokens"][hash_ingestion_token(token)] = arguments.username

    tokens_path.parent.mkdir(parents=True, exist_ok=True)
    with tokens_path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(config, handle, default_flow_style=False)

    print(f"Token for {arguments.username!r} (paste into the EA's ApiToken input, shown only once):")
    print(token)
    print(f"Their data will be written to {user_database_path(arguments.username)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
