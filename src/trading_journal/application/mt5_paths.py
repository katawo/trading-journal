"""Resolve the local Common Files folder used by the read-only MT5 exporters."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import os
from pathlib import Path


MT5_EXPORT_DIRECTORY = Path("trading_journal")
LEGACY_MT5_EXPORT_FILENAME = "positions.csv"
_COMMON_FILES_SUFFIX = Path("AppData") / "Roaming" / "MetaQuotes" / "Terminal" / "Common" / "Files"


@dataclass(frozen=True)
class MT5CommonFilesLocation:
    """A discovered MT5 Common Files directory and the rule that selected it."""

    path: Path | None
    source: str


@dataclass(frozen=True)
class _Candidate:
    path: Path
    source: str


def mt5_export_filename(account_login: str | None = None) -> str:
    """Build the safe, account-specific filename used by the MT5 exporters."""

    login = (account_login or "").strip()
    return f"{login}_positions.csv" if login.isdecimal() else LEGACY_MT5_EXPORT_FILENAME


def mt5_live_export_filename(account_login: str) -> str:
    """Filename of the independent current-position snapshot for one account."""

    login = account_login.strip()
    return f"{login}_open_positions.csv"


def _wine_common_files_candidates(prefix: Path, source: str, home: Path) -> list[_Candidate]:
    users_directory = prefix / "drive_c" / "users"
    user_names = [home.name]
    if users_directory.is_dir():
        user_names.extend(item.name for item in users_directory.iterdir() if item.is_dir() and item.name != home.name)
    return [_Candidate(users_directory / user_name / _COMMON_FILES_SUFFIX, source) for user_name in user_names]


def _append_candidate(candidates: list[_Candidate], candidate: _Candidate) -> None:
    if all(existing.path != candidate.path for existing in candidates):
        candidates.append(candidate)


def _common_files_candidates(home: Path, environment: Mapping[str, str]) -> list[_Candidate]:
    candidates: list[_Candidate] = []

    wine_prefix = environment.get("WINEPREFIX")
    if wine_prefix:
        for candidate in _wine_common_files_candidates(Path(wine_prefix).expanduser(), "Linux WINEPREFIX", home):
            _append_candidate(candidates, candidate)

    for prefix_name, source in ((".wine", "Linux Wine (~/.wine)"), (".mt5", "Linux MT5 prefix (~/.mt5)")):
        for candidate in _wine_common_files_candidates(home / prefix_name, source, home):
            _append_candidate(candidates, candidate)

    for users_directory in home.glob(".*/drive_c/users"):
        prefix = users_directory.parent.parent
        source = f"Linux Wine prefix ({prefix.name})"
        for candidate in _wine_common_files_candidates(prefix, source, home):
            _append_candidate(candidates, candidate)

    appdata = environment.get("APPDATA")
    if appdata:
        _append_candidate(
            candidates,
            _Candidate(Path(appdata).expanduser() / "MetaQuotes" / "Terminal" / "Common" / "Files", "Windows APPDATA"),
        )

    userprofile = environment.get("USERPROFILE")
    if userprofile:
        _append_candidate(candidates, _Candidate(Path(userprofile).expanduser() / _COMMON_FILES_SUFFIX, "Windows user profile"))
    _append_candidate(candidates, _Candidate(home / _COMMON_FILES_SUFFIX, "Windows user profile"))
    return candidates


def find_mt5_common_files(
    account_login: str | None = None,
    *,
    home: Path | None = None,
    environment: Mapping[str, str] | None = None,
) -> MT5CommonFilesLocation:
    """Find the best local MT5 Common Files directory without changing it.

    A user-supplied ``TRADING_JOURNAL_MT5_COMMON_FILES`` directory always wins.
    Otherwise, an existing account-specific export wins over a legacy export and
    empty installation directories. Linux Wine prefixes and native Windows
    locations are both considered.
    """

    env = os.environ if environment is None else environment
    configured_root = env.get("TRADING_JOURNAL_MT5_COMMON_FILES")
    if configured_root:
        return MT5CommonFilesLocation(Path(configured_root).expanduser(), "Environment override")

    resolved_home = home or Path.home()
    candidates = _common_files_candidates(resolved_home, env)
    filename = mt5_export_filename(account_login)
    for candidate in candidates:
        if (candidate.path / MT5_EXPORT_DIRECTORY / filename).is_file():
            return MT5CommonFilesLocation(candidate.path, candidate.source)

    for candidate in candidates:
        if (candidate.path / MT5_EXPORT_DIRECTORY / LEGACY_MT5_EXPORT_FILENAME).is_file():
            return MT5CommonFilesLocation(candidate.path, candidate.source)

    for candidate in candidates:
        if candidate.path.is_dir():
            return MT5CommonFilesLocation(candidate.path, candidate.source)
    return MT5CommonFilesLocation(None, "Not detected")


def default_mt5_export_path(account_login: str | None = None, *, home: Path | None = None) -> str:
    """Return the app-side path matching the EA's default Common Files export."""

    filename = mt5_export_filename(account_login)
    location = find_mt5_common_files(account_login, home=home)
    if location.path is None:
        # This is a portable, app-relative default rather than a local filesystem
        # path, so keep its separator stable on Windows as well.
        return (MT5_EXPORT_DIRECTORY / filename).as_posix()
    return str(location.path / MT5_EXPORT_DIRECTORY / filename)


def resolve_account_export_path(configured_path: str, account_login: str) -> Path:
    """Prefer a new account-specific export beside a legacy configured path."""

    path = Path(configured_path).expanduser()
    if path.name != LEGACY_MT5_EXPORT_FILENAME:
        return path
    account_specific_path = path.with_name(mt5_export_filename(account_login))
    return account_specific_path if account_specific_path.is_file() else path


def resolve_account_live_export_path(configured_path: str, account_login: str) -> Path:
    """Keep the live snapshot beside the configured closed-position export."""

    return resolve_account_export_path(configured_path, account_login).with_name(mt5_live_export_filename(account_login))
