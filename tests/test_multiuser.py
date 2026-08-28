from pathlib import Path

import pytest

from trading_journal.application.multiuser import (
    hash_ingestion_token,
    ingestion_tokens_path,
    is_multiuser_mode,
    is_valid_username,
    multiuser_data_directory,
    resolve_username_for_token,
    user_database_path,
    users_config_path,
)


def test_multiuser_mode_is_off_unless_explicitly_enabled() -> None:
    assert is_multiuser_mode(environment={}) is False
    assert is_multiuser_mode(environment={"TRADING_JOURNAL_MULTIUSER_MODE": "1"}) is True
    assert is_multiuser_mode(environment={"TRADING_JOURNAL_MULTIUSER_MODE": "0"}) is False


def test_multiuser_data_directory_defaults_and_overrides() -> None:
    assert multiuser_data_directory(environment={}) == Path("data/multiuser")
    assert multiuser_data_directory(environment={"TRADING_JOURNAL_MULTIUSER_DATA_DIR": "/srv/journal"}) == Path("/srv/journal")


def test_users_config_path_lives_under_the_data_directory() -> None:
    assert users_config_path(environment={"TRADING_JOURNAL_MULTIUSER_DATA_DIR": "/srv/journal"}) == Path("/srv/journal/users.yaml")


def test_valid_usernames_are_filesystem_safe() -> None:
    assert is_valid_username("trader1") is True
    assert is_valid_username("trader-1_x") is True
    assert is_valid_username("a" * 32) is True
    assert is_valid_username("a" * 33) is False
    assert is_valid_username("") is False
    assert is_valid_username("Trader1") is False
    assert is_valid_username("../etc/passwd") is False
    assert is_valid_username("has space") is False
    assert is_valid_username(".hidden") is False


def test_user_database_path_is_deterministic_and_isolated_per_user() -> None:
    environment = {"TRADING_JOURNAL_MULTIUSER_DATA_DIR": "/srv/journal"}
    assert user_database_path("alice", environment=environment) == Path("/srv/journal/users/alice/trading_journal.db")
    assert user_database_path("bob", environment=environment) == Path("/srv/journal/users/bob/trading_journal.db")
    assert user_database_path("alice", environment=environment) != user_database_path("bob", environment=environment)


def test_user_database_path_rejects_an_unsafe_username() -> None:
    with pytest.raises(ValueError, match="Invalid username"):
        user_database_path("../../etc/passwd")


def test_ingestion_tokens_path_lives_under_the_data_directory() -> None:
    assert ingestion_tokens_path(environment={"TRADING_JOURNAL_MULTIUSER_DATA_DIR": "/srv/journal"}) == Path("/srv/journal/ingestion_tokens.yaml")


def test_hash_ingestion_token_is_deterministic_and_not_reversible_from_source() -> None:
    hashed = hash_ingestion_token("a-secret-token")
    assert hashed == hash_ingestion_token("a-secret-token")
    assert hashed != "a-secret-token"
    assert hash_ingestion_token("a-different-token") != hashed


def test_resolve_username_for_token_with_no_tokens_file(tmp_path) -> None:
    environment = {"TRADING_JOURNAL_MULTIUSER_DATA_DIR": str(tmp_path)}
    assert resolve_username_for_token("anything", environment=environment) is None


def test_resolve_username_for_token_matches_a_stored_hash(tmp_path) -> None:
    pytest.importorskip("yaml", reason="pyyaml is only installed via the optional 'multiuser'/'ingestion' extras")
    environment = {"TRADING_JOURNAL_MULTIUSER_DATA_DIR": str(tmp_path)}
    token = "example-token-value"
    tokens_path = ingestion_tokens_path(environment=environment)
    tokens_path.parent.mkdir(parents=True, exist_ok=True)
    tokens_path.write_text(f"tokens:\n  {hash_ingestion_token(token)}: alice\n", encoding="utf-8")
    users_config_path(environment=environment).write_text("credentials:\n  usernames:\n    alice: {}\n", encoding="utf-8")

    assert resolve_username_for_token(token, environment=environment) == "alice"
    assert resolve_username_for_token("wrong-token", environment=environment) is None


def test_resolve_username_for_token_rejects_a_user_removed_from_users_yaml(tmp_path) -> None:
    pytest.importorskip("yaml", reason="pyyaml is only installed via the optional 'multiuser'/'ingestion' extras")
    environment = {"TRADING_JOURNAL_MULTIUSER_DATA_DIR": str(tmp_path)}
    token = "example-token-value"
    tokens_path = ingestion_tokens_path(environment=environment)
    tokens_path.parent.mkdir(parents=True, exist_ok=True)
    tokens_path.write_text(f"tokens:\n  {hash_ingestion_token(token)}: alice\n", encoding="utf-8")
    users_config_path(environment=environment).write_text("credentials:\n  usernames: {}\n", encoding="utf-8")

    assert resolve_username_for_token(token, environment=environment) is None
