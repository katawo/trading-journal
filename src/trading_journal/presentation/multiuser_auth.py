"""Optional multi-user web mode: one SQLite file per logged-in user.

This is a third deployment mode alongside desktop mode and today's single-user
web mode (see trading_journal.desktop.is_desktop_mode) - it is off unless
TRADING_JOURNAL_MULTIUSER_MODE=1 is set, and touches nothing when it is off.
Login is handled by streamlit-authenticator (an optional dependency, see the
"multiuser" extra in pyproject.toml) against a small YAML credentials file;
accounts are created with scripts/add_web_user.py, not self-serve signup.
"""

from __future__ import annotations

import os

import streamlit as st

from trading_journal.application.multiuser import is_multiuser_mode, user_database_path, users_config_path

__all__ = [
    "current_username",
    "is_multiuser_mode",
    "render_login_gate",
    "render_logout_control",
    "user_database_path",
    "users_config_path",
]


_PLACEHOLDER_COOKIE_KEY = "trade-compass-dev-only-change-me"


def _cookie_key() -> str:
    """The HS256 secret signing the session cookie's JWT - fail closed, never guess one.

    An unset/placeholder/weak key here lets anyone forge a cookie for any
    username, and repository() then opens that user's SQLite file for them:
    unauthenticated read/write to any user's journal. Generate a real one
    with: openssl rand -hex 32
    """

    key = os.environ.get("TRADING_JOURNAL_MULTIUSER_COOKIE_KEY", "").strip()
    if not key or key == _PLACEHOLDER_COOKIE_KEY or len(key) < 32:
        raise RuntimeError(
            "TRADING_JOURNAL_MULTIUSER_COOKIE_KEY must be set to a random secret of at "
            "least 32 characters in multiuser mode - it signs the session cookie. "
            "Generate one with: openssl rand -hex 32"
        )
    return key


def _authenticator():  # -> streamlit_authenticator.Authenticate, kept lazy: optional dependency
    """One Authenticate instance per session, reused across reruns and within a run.

    Its cookie controller wraps extra_streamlit_components.CookieManager(),
    which renders an internal component with a fixed key - constructing a
    second Authenticate() in the same script run (e.g. once for the login
    gate, once for a logout button) collides on that key and raises
    StreamlitDuplicateElementKey. Caching in session_state avoids that and
    also avoids re-parsing the credentials file on every rerun.
    """

    if "_multiuser_authenticator" not in st.session_state:
        import streamlit_authenticator as stauth
        import yaml

        # Passed as a path, Authenticate/CookieModel requires the cookie signing
        # key to live in the same YAML file as the password hashes. Loading the
        # credentials dict ourselves keeps the cookie key as a separate env var
        # instead, so it isn't sitting in a file that also holds password hashes.
        with users_config_path().open("r", encoding="utf-8") as handle:
            config = yaml.safe_load(handle) or {}
        credentials = config.get("credentials", {"usernames": {}})
        st.session_state["_multiuser_authenticator"] = stauth.Authenticate(
            credentials,
            cookie_name="trade_compass_auth",
            cookie_key=_cookie_key(),
            cookie_expiry_days=30,
        )
    return st.session_state["_multiuser_authenticator"]


def _hide_app_chrome() -> None:
    """Hide the app sidebar/nav for the current run — used only on the login screen.

    main() gates before st.navigation(), but Streamlit's multipage nav can linger
    in the sidebar on a logged-out rerun (e.g. right after logout). Injected per
    run, so authenticated runs — which never call this — keep the normal sidebar.
    """

    st.markdown(
        "<style>[data-testid='stSidebar'], [data-testid='stSidebarNav'], "
        "[data-testid='stSidebarCollapsedControl'] { display: none !important; }</style>",
        unsafe_allow_html=True,
    )


def render_login_gate() -> str | None:
    """Render the login form when needed; return the authenticated username, else None.

    Callers must not resolve a repository/database path until this returns a
    username - the caller's job is just to `return` early from its page while
    this is None, since the login widget has already rendered its own UI.

    Calls st.set_page_config() unconditionally: Streamlit requires it to be
    the very first Streamlit command in a run, before login()'s own widgets
    (including a cookie-restored login, which still calls into st internally)
    - so the caller must NOT also call set_page_config() when this returns a
    username in multiuser mode; this login screen is intentionally untranslated
    (no per-user language is known yet at this point).
    """

    st.set_page_config(page_title="Trade Compass", page_icon="📈", layout="wide")

    config_path = users_config_path()
    if not config_path.is_file():
        _hide_app_chrome()
        st.error(f"No user accounts are configured yet. Add one with scripts/add_web_user.py (writes to {config_path}).")
        return None

    try:
        authenticator = _authenticator()
    except RuntimeError as error:
        _hide_app_chrome()
        st.error(str(error))
        return None

    # Already signed in this session: keep the authenticator's state fresh (this
    # draws nothing) and fall straight through to the app with no login chrome.
    if st.session_state.get("authentication_status"):
        authenticator.login("main")
        return st.session_state.get("username")

    # Otherwise render the login form inside a centered column. On a fresh
    # cookie-restore, login() authenticates and draws nothing, so the empty
    # column is invisible and we still fall through to the app.
    _, center, _ = st.columns([1, 1.4, 1])
    with center:
        brand = st.empty()
        authenticator.login("main")
        message = st.empty()

    authentication_status = st.session_state.get("authentication_status")
    if authentication_status:
        # A fresh, successful submit already drew the login form above (the widgets
        # are placed before login() can know the result) - returning here would let
        # main() render the dashboard right below that still-visible form in this
        # same run. Rerun instead so the next run's already-signed-in fast path
        # (above) skips drawing the form at all.
        st.rerun()

    # Not signed in: this run only shows the login screen. Hide the app sidebar
    # and nav so no menu leaks onto the login page — before first login or after
    # logout — and dress the form as a simple branded card.
    _hide_app_chrome()
    brand.markdown("### 📈 Trade Compass\nLocal-first trade review, guided by discipline.")
    if authentication_status is False:
        message.error("Incorrect username or password.")
    return None


def render_logout_control() -> None:
    """A small sidebar logout button; safe to call only after render_login_gate() succeeds."""

    with st.sidebar:
        _authenticator().logout("Log out", "sidebar")


def current_username() -> str | None:
    """The logged-in user for this session, once render_login_gate() has succeeded.

    Callers elsewhere in the app (e.g. repository()) can use this without
    needing to know streamlit-authenticator's own session_state keys.
    """

    return st.session_state.get("username") if st.session_state.get("authentication_status") else None
