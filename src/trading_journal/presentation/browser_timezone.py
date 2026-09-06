"""Resolve the browser's IANA timezone for hosted local-calendar views."""

from __future__ import annotations

from datetime import tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import streamlit as st


_BROWSER_TIMEZONE = st.components.v2.component(
    "trade_compass_browser_timezone",
    js="""
    export default function (component) {
      const { setStateValue } = component
      setStateValue('timezone_name', Intl.DateTimeFormat().resolvedOptions().timeZone)
    }
    """,
)

_BROWSER_TIMEZONE_NAME_KEY = "_trade_compass_browser_timezone_name"


def _timezone_from_name(name: object) -> tzinfo | None:
    if not isinstance(name, str) or not name:
        return None
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return None


def browser_timezone() -> tzinfo | None:
    """Render the browser probe once and remember its IANA zone for this session."""

    result = _BROWSER_TIMEZONE(
        key="trade-compass-browser-timezone",
        on_timezone_name_change=lambda: None,
        width=1,
        height=1,
    )
    name = getattr(result, "timezone_name", None)
    zone = _timezone_from_name(name)
    if zone is not None:
        st.session_state[_BROWSER_TIMEZONE_NAME_KEY] = str(zone)
        return zone
    return current_browser_timezone()


def current_browser_timezone() -> tzinfo | None:
    """Return the last browser zone without rendering the keyed component again."""

    return _timezone_from_name(st.session_state.get(_BROWSER_TIMEZONE_NAME_KEY))
