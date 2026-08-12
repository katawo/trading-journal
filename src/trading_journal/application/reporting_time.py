"""Normalize MT5 server-clock exports and resolve the reporting calendar."""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone, tzinfo
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


REPORTING_TIME_BASES = frozenset({"utc", "server", "local"})


def normalize_server_timestamp(value: str, server_utc_offset_minutes: int) -> str:
    """Store a schema-v5 MT5 server-clock timestamp as an absolute UTC time."""
    if not -840 <= server_utc_offset_minutes <= 840:
        raise ValueError("MT5 server UTC offset must be between -840 and 840 minutes")
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("MT5 timestamp must use ISO-8601 date and time") from error
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone(timedelta(minutes=server_utc_offset_minutes)))
    return timestamp.astimezone(timezone.utc).isoformat()


def reporting_datetime(
    timestamp_utc: str,
    server_utc_offset_minutes: int,
    time_basis: str,
    *,
    local_zone: tzinfo | None = None,
) -> datetime:
    """Return a stored UTC timestamp in the selected journal reporting clock."""
    if time_basis not in REPORTING_TIME_BASES:
        raise ValueError("Reporting time basis must be UTC, Server Timezone, or Local Timezone")
    timestamp = datetime.fromisoformat(timestamp_utc.replace("Z", "+00:00"))
    timestamp = timestamp.replace(tzinfo=timezone.utc) if timestamp.tzinfo is None else timestamp.astimezone(timezone.utc)
    if time_basis == "utc":
        return timestamp
    if time_basis == "server":
        return timestamp.astimezone(timezone(timedelta(minutes=server_utc_offset_minutes)))
    return timestamp.astimezone(local_zone or detect_local_timezone())


def reporting_date(timestamp_utc: str, server_utc_offset_minutes: int, time_basis: str, *, local_zone: tzinfo | None = None) -> date:
    return reporting_datetime(timestamp_utc, server_utc_offset_minutes, time_basis, local_zone=local_zone).date()


def detect_local_timezone() -> tzinfo:
    """Use the host's IANA zone where available, without adding a dependency."""
    configured = os.environ.get("TZ")
    if configured:
        try:
            return ZoneInfo(configured)
        except ZoneInfoNotFoundError:
            pass
    localtime = Path("/etc/localtime")
    try:
        resolved = str(localtime.resolve())
        marker = "/zoneinfo/"
        if marker in resolved:
            return ZoneInfo(resolved.split(marker, 1)[1])
    except OSError:
        pass
    return datetime.now().astimezone().tzinfo or timezone.utc
