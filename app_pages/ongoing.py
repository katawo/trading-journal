"""Ongoing live-position workspace page."""

from app import monitor_mt5_exports, render_auto_sync_notice, repository
from trading_journal.presentation.ongoing import render_ongoing_positions_page


repo = repository()
monitor_mt5_exports(repo)
render_auto_sync_notice()
render_ongoing_positions_page(repo)
