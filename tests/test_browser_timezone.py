from types import SimpleNamespace
from zoneinfo import ZoneInfo

from trading_journal.presentation import browser_timezone as browser_timezone_module


def test_browser_timezone_captures_valid_zone_for_non_rendering_consumers(monkeypatch) -> None:
    session_state: dict[str, object] = {}
    monkeypatch.setattr(browser_timezone_module.st, "session_state", session_state)
    monkeypatch.setattr(
        browser_timezone_module,
        "_BROWSER_TIMEZONE",
        lambda **_kwargs: SimpleNamespace(timezone_name="Asia/Ho_Chi_Minh"),
    )

    captured = browser_timezone_module.browser_timezone()

    assert captured == ZoneInfo("Asia/Ho_Chi_Minh")
    assert browser_timezone_module.current_browser_timezone() == captured
    assert session_state[browser_timezone_module._BROWSER_TIMEZONE_NAME_KEY] == "Asia/Ho_Chi_Minh"


def test_browser_timezone_keeps_last_valid_zone_when_probe_is_missing_or_invalid(monkeypatch) -> None:
    session_state = {browser_timezone_module._BROWSER_TIMEZONE_NAME_KEY: "Asia/Ho_Chi_Minh"}
    monkeypatch.setattr(browser_timezone_module.st, "session_state", session_state)

    for timezone_name in (None, "Not/A_Zone"):
        monkeypatch.setattr(
            browser_timezone_module,
            "_BROWSER_TIMEZONE",
            lambda timezone_name=timezone_name, **_kwargs: SimpleNamespace(timezone_name=timezone_name),
        )
        assert browser_timezone_module.browser_timezone() == ZoneInfo("Asia/Ho_Chi_Minh")

    session_state[browser_timezone_module._BROWSER_TIMEZONE_NAME_KEY] = "Not/A_Zone"
    assert browser_timezone_module.current_browser_timezone() is None
