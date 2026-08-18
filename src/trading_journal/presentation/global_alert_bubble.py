"""A browser-persisted, draggable global framework-alert overlay."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TypedDict

import streamlit as st


class GlobalAlertItem(TypedDict):
    account_name: str
    code: str
    message: str
    severity: str


_ALERT_BUBBLE = st.components.v2.component(
    "global_framework_alert_bubble",
    html="""
    <div id="tj-alert-bubble-anchor"></div>
    """,
    css="""
    #tj-alert-bubble-anchor { height: 1px; width: 1px; }
    #tj-global-framework-alert-bubble .tj-floating { position: fixed; z-index: 1000; }
    #tj-global-framework-alert-bubble .tj-bubble, #tj-global-framework-alert-bubble .tj-panel { font-family: var(--st-font); }
    #tj-global-framework-alert-bubble .tj-bubble {
      align-items: center; background: var(--st-orange-background-color, #fff1d6);
      border: 1px solid var(--st-orange-color, #d97706); border-radius: var(--st-button-radius, 0.5rem);
      box-shadow: 0 10px 28px color-mix(in srgb, var(--st-text-color, #1f2933) 25%, transparent);
      color: var(--st-orange-text-color, #8a4b00); cursor: grab; display: flex; font-size: 0.92rem;
      font-weight: 700; gap: 0.45rem; padding: 0.55rem 0.72rem; touch-action: none;
      user-select: none; white-space: nowrap;
    }
    #tj-global-framework-alert-bubble .tj-bubble:focus-visible { outline: 3px solid var(--st-primary-color, #c73545); outline-offset: 2px; }
    #tj-global-framework-alert-bubble .tj-bubble.critical { background: var(--st-red-background-color, #ffe1e5); border-color: var(--st-red-color, #c73545); color: var(--st-red-text-color, #8a1621); animation: tj-alert-pulse 1.6s ease-in-out infinite; }
    #tj-global-framework-alert-bubble .tj-bubble.dragging { cursor: grabbing; animation: none; }
    #tj-global-framework-alert-bubble .tj-bubble-compact { display: none; }
    @media (max-width: 640px) {
      /* The full pill can span most of a phone's width and fully cover whatever
         control happens to sit in its bottom-right default drop point. Collapse it
         to an icon + count badge there instead - still draggable, far smaller. */
      #tj-global-framework-alert-bubble .tj-bubble { padding: 0.6rem; border-radius: 999px; min-width: 2.7rem; justify-content: center; }
      #tj-global-framework-alert-bubble .tj-bubble-full { display: none; }
      #tj-global-framework-alert-bubble .tj-bubble-compact { display: inline; font-size: 0.95rem; }
    }
    #tj-global-framework-alert-bubble .tj-panel {
      background: var(--st-secondary-background-color, #ffffff); border: 1px solid var(--st-border-color, #c8d0c8);
      border-radius: var(--st-base-radius, 0.5rem); box-shadow: 0 14px 36px color-mix(in srgb, var(--st-text-color, #1f2933) 28%, transparent);
      color: var(--st-text-color, #1f2933); max-height: min(24rem, calc(100vh - 2rem)); overflow-y: auto;
      padding: 0.7rem; position: fixed; width: min(25rem, calc(100vw - 1.5rem)); z-index: 1001;
    }
    #tj-global-framework-alert-bubble .tj-panel[hidden] { display: none; }
    #tj-global-framework-alert-bubble .tj-panel-title { font-size: 0.95rem; font-weight: 700; margin: 0 0 0.45rem; }
    #tj-global-framework-alert-bubble .tj-alert-item {
      background: var(--st-orange-background-color, #fff0d8); border-left: 5px solid var(--st-orange-color, #a65f00);
      border-radius: 0 0.32rem 0.32rem 0; margin-top: 0.4rem; padding: 0.5rem 0.6rem;
    }
    #tj-global-framework-alert-bubble .tj-alert-item.critical {
      background: var(--st-red-background-color, #f8e4e5); border-left-color: var(--st-red-color, #c73545);
      box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--st-red-color, #c73545) 20%, transparent);
    }
    #tj-global-framework-alert-bubble .tj-alert-account { display: block; font-size: 0.83rem; font-weight: 700; margin-bottom: 0.12rem; }
    #tj-global-framework-alert-bubble .tj-alert-message { font-size: 0.88rem; line-height: 1.35; }
    @keyframes tj-alert-pulse { 50% { box-shadow: 0 10px 34px color-mix(in srgb, var(--st-red-color) 52%, transparent); transform: translateY(-2px); } }
    """,
    js="""
    export default function (component) {
      const { data, parentElement } = component
      const root = parentElement.querySelector('#tj-alert-bubble-anchor')
      if (!root || !data) return
      root._cleanup?.()

      const storageKey = 'trading-journal:global-framework-alert-bubble:position'
      document.getElementById('tj-global-framework-alert-bubble')?.remove()
      const portal = document.createElement('div')
      portal.id = 'tj-global-framework-alert-bubble'
      const themeSource = parentElement.host ?? parentElement
      const themeTokens = [
        '--st-background-color', '--st-secondary-background-color', '--st-text-color',
        '--st-primary-color', '--st-border-color', '--st-base-radius', '--st-button-radius',
        '--st-font', '--st-orange-background-color', '--st-orange-color', '--st-orange-text-color',
        '--st-red-background-color', '--st-red-color', '--st-red-text-color',
      ]
      const themeSources = [themeSource, document.documentElement, document.body]
      for (const token of themeTokens) {
        const value = themeSources
          .map((source) => getComputedStyle(source).getPropertyValue(token).trim())
          .find((candidate) => candidate && candidate !== 'unset')
        if (value && value !== 'unset') portal.style.setProperty(token, value)
      }
      document.body.appendChild(portal)
      const state = { dragged: false, pointerId: null, startX: 0, startY: 0, originLeft: 0, originTop: 0, left: 0, top: 0 }
      const floating = document.createElement('div')
      floating.className = 'tj-floating'
      const button = document.createElement('button')
      button.type = 'button'
      button.className = `tj-bubble${data.has_critical ? ' critical' : ''}`
      button.setAttribute('aria-expanded', root.dataset.open === 'true' ? 'true' : 'false')
      button.title = data.drag_hint
      button.setAttribute('aria-label', data.label)
      const bubbleIcon = document.createElement('span')
      bubbleIcon.textContent = data.has_critical ? '⨯' : '⚠'
      const bubbleFull = document.createElement('span')
      bubbleFull.className = 'tj-bubble-full'
      bubbleFull.textContent = data.label
      const bubbleCompact = document.createElement('span')
      bubbleCompact.className = 'tj-bubble-compact'
      bubbleCompact.textContent = String(data.count)
      button.append(bubbleIcon, bubbleFull, bubbleCompact)
      const panel = document.createElement('section')
      panel.className = 'tj-panel'
      panel.hidden = root.dataset.open !== 'true'
      const title = document.createElement('div')
      title.className = 'tj-panel-title'
      title.textContent = data.panel_title
      panel.appendChild(title)
      for (const alert of data.alerts ?? []) {
        const item = document.createElement('div')
        item.className = `tj-alert-item${alert.severity === 'critical' ? ' critical' : ''}`
        const account = document.createElement('span')
        account.className = 'tj-alert-account'
        account.textContent = alert.account_name
        const message = document.createElement('div')
        message.className = 'tj-alert-message'
        message.textContent = alert.message
        item.append(account, message)
        panel.appendChild(item)
      }
      floating.append(button)
      portal.append(floating, panel)

      const clamp = (value, minimum, maximum) => Math.min(Math.max(value, minimum), Math.max(minimum, maximum))
      const persist = () => localStorage.setItem(storageKey, JSON.stringify({ left: state.left, top: state.top }))
      const place = (left, top, save = false) => {
        const width = button.offsetWidth || 1
        const height = button.offsetHeight || 1
        state.left = clamp(left, 8, window.innerWidth - width - 8)
        state.top = clamp(top, 8, window.innerHeight - height - 8)
        floating.style.left = `${state.left}px`
        floating.style.top = `${state.top}px`
        if (save) persist()
        if (!panel.hidden) placePanel()
      }
      const placePanel = () => {
        if (panel.hidden) return
        const rect = button.getBoundingClientRect()
        const panelRect = panel.getBoundingClientRect()
        const left = clamp(rect.left, 8, window.innerWidth - panelRect.width - 8)
        const preferredTop = rect.top - panelRect.height - 10
        const top = preferredTop >= 8 ? preferredTop : clamp(rect.bottom + 10, 8, window.innerHeight - panelRect.height - 8)
        panel.style.left = `${left}px`
        panel.style.top = `${top}px`
      }
      const saved = (() => {
        try { return JSON.parse(localStorage.getItem(storageKey) || 'null') } catch { return null }
      })()
      const savedLeft = Number(saved?.left)
      const savedTop = Number(saved?.top)
      place(
        Number.isFinite(savedLeft) ? savedLeft : window.innerWidth - button.offsetWidth - 22,
        Number.isFinite(savedTop) ? savedTop : window.innerHeight - button.offsetHeight - 22,
      )
      if (!panel.hidden) placePanel()

      const onPointerDown = (event) => {
        state.pointerId = event.pointerId; state.dragged = false; state.startX = event.clientX; state.startY = event.clientY
        state.originLeft = button.getBoundingClientRect().left; state.originTop = button.getBoundingClientRect().top
        state.left = state.originLeft; state.top = state.originTop
        button.setPointerCapture(event.pointerId); button.classList.add('dragging')
      }
      const onPointerMove = (event) => {
        if (state.pointerId !== event.pointerId) return
        const deltaX = event.clientX - state.startX; const deltaY = event.clientY - state.startY
        if (Math.abs(deltaX) > 4 || Math.abs(deltaY) > 4) state.dragged = true
        if (state.dragged) place(state.originLeft + deltaX, state.originTop + deltaY)
      }
      const togglePanel = () => {
        panel.hidden = !panel.hidden
        root.dataset.open = String(!panel.hidden)
        button.setAttribute('aria-expanded', String(!panel.hidden))
        placePanel()
      }
      const onPointerUp = (event) => {
        if (state.pointerId !== event.pointerId) return
        button.releasePointerCapture?.(event.pointerId); button.classList.remove('dragging')
        if (state.dragged) persist()
        state.pointerId = null
      }
      const onPointerCancel = (event) => {
        if (state.pointerId !== event.pointerId) return
        button.releasePointerCapture?.(event.pointerId); button.classList.remove('dragging')
        if (state.dragged) persist()
        state.pointerId = null
      }
      const onClick = () => {
        if (state.dragged) { state.dragged = false; return }
        togglePanel()
      }
      const onWindowResize = () => place(state.left, state.top, true)
      const onOutsidePointer = (event) => {
        if (!panel.hidden && !floating.contains(event.target) && !panel.contains(event.target)) {
          panel.hidden = true; root.dataset.open = 'false'; button.setAttribute('aria-expanded', 'false')
        }
      }
      button.addEventListener('pointerdown', onPointerDown)
      button.addEventListener('pointermove', onPointerMove)
      button.addEventListener('pointerup', onPointerUp)
      button.addEventListener('pointercancel', onPointerCancel)
      button.addEventListener('click', onClick)
      window.addEventListener('resize', onWindowResize)
      document.addEventListener('pointerdown', onOutsidePointer, true)
      root._cleanup = () => {
        button.removeEventListener('pointerdown', onPointerDown); button.removeEventListener('pointermove', onPointerMove); button.removeEventListener('pointerup', onPointerUp); button.removeEventListener('pointercancel', onPointerCancel); button.removeEventListener('click', onClick)
        window.removeEventListener('resize', onWindowResize); document.removeEventListener('pointerdown', onOutsidePointer, true)
        portal.remove()
      }
      return root._cleanup
    }
    """,
    isolate_styles=False,
)


def render_global_alert_bubble(
    *,
    alerts: Sequence[GlobalAlertItem],
    label: str,
    has_critical: bool,
    panel_title: str = "Active alerts",
    drag_hint: str = "Drag to move. Click to view active alerts.",
) -> None:
    """Render the global alert overlay without persisting UI placement to the journal."""
    _ALERT_BUBBLE(
        key="global-framework-alert-bubble",
        data={
            "alerts": list(alerts),
            "label": label,
            "has_critical": has_critical,
            "panel_title": panel_title,
            "drag_hint": drag_hint,
            "count": len(alerts),
        },
        width="content",
        height="content",
    )
