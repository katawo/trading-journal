import pytest

from trading_journal.presentation.multiuser_auth import _cookie_key


def test_cookie_key_rejects_an_unset_secret(monkeypatch) -> None:
    monkeypatch.delenv("TRADING_JOURNAL_MULTIUSER_COOKIE_KEY", raising=False)
    with pytest.raises(RuntimeError, match="TRADING_JOURNAL_MULTIUSER_COOKIE_KEY"):
        _cookie_key()


def test_cookie_key_rejects_the_placeholder_value(monkeypatch) -> None:
    monkeypatch.setenv("TRADING_JOURNAL_MULTIUSER_COOKIE_KEY", "trade-compass-dev-only-change-me")
    with pytest.raises(RuntimeError, match="TRADING_JOURNAL_MULTIUSER_COOKIE_KEY"):
        _cookie_key()


def test_cookie_key_rejects_a_short_secret(monkeypatch) -> None:
    monkeypatch.setenv("TRADING_JOURNAL_MULTIUSER_COOKIE_KEY", "too-short")
    with pytest.raises(RuntimeError, match="at least 32 characters"):
        _cookie_key()


def test_cookie_key_accepts_a_sufficiently_long_random_secret(monkeypatch) -> None:
    secret = "a" * 32
    monkeypatch.setenv("TRADING_JOURNAL_MULTIUSER_COOKIE_KEY", secret)
    assert _cookie_key() == secret
