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
def test_websocket_disconnect_blocks_stale_ui_until_user_reloads() -> None:
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
            health_status = {"value": 503}
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
                lambda route: route.fulfill(
                    status=health_status["value"],
                    body="ok" if health_status["value"] == 200 else "unavailable",
                ),
            )
            page.goto("http://trade-compass.test/")
            page.add_script_tag(content=browser_script)
            component_data = {
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
                "recovered_detail": "Reload before continuing so the controls use a fresh session.",
                "reload_required_status": "Review unsaved entries, then reload the page to continue.",
                "waiting_for_session_status": "Server reachable. Waiting for the live session to reconnect…",
                "disconnect_grace_ms": 200,
            }
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
                component_data,
            )

            dialog = page.get_by_role("alertdialog")
            playwright.expect(dialog).to_be_hidden()
            page.wait_for_timeout(300)
            assert health_requests == []

            # A sleeping tab can reconnect before the grace period elapses. The
            # disconnect must still invalidate the old controls and require a
            # user-confirmed reload instead of silently keeping the stale DOM.
            page.locator('[data-testid="stApp"]').evaluate(
                "element => element.setAttribute('data-test-connection-state', 'PINGING_SERVER')"
            )
            page.wait_for_timeout(25)
            playwright.expect(dialog).to_be_hidden()
            page.locator('[data-testid="stApp"]').evaluate(
                "element => element.setAttribute('data-test-connection-state', 'CONNECTED')"
            )

            playwright.expect(dialog).to_be_visible(timeout=1_000)
            playwright.expect(page.get_by_role("heading", name="Connection restored")).to_be_visible()
            playwright.expect(page.get_by_text("fresh session", exact=False)).to_be_visible()
            playwright.expect(page.get_by_role("button", name="Retry now")).to_be_hidden()
            playwright.expect(page.get_by_role("button", name="Reload page")).to_be_focused()
            assert page.locator('[data-testid="stAppViewContainer"]').evaluate("element => element.inert") is True
            page.wait_for_timeout(300)
            assert page.evaluate("window.__connectionRecoveryReloading") is False
            assert health_requests == []

            page.evaluate("document.querySelector('#tj-connection-recovery-anchor')._cleanup()")
            playwright.expect(dialog).to_have_count(0)
            assert page.locator('[data-testid="stAppViewContainer"]').evaluate("element => element.inert") is False

            # Remount and exercise a longer outage. HTTP health alone must not
            # claim recovery while Streamlit's live WebSocket is still down.
            page.evaluate(
                """
                (data) => window.mountConnectionRecovery({
                  data,
                  parentElement: document.querySelector('#component-root'),
                })
                """,
                component_data,
            )
            dialog = page.get_by_role("alertdialog")
            page.locator('[data-testid="stApp"]').evaluate(
                "element => element.setAttribute('data-test-connection-state', 'PINGING_SERVER')"
            )

            playwright.expect(dialog).to_be_visible(timeout=1_000)
            playwright.expect(page.get_by_role("heading", name="Connection lost")).to_be_visible()
            playwright.expect(page.get_by_text("Trade Compass lost its live session", exact=False)).to_be_visible()
            playwright.expect(page.get_by_role("button", name="Retry now")).to_be_focused()
            assert page.locator('[data-testid="stAppViewContainer"]').evaluate("element => element.inert") is True

            health_status["value"] = 200
            page.get_by_role("button", name="Retry now").click()
            page.wait_for_function(
                "() => window.performance.getEntriesByName('http://trade-compass.test/_stcore/health').length > 0"
            )
            assert health_requests == ["http://trade-compass.test/_stcore/health"]
            playwright.expect(page.get_by_role("heading", name="Connection lost")).to_be_visible()
            playwright.expect(page.get_by_text("Waiting for the live session", exact=False)).to_be_visible()
            playwright.expect(page.get_by_role("button", name="Retry now")).to_be_visible()

            page.locator('[data-testid="stApp"]').evaluate(
                "element => element.setAttribute('data-test-connection-state', 'CONNECTED')"
            )

            playwright.expect(page.get_by_role("heading", name="Connection restored")).to_be_visible(timeout=1_000)
            playwright.expect(page.get_by_role("button", name="Reload page")).to_be_focused()
            page.wait_for_timeout(300)
            assert page.evaluate("window.__connectionRecoveryReloading") is False

            with page.expect_navigation():
                page.get_by_role("button", name="Reload page").click()
            playwright.expect(dialog).to_have_count(0)
            assert page.locator('[data-testid="stAppViewContainer"]').evaluate("element => element.inert") is False
        finally:
            browser.close()
