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


def browser_timezone() -> tzinfo | None:
    """Return the current browser zone after the component's first state update."""

    result = _BROWSER_TIMEZONE(
        key="trade-compass-browser-timezone",
        on_timezone_name_change=lambda: None,
        width=1,
        height=1,
    )
    name = getattr(result, "timezone_name", None)
    if not isinstance(name, str) or not name:
        return None
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return None
