from __future__ import annotations

import os
from datetime import date
from decimal import Decimal
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from trading_journal.application.auto_sync import MT5AutoSyncResult, MT5AutoSyncService
from trading_journal.application.dashboard import DashboardService
from trading_journal.application.display_time import format_relative_time
from trading_journal.application.mt5_paths import default_mt5_export_path, find_mt5_common_files
from trading_journal.infrastructure.sqlite_repository import SQLiteJournalRepository


_CURRENCY_SYMBOLS = {"USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥", "AUD": "A$", "CAD": "C$", "CHF": "CHF", "NZD": "NZ$"}
_AUTO_SYNC_INTERVAL_SECONDS = 15
_FRESHNESS_INTERVAL_SECONDS = 1


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


def style_chart(figure: go.Figure, *, yaxis_title: str) -> go.Figure:
    figure.update_layout(
        height=330,
        margin=dict(l=16, r=16, t=48, b=16),
        paper_bgcolor="#ffffff",
        plot_bgcolor="#ffffff",
        font=dict(family="Avenir Next, Segoe UI, sans-serif", color="#17211c", size=12),
        title=dict(font=dict(family="Georgia, Palatino, serif", color="#101713", size=17), x=0.02, y=0.96),
        hovermode="x unified",
        hoverlabel=dict(bgcolor="#101713", font=dict(color="#ffffff"), bordercolor="#101713"),
        showlegend=False,
    )
    figure.update_xaxes(showgrid=False, zeroline=False, tickfont=dict(color="#3c4942"), linecolor="#aeb8b1")
    figure.update_yaxes(title=yaxis_title, gridcolor="#dce2dd", zeroline=True, zerolinecolor="#aeb8b1", tickfont=dict(color="#3c4942"))
    return figure


def apply_application_style() -> None:
    st.markdown(
        """
        <style>
        :root { --ink: #101713; --muted: #46534b; --paper: #ffffff; --canvas: #ffffff; --line: #aeb8b1; --green: #007a58; --amber: #9f5e00; --red: #b42318; }
        .stApp { background: var(--canvas); color: var(--ink); }
        [data-testid="stHeader"] { background: #ffffff; border-bottom: 1px solid #d6ddd8; }
        [data-testid="stSidebar"] { background: #f3f5f2; border-right: 2px solid #17211c; }
        [data-testid="stSidebar"] * { color: #101713 !important; }
        [data-testid="stSidebar"] div[data-testid="stRadio"] { background: transparent; border: 0; border-radius: 0; padding: 0; width: 100%; }
        [data-testid="stSidebar"] div[data-testid="stRadio"] label { padding: 0.32rem 0.42rem; border-radius: 4px; font-weight: 650; }
        [data-testid="stSidebar"] div[data-testid="stRadio"] label:hover { background: #dfe6e0; }
        h1, h2, h3 { font-family: Georgia, Palatino, "Times New Roman", serif; color: var(--ink); letter-spacing: -0.025em; }
        h1 { font-size: 2.55rem !important; margin-bottom: 0.1rem !important; }
        [data-testid="stCaptionContainer"] { color: var(--muted); }
        [data-testid="stMetric"] { background: #ffffff; border: 2px solid #17211c; border-radius: 6px; padding: 0.9rem 0.95rem; box-shadow: none; }
        [data-testid="stMetricLabel"] p { color: var(--muted); font-family: "Avenir Next", "Segoe UI", sans-serif; font-size: 0.68rem; font-weight: 800; letter-spacing: 0.08em; text-transform: uppercase; }
        [data-testid="stMetricValue"] { font-family: Georgia, Palatino, serif; color: var(--ink); font-size: 2rem; letter-spacing: -0.035em; }
        [data-testid="stMetricDelta"] { font-family: "Avenir Next", "Segoe UI", sans-serif; }
        div[data-testid="stRadio"] { background: #ffffff; border: 1px solid #17211c; border-radius: 4px; padding: 0.2rem 0.7rem; width: fit-content; }
        div[data-testid="stRadio"] label { padding: 0.12rem 0.2rem; }
        [data-testid="stDataFrame"] { border: 1px solid #17211c; border-radius: 4px; overflow: hidden; background: var(--paper); }
        .dashboard-kicker { color: #007a58; font-family: "Avenir Next", "Segoe UI", sans-serif; font-size: 0.7rem; font-weight: 800; letter-spacing: 0.14em; margin-bottom: -0.15rem; }
        .dashboard-period { color: var(--muted); font-size: 0.9rem; font-weight: 600; margin-top: -0.3rem; margin-bottom: 0.4rem; }
        </style>
        """,
        unsafe_allow_html=True,
    )


def repository() -> SQLiteJournalRepository:
    database_path = Path(os.environ.get("TRADING_JOURNAL_DB", "data/trading_journal.db"))
    database_path.parent.mkdir(parents=True, exist_ok=True)
    repo = SQLiteJournalRepository(database_path)
    repo.initialize()
    return repo


@st.fragment(run_every=_AUTO_SYNC_INTERVAL_SECONDS)
def monitor_mt5_exports(repo: SQLiteJournalRepository) -> None:
    results = MT5AutoSyncService(repo).sync_configured_exports()
    st.session_state["auto_sync_results"] = results
    render_sync_failures(results, prefix="MT5 auto-sync needs attention")
    imported = [item for item in results if item.status == "imported"]
    if not imported:
        return
    created = sum(item.created_count for item in imported)
    updated = sum(item.updated_count for item in imported)
    st.session_state["auto_sync_notice"] = f"Auto-imported {created} created and {updated} updated MT5 position(s)."
    st.rerun(scope="app")


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
    message = f"Last update: {format_relative_time(last_update)}" if last_update is not None else "No export checked yet"
    if include_sync_hint:
        message += " · Checks every configured account immediately (read-only)."
    st.caption(message)


def render_manual_sync_button(repo: SQLiteJournalRepository, *, key: str) -> None:
    action, context = st.columns([1, 8], vertical_alignment="center")
    sync_requested = action.button("Sync MT5 now", key=key)
    results = st.session_state.get("auto_sync_results", [])
    if sync_requested:
        results = MT5AutoSyncService(repo).sync_configured_exports()
        st.session_state["auto_sync_results"] = results
    with context:
        render_live_sync_freshness(include_sync_hint=True)
    if not sync_requested:
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


def render_journal_settings(repo: SQLiteJournalRepository) -> None:
    try:
        current = repo.get_journal_settings()
    except RuntimeError:
        current = None

    st.markdown("#### Journal defaults")
    with st.form("journal-settings"):
        st.markdown("**Reporting**")
        currency, timezone, target = st.columns([1, 2, 2])
        base_currency = currency.text_input("Base currency", value=current.base_currency if current else "USD", max_chars=3).upper()
        reporting_timezone = timezone.text_input("Reporting timezone", value=current.reporting_timezone if current else "UTC")
        monthly_target = target.number_input("Monthly target", min_value=0.0, value=float(current.monthly_target) if current else 1000.0, step=100.0)

        st.divider()
        st.markdown("**Balance tracking**")
        balance_choice, balance_value = st.columns([3, 2], vertical_alignment="bottom")
        use_starting_balance = balance_choice.checkbox("Track balance growth and percentage drawdown", value=bool(current and current.starting_balance))
        balance_choice.caption("Enter the balance immediately before your first imported trade.")
        starting_balance = balance_value.number_input(
            "Opening balance",
            min_value=0.01,
            value=float(current.starting_balance) if current and current.starting_balance else 1000.0,
            step=100.0,
            disabled=not use_starting_balance,
        )

        st.divider()
        st.markdown("**Risk baseline**")
        risk_choice, risk_value = st.columns([3, 2], vertical_alignment="bottom")
        use_baseline = risk_choice.checkbox("Apply a default planned-risk baseline to all trades", value=bool(current and current.default_planned_risk_amount))
        risk_choice.caption("Changing this value immediately recalculates R for every imported trade.")
        default_risk = risk_value.number_input(
            "Default risk (1R)",
            min_value=0.01,
            value=float(current.default_planned_risk_amount) if current and current.default_planned_risk_amount else 10.0,
            step=1.0,
            disabled=not use_baseline,
        )
        if current and current.default_strategy_name:
            st.caption(f"Default strategy: {current.default_strategy_name}. Manage it in Settings → Strategies.")
        submitted = st.form_submit_button("Save journal settings")
    if submitted:
        try:
            repo.configure_journal(
                base_currency=base_currency,
                reporting_timezone=reporting_timezone,
                monthly_target=str(monthly_target),
                default_planned_risk_amount=str(default_risk) if use_baseline else None,
                starting_balance=str(starting_balance) if use_starting_balance else None,
            )
        except ValueError as error:
            st.error(str(error))
        else:
            st.success("Journal settings saved.")


def render_mt5_account_settings(repo: SQLiteJournalRepository) -> None:
    st.markdown("#### Approved MT5 accounts")
    st.caption("Add each trading account once. Saving the same account ID and server updates its settings.")
    common_files_location = find_mt5_common_files()
    with st.form("mt5-account"):
        identity, currency = st.columns([3, 1])
        display_name = identity.text_input("Account name", value="Primary account")
        account_currency = currency.text_input("Currency", value="USD", max_chars=3).upper()
        account_id, broker = st.columns(2)
        login = account_id.text_input("MT5 account ID")
        broker_server = broker.text_input("Broker server")
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
            )
            st.caption("Default: `trading_journal/<MT5-login>_positions.csv` under MT5 Common Files.")
        registered = st.form_submit_button("Save account")
    if registered:
        if not login.isdecimal() or not broker_server:
            st.error("A numeric MT5 login and broker server are required.")
        else:
            resolved_export_path = export_file_path.strip() or default_mt5_export_path(login)
            repo.register_mt5_account(display_name=display_name, login=login, broker_server=broker_server, account_currency=account_currency, export_file_path=resolved_export_path)
            st.success("MT5 account saved.")

    accounts = repo.list_mt5_accounts()
    if accounts:
        account_table = pd.DataFrame(
            [
                {
                    "Account": account.display_name,
                    "MT5 ID": account.login,
                    "Server": account.broker_server,
                    "Currency": account.account_currency,
                    "Export file": Path(account.export_file_path).name if account.export_file_path else "—",
                }
                for account in accounts
            ]
        )
        st.dataframe(account_table, hide_index=True, width="stretch")


def render_settings(repo: SQLiteJournalRepository) -> None:
    st.subheader("Settings")
    st.caption("Configure journal defaults, approved MT5 accounts, and reusable strategies.")
    general_tab, accounts_tab, strategies_tab = st.tabs(["General", "MT5 Accounts", "Strategies"])
    with general_tab:
        render_journal_settings(repo)
    with accounts_tab:
        render_mt5_account_settings(repo)
    with strategies_tab:
        render_strategy_settings(repo)


def render_strategy_settings(repo: SQLiteJournalRepository) -> None:
    st.subheader("Strategy library")
    st.caption("Save reusable strategy definitions and, when available, their backtest evidence. Backtest fields are optional and do not alter live-trade results.")
    profiles = repo.list_strategy_profiles()
    names = [profile.name for profile in profiles]
    selected_name = st.selectbox("Strategy profile", ["Create new strategy", *names])
    selected = next((profile for profile in profiles if profile.name == selected_name), None)
    has_backtest = bool(
        selected
        and any(
            [
                selected.backtest_start_date,
                selected.backtest_end_date,
                selected.backtest_trade_count,
                selected.backtest_win_rate,
                selected.backtest_expectancy_r,
                selected.backtest_net_r,
                selected.backtest_notes,
            ]
        )
    )
    try:
        journal_settings = repo.get_journal_settings()
        default_strategy = journal_settings.default_strategy_name
        default_strategy_id = journal_settings.default_strategy_profile_id
    except RuntimeError:
        default_strategy = None
        default_strategy_id = None

    with st.form("strategy-profile"):
        name = st.text_input("Strategy name", value=selected.name if selected else "")
        description = st.text_area("Strategy description", value=selected.description or "" if selected else "", placeholder="When and why this setup should be traded.")
        use_as_default = st.checkbox(
            "Use as the journal default strategy",
            value=bool(selected and selected.id == default_strategy_id),
        )
        st.caption("Only one default strategy can exist. Every imported trade inherits it dynamically.")
        with st.expander("Add backtest evidence (optional)", expanded=has_backtest):
            st.caption("Leave this closed unless you want to record the evidence behind this strategy.")
            first, second = st.columns(2)
            backtest_start_date = first.text_input("Backtest start date", value=selected.backtest_start_date or "" if selected else "", placeholder="YYYY-MM-DD")
            backtest_end_date = second.text_input("Backtest end date", value=selected.backtest_end_date or "" if selected else "", placeholder="YYYY-MM-DD")
            first, second = st.columns(2)
            backtest_trade_count = first.text_input("Backtest sample size", value=str(selected.backtest_trade_count) if selected and selected.backtest_trade_count is not None else "", placeholder="e.g. 120")
            backtest_win_rate = second.text_input("Backtest win rate (%)", value=selected.backtest_win_rate or "" if selected else "", placeholder="e.g. 57.5")
            first, second = st.columns(2)
            backtest_expectancy_r = first.text_input("Backtest expectancy (R)", value=selected.backtest_expectancy_r or "" if selected else "", placeholder="e.g. 0.42")
            backtest_net_r = second.text_input("Backtest net R", value=selected.backtest_net_r or "" if selected else "", placeholder="e.g. 50.4")
            backtest_notes = st.text_area("Backtest notes", value=selected.backtest_notes or "" if selected else "", placeholder="Market, timeframe, rules, and any material caveats.")
        submitted = st.form_submit_button("Save strategy")
    if submitted:
        try:
            repository_trade_count = int(backtest_trade_count) if backtest_trade_count.strip() else None
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
                strategy_id=selected.id if selected else None,
            )
            if use_as_default:
                repo.set_default_strategy(profile.id)
            elif selected and selected.id == default_strategy_id:
                repo.set_default_strategy(None)
        except ValueError as error:
            st.error(str(error))
        else:
            st.success("Strategy profile saved.")

    profiles = repo.list_strategy_profiles()
    try:
        default_strategy_id = repo.get_journal_settings().default_strategy_profile_id
    except RuntimeError:
        default_strategy_id = None
    if profiles:
        st.subheader("Saved strategy profiles")
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "strategy": profile.name,
                        "journal_default": profile.id == default_strategy_id,
                        "description": profile.description,
                        "backtest_period": profile.backtest_period,
                        "backtest_trades": profile.backtest_trade_count,
                        "backtest_win_rate": profile.backtest_win_rate,
                        "backtest_expectancy_r": profile.backtest_expectancy_r,
                        "backtest_net_r": profile.backtest_net_r,
                        "backtest_notes": profile.backtest_notes,
                    }
                    for profile in profiles
                ]
            ),
            hide_index=True,
            width="stretch",
        )


def render_dashboard(repo: SQLiteJournalRepository) -> None:
    st.markdown('<div class="dashboard-kicker">CLOSED-TRADE REVIEW</div>', unsafe_allow_html=True)
    st.subheader("Performance dashboard")
    try:
        settings = repo.get_journal_settings()
    except RuntimeError:
        st.info("Configure journal settings before viewing reports.")
        return

    render_manual_sync_button(repo, key="dashboard-manual-sync")

    today = date.today()
    dashboard_service = DashboardService(repo)
    period = st.radio("Report period", ["This month", "All time", "Custom"], horizontal=True)
    if period == "This month":
        start_date, end_date = today.replace(day=1), today
    elif period == "All time":
        start_date, end_date = dashboard_service.earliest_trade_date() or today, today
    else:
        first, second = st.columns(2)
        start_date = first.date_input("Start date", value=today.replace(day=1))
        end_date = second.date_input("End date", value=today)
        if start_date > end_date:
            st.error("Start date must be on or before end date.")
            return

    report = dashboard_service.build_report(start_date=start_date.isoformat(), end_date=end_date.isoformat())
    if report.trade_count == 0:
        st.info("No trades were closed in the selected period.")
        return

    period_label = f"{start_date.strftime('%d %b')} – {end_date.strftime('%d %b %Y')}"
    st.markdown(f'<div class="dashboard-period">{period_label} · {report.trade_count} closed trades</div>', unsafe_allow_html=True)
    metrics = st.columns(5)
    metrics[0].metric("Balance", "—" if report.ending_balance is None else format_currency(report.ending_balance, settings.base_currency))
    metrics[1].metric("Balance growth", "—" if report.balance_growth_percent is None else f"{format_signed(report.balance_growth_percent, '%', 1)}")
    metrics[2].metric("Net P&L", format_currency(report.net_pnl, settings.base_currency))
    metrics[3].metric("Max drawdown", format_currency(-Decimal(report.max_drawdown), settings.base_currency))
    target_label = "Monthly target" if report.target_month_count == 1 else "Period target"
    metrics[4].metric(target_label, "—" if report.target_progress is None else f"{format_number(report.target_progress, 1)}%")

    st.markdown("#### Trade quality")
    detail_metrics = st.columns(5)
    detail_metrics[0].metric("Total R", "Awaiting risk" if report.total_r is None else format_signed(report.total_r, "R"))
    detail_metrics[1].metric("Win rate", f"{format_number(report.win_rate, 1)}%")
    detail_metrics[2].metric("Profit factor", "No losses" if report.profit_factor is None else format_number(report.profit_factor, 2))
    detail_metrics[3].metric("Expectancy", "—" if report.expectancy is None else format_currency(report.expectancy, settings.base_currency))
    detail_metrics[4].metric("Worst day", "—" if report.worst_day is None else format_currency(report.worst_day, settings.base_currency))
    if report.r_trade_count < report.trade_count:
        st.caption(f"R is based on {report.r_trade_count:,} of {report.trade_count:,} trades with an effective planned risk.")
    else:
        target_context = "monthly target" if report.target_month_count == 1 else f"{report.target_month_count} calendar-month target"
        st.caption(
            f"All {report.trade_count:,} trades have an effective risk value. "
            f"{target_context.title()}: {format_currency(report.target_amount or '0', settings.base_currency)}."
        )
    if report.starting_balance is None:
        st.caption("Set an opening account balance in Settings to enable the balance curve, balance growth, and drawdown percentage.")
    else:
        st.caption(
            f"Current drawdown: {format_currency(-Decimal(report.current_drawdown), settings.base_currency)}"
            + (f" ({format_number(report.current_drawdown_percent, 1)}%)" if report.current_drawdown_percent is not None else "")
            + f" · Balance at period start: {format_currency(report.starting_balance, settings.base_currency)}."
        )

    chart_view = st.radio("Chart view", ["Daily", "Per trade"], horizontal=True, key="dashboard_chart_view")
    cumulative = pd.DataFrame([item.__dict__ for item in report.cumulative])
    per_trade = pd.DataFrame([item.__dict__ for item in report.per_trade])
    daily = pd.DataFrame([item.__dict__ for item in report.daily])
    strategies = pd.DataFrame([item.__dict__ for item in report.by_strategy])
    cumulative["cumulative_pnl"] = cumulative["cumulative_pnl"].astype(float)
    cumulative["balance"] = pd.to_numeric(cumulative["balance"], errors="coerce")
    cumulative["drawdown"] = cumulative["drawdown"].astype(float)
    per_trade["cumulative_pnl"] = per_trade["cumulative_pnl"].astype(float)
    per_trade["balance"] = pd.to_numeric(per_trade["balance"], errors="coerce")
    per_trade["drawdown"] = per_trade["drawdown"].astype(float)
    per_trade["net_pnl"] = per_trade["net_pnl"].astype(float)
    per_trade["trade_label"] = per_trade.apply(
        lambda item: f"Trade {item.sequence} · {item.symbol} · #{item.position_id or '—'}",
        axis=1,
    )
    per_trade["hover_label"] = per_trade.apply(
        lambda item: f"{item.trade_label}<br>Closed {item.exit_time}",
        axis=1,
    )
    daily["net_pnl"] = daily["net_pnl"].astype(float)
    strategies["net_pnl"] = strategies["net_pnl"].astype(float)

    if chart_view == "Daily":
        timeline = cumulative.assign(hover_label=cumulative["date"])
        timeline_x = timeline["date"]
        curve_column = "balance" if report.ending_balance is not None else "cumulative_pnl"
        curve_title = "Balance curve" if report.ending_balance is not None else "Equity curve · P&L"
        drawdown_title = "Daily drawdown from peak"
        pnl_data = daily.assign(hover_label=daily["date"])
        pnl_x = pnl_data["date"]
        pnl_title = "Daily P&L"
    else:
        timeline = per_trade
        timeline_x = timeline["exit_time"]
        curve_column = "balance" if report.ending_balance is not None else "cumulative_pnl"
        curve_title = "Balance after each closed trade" if report.ending_balance is not None else "Equity after each closed trade"
        drawdown_title = "Post-close drawdown by trade"
        pnl_data = per_trade
        pnl_x = pnl_data["exit_time"]
        pnl_title = "Per-trade P&L"

    left, right = st.columns(2)
    with left:
        pnl_figure = go.Figure(
            go.Scatter(
                x=timeline_x,
                y=timeline[curve_column],
                customdata=timeline["hover_label"],
                mode="lines+markers",
                line=dict(color="#147d64", width=3),
                marker=dict(color="#147d64", size=7, line=dict(color="#fffdf8", width=1.5)),
                fill=None if report.ending_balance is not None else "tozeroy",
                fillcolor="rgba(20, 125, 100, 0.10)" if report.ending_balance is None else None,
                hovertemplate=f"%{{customdata}}<br><b>{settings.base_currency} %{{y:,.2f}}</b><extra></extra>",
            )
        )
        pnl_figure.update_layout(title=curve_title)
        st.plotly_chart(style_chart(pnl_figure, yaxis_title=settings.base_currency), width="stretch", config={"displayModeBar": False})
    with right:
        drawdown_figure = go.Figure(
            go.Scatter(
                x=timeline_x,
                y=-timeline["drawdown"],
                customdata=timeline[["hover_label", "drawdown"]],
                mode="lines+markers",
                line=dict(color="#b42318", width=3),
                marker=dict(color="#b42318", size=7, line=dict(color="#ffffff", width=1.5)),
                fill="tozeroy",
                fillcolor="rgba(180, 35, 24, 0.12)",
                hovertemplate=f"%{{customdata[0]}}<br><b>−{settings.base_currency} %{{customdata[1]:,.2f}}</b><extra></extra>",
            )
        )
        drawdown_figure.update_layout(title=drawdown_title)
        st.plotly_chart(style_chart(drawdown_figure, yaxis_title=f"{settings.base_currency} drawdown"), width="stretch", config={"displayModeBar": False})

    left, right = st.columns(2)
    with left:
        bar_colours = ["#147d64" if value >= 0 else "#b84745" for value in pnl_data["net_pnl"]]
        daily_figure = go.Figure(
            go.Bar(
                x=pnl_x,
                y=pnl_data["net_pnl"],
                customdata=pnl_data["hover_label"],
                marker_color=bar_colours,
                marker_line_width=0,
                hovertemplate=f"%{{customdata}}<br><b>{settings.base_currency} %{{y:,.2f}}</b><extra></extra>",
            )
        )
        daily_figure.update_layout(title=pnl_title)
        st.plotly_chart(style_chart(daily_figure, yaxis_title=settings.base_currency), width="stretch", config={"displayModeBar": False})
    with right:
        strategies = strategies.sort_values("net_pnl", ascending=False)
        strategy_figure = go.Figure(
            go.Bar(
                x=strategies["strategy"],
                y=strategies["net_pnl"],
                marker_color=["#147d64" if value >= 0 else "#b84745" for value in strategies["net_pnl"]],
                marker_line_width=0,
                hovertemplate=f"%{{x}}<br><b>{settings.base_currency} %{{y:,.2f}}</b><extra></extra>",
            )
        )
        strategy_figure.update_layout(title="Strategy P&L")
        st.plotly_chart(style_chart(strategy_figure, yaxis_title=settings.base_currency), width="stretch", config={"displayModeBar": False})

    if chart_view == "Per trade":
        st.markdown("#### Closed-trade detail")
        trade_table = pd.DataFrame(
            {
                "Closed": per_trade["exit_time"],
                "Position": [f"#{position_id}" if position_id else "—" for position_id in per_trade["position_id"]],
                "Symbol": per_trade["symbol"],
                f"P&L ({settings.base_currency})": [format_currency(value, settings.base_currency) for value in per_trade["net_pnl"]],
                "Result R": ["—" if value is None else format_signed(value, "R") for value in per_trade["result_r"]],
                "Post-close drawdown": [format_currency(-Decimal(value), settings.base_currency) for value in per_trade["drawdown"]],
            }
        )
        if report.ending_balance is not None:
            trade_table["Balance"] = [format_currency(value, settings.base_currency) for value in per_trade["balance"]]
        st.dataframe(trade_table, hide_index=True, width="stretch")
        st.caption("Drawdown is measured after each trade closes. It does not represent MT5 floating or intra-trade drawdown.")

    st.subheader("Strategy results and backtest context")
    strategy_table = pd.DataFrame(
        {
            "Strategy": strategies["strategy"],
            f"Live P&L ({settings.base_currency})": [format_currency(value, settings.base_currency) for value in strategies["net_pnl"]],
            "Live total R": ["—" if value is None else format_signed(value, "R") for value in strategies["total_r"]],
            "Backtest trades": strategies["backtest_trade_count"].fillna("—"),
            "Backtest win rate": ["—" if value is None else f"{format_number(value, 1)}%" for value in strategies["backtest_win_rate"]],
            "Backtest expectancy": ["—" if value is None else format_signed(value, "R") for value in strategies["backtest_expectancy_r"]],
            "Backtest net R": ["—" if value is None else format_signed(value, "R") for value in strategies["backtest_net_r"]],
        }
    )
    st.dataframe(strategy_table, hide_index=True, width="stretch")


def main() -> None:
    st.set_page_config(page_title="Trading Journal", page_icon="📈", layout="wide")
    apply_application_style()
    st.title("Trading Journal")
    st.caption("Local-only journal with read-only MT5 imports.")
    repo = repository()
    page = st.sidebar.radio("Workspace", ["Dashboard", "Settings"])
    if page == "Dashboard":
        monitor_mt5_exports(repo)
        render_auto_sync_notice()
    if page == "Dashboard":
        render_dashboard(repo)
    else:
        render_settings(repo)


if __name__ == "__main__":
    main()
