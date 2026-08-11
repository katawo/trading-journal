"""Post-trade framework workspace page."""

from app import monitor_mt5_exports, render_auto_sync_notice, repository
from trading_journal.presentation.framework import render_framework_page


repo = repository()
monitor_mt5_exports(repo)
render_auto_sync_notice()
render_framework_page(repo)
