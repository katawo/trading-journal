import pytest

from trading_journal.presentation import multiuser_auth
from trading_journal.presentation.branding import TRADE_COMPASS_ICON
from trading_journal.presentation.multiuser_auth import _cookie_key


class _Sidebar:
    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None


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


def test_logout_clears_user_specific_session_state_and_blocks_cookie_restore(monkeypatch) -> None:
    session_state = {
        "username": "alice",
        "authentication_status": True,
        "post-trade-review-trade-id": 42,
        "logical-trade-regroup-confirmation": {"account_id": 1},
        "auto_sync_results": [object()],
        "display_language": "vi",
        "_multiuser_authenticator": object(),
    }
    monkeypatch.setattr(multiuser_auth.st, "session_state", session_state)

    multiuser_auth._reset_session_for_logout({"username": "alice"})

    assert session_state == {"logout": True}


def test_logout_control_registers_the_full_session_reset_callback(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class Authenticator:
        def logout(self, button_name, location, *, callback) -> None:
            captured.update(
                button_name=button_name,
                location=location,
                callback=callback,
            )

    monkeypatch.setattr(multiuser_auth.st, "sidebar", _Sidebar())
    monkeypatch.setattr(multiuser_auth, "_authenticator", Authenticator)

    multiuser_auth.render_logout_control()

    assert captured == {
        "button_name": "Log out",
        "location": "sidebar",
        "callback": multiuser_auth._reset_session_for_logout,
    }


def test_password_login_marks_the_cookie_write_as_pending(monkeypatch) -> None:
    session_state: dict[str, object] = {}
    monkeypatch.setattr(multiuser_auth.st, "session_state", session_state)

    multiuser_auth._mark_password_login_cookie_pending({"username": "kata"})

    assert session_state[multiuser_auth._PASSWORD_LOGIN_COOKIE_PENDING_KEY] is True


def test_password_login_waits_for_the_cookie_component_before_rerunning(monkeypatch) -> None:
    class Stopped(Exception):
        pass

    reran = False

    def rerun() -> None:
        nonlocal reran
        reran = True

    def stop() -> None:
        raise Stopped

    monkeypatch.setattr(
        multiuser_auth.st,
        "session_state",
        {multiuser_auth._PASSWORD_LOGIN_COOKIE_PENDING_KEY: True},
    )
    monkeypatch.setattr(multiuser_auth.st, "stop", stop)
    monkeypatch.setattr(multiuser_auth.st, "rerun", rerun)

    with pytest.raises(Stopped):
        multiuser_auth._finish_authenticated_login_run()

    assert reran is False


def test_cookie_restored_login_reruns_immediately(monkeypatch) -> None:
    reran = False

    def rerun() -> None:
        nonlocal reran
        reran = True

    monkeypatch.setattr(multiuser_auth.st, "session_state", {})
    monkeypatch.setattr(multiuser_auth.st, "rerun", rerun)

    multiuser_auth._finish_authenticated_login_run()

    assert reran is True
