from __future__ import annotations

from pathlib import Path


def test_connection_recovery_mounts_one_stable_localized_monitor(monkeypatch) -> None:
    from trading_journal.presentation import connection_recovery, i18n

    captured: dict[str, object] = {}
    monkeypatch.setattr(i18n, "language", lambda: "en")
    monkeypatch.setattr(connection_recovery, "_CONNECTION_RECOVERY", lambda **kwargs: captured.update(kwargs))

    connection_recovery.render_connection_recovery()

    assert captured["key"] == "trade-compass-connection-recovery"
    assert captured["width"] == "content"
    assert captured["height"] == "content"
    data = captured["data"]
    assert isinstance(data, dict)
    assert data["health_url"] == "/_stcore/health"
    assert data["title"] == "Connection lost"
    assert "may not have been saved" in data["detail"]
    assert data["retry_label"] == "Retry now"
    assert "lost its live session" in data["websocket_status"]
    assert data["recovered_title"] == "Connection restored"


def test_connection_recovery_supplies_vietnamese_browser_copy(monkeypatch) -> None:
    from trading_journal.presentation import connection_recovery, i18n

    captured: dict[str, object] = {}
    monkeypatch.setattr(i18n, "language", lambda: "vi")
    monkeypatch.setattr(connection_recovery, "_CONNECTION_RECOVERY", lambda **kwargs: captured.update(kwargs))

    connection_recovery.render_connection_recovery()

    data = captured["data"]
    assert isinstance(data, dict)
    assert data["title"] == "Mất kết nối"
    assert data["retry_label"] == "Thử lại ngay"
    assert "mất phiên kết nối trực tiếp" in data["websocket_status"]
    assert data["reloading_status"] == "Đang tải lại trang…"
    assert "có thể chưa được lưu" in data["detail"]


def test_multiuser_login_screen_mounts_recovery_monitor(monkeypatch) -> None:
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1]))
    import app as journal_app

    mounted: list[bool] = []
    monkeypatch.setattr(journal_app.st, "set_page_config", lambda **_kwargs: None)
    monkeypatch.setattr(journal_app, "is_multiuser_mode", lambda: True)
    monkeypatch.setattr(journal_app, "render_login_gate", lambda: None)
    monkeypatch.setattr(journal_app, "render_connection_recovery", lambda: mounted.append(True))

    journal_app.main()

    assert mounted == [True]
