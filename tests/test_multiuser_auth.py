import inspect

import pytest

from trading_journal.presentation import multiuser_auth
from trading_journal.presentation.branding import TRADE_COMPASS_ICON
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


def test_login_page_uses_the_trade_compass_favicon(monkeypatch, tmp_path) -> None:
    configured: dict[str, object] = {}
    monkeypatch.setattr(multiuser_auth, "users_config_path", lambda: tmp_path / "missing-users.yaml")
    monkeypatch.setattr(multiuser_auth.st, "set_page_config", lambda **kwargs: configured.update(kwargs))
    monkeypatch.setattr(multiuser_auth, "_hide_app_chrome", lambda: None)
    monkeypatch.setattr(multiuser_auth.st, "error", lambda *_args, **_kwargs: None)

    assert multiuser_auth.render_login_gate() is None
    assert configured["page_icon"] == TRADE_COMPASS_ICON


def test_login_header_uses_the_current_trade_compass_icon() -> None:
    source = inspect.getsource(multiuser_auth.render_login_gate)

    assert "st.image(TRADE_COMPASS_ICON" in source
    assert "📈" not in source
