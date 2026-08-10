"""Small presentation helpers for local timestamps."""

from __future__ import annotations

from datetime import datetime, timezone


def format_relative_time(value: datetime, *, now: datetime | None = None) -> str:
    """Format a timestamp as a compact, user-facing relative duration."""

    current_time = now or datetime.now(timezone.utc)
    timestamp = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    elapsed_seconds = max(0, int((current_time - timestamp).total_seconds()))
    if elapsed_seconds == 0:
        return "just now"
    if elapsed_seconds < 60:
        return f"{elapsed_seconds}s ago"
    elapsed_minutes = elapsed_seconds // 60
    if elapsed_minutes < 60:
        return f"{elapsed_minutes} min ago"
    elapsed_hours = elapsed_minutes // 60
    if elapsed_hours < 24:
        return f"{elapsed_hours} hr ago"
    elapsed_days = elapsed_hours // 24
    return f"{elapsed_days} day ago" if elapsed_days == 1 else f"{elapsed_days} days ago"
