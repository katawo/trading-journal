"""Dashboard workspace page."""

from app import monitor_mt5_exports, render_auto_sync_notice, render_dashboard, repository
from trading_journal.presentation.framework import render_framework_dashboard


repo = repository()
monitor_mt5_exports(repo)
render_auto_sync_notice()
account = render_dashboard(repo)
if account is not None:
    render_framework_dashboard(repo, account)
