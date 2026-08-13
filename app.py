from __future__ import annotations

import os
from uuid import uuid4
from decimal import Decimal
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from trading_journal.application.auto_sync import MT5AutoSyncResult, MT5AutoSyncService
from trading_journal.application.dashboard import DashboardService
from trading_journal.application.framework import FrameworkService
from trading_journal.application.display_time import format_relative_time
from trading_journal.application.mt5_paths import default_mt5_export_path, find_mt5_common_files
from trading_journal.desktop import DesktopSyncControl, DesktopSyncStatusStore, desktop_runtime_paths, is_desktop_mode
from trading_journal.infrastructure.sqlite_repository import AccountListItem, JournalDatabaseResetRequiredError, SQLiteJournalRepository
from trading_journal.presentation.framework import (
    _render_framework_rules,
    _render_risk_policy,
    render_framework_dashboard,
    render_framework_page,
)
from trading_journal.presentation.global_alert_bubble import GlobalAlertItem, render_global_alert_bubble
from trading_journal.presentation.desktop_reset_restart import render_desktop_reset_restart_bridge
from trading_journal.presentation.i18n import (
    LANGUAGES,
    format_relative_time_localized,
    framework_alert_message,
    install_streamlit_translations,
    tr,
)


_CURRENCY_SYMBOLS = {"USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥", "AUD": "A$", "CAD": "C$", "CHF": "CHF", "NZD": "NZ$"}
_AUTO_SYNC_INTERVAL_SECONDS = 15
_FRESHNESS_INTERVAL_SECONDS = 5
_ANALYTICS_CACHE_TTL_SECONDS = 15
_CHART_POSITIVE = "#0e9163"
_CHART_NEGATIVE = "#c73545"


def format_number(value: str | Decimal, decimal_places: int = 2) -> str:
    return f"{Decimal(value):,.{decimal_places}f}"


def format_signed(value: str | Decimal, suffix: str = "", decimal_places: int = 2) -> str:
    number = Decimal(value)
    prefix = "+" if number > 0 else "−" if number < 0 else ""
    return f"{prefix}{format_number(abs(number), decimal_places)}{suffix}"


def format_currency(value: str | Decimal, currency: str) -> str:
    number = Decimal(value)
    prefix = "+" if number > 0 else "−" if number < 0 else ""
    symbol = _CURRENCY_SYMBOLS.get(currency.upper())
    amount = format_number(abs(number))
    return f"{prefix}{symbol}{amount}" if symbol else f"{prefix}{currency.upper()} {amount}"


def format_currency_caption(value: str | Decimal, currency: str) -> str:
    """Format currency safely for Streamlit text elements that parse Markdown."""
    return format_currency(value, currency).replace("$", r"\$")


def format_account_label(account: AccountListItem) -> str:
    return f"{account.display_name} · {account.login} · {account.broker_server}"


def style_chart(figure: go.Figure, *, yaxis_title: str) -> go.Figure:
    """Keep data semantics while Streamlit supplies the active chart theme.

    The browser can switch Streamlit's Light/Dark theme without a Python rerun.
    Avoid baking a server-side palette into Plotly so that switch remains
    coherent with the rest of the application.
    """
    figure.update_layout(
        height=330,
        margin=dict(l=12, r=12, t=42, b=12),
        title=dict(x=0.02, y=0.96),
        hovermode="x unified",
        showlegend=False,
    )
    figure.update_xaxes(showgrid=False, zeroline=False)
    figure.update_yaxes(title=yaxis_title, zeroline=True)
    return figure


def apply_application_style() -> None:
    st.html("""
        <style>
        [data-testid="stAppViewContainer"] > .main .block-container { max-width: 1480px; padding-top: 2.6rem; padding-bottom: 4rem; }
        [data-testid="stSidebar"] [data-testid="stSidebarNav"] { padding-top: 1.75rem; }
        [data-testid="stSidebar"] [data-testid="stSidebarNav"] a { border-radius: 8px; margin: 0.12rem 0.75rem; padding: 0.48rem 0.65rem; font-weight: 600; }
        h1, h2, h3 { letter-spacing: -0.03em; }
        h1 { font-size: 2.8rem !important; margin-bottom: 0.15rem !important; }
        h2 { margin-top: 1.35rem !important; }
        .stButton > button { border-radius: 6px; font-weight: 650; }
        </style>
        """)


@st.cache_data(ttl=_ANALYTICS_CACHE_TTL_SECONDS, max_entries=32, show_spinner=False)
def _cached_global_framework_alerts(database_path: str, database_mtime_ns: int) -> tuple[tuple[AccountListItem, object], ...]:
    """Cache cross-account analytics until the local database changes."""
    del database_mtime_ns
    repo = SQLiteJournalRepository(database_path)
    return tuple(
        (account, alert)
        for account in repo.list_mt5_accounts()
        for alert in FrameworkService(repo).framework_alerts(account.id)
    )


def _database_mtime_ns(database_path: Path) -> int:
    try:
        return database_path.stat().st_mtime_ns
    except FileNotFoundError:
        return 0


def render_global_framework_alert_bubble(repo: SQLiteJournalRepository) -> None:
    """Persistent cross-account warning/critical alert entry point."""
    severity_order = {"critical": 0, "warning": 1}
    database_path = getattr(repo, "database_path", None)
    source = (
        _cached_global_framework_alerts(str(database_path), _database_mtime_ns(database_path))
        if database_path is not None
        else tuple(
            (account, alert)
            for account in repo.list_mt5_accounts()
            for alert in FrameworkService(repo).framework_alerts(account.id)
        )
    )
    alerts = [(account, alert) for account, alert in source if alert.severity in severity_order]
    if not alerts:
        return
    alerts.sort(key=lambda item: (severity_order[item[1].severity], item[0].display_name, item[1].code))
    critical = sum(alert.severity == "critical" for _, alert in alerts)
    warnings = len(alerts) - critical
    label = (
        tr("{critical} critical · {warnings} warning{plural}", critical=critical, warnings=warnings, plural="s" if warnings != 1 else "")
        if critical
        else tr("{warnings} warning{plural}", warnings=warnings, plural="s" if warnings != 1 else "")
    )
    bubble_alerts: list[GlobalAlertItem] = [
        {
            "account_name": account.display_name,
            "code": alert.code,
            "message": framework_alert_message(alert.code, alert.message),
            "severity": alert.severity,
        }
        for account, alert in alerts
    ]
    render_global_alert_bubble(
        alerts=bubble_alerts,
        label=label,
        has_critical=bool(critical),
        panel_title=tr("Active alerts"),
        drag_hint=tr("Drag to move. Click to view active alerts."),
    )


@st.cache_resource(show_spinner=False)
def _cached_repository(database_path: str) -> SQLiteJournalRepository:
    repo = SQLiteJournalRepository(database_path)
    repo.initialize()
    return repo


def repository() -> SQLiteJournalRepository:
    database_path = Path(os.environ.get("TRADING_JOURNAL_DB", "data/trading_journal.db")).resolve()
    database_path.parent.mkdir(parents=True, exist_ok=True)
    return _cached_repository(str(database_path))


@st.cache_data(ttl=_ANALYTICS_CACHE_TTL_SECONDS, max_entries=128, show_spinner=False)
def _cached_dashboard_report(
    database_path: str,
    database_mtime_ns: int,
    account_id: int,
    start_date: str,
    end_date: str,
):
    del database_mtime_ns
    return DashboardService(SQLiteJournalRepository(database_path)).build_report(
        account_id=account_id,
        start_date=start_date,
        end_date=end_date,
    )


def build_dashboard_report(repo: SQLiteJournalRepository, *, account_id: int, start_date: str, end_date: str):
    return _cached_dashboard_report(
        str(repo.database_path),
        _database_mtime_ns(repo.database_path),
        account_id,
        start_date,
        end_date,
    )


def _render_sync_results(results: list[MT5AutoSyncResult], *, notice_key: str | None = None) -> None:
    st.session_state["auto_sync_results"] = results
    render_sync_failures(results, prefix="MT5 auto-sync needs attention")
    imported = [item for item in results if item.status == "imported"]
    if not imported or notice_key is None:
        return
    if st.session_state.get("auto_sync_notice_key") == notice_key:
        return
    st.session_state["auto_sync_notice_key"] = notice_key
    created = sum(item.created_count for item in imported)
    updated = sum(item.updated_count for item in imported)
    st.session_state["auto_sync_notice"] = f"Auto-imported {created} created and {updated} updated MT5 position(s)."


@st.fragment(run_every=_AUTO_SYNC_INTERVAL_SECONDS)
def _monitor_local_mt5_exports(repo: SQLiteJournalRepository) -> None:
    results = MT5AutoSyncService(repo).sync_configured_exports()
    notice_key = ";".join(
        f"{item.account_login}:{item.broker_server}:{item.export_updated_at.isoformat() if item.export_updated_at else ''}"
        for item in results
        if item.status == "imported"
    )
    _render_sync_results(results, notice_key=notice_key or None)


@st.fragment(run_every=_FRESHNESS_INTERVAL_SECONDS)
def _monitor_desktop_mt5_exports() -> None:
    paths = desktop_runtime_paths()
    status = DesktopSyncStatusStore(paths.sync_status_path)
    results = status.results()
    _render_sync_results(results, notice_key=status.last_import_at().isoformat() if status.last_import_at() else None)
    if error := status.worker_error():
        st.error(f"MT5 desktop sync needs attention: {error}")


def monitor_mt5_exports(repo: SQLiteJournalRepository) -> None:
    """Render live MT5 state without giving desktop mode a second importer."""

    if is_desktop_mode():
        _monitor_desktop_mt5_exports()
    else:
        _monitor_local_mt5_exports(repo)


def render_auto_sync_notice() -> None:
    notice = st.session_state.pop("auto_sync_notice", None)
    if notice:
        st.success(notice)


def render_sync_failures(results: list[MT5AutoSyncResult], *, prefix: str) -> None:
    failures = [item for item in results if item.status == "failed"]
    if not failures:
        return
    details = "; ".join(
        f"{item.account_name}: {item.message or 'Unknown error'}"
        for item in failures
    )
    st.error(f"{prefix}: {details}")


@st.fragment(run_every=_FRESHNESS_INTERVAL_SECONDS)
def render_live_sync_freshness(account_key: tuple[str, str] | None = None, *, include_sync_hint: bool = False) -> None:
    results = st.session_state.get("auto_sync_results", [])
    if account_key is not None:
        account_login, broker_server = account_key
        result = next(
            (item for item in results if (item.account_login, item.broker_server) == (account_login, broker_server)),
            None,
        )
        last_update = result.export_updated_at if result else None
    else:
        last_update = max((item.export_updated_at for item in results if item.export_updated_at is not None), default=None)
    message = f"Last update: {format_relative_time_localized(format_relative_time(last_update))}" if last_update is not None else "No export checked yet"
    if include_sync_hint:
        message += " · Checks every configured account immediately (read-only)."
    st.caption(message)


def render_manual_sync_button(repo: SQLiteJournalRepository, *, key: str) -> None:
    actions = st.container(horizontal=True, vertical_alignment="center", gap="medium")
    sync_requested = actions.button("Sync MT5 now", key=key, icon=":material/sync:")
    results = st.session_state.get("auto_sync_results", [])
    if sync_requested:
        if is_desktop_mode():
            paths = desktop_runtime_paths()
            DesktopSyncControl(paths.sync_request_path, paths.shutdown_request_path).request_sync()
            st.info("Desktop sync requested. The local worker will check every configured export within one second.")
        else:
            with st.spinner(tr("Syncing MT5 now…")):
                results = MT5AutoSyncService(repo).sync_configured_exports()
            st.session_state["auto_sync_results"] = results
            st.toast(tr("MT5 sync complete."))
    with actions:
        render_live_sync_freshness(include_sync_hint=True)
    if not sync_requested or is_desktop_mode():
        return

    imported = [item for item in results if item.status == "imported"]
    failures = [item for item in results if item.status == "failed"]
    waiting = [item for item in results if item.status == "waiting"]
    if imported:
        created = sum(item.created_count for item in imported)
        updated = sum(item.updated_count for item in imported)
        st.success(f"Manual sync imported {created} created and {updated} updated MT5 position(s).")
    elif not failures and waiting:
        st.info("MT5 sync is waiting: " + "; ".join(item.message or item.account_name for item in waiting))
    elif not failures and results:
        st.success("MT5 journal is already up to date.")
    elif not failures:
        st.info("No approved MT5 accounts are configured for sync.")
    render_sync_failures(results, prefix="MT5 sync needs attention")


def render_journal_reporting_settings(repo: SQLiteJournalRepository) -> None:
    st.markdown("#### Reporting calendar")
    with st.form("journal-reporting-settings", border=False):
        current = repo.get_journal_settings()
        labels = {"server": "Server Timezone", "utc": "UTC", "local": "Local Timezone"}
        selected = st.segmented_control(
            "Group trades and reports by",
            list(labels.values()),
            default=labels[current.reporting_time_basis],
            required=True,
            width="content",
        )
        st.caption("Choose the calendar used for reports and limits. Server Timezone follows the MT5 broker clock.")
        submitted = st.form_submit_button("Save calendar", type="primary", icon=":material/save:")
    if submitted:
        try:
            with st.spinner(tr("Saving…")):
                repo.configure_journal(reporting_time_basis=next(key for key, label in labels.items() if label == selected))
        except ValueError as error:
            st.error(str(error))
        else:
            st.toast(tr("Journal settings saved."))
            st.success("Journal settings saved.")


def render_mt5_account_settings(repo: SQLiteJournalRepository) -> AccountListItem | None:
    st.markdown("#### Approved MT5 accounts")
    st.caption("Each account has a unique MT5 account ID. Its broker server confirms the export source. Funded capital can be updated later; it recalculates historical growth, drawdown, and Risk limits without changing MT5 trades. Dashboard and Framework always show the single active account below.")
    common_files_location = find_mt5_common_files()
    accounts = repo.list_mt5_accounts()
    accounts_by_id = {str(account.id): account for account in accounts}
    active_account = repo.get_active_mt5_account()
    selected_id = st.session_state.get("mt5-account-selected-id")
    if selected_id not in accounts_by_id and selected_id != "new":
        selected_id = str(accounts[0].id) if accounts else "new"
        st.session_state["mt5-account-selected-id"] = selected_id

    def begin_new_account() -> None:
        st.session_state["mt5-account-selected-id"] = "new"
        for name in ("display-name", "currency", "funded-capital", "login", "broker-server", "export-path"):
            st.session_state.pop(f"mt5-account-new-{name}", None)

    def select_account_from_list() -> None:
        click = st.session_state.get("mt5-account-list")
        if click is None or click["row"] >= len(accounts):
            return
        st.session_state["mt5-account-selected-id"] = str(accounts[click["row"]].id)

    master, detail = st.columns([2, 3], gap="large")
    with master:
        st.markdown("##### Accounts")
        st.button("New account", icon=":material/add:", width="stretch", key="new-mt5-account", on_click=begin_new_account)
        if accounts:

            def status_label(account: AccountListItem) -> str:
                is_active = active_account is not None and account.id == active_account.id
                is_editing = selected_id == str(account.id)
                labels = [tr(label) for label, flag in (("Active", is_active), ("Editing", is_editing)) if flag]
                return " · ".join(labels)

            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "Account": account.display_name,
                            "Connection": f"{account.login} · {account.broker_server}",
                            "Status": status_label(account),
                            "Open": ":material/edit:",
                        }
                        for account in accounts
                    ]
                ),
                column_config={
                    "Account": st.column_config.TextColumn("Account", width="medium", pinned=True),
                    "Connection": st.column_config.TextColumn("MT5 connection", width="medium"),
                    "Status": st.column_config.TextColumn("", width="small"),
                    "Open": st.column_config.ButtonColumn(
                        "",
                        type="tertiary",
                        width="small",
                        help="Open this account",
                        on_click=select_account_from_list,
                        key="mt5-account-list",
                    ),
                },
                hide_index=True,
                width="stretch",
            )
        else:
            st.caption("No accounts yet. Create the first one to start importing MT5 trades.")

    selected = accounts_by_id.get(selected_id)
    form_scope = "new" if selected is None else str(selected.id)

    def field_key(name: str) -> str:
        return f"mt5-account-{form_scope}-{name}"

    defaults = {
        "display-name": selected.display_name if selected else "",
        "currency": selected.account_currency if selected else "USD",
        "funded-capital": (selected.funded_capital or "") if selected else "",
        "login": selected.login if selected else "",
        "broker-server": selected.broker_server if selected else "",
        "export-path": (
            ""
            if selected is None or selected.export_file_path == default_mt5_export_path(selected.login)
            else selected.export_file_path
        ),
    }
    for name, value in defaults.items():
        st.session_state.setdefault(field_key(name), value)

    has_imported_trades = bool(selected and repo.account_has_imported_trades(selected.id))
    identity_locked = has_imported_trades

    def clear_account_form() -> None:
        for name in defaults:
            st.session_state.pop(field_key(name), None)

    def save_account() -> bool:
        display_name = st.session_state[field_key("display-name")].strip()
        account_currency = st.session_state[field_key("currency")].upper()
        funded_capital = st.session_state[field_key("funded-capital")]
        login = st.session_state[field_key("login")]
        broker_server = st.session_state[field_key("broker-server")].strip()
        export_file_path = st.session_state[field_key("export-path")]
        if not display_name or not login.isdecimal() or not broker_server:
            st.session_state["mt5-account-save-error"] = "An account name, a numeric MT5 login, and broker server are required."
            return False
        if selected is None and any(account.login == login for account in accounts):
            st.session_state["mt5-account-save-error"] = "This MT5 account ID is already registered. Select it from the accounts list to update its settings."
            return False

        resolved_export_path = export_file_path.strip() or default_mt5_export_path(login)
        try:
            with st.spinner(tr("Saving…")):
                if selected is None:
                    repo.register_mt5_account(
                        display_name=display_name,
                        login=login,
                        broker_server=broker_server,
                        account_currency=account_currency,
                        export_file_path=resolved_export_path,
                        opening_balance=funded_capital or None,
                    )
                else:
                    repo.update_mt5_account(
                        account_id=selected.id,
                        display_name=display_name,
                        login=login,
                        broker_server=broker_server,
                        account_currency=account_currency,
                        export_file_path=resolved_export_path,
                        opening_balance=funded_capital or None,
                    )
        except ValueError as error:
            st.session_state["mt5-account-save-error"] = str(error)
            return False
        if selected is None:
            clear_account_form()
            registered = next(
                item for item in repo.list_mt5_accounts() if item.login == login and item.broker_server == broker_server
            )
            st.session_state["mt5-account-selected-id"] = str(registered.id)
            st.session_state["mt5-account-notice"] = "MT5 account added."
        else:
            st.session_state["mt5-account-notice"] = "MT5 account updated."
        st.toast(tr(st.session_state["mt5-account-notice"]))
        return True

    with detail:
        account_editor = st.container(border=True)
        header_name, header_status = account_editor.columns([3, 2], vertical_alignment="center")
        header_name.markdown(f"##### {'New account' if selected is None else selected.display_name}")
        if selected is not None:
            if active_account is not None and selected.id == active_account.id:
                header_status.badge(tr("Active"), color="green", icon=":material/check_circle:")
            else:
                activate_clicked = header_status.button(
                    "Set as active account",
                    icon=":material/toggle_on:",
                    key=f"set-active-mt5-account-{selected.id}",
                )
                if activate_clicked:
                    with st.spinner(tr("Saving…")):
                        repo.set_active_mt5_account(selected.id)
                    st.session_state["mt5-account-notice"] = tr("{account} is now the active account.", account=selected.display_name)
                    st.toast(st.session_state["mt5-account-notice"])
                    st.rerun()
        if identity_locked:
            account_editor.caption("MT5 account ID, broker server, and currency are locked because this account already has imported trades. Its name, funded capital, and export location remain editable.")
        with account_editor.form("mt5-account-editor-form", border=False):
            identity, currency, baseline = st.columns([3, 1, 2])
            display_name = identity.text_input(
                "Account name",
                placeholder="e.g. Live account, Prop firm eval",
                key=field_key("display-name"),
            )
            account_currency = currency.text_input("Currency", max_chars=3, key=field_key("currency"), disabled=identity_locked).upper()
            funded_capital = baseline.text_input(
                "Funded capital (optional)",
                placeholder="Set now or later",
                help="Enter the funded capital before the earliest imported trade. It is the fixed basis for balance growth, drawdown, and Risk limits; it does not replace the latest live MT5 balance.",
                key=field_key("funded-capital"),
            )
            account_id, broker = st.columns(2)
            login = account_id.text_input("MT5 account ID", key=field_key("login"), disabled=identity_locked)
            broker_server = broker.text_input("Broker server", key=field_key("broker-server"), disabled=identity_locked)
            with st.expander("Advanced: custom export location"):
                if common_files_location.path is None:
                    st.caption("MT5 Common Files was not detected. Set a custom path or `TRADING_JOURNAL_MT5_COMMON_FILES`.")
                else:
                    st.caption(f"Detected Common Files ({common_files_location.source})")
                    st.caption(str(common_files_location.path))
                generated_path_placeholder = default_mt5_export_path("000000000").replace("000000000", "<MT5-login>")
                export_file_path = st.text_input(
                    "Custom export path (optional)",
                    placeholder=generated_path_placeholder,
                    help="Leave blank to use the EA default: trading_journal/<MT5-login>_positions.csv.",
                    key=field_key("export-path"),
                )
                st.caption("Default: `trading_journal/<MT5-login>_positions.csv` under MT5 Common Files.")
            account_submitted = st.form_submit_button(
                "Update account" if selected else "Add account",
                type="primary",
                icon=":material/save:",
            )
        if account_submitted and save_account():
            st.rerun()

        save_error = st.session_state.pop("mt5-account-save-error", None)
        if save_error:
            st.error(save_error)

        if selected is not None:
            with st.expander("Account maintenance"):
                if has_imported_trades:
                    st.caption("This account has imported trades or reviews. Deactivate it to remove it from imports and reports while retaining its local history. Adding the same MT5 account ID later reactivates it.")

                    deactivate_clicked = st.button("Deactivate account", key=f"deactivate-mt5-account-{selected.id}")
                    if deactivate_clicked:
                        with st.spinner(tr("Deactivating account…")):
                            repo.deactivate_mt5_account(selected.id)
                        clear_account_form()
                        st.session_state["mt5-account-selected-id"] = "new"
                        st.session_state["mt5-account-notice"] = "MT5 account deactivated."
                        st.toast(tr("MT5 account deactivated."))
                        st.rerun()
                else:
                    st.caption("This account has no imported trades. Deleting it permanently removes its account settings, import log, and risk-policy setup.")
                    delete_confirmation_key = f"delete-mt5-account-confirm-{selected.id}"
                    delete_confirmed = st.checkbox(
                        "I understand this permanently deletes this unused account",
                        key=delete_confirmation_key,
                    )

                    delete_clicked = st.button(
                        "Delete account",
                        type="primary",
                        key=f"delete-mt5-account-{selected.id}",
                        disabled=not delete_confirmed,
                    )
                    if delete_clicked:
                        if not st.session_state.get(delete_confirmation_key, False):
                            st.session_state["mt5-account-save-error"] = "Confirm deletion before deleting the account."
                        else:
                            with st.spinner(tr("Deleting account…")):
                                repo.delete_mt5_account(selected.id)
                            clear_account_form()
                            st.session_state["mt5-account-selected-id"] = "new"
                            st.session_state["mt5-account-notice"] = "MT5 account deleted."
                            st.toast(tr("MT5 account deleted."))
                            st.rerun()

        notice = st.session_state.pop("mt5-account-notice", None)
        if notice:
            st.success(notice)
    return selected

def render_settings(repo: SQLiteJournalRepository) -> None:
    st.subheader("Settings")
    st.caption("Configure reporting, account risk, reusable strategies, and review rules.")
    accounts_tab, strategies_tab, rules_tab = st.tabs(["Account & risk", "Strategies", "Review rules"])
    with accounts_tab:
        render_journal_reporting_settings(repo)
        st.divider()
        account = render_mt5_account_settings(repo)
        st.divider()
        if account is None:
            st.info("Save an MT5 account before configuring its Risk policy.")
        else:
            _render_risk_policy(repo, account)
    with strategies_tab:
        render_strategy_settings(repo)
    with rules_tab:
        _render_framework_rules(repo)
    if is_desktop_mode():
        st.divider()
        with st.container(border=True):
            st.markdown("##### Desktop application")
            st.caption("The journal, MT5 sync worker, and your data are running locally on this computer. Closing this desktop application stops automatic MT5 imports.")
            if st.button("Quit desktop journal", icon=":material/power_settings_new:", type="primary"):
                paths = desktop_runtime_paths()
                DesktopSyncControl(paths.sync_request_path, paths.shutdown_request_path, paths.reset_request_path).request_shutdown()
                st.success("Closing the local Trading Journal…")
        render_desktop_database_reset()


def render_desktop_database_reset() -> None:
    """Request a supervisor-owned reset; the browser never deletes SQLite files."""

    pending_reset_id = st.session_state.get("desktop-database-reset-pending")
    if pending_reset_id:
        st.info("Restarting Trading Journal. This page will reload automatically when the clean journal is ready.")
        ready_reset_id = render_desktop_reset_restart_bridge(pending_reset_id)
        if ready_reset_id == pending_reset_id and st.session_state.get("desktop-database-reset-dispatched") != pending_reset_id:
            request_desktop_database_reset()
            st.session_state["desktop-database-reset-dispatched"] = pending_reset_id
        return

    with st.container(border=True):
        st.markdown("##### Reset local database")
        st.warning("This permanently removes all local accounts, imports, reviews, policies, strategies, and framework evidence. MT5 export files and desktop logs are kept.")
        confirmation = st.text_input("Type RESET to confirm", key="desktop-database-reset-confirmation")
        if st.button(
            "Reset local database",
            icon=":material/delete_forever:",
            type="primary",
            disabled=confirmation.strip() != "RESET",
            key="desktop-database-reset",
        ):
            st.session_state["desktop-database-reset-pending"] = uuid4().hex
            st.session_state.pop("desktop-database-reset-dispatched", None)
            st.rerun()


def request_desktop_database_reset() -> None:
    """Send the reset signal only after the browser restart bridge is ready."""

    paths = desktop_runtime_paths()
    DesktopSyncControl(paths.sync_request_path, paths.shutdown_request_path, paths.reset_request_path).request_reset()


def render_desktop_database_diagnostic(error: Exception) -> None:
    """Keep unexpected local database failures in the browser, without resetting data."""

    st.set_page_config(page_title="Trading Journal recovery", page_icon="📈", layout="wide")
    st.title("Trading Journal recovery")
    st.error("Trading Journal could not open its local database.")
    st.caption("No data was changed. Inspect desktop.log in the Trading Journal data directory before taking further action.")
    st.code(str(error), language="text")
    print("Trading Journal diagnostic recovery screen active.", flush=True)


def render_strategy_settings(repo: SQLiteJournalRepository) -> None:
    st.subheader("Strategy library")
    st.caption("Save reusable strategy definitions and, when available, their backtest evidence. Backtest fields are optional and do not alter live-trade results.")
    profiles = repo.list_strategy_profiles()
    try:
        journal_settings = repo.get_journal_settings()
        default_strategy_id = journal_settings.default_strategy_profile_id
    except RuntimeError:
        default_strategy_id = None

    profiles_by_id = {profile.id: profile for profile in profiles}
    selected_id = st.session_state.get("strategy-selected-id")
    if selected_id not in profiles_by_id and selected_id != "new":
        selected_id = profiles[0].id if profiles else "new"
        st.session_state["strategy-selected-id"] = selected_id

    def begin_new_strategy() -> None:
        st.session_state["strategy-selected-id"] = "new"
        for name in (
            "name",
            "description",
            "magic-numbers",
            "default",
            "backtest-start",
            "backtest-end",
            "backtest-trades",
            "backtest-win-rate",
            "backtest-expectancy",
            "backtest-net-r",
            "backtest-notes",
        ):
            st.session_state.pop(f"strategy-new-{name}", None)

    def select_strategy_from_list() -> None:
        click = st.session_state.get("strategy-list")
        if click is None or click["row"] >= len(profiles):
            return
        st.session_state["strategy-selected-id"] = profiles[click["row"]].id

    master, detail = st.columns([1, 2], gap="large")
    with master:
        st.markdown("##### Strategies")
        st.button("Add strategy", icon=":material/add:", width="stretch", key="new-strategy", on_click=begin_new_strategy)
        if profiles:
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "Strategy": profile.name,
                            "Mapping": "Journal default"
                            if profile.id == default_strategy_id
                            else "Magic: " + ", ".join(profile.magic_numbers)
                            if profile.magic_numbers
                            else "Not mapped",
                            "Editing": "Current" if selected_id == profile.id else "",
                            "Open": ":material/edit:",
                        }
                        for profile in profiles
                    ]
                ),
                column_config={
                    "Strategy": st.column_config.TextColumn("Strategy", pinned=True),
                    "Mapping": st.column_config.TextColumn("MT5 mapping"),
                    "Editing": st.column_config.TextColumn(""),
                    "Open": st.column_config.ButtonColumn(
                        "",
                        type="tertiary",
                        help="Open this strategy",
                        on_click=select_strategy_from_list,
                        key="strategy-list",
                    ),
                },
                hide_index=True,
                width="stretch",
            )
        else:
            st.caption("No strategies yet. Create one before reviewing trade quality.")

    selected = profiles_by_id.get(selected_id)
    form_scope = "new" if selected is None else str(selected.id)

    def field_key(name: str) -> str:
        return f"strategy-{form_scope}-{name}"

    defaults = {
        "name": selected.name if selected else "",
        "description": selected.description or "" if selected else "",
        "magic-numbers": ", ".join(selected.magic_numbers) if selected else "",
        "default": bool(selected and selected.id == default_strategy_id),
        "backtest-start": selected.backtest_start_date or "" if selected else "",
        "backtest-end": selected.backtest_end_date or "" if selected else "",
        "backtest-trades": str(selected.backtest_trade_count) if selected and selected.backtest_trade_count is not None else "",
        "backtest-win-rate": selected.backtest_win_rate or "" if selected else "",
        "backtest-expectancy": selected.backtest_expectancy_r or "" if selected else "",
        "backtest-net-r": selected.backtest_net_r or "" if selected else "",
        "backtest-notes": selected.backtest_notes or "" if selected else "",
    }
    for name, value in defaults.items():
        st.session_state.setdefault(field_key(name), value)
    has_backtest = bool(selected and any(defaults[name] for name in defaults if name.startswith("backtest-")))

    with detail:
        strategy_editor = st.container(border=True)
        strategy_editor.markdown(f"##### {'New strategy' if selected is None else selected.name}")
        with strategy_editor.form("strategy-profile", border=False):
            name = st.text_input("Strategy name", key=field_key("name"))
            description = st.text_area(
                "Strategy description",
                placeholder="When and why this setup should be traded.",
                key=field_key("description"),
            )
            magic_numbers = st.text_input(
                "MT5 magic numbers (optional)",
                placeholder="e.g. 10001, 10002",
                help="Automatically maps exported MT5 trades to this strategy. Each magic number can belong to only one strategy.",
                key=field_key("magic-numbers"),
            )
            use_as_default = st.checkbox("Use as the journal default strategy", key=field_key("default"))
            st.caption("Only one default strategy can exist. Every imported trade inherits it dynamically.")
            with st.expander("Add backtest evidence (optional)", expanded=has_backtest):
                st.caption("Leave this closed unless you want to record the evidence behind this strategy.")
                first, second = st.columns(2)
                backtest_start_date = first.text_input("Backtest start date", placeholder="YYYY-MM-DD", key=field_key("backtest-start"))
                backtest_end_date = second.text_input("Backtest end date", placeholder="YYYY-MM-DD", key=field_key("backtest-end"))
                first, second = st.columns(2)
                backtest_trade_count = first.text_input("Backtest sample size", placeholder="e.g. 120", key=field_key("backtest-trades"))
                backtest_win_rate = second.text_input("Backtest win rate (%)", placeholder="e.g. 57.5", key=field_key("backtest-win-rate"))
                first, second = st.columns(2)
                backtest_expectancy_r = first.text_input("Backtest expectancy (R)", placeholder="e.g. 0.42", key=field_key("backtest-expectancy"))
                backtest_net_r = second.text_input("Backtest net R", placeholder="e.g. 50.4", key=field_key("backtest-net-r"))
                backtest_notes = st.text_area("Backtest notes", placeholder="Market, timeframe, rules, and any material caveats.", key=field_key("backtest-notes"))
            submitted = st.form_submit_button("Save strategy", type="primary", icon=":material/save:")
        if submitted:
            try:
                repository_trade_count = int(backtest_trade_count) if backtest_trade_count.strip() else None
                with st.spinner(tr("Saving…")):
                    profile = repo.save_strategy_profile(
                        name=name,
                        description=description or None,
                        backtest_start_date=backtest_start_date or None,
                        backtest_end_date=backtest_end_date or None,
                        backtest_trade_count=repository_trade_count,
                        backtest_win_rate=backtest_win_rate or None,
                        backtest_expectancy_r=backtest_expectancy_r or None,
                        backtest_net_r=backtest_net_r or None,
                        backtest_notes=backtest_notes or None,
                        magic_numbers=magic_numbers or None,
                        strategy_id=selected.id if selected else None,
                    )
                    if use_as_default:
                        repo.set_default_strategy(profile.id)
                    elif selected and selected.id == default_strategy_id:
                        repo.set_default_strategy(None)
            except ValueError as error:
                st.error(str(error))
            else:
                st.session_state["strategy-selected-id"] = profile.id
                st.toast(tr("Strategy profile saved."))
                st.success("Strategy profile saved.")


def render_dashboard(repo: SQLiteJournalRepository) -> AccountListItem | None:
    st.markdown('<div class="dashboard-kicker">CLOSED-TRADE REVIEW</div>', unsafe_allow_html=True)
    st.subheader("Performance dashboard")
    try:
        settings = repo.get_journal_settings()
    except RuntimeError:
        st.info("Configure journal settings before viewing reports.")
        return

    account = repo.get_active_mt5_account()
    if account is None:
        st.info("Add an approved MT5 account in Settings before viewing reports.")
        return
    render_manual_sync_button(repo, key="dashboard-manual-sync")
    with st.container(border=True):
        st.markdown("**Report scope**")
        st.caption(tr("Reporting on {account}. Change the active account in Settings → Approved MT5 accounts.", account=format_account_label(account)))
        dashboard_service = DashboardService(repo)
        today = dashboard_service.current_report_date(account.id)
        period = st.segmented_control(
            "Report period",
            ["This month", "All time", "Custom"],
            default="This month",
            required=True,
            width="content",
            key="dashboard-report-period",
        )
        if period == "This month":
            start_date, end_date = today.replace(day=1), today
        elif period == "All time":
            start_date, end_date = dashboard_service.earliest_trade_date(account.id) or today, today
        else:
            first, second = st.columns(2)
            start_date = first.date_input("Start date", value=today.replace(day=1))
            end_date = second.date_input("End date", value=today)
            if start_date > end_date:
                st.error("Start date must be on or before end date.")
                return account
    currency = account.account_currency
    time_label = {"server": "MT5 server time", "utc": "UTC", "local": "local computer time"}[settings.reporting_time_basis]
    st.caption(f"All monetary figures below are for this {currency} account only. No currency conversion is applied. Report dates use {time_label}.")

    report = build_dashboard_report(
        repo,
        account_id=account.id,
        start_date=start_date.isoformat(),
        end_date=end_date.isoformat(),
    )
    if report.raw_position_count == 0:
        st.info("No MT5 positions were closed in the selected period.")
        return account

    period_label = f"{start_date.strftime('%d %b')} – {end_date.strftime('%d %b %Y')}"
    logical_label = f"{report.trade_count} closed logical trade{'s' if report.trade_count != 1 else ''}"
    position_label = f"{report.raw_position_count} MT5 position{'s' if report.raw_position_count != 1 else ''}"
    st.markdown(f'<div class="dashboard-period">{period_label} · {logical_label} · {position_label}</div>', unsafe_allow_html=True)
    with st.container(horizontal=True, gap="small"):
        st.metric("Account balance", "—" if report.ending_balance is None else format_currency(report.ending_balance, currency), border=True)
        st.metric("Account growth", "—" if report.balance_growth_percent is None else f"{format_signed(report.balance_growth_percent, '%', 1)}", border=True)
        st.metric("Realized P&L", format_currency(report.net_pnl, currency), border=True)
        st.metric("Account drawdown", format_currency(-Decimal(report.max_drawdown), currency), border=True)

    st.markdown("#### Logical-trade quality")
    with st.container(horizontal=True, gap="small"):
        st.metric("Total R", "Awaiting risk" if report.total_r is None else format_signed(report.total_r, "R"), border=True)
        st.metric("Win rate", f"{format_number(report.win_rate, 1)}%", border=True)
        st.metric("Profit factor", "No losses" if report.profit_factor is None else format_number(report.profit_factor, 2), border=True)
        st.metric("Expectancy", "—" if report.expectancy is None else format_currency(report.expectancy, currency), border=True)
        st.metric("Worst day", "—" if report.worst_day is None else format_currency(report.worst_day, currency), border=True)
    if report.r_trade_count < report.trade_count:
        st.caption(f"R is based on {report.r_trade_count:,} of {report.trade_count:,} logical trades with an effective planned risk.")
    else:
        st.caption(f"All {report.trade_count:,} logical trades have an effective risk value.")
    if report.starting_balance is None:
        st.caption("Set funded capital in Settings to enable the balance curve, balance growth, and drawdown percentage.")
    else:
        st.caption(
            f"Current drawdown: {format_currency_caption(-Decimal(report.current_drawdown), currency)}"
            + (f" ({format_number(report.current_drawdown_percent, 1)}%)" if report.current_drawdown_percent is not None else "")
            + f" · Balance at period start: {format_currency_caption(report.starting_balance, currency)}."
        )

    chart_view = st.segmented_control(
        "Chart view",
        ["Daily", "Per trade"],
        default="Daily",
        required=True,
        key="dashboard_chart_view",
        width="content",
    )
    cumulative = pd.DataFrame([item.__dict__ for item in report.cumulative])
    per_trade = pd.DataFrame(
        [item.__dict__ for item in report.per_trade],
        columns=[
            "sequence", "logical_trade_id", "display_label", "position_ids", "position_count", "exit_time", "position_id",
            "symbol", "net_pnl", "result_r", "strategy", "cumulative_pnl", "balance", "drawdown", "drawdown_percent",
        ],
    )
    daily = pd.DataFrame([item.__dict__ for item in report.daily])
    strategies = pd.DataFrame(
        [item.__dict__ for item in report.by_strategy],
        columns=["strategy", "net_pnl", "total_r", "backtest_trade_count", "backtest_win_rate", "backtest_expectancy_r", "backtest_net_r"],
    )
    cumulative["cumulative_pnl"] = cumulative["cumulative_pnl"].astype(float)
    cumulative["balance"] = pd.to_numeric(cumulative["balance"], errors="coerce")
    cumulative["drawdown"] = cumulative["drawdown"].astype(float)
    per_trade["cumulative_pnl"] = per_trade["cumulative_pnl"].astype(float)
    per_trade["drawdown"] = per_trade["drawdown"].astype(float)
    per_trade["net_pnl"] = per_trade["net_pnl"].astype(float)
    per_trade["trade_label"] = per_trade.apply(
        lambda item: f"Trade {item.sequence} · {item.display_label}",
        axis=1,
    )
    per_trade["hover_label"] = per_trade.apply(
        lambda item: f"{item.trade_label}<br>Closed {item.exit_time}",
        axis=1,
    )
    daily["net_pnl"] = daily["net_pnl"].astype(float)
    strategies["net_pnl"] = strategies["net_pnl"].astype(float)

    if chart_view == "Per trade" and not report.per_trade:
        st.info("No complete logical trades closed in this period. Showing the immutable MT5 position history instead.")
        chart_view = "Daily"

    if chart_view == "Daily":
        timeline = cumulative.assign(hover_label=cumulative["date"])
        timeline_x = timeline["date"]
        curve_column = "balance" if report.ending_balance is not None else "cumulative_pnl"
        curve_title = tr("Account balance curve") if report.ending_balance is not None else tr("Account equity curve · P&L")
        drawdown_title = tr("Account drawdown from daily peak")
        pnl_data = daily.assign(hover_label=daily["date"])
        pnl_x = pnl_data["date"]
        pnl_title = tr("Daily realized P&L")
    else:
        timeline = per_trade
        timeline_x = timeline["exit_time"]
        curve_column = "cumulative_pnl"
        curve_title = tr("Cumulative logical-trade P&L")
        drawdown_title = tr("Logical-trade drawdown")
        pnl_data = per_trade
        pnl_x = pnl_data["exit_time"]
        pnl_title = tr("Logical-trade P&L")
    curve_is_balance = chart_view == "Daily" and report.ending_balance is not None

    left, right = st.columns(2)
    with left:
        with st.container(border=True):
            pnl_figure = go.Figure(
                go.Scatter(
                    x=timeline_x,
                    y=timeline[curve_column],
                    customdata=timeline["hover_label"],
                    mode="lines+markers",
                    line=dict(color=_CHART_POSITIVE, width=3),
                    marker=dict(color=_CHART_POSITIVE, size=7),
                    fill=None if curve_is_balance else "tozeroy",
                    fillcolor="rgba(14, 145, 99, 0.14)" if not curve_is_balance else None,
                    hovertemplate=f"%{{customdata}}<br><b>{currency} %{{y:,.2f}}</b><extra></extra>",
                )
            )
            pnl_figure.update_layout(title=curve_title)
            st.plotly_chart(style_chart(pnl_figure, yaxis_title=currency), width="stretch", config={"displayModeBar": False})
    with right:
        with st.container(border=True):
            drawdown_figure = go.Figure(
                go.Scatter(
                    x=timeline_x,
                    y=-timeline["drawdown"],
                    customdata=timeline[["hover_label", "drawdown"]],
                    mode="lines+markers",
                    line=dict(color=_CHART_NEGATIVE, width=3),
                    marker=dict(color=_CHART_NEGATIVE, size=7),
                    fill="tozeroy",
                    fillcolor="rgba(199, 53, 69, 0.16)",
                    hovertemplate=f"%{{customdata[0]}}<br><b>−{currency} %{{customdata[1]:,.2f}}</b><extra></extra>",
                )
            )
            drawdown_figure.update_layout(title=drawdown_title)
            st.plotly_chart(style_chart(drawdown_figure, yaxis_title=f"{currency} drawdown"), width="stretch", config={"displayModeBar": False})

    left, right = st.columns(2)
    with left:
        with st.container(border=True):
            bar_colours = [_CHART_POSITIVE if value >= 0 else _CHART_NEGATIVE for value in pnl_data["net_pnl"]]
            daily_figure = go.Figure(
                go.Bar(
                    x=pnl_x,
                    y=pnl_data["net_pnl"],
                    customdata=pnl_data["hover_label"],
                    marker_color=bar_colours,
                    marker_line_width=0,
                    hovertemplate=f"%{{customdata}}<br><b>{currency} %{{y:,.2f}}</b><extra></extra>",
                )
            )
            daily_figure.update_layout(title=pnl_title)
            st.plotly_chart(style_chart(daily_figure, yaxis_title=currency), width="stretch", config={"displayModeBar": False})
    with right:
        with st.container(border=True):
            strategies = strategies.sort_values("net_pnl", ascending=False)
            strategy_figure = go.Figure(
                go.Bar(
                    x=strategies["strategy"],
                    y=strategies["net_pnl"],
                    marker_color=[_CHART_POSITIVE if value >= 0 else _CHART_NEGATIVE for value in strategies["net_pnl"]],
                    marker_line_width=0,
                    hovertemplate=f"%{{x}}<br><b>{currency} %{{y:,.2f}}</b><extra></extra>",
                )
            )
            strategy_figure.update_layout(title=tr("Strategy P&L"))
            st.plotly_chart(style_chart(strategy_figure, yaxis_title=currency), width="stretch", config={"displayModeBar": False})

    if chart_view == "Per trade":
        with st.container(border=True):
            st.markdown("#### Closed-trade detail")
            trade_table = pd.DataFrame(
                {
                    tr("Closed"): per_trade["exit_time"],
                    tr("Logical trade"): [f"LT-{trade_id}" for trade_id in per_trade["logical_trade_id"]],
                    tr("Trade"): per_trade["display_label"],
                    tr("Positions"): [", ".join(f"#{position_id}" for position_id in position_ids) for position_ids in per_trade["position_ids"]],
                    tr("Symbol"): per_trade["symbol"],
                    f"P&L ({currency})": [format_currency(value, currency) for value in per_trade["net_pnl"]],
                    tr("Result R"): ["—" if value is None else format_signed(value, "R") for value in per_trade["result_r"]],
                    tr("Post-close drawdown"): [format_currency(-Decimal(value), currency) for value in per_trade["drawdown"]],
                }
            )
            st.dataframe(trade_table, hide_index=True, width="stretch")
            st.caption("This view follows the current logical-trade grouping for review analysis. Account balance and account drawdown remain based on immutable MT5 positions in Daily view.")

    with st.container(border=True):
        st.subheader("Strategy results and backtest context")
        strategy_table = pd.DataFrame(
            {
                tr("Strategy"): strategies["strategy"],
                tr("Live P&L ({currency})", currency=currency): [format_currency(value, currency) for value in strategies["net_pnl"]],
                tr("Live total R"): ["—" if value is None else format_signed(value, "R") for value in strategies["total_r"]],
                tr("Backtest trades"): strategies["backtest_trade_count"].fillna("—"),
                tr("Backtest win rate"): ["—" if value is None else f"{format_number(value, 1)}%" for value in strategies["backtest_win_rate"]],
                tr("Backtest expectancy"): ["—" if value is None else format_signed(value, "R") for value in strategies["backtest_expectancy_r"]],
                tr("Backtest net R"): ["—" if value is None else format_signed(value, "R") for value in strategies["backtest_net_r"]],
            }
        )
        st.dataframe(strategy_table, hide_index=True, width="stretch")
    return account


def main() -> None:
    try:
        repo = repository()
    except JournalDatabaseResetRequiredError as error:
        if is_desktop_mode():
            st.set_page_config(page_title="Trading Journal recovery", page_icon="📈", layout="wide")
            st.title("Trading Journal recovery")
            st.error(str(error))
            st.caption("Reset the local database to start a clean journal. This cannot be undone.")
            render_desktop_database_reset()
            print("Trading Journal reset recovery screen active.", flush=True)
            return
        st.error(str(error))
        st.code("make reset-db CONFIRM_RESET=yes", language="bash")
        return
    except Exception as error:
        if is_desktop_mode():
            render_desktop_database_diagnostic(error)
            return
        raise
    settings = repo.get_journal_settings()
    st.session_state.setdefault("display_language", settings.display_language)
    install_streamlit_translations()
    st.set_page_config(page_title=tr("Trading Journal"), page_icon="📈", layout="wide")
    with st.sidebar:
        selected_language = st.selectbox(
            "Language",
            options=list(LANGUAGES),
            format_func=LANGUAGES.get,
            key="display_language",
            width="stretch",
        )
    if selected_language != settings.display_language:
        repo.configure_journal(
            reporting_time_basis=settings.reporting_time_basis,
            display_language=selected_language,
        )
        st.rerun()
    apply_application_style()
    st.title("Trading Journal")
    st.caption("Local-only journal with read-only MT5 imports.")
    page = st.navigation(
        {
            "Workspace": [
                st.Page("app_pages/dashboard.py", title=tr("Dashboard"), icon=":material/dashboard:", default=True),
                st.Page("app_pages/framework.py", title=tr("Framework"), icon=":material/fact_check:"),
                st.Page("app_pages/settings.py", title=tr("Settings"), icon=":material/settings:"),
                st.Page("app_pages/guidance.py", title=tr("Guide"), icon=":material/menu_book:"),
            ]
        },
        position="sidebar",
    )
    page.run()
    render_global_framework_alert_bubble(repo)


if __name__ == "__main__":
    main()
