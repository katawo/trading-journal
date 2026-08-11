from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal

from trading_journal.application.reporting_time import reporting_date, reporting_datetime
from trading_journal.infrastructure.sqlite_repository import SQLiteJournalRepository, TradePerformanceItem, normalize_strategy_name


def _decimal_string(value: Decimal) -> str:
    if value == 0:
        return "0"
    normalized = format(value.normalize(), "f")
    return normalized.rstrip("0").rstrip(".") if "." in normalized else normalized


@dataclass(frozen=True)
class CumulativePoint:
    date: str
    cumulative_pnl: str
    cumulative_r: str | None
    balance: str | None
    drawdown: str
    drawdown_percent: str | None


@dataclass(frozen=True)
class TradePerformancePoint:
    sequence: int
    exit_time: str
    position_id: str | None
    symbol: str
    net_pnl: str
    result_r: str | None
    strategy: str | None
    cumulative_pnl: str
    balance: str | None
    drawdown: str
    drawdown_percent: str | None


@dataclass(frozen=True)
class DailyPerformance:
    date: str
    net_pnl: str
    net_r: str | None


@dataclass(frozen=True)
class StrategyPerformance:
    strategy: str
    net_pnl: str
    total_r: str | None
    backtest_trade_count: int | None = None
    backtest_win_rate: str | None = None
    backtest_expectancy_r: str | None = None
    backtest_net_r: str | None = None


@dataclass(frozen=True)
class DashboardReport:
    trade_count: int
    net_pnl: str
    total_r: str | None
    r_trade_count: int
    win_rate: str
    starting_balance: str | None
    ending_balance: str | None
    balance_growth_percent: str | None
    max_drawdown: str
    max_drawdown_percent: str | None
    current_drawdown: str
    current_drawdown_percent: str | None
    worst_day: str | None
    profit_factor: str | None
    expectancy: str | None
    average_win: str | None
    average_loss: str | None
    cumulative: list[CumulativePoint]
    per_trade: list[TradePerformancePoint]
    daily: list[DailyPerformance]
    by_strategy: list[StrategyPerformance]


class DashboardService:
    def __init__(self, repository: SQLiteJournalRepository) -> None:
        self._repository = repository

    def build_report(self, *, start_date: str, end_date: str, account_id: int | None = None) -> DashboardReport:
        settings = self._repository.get_journal_settings()
        account_id = self._single_account_id(account_id)
        time_basis = settings.reporting_time_basis
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)
        dated_trades = [(trade, self._trade_date(trade, time_basis)) for trade in self._repository.list_trade_performance(account_id)]
        trades = [trade for trade, trade_date in dated_trades if start <= trade_date <= end]

        pnl_total = sum((Decimal(trade.net_pnl) for trade in trades), Decimal("0"))
        r_values = [Decimal(trade.result_r) for trade in trades if trade.result_r is not None]
        r_total = sum(r_values, Decimal("0")) if r_values else None
        wins = sum(Decimal(trade.net_pnl) > 0 for trade in trades)
        win_rate = Decimal(wins * 100) / Decimal(len(trades)) if trades else Decimal("0")
        winning_pnls = [Decimal(trade.net_pnl) for trade in trades if Decimal(trade.net_pnl) > 0]
        losing_pnls = [Decimal(trade.net_pnl) for trade in trades if Decimal(trade.net_pnl) < 0]
        gross_profit = sum(winning_pnls, Decimal("0"))
        gross_loss = -sum(losing_pnls, Decimal("0"))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else None
        expectancy = pnl_total / len(trades) if trades else None
        average_win = gross_profit / len(winning_pnls) if winning_pnls else None
        average_loss = sum(losing_pnls, Decimal("0")) / len(losing_pnls) if losing_pnls else None
        daily: dict[str, Decimal] = {}
        daily_r: dict[str, Decimal] = {}
        strategies: dict[str, tuple[Decimal, Decimal | None]] = {}
        profiles_by_name = {normalize_strategy_name(profile.name): profile for profile in self._repository.list_strategy_profiles()}
        for trade in trades:
            trade_date = self._trade_date(trade, time_basis).isoformat()
            pnl = Decimal(trade.net_pnl)
            daily[trade_date] = daily.get(trade_date, Decimal("0")) + pnl
            if trade.result_r is not None:
                daily_r[trade_date] = daily_r.get(trade_date, Decimal("0")) + Decimal(trade.result_r)

            strategy = trade.strategy or "Untagged"
            strategy_pnl, strategy_r = strategies.get(strategy, (Decimal("0"), None))
            next_r = strategy_r
            if trade.result_r is not None:
                next_r = (strategy_r or Decimal("0")) + Decimal(trade.result_r)
            strategies[strategy] = (strategy_pnl + pnl, next_r)

        cumulative: list[CumulativePoint] = []
        cumulative_pnl = Decimal("0")
        cumulative_r = Decimal("0")
        has_r = False
        account_baseline = self._repository.get_account_opening_balance(account_id)
        configured_starting_balance = None if account_baseline is None else Decimal(account_baseline)
        prior_pnl = sum((Decimal(trade.net_pnl) for trade, trade_date in dated_trades if trade_date < start), Decimal("0"))
        starting_balance = None if configured_starting_balance is None else configured_starting_balance + prior_pnl

        per_trade: list[TradePerformancePoint] = []
        trade_cumulative_pnl = Decimal("0")
        trade_peak_pnl = Decimal("0")
        trade_peak_balance = starting_balance
        for sequence, trade in enumerate(trades, start=1):
            trade_pnl = Decimal(trade.net_pnl)
            trade_cumulative_pnl += trade_pnl
            trade_peak_pnl = max(trade_peak_pnl, trade_cumulative_pnl)
            trade_drawdown = trade_peak_pnl - trade_cumulative_pnl
            trade_balance = None if starting_balance is None else starting_balance + trade_cumulative_pnl
            trade_drawdown_percent = None
            if trade_balance is not None and trade_peak_balance is not None:
                trade_peak_balance = max(trade_peak_balance, trade_balance)
                trade_drawdown_percent = trade_drawdown * Decimal("100") / trade_peak_balance
            per_trade.append(
                TradePerformancePoint(
                    sequence=sequence,
                    exit_time=reporting_datetime(trade.exit_time, trade.server_utc_offset_minutes, time_basis).isoformat(),
                    position_id=trade.position_id,
                    symbol=trade.symbol,
                    net_pnl=_decimal_string(trade_pnl),
                    result_r=trade.result_r,
                    strategy=trade.strategy,
                    cumulative_pnl=_decimal_string(trade_cumulative_pnl),
                    balance=None if trade_balance is None else _decimal_string(trade_balance),
                    drawdown=_decimal_string(trade_drawdown),
                    drawdown_percent=None if trade_drawdown_percent is None else _decimal_string(trade_drawdown_percent),
                )
            )

        peak_pnl = Decimal("0")
        peak_balance = starting_balance
        max_drawdown = Decimal("0")
        max_drawdown_percent: Decimal | None = None
        current_drawdown = Decimal("0")
        current_drawdown_percent: Decimal | None = None
        for trade_date, pnl in sorted(daily.items()):
            cumulative_pnl += pnl
            if trade_date in daily_r:
                cumulative_r += daily_r[trade_date]
                has_r = True
            peak_pnl = max(peak_pnl, cumulative_pnl)
            current_drawdown = peak_pnl - cumulative_pnl
            max_drawdown = max(max_drawdown, current_drawdown)
            balance = None if starting_balance is None else starting_balance + cumulative_pnl
            if balance is not None and peak_balance is not None:
                peak_balance = max(peak_balance, balance)
                current_drawdown_percent = current_drawdown * Decimal("100") / peak_balance
                if max_drawdown == current_drawdown:
                    max_drawdown_percent = current_drawdown_percent
            cumulative.append(
                CumulativePoint(
                    trade_date,
                    _decimal_string(cumulative_pnl),
                    _decimal_string(cumulative_r) if has_r else None,
                    None if balance is None else _decimal_string(balance),
                    _decimal_string(current_drawdown),
                    None if current_drawdown_percent is None else _decimal_string(current_drawdown_percent),
                )
            )

        ending_balance = None if starting_balance is None else starting_balance + pnl_total
        balance_growth_percent = None if starting_balance is None else pnl_total * Decimal("100") / starting_balance
        worst_day = min(daily.values()) if daily else None

        return DashboardReport(
            trade_count=len(trades),
            net_pnl=_decimal_string(pnl_total),
            total_r=None if r_total is None else _decimal_string(r_total),
            r_trade_count=len(r_values),
            win_rate=_decimal_string(win_rate),
            starting_balance=None if starting_balance is None else _decimal_string(starting_balance),
            ending_balance=None if ending_balance is None else _decimal_string(ending_balance),
            balance_growth_percent=None if balance_growth_percent is None else _decimal_string(balance_growth_percent),
            max_drawdown=_decimal_string(max_drawdown),
            max_drawdown_percent=None if max_drawdown_percent is None else _decimal_string(max_drawdown_percent),
            current_drawdown=_decimal_string(current_drawdown),
            current_drawdown_percent=None if current_drawdown_percent is None else _decimal_string(current_drawdown_percent),
            worst_day=None if worst_day is None else _decimal_string(worst_day),
            profit_factor=None if profit_factor is None else _decimal_string(profit_factor),
            expectancy=None if expectancy is None else _decimal_string(expectancy),
            average_win=None if average_win is None else _decimal_string(average_win),
            average_loss=None if average_loss is None else _decimal_string(average_loss),
            cumulative=cumulative,
            per_trade=per_trade,
            daily=[DailyPerformance(day, _decimal_string(pnl), _decimal_string(daily_r[day]) if day in daily_r else None) for day, pnl in sorted(daily.items())],
            by_strategy=[
                StrategyPerformance(
                    strategy,
                    _decimal_string(pnl),
                    None if total_r is None else _decimal_string(total_r),
                    backtest_trade_count=profiles_by_name[normalize_strategy_name(strategy)].backtest_trade_count if normalize_strategy_name(strategy) in profiles_by_name else None,
                    backtest_win_rate=profiles_by_name[normalize_strategy_name(strategy)].backtest_win_rate if normalize_strategy_name(strategy) in profiles_by_name else None,
                    backtest_expectancy_r=profiles_by_name[normalize_strategy_name(strategy)].backtest_expectancy_r if normalize_strategy_name(strategy) in profiles_by_name else None,
                    backtest_net_r=profiles_by_name[normalize_strategy_name(strategy)].backtest_net_r if normalize_strategy_name(strategy) in profiles_by_name else None,
                )
                for strategy, (pnl, total_r) in sorted(strategies.items())
            ],
        )

    def earliest_trade_date(self, account_id: int | None = None) -> date | None:
        account_id = self._single_account_id(account_id)
        settings = self._repository.get_journal_settings()
        dates = [self._trade_date(trade, settings.reporting_time_basis) for trade in self._repository.list_trade_performance(account_id)]
        return min(dates) if dates else None

    def current_report_date(self, account_id: int) -> date:
        """Return today in the same clock used to group this account's trades."""
        settings = self._repository.get_journal_settings()
        account = next((item for item in self._repository.list_mt5_accounts() if item.id == account_id), None)
        offset = 0 if account is None or account.latest_server_utc_offset_minutes is None else account.latest_server_utc_offset_minutes
        return reporting_datetime(datetime.now(timezone.utc).isoformat(), offset, settings.reporting_time_basis).date()

    @staticmethod
    def _trade_date(trade: TradePerformanceItem, reporting_time_basis: str) -> date:
        return reporting_date(trade.exit_time, trade.server_utc_offset_minutes, reporting_time_basis)

    def _single_account_id(self, account_id: int | None) -> int:
        if account_id is not None:
            return account_id
        accounts = self._repository.list_mt5_accounts()
        if len(accounts) != 1:
            raise ValueError("Select one account before building a monetary report")
        return accounts[0].id
