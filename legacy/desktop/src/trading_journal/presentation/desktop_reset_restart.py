"""Browser bridge that survives the desktop server restart after a reset."""

from __future__ import annotations

import streamlit as st


_RESET_RESTART_BRIDGE = st.components.v2.component(
    "desktop_reset_restart_bridge",
    html="""<p id="tj-desktop-reset-status" role="status"></p>""",
    css="""#tj-desktop-reset-status { color: var(--st-text-color); margin: 0.35rem 0; }""",
    js="""
    export default function (component) {
      const { data, parentElement, setTriggerValue } = component
      const status = parentElement.querySelector('#tj-desktop-reset-status')
      const resetId = data?.reset_id
      if (!status || !resetId) return
      const bridgeKey = `trading-journal:desktop-reset:${resetId}`
      const existing = window[bridgeKey]
      if (existing) {
        status.textContent = existing.message
        return
      }

      const state = { unavailable: false, message: 'Restarting Trade Compass…' }
      window[bridgeKey] = state
      status.textContent = state.message
      const startedAt = Date.now()
      const poll = async () => {
        try {
          const response = await fetch('/_stcore/health', {
            cache: 'no-store',
            signal: AbortSignal.timeout(1000),
          })
          // A request can span a very fast server restart without failing. The
          // reset signal is dispatched before this bridge starts polling, so a
          // healthy server after this grace period is the restarted instance.
          if (response.ok && (state.unavailable || Date.now() - startedAt > 5000)) {
            window.location.reload()
            return
          }
        } catch {
          state.unavailable = true
        }
        if (Date.now() - startedAt > 45000) {
          state.message = 'Restart is taking longer than expected. Refresh this page to try again.'
          status.textContent = state.message
          return
        }
        window.setTimeout(poll, 500)
      }
      window.setTimeout(poll, 250)
      window.setTimeout(() => setTriggerValue('ready', resetId), 0)
    }
    """,
)


def render_desktop_reset_restart_bridge(reset_id: str) -> str | None:
    """Mount the trusted same-origin restart bridge and return its ready trigger."""

    result = _RESET_RESTART_BRIDGE(
        key=f"desktop-reset-restart-{reset_id}",
        data={"reset_id": reset_id},
        on_ready_change=lambda: None,
        width="stretch",
        height="content",
    )
    return result.ready
