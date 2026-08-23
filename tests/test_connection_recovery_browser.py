from __future__ import annotations

from pathlib import Path
import shutil

import pytest


def _chromium_executable(browser_type) -> str | None:  # type: ignore[no-untyped-def]
    bundled = Path(browser_type.executable_path)
    if bundled.is_file():
        return str(bundled)
    return next(
        (
            executable
            for command in ("google-chrome", "chromium", "chromium-browser")
            if (executable := shutil.which(command)) is not None
        ),
        None,
    )


@pytest.mark.browser
def test_websocket_only_disconnect_blocks_stale_ui_and_recovers() -> None:
    playwright = pytest.importorskip("playwright.sync_api", reason="Playwright is required for browser component tests")

    from trading_journal.presentation.connection_recovery import (
        _CONNECTION_RECOVERY_CSS,
        _CONNECTION_RECOVERY_HTML,
        _CONNECTION_RECOVERY_JS,
    )

    browser_script = _CONNECTION_RECOVERY_JS.replace(
        "export default function (component)",
        "window.mountConnectionRecovery = function (component)",
        1,
    )
    assert browser_script != _CONNECTION_RECOVERY_JS

    with playwright.sync_playwright() as runtime:
        executable = _chromium_executable(runtime.chromium)
        if executable is None:
            pytest.skip("No Chromium-compatible browser is installed")
        try:
            browser = runtime.chromium.launch(headless=True, executable_path=executable)
        except playwright.Error as error:
            if "Operation not permitted" in str(error):
                pytest.skip("Chromium cannot create its sandbox IPC sockets in this environment")
            raise
        page = browser.new_page()
        try:
            health_requests: list[str] = []
            page.on(
                "request",
                lambda request: health_requests.append(request.url)
                if request.url.endswith("/_stcore/health")
                else None,
            )
            document = f"""
                <style>{_CONNECTION_RECOVERY_CSS}</style>
                <main data-testid="stApp" data-test-connection-state="CONNECTED">
                  <section data-testid="stAppViewContainer">
                    <div id="component-root">{_CONNECTION_RECOVERY_HTML}</div>
                    <button id="stale-action">Stale action</button>
                  </section>
                </main>
                """
            page.route("http://trade-compass.test/", lambda route: route.fulfill(status=200, body=document))
            page.route(
                "http://trade-compass.test/_stcore/health",
                lambda route: route.fulfill(status=503, body="unavailable"),
            )
            page.goto("http://trade-compass.test/")
            page.add_script_tag(content=browser_script)
            page.evaluate(
                """
                (data) => {
                  window.__connectionRecoveryReloading = false
                  window.addEventListener(
                    'trade-compass:connection-recovery-reloading',
                    () => { window.__connectionRecoveryReloading = true },
                    { once: true },
                  )
                  window.mountConnectionRecovery({
                    data,
                    parentElement: document.querySelector('#component-root'),
                  })
                }
                """,
                {
                    "health_url": "/_stcore/health",
                    "kicker": "Connection status",
                    "title": "Connection lost",
                    "detail": "Trade Compass cannot reach the server. Your latest action may not have been saved.",
                    "retry_label": "Retry now",
                    "reload_label": "Reload page",
                    "retrying_status": "Retrying automatically… Attempt {attempt}.",
                    "offline_status": "This device appears to be offline.",
                    "websocket_status": "Trade Compass lost its live session. Reconnecting automatically…",
                    "checking_status": "Checking the connection…",
                    "recovered_title": "Connection restored",
                    "recovered_detail": "Trade Compass can reach the server again.",
                    "reloading_status": "Reloading the page…",
                    "reload_limited_status": "Reload manually.",
                    "disconnect_grace_ms": 10,
                    "reload_cooldown_ms": 0,
                    "reload_delay_ms": 60_000,
                },
            )

            dialog = page.get_by_role("alertdialog")
            playwright.expect(dialog).to_be_hidden()
            page.wait_for_timeout(1_200)
            assert health_requests == []

            page.locator('[data-testid="stApp"]').evaluate(
                "element => element.setAttribute('data-test-connection-state', 'PINGING_SERVER')"
            )

            playwright.expect(dialog).to_be_visible(timeout=1_000)
            playwright.expect(page.get_by_role("heading", name="Connection lost")).to_be_visible()
            playwright.expect(page.get_by_text("Trade Compass lost its live session", exact=False)).to_be_visible()
            playwright.expect(page.get_by_role("button", name="Retry now")).to_be_focused()
            assert page.locator('[data-testid="stAppViewContainer"]').evaluate("element => element.inert") is True

            page.get_by_role("button", name="Retry now").click()
            page.wait_for_function(
                "() => window.performance.getEntriesByName('http://trade-compass.test/_stcore/health').length > 0"
            )
            assert health_requests == ["http://trade-compass.test/_stcore/health"]

            page.locator('[data-testid="stApp"]').evaluate(
                "element => element.setAttribute('data-test-connection-state', 'CONNECTED')"
            )

            playwright.expect(page.get_by_role("heading", name="Connection restored")).to_be_visible(timeout=1_000)
            page.wait_for_function("window.__connectionRecoveryReloading === true")
            assert page.evaluate("sessionStorage.getItem('trade-compass:connection-recovery:last-reload') !== null")

            page.evaluate("document.querySelector('#tj-connection-recovery-anchor')._cleanup()")
            playwright.expect(dialog).to_have_count(0)
            assert page.locator('[data-testid="stAppViewContainer"]').evaluate("element => element.inert") is False
        finally:
            browser.close()
