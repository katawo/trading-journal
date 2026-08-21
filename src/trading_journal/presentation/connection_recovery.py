"""Browser-side recovery UI for a lost Streamlit server connection."""

from __future__ import annotations

import streamlit as st

from trading_journal.presentation.i18n import tr


_CONNECTION_RECOVERY_HTML = """
    <span id="tj-connection-recovery-anchor" aria-hidden="true"></span>
    """

_CONNECTION_RECOVERY_CSS = """
    #tj-connection-recovery-anchor { display: none; }
    #tj-connection-recovery-overlay {
      align-items: center; background: color-mix(in srgb, var(--st-background-color, #0b0f0e) 78%, transparent);
      box-sizing: border-box; display: flex; inset: 0; justify-content: center; padding: 1rem;
      position: fixed; z-index: 2147483000;
    }
    #tj-connection-recovery-overlay[hidden] { display: none; }
    #tj-connection-recovery-overlay .tj-connection-card {
      background: var(--st-secondary-background-color, #ffffff); border: 1px solid var(--st-red-color, #c73545);
      border-radius: var(--st-base-radius, 0.65rem); box-shadow: 0 18px 55px color-mix(in srgb, #000000 45%, transparent);
      box-sizing: border-box; color: var(--st-text-color, #19201b); font-family: var(--st-font, sans-serif);
      max-width: 34rem; padding: 1.2rem; width: 100%;
    }
    #tj-connection-recovery-overlay .tj-connection-kicker {
      color: var(--st-red-text-color, #8b1725); font-size: 0.78rem; font-weight: 700;
      letter-spacing: 0.1em; margin: 0 0 0.4rem; text-transform: uppercase;
    }
    #tj-connection-recovery-overlay .tj-connection-title { font-size: 1.45rem; line-height: 1.2; margin: 0; }
    #tj-connection-recovery-overlay .tj-connection-detail { line-height: 1.5; margin: 0.7rem 0; }
    #tj-connection-recovery-overlay .tj-connection-status {
      color: var(--st-gray-text-color, var(--st-text-color, #19201b)); font-size: 0.9rem; margin: 0;
    }
    #tj-connection-recovery-overlay .tj-connection-actions {
      display: flex; flex-wrap: wrap; gap: 0.6rem; justify-content: flex-end; margin-top: 1rem;
    }
    #tj-connection-recovery-overlay button {
      border: 1px solid var(--st-border-color, #cbd2ca); border-radius: var(--st-button-radius, 0.45rem);
      cursor: pointer; font: inherit; font-weight: 650; min-height: 2.5rem; padding: 0.45rem 0.8rem;
    }
    #tj-connection-recovery-overlay button:focus-visible {
      outline: 3px solid var(--st-primary-color, #b32639); outline-offset: 2px;
    }
    #tj-connection-recovery-overlay .tj-retry-button {
      background: var(--st-primary-color, #b32639); border-color: var(--st-primary-color, #b32639); color: white;
    }
    #tj-connection-recovery-overlay .tj-reload-button {
      background: var(--st-secondary-background-color, #ffffff); color: var(--st-text-color, #19201b);
    }
    @media (max-width: 480px) {
      #tj-connection-recovery-overlay { align-items: flex-end; padding: 0.65rem; }
      #tj-connection-recovery-overlay .tj-connection-card { padding: 1rem; }
      #tj-connection-recovery-overlay .tj-connection-actions { flex-direction: column; }
      #tj-connection-recovery-overlay button { width: 100%; }
    }
    """

_CONNECTION_RECOVERY_JS = """
    export default function (component) {
      const { data, parentElement } = component
      const anchor = parentElement.querySelector('#tj-connection-recovery-anchor')
      if (!anchor || !data) return
      anchor._cleanup?.()

      document.getElementById('tj-connection-recovery-overlay')?.remove()
      const overlay = document.createElement('div')
      overlay.id = 'tj-connection-recovery-overlay'
      overlay.hidden = true
      overlay.setAttribute('role', 'alertdialog')
      overlay.setAttribute('aria-modal', 'true')
      overlay.setAttribute('aria-labelledby', 'tj-connection-recovery-title')
      overlay.setAttribute('aria-describedby', 'tj-connection-recovery-detail tj-connection-recovery-status')

      const themeSource = parentElement.host ?? parentElement
      const themeTokens = [
        '--st-background-color', '--st-secondary-background-color', '--st-text-color', '--st-primary-color',
        '--st-border-color', '--st-base-radius', '--st-button-radius', '--st-font', '--st-gray-text-color',
        '--st-red-color', '--st-red-text-color',
      ]
      const themeSources = [themeSource, document.documentElement, document.body]
      for (const token of themeTokens) {
        const value = themeSources
          .map((source) => getComputedStyle(source).getPropertyValue(token).trim())
          .find((candidate) => candidate && candidate !== 'unset')
        if (value && value !== 'unset') overlay.style.setProperty(token, value)
      }

      const card = document.createElement('section')
      card.className = 'tj-connection-card'
      const kicker = document.createElement('p')
      kicker.className = 'tj-connection-kicker'
      kicker.textContent = data.kicker
      const title = document.createElement('h2')
      title.id = 'tj-connection-recovery-title'
      title.className = 'tj-connection-title'
      title.textContent = data.title
      const detail = document.createElement('p')
      detail.id = 'tj-connection-recovery-detail'
      detail.className = 'tj-connection-detail'
      detail.textContent = data.detail
      const status = document.createElement('p')
      status.id = 'tj-connection-recovery-status'
      status.className = 'tj-connection-status'
      status.setAttribute('role', 'status')
      status.setAttribute('aria-live', 'polite')
      const actions = document.createElement('div')
      actions.className = 'tj-connection-actions'
      const reloadButton = document.createElement('button')
      reloadButton.type = 'button'
      reloadButton.className = 'tj-reload-button'
      reloadButton.textContent = data.reload_label
      const retryButton = document.createElement('button')
      retryButton.type = 'button'
      retryButton.className = 'tj-retry-button'
      retryButton.textContent = data.retry_label
      actions.append(reloadButton, retryButton)
      card.append(kicker, title, detail, status, actions)
      overlay.appendChild(card)
      document.body.appendChild(overlay)

      const streamlitRoot = document.querySelector('[data-testid="stApp"]')
      const appRoot = document.querySelector('[data-testid="stAppViewContainer"]')
      const appWasInert = Boolean(appRoot?.inert)
      const state = {
        stopped: false, checking: false, failures: 0, attempts: 0, visible: false,
        recovering: false, timer: null, disconnectTimer: null, reloadTimer: null,
      }
      const healthyIntervalMs = Number(data.healthy_interval_ms ?? 5000)
      const retryIntervalMs = Number(data.retry_interval_ms ?? 2000)
      const requestTimeoutMs = Number(data.request_timeout_ms ?? 2500)
      const failureThreshold = Number(data.failure_threshold ?? 2)
      const disconnectGraceMs = Number(data.disconnect_grace_ms ?? 1500)
      const initialCheckDelayMs = Number(data.initial_check_delay_ms ?? 1000)
      const reloadCooldownMs = Number(data.reload_cooldown_ms ?? 30000)
      const reloadDelayMs = Number(data.reload_delay_ms ?? 650)
      const reloadStorageKey = 'trade-compass:connection-recovery:last-reload'
      const connectedStates = new Set(['CONNECTED', 'STATIC_CONNECTED'])

      const retryText = () => data.retrying_status.replace('{attempt}', String(state.attempts))
      const setStatus = (message) => { status.textContent = message }
      const lastReloadAt = () => {
        try { return Number(window.sessionStorage.getItem(reloadStorageKey) || '0') } catch { return 0 }
      }
      const rememberReload = () => {
        try { window.sessionStorage.setItem(reloadStorageKey, String(Date.now())) } catch { /* best effort */ }
      }
      const schedule = (delay) => {
        if (state.stopped) return
        window.clearTimeout(state.timer)
        state.timer = window.setTimeout(check, delay)
      }
      const showDisconnected = (message) => {
        if (!state.visible) {
          state.visible = true
          overlay.hidden = false
          if (appRoot) appRoot.inert = true
          window.setTimeout(() => retryButton.focus(), 0)
        }
        title.textContent = data.title
        detail.textContent = data.detail
        retryButton.hidden = false
        setStatus(message)
      }
      const recover = () => {
        if (state.recovering) return
        state.recovering = true
        title.textContent = data.recovered_title
        detail.textContent = data.recovered_detail
        retryButton.hidden = true
        const lastReload = lastReloadAt()
        if (Date.now() - lastReload < reloadCooldownMs) {
          setStatus(data.reload_limited_status)
          reloadButton.focus()
          state.recovering = false
          return
        }
        setStatus(data.reloading_status)
        rememberReload()
        window.dispatchEvent(new CustomEvent('trade-compass:connection-recovery-reloading'))
        state.reloadTimer = window.setTimeout(() => window.location.reload(), reloadDelayMs)
      }
      async function check() {
        if (state.stopped || state.checking) return
        state.checking = true
        if (state.visible) {
          state.attempts += 1
          setStatus(retryText())
        }
        const controller = new AbortController()
        const timeout = window.setTimeout(() => controller.abort(), requestTimeoutMs)
        try {
          const response = await fetch(data.health_url, {
            cache: 'no-store', credentials: 'same-origin', signal: controller.signal,
          })
          if (!response.ok) throw new Error(`Health check returned ${response.status}`)
          state.failures = 0
          if (state.visible) recover()
          else schedule(healthyIntervalMs)
        } catch {
          state.failures += 1
          if (!navigator.onLine || state.failures >= failureThreshold) {
            if (!state.visible) state.attempts = 1
            showDisconnected(navigator.onLine ? retryText() : data.offline_status)
          }
          schedule(retryIntervalMs)
        } finally {
          window.clearTimeout(timeout)
          state.checking = false
        }
      }
      const onOffline = () => {
        state.failures = failureThreshold
        state.attempts = Math.max(1, state.attempts)
        showDisconnected(data.offline_status)
        schedule(retryIntervalMs)
      }
      const onOnline = () => {
        if (state.visible) setStatus(data.checking_status)
        check()
      }
      const onVisibilityChange = () => {
        if (document.visibilityState === 'visible') check()
      }
      const connectionState = () => streamlitRoot?.getAttribute('data-test-connection-state') ?? 'CONNECTED'
      const onStreamlitConnectionChange = () => {
        if (connectedStates.has(connectionState())) {
          window.clearTimeout(state.disconnectTimer)
          state.disconnectTimer = null
          if (state.visible) recover()
          return
        }
        if (state.disconnectTimer !== null) return
        state.disconnectTimer = window.setTimeout(() => {
          state.disconnectTimer = null
          if (state.stopped || connectedStates.has(connectionState())) return
          if (!state.visible) state.attempts = 1
          showDisconnected(data.websocket_status)
        }, disconnectGraceMs)
      }
      const onRetry = () => {
        setStatus(data.checking_status)
        check()
      }
      const onReload = () => {
        rememberReload()
        window.location.reload()
      }
      const keepFocusInDialog = (event) => {
        if (!state.visible || event.key !== 'Tab') return
        const controls = [reloadButton, retryButton].filter((button) => !button.hidden)
        if (!controls.length) return
        const first = controls[0]
        const last = controls[controls.length - 1]
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault(); last.focus()
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault(); first.focus()
        }
      }

      window.addEventListener('offline', onOffline)
      window.addEventListener('online', onOnline)
      document.addEventListener('visibilitychange', onVisibilityChange)
      document.addEventListener('keydown', keepFocusInDialog)
      retryButton.addEventListener('click', onRetry)
      reloadButton.addEventListener('click', onReload)
      const connectionObserver = streamlitRoot ? new MutationObserver(onStreamlitConnectionChange) : null
      connectionObserver?.observe(streamlitRoot, { attributes: true, attributeFilter: ['data-test-connection-state'] })
      onStreamlitConnectionChange()
      schedule(initialCheckDelayMs)

      anchor._cleanup = () => {
        state.stopped = true
        window.clearTimeout(state.timer)
        window.clearTimeout(state.disconnectTimer)
        window.clearTimeout(state.reloadTimer)
        connectionObserver?.disconnect()
        window.removeEventListener('offline', onOffline)
        window.removeEventListener('online', onOnline)
        document.removeEventListener('visibilitychange', onVisibilityChange)
        document.removeEventListener('keydown', keepFocusInDialog)
        retryButton.removeEventListener('click', onRetry)
        reloadButton.removeEventListener('click', onReload)
        if (appRoot) appRoot.inert = appWasInert
        overlay.remove()
      }
      return anchor._cleanup
    }
    """


def _build_connection_recovery_component():  # type: ignore[no-untyped-def]
    """Register the inline definition with the active Streamlit runtime."""

    return st.components.v2.component(
        "trade_compass_connection_recovery",
        html=_CONNECTION_RECOVERY_HTML,
        css=_CONNECTION_RECOVERY_CSS,
        js=_CONNECTION_RECOVERY_JS,
        isolate_styles=False,
    )


_CONNECTION_RECOVERY = _build_connection_recovery_component()


def render_connection_recovery() -> None:
    """Mount a connection monitor that remains useful after Streamlit disconnects."""

    global _CONNECTION_RECOVERY

    mount_options = {
        "key": "trade-compass-connection-recovery",
        "data": {
            "health_url": "/_stcore/health",
            "kicker": tr("Connection status"),
            "title": tr("Connection lost"),
            "detail": tr(
                "Trade Compass cannot reach the server. Check your internet connection. "
                "Your latest action may not have been saved."
            ),
            "retry_label": tr("Retry now"),
            "reload_label": tr("Reload page"),
            "retrying_status": tr("Retrying automatically… Attempt {attempt}."),
            "offline_status": tr("This device appears to be offline. Retrying automatically…"),
            "websocket_status": tr("Trade Compass lost its live session. Reconnecting automatically…"),
            "checking_status": tr("Checking the connection…"),
            "recovered_title": tr("Connection restored"),
            "recovered_detail": tr("Trade Compass can reach the server again."),
            "reloading_status": tr("Reloading the page…"),
            "reload_limited_status": tr(
                "The connection is available, but automatic reload was paused to avoid a loop. Reload the page to continue."
            ),
        },
        "width": "content",
        "height": "content",
    }
    try:
        _CONNECTION_RECOVERY(**mount_options)
    except ValueError as error:
        # AppTest and other embedded runtimes can replace Streamlit's component
        # registry while keeping this Python module cached. Re-register only for
        # that exact boundary; production reruns keep the original renderer.
        if "is not registered" not in str(error):
            raise
        _CONNECTION_RECOVERY = _build_connection_recovery_component()
        _CONNECTION_RECOVERY(**mount_options)
