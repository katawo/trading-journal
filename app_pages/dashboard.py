"""Dashboard workspace page."""

from app import monitor_mt5_exports, render_auto_sync_notice, render_dashboard, repository


repo = repository()
monitor_mt5_exports(repo)
render_auto_sync_notice()
render_dashboard(repo)
