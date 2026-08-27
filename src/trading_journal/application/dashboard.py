from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Callable

from trading_journal.application.reporting_time import reporting_date, reporting_datetime
from trading_journal.infrastructure.sqlite_repository import (
    SQLiteJournalRepository,
    TradePerformanceItem,
    normalize_strategy_name,
)


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
    logical_trade_id: int
    display_label: str
    position_ids: tuple[str, ...]
    position_count: int
    exit_time: str
    position_id: str | None
    symbol: str
    direction: str
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
    backtest_verified: bool = False


@dataclass(frozen=True)
class PerformanceBreakdown:
    label: str
    trade_count: int
    win_count: int
    loss_count: int
    breakeven_count: int
    win_rate: str
    net_pnl: str
    total_r: str | None
    r_trade_count: int
    expectancy_r: str | None
    profit_factor: str | None


@dataclass(frozen=True)
class ConcentrationItem:
    label: str
    trade_count: int
    amount: str
    share_percent: str
    cumulative_share_percent: str


@dataclass(frozen=True)
class ConcentrationSide:
    gross_amount: str
    group_count: int
    target_group_count: int
    target_group_percent: str | None
    items: list[ConcentrationItem]


@dataclass(frozen=True)
class ConcentrationBreakdown:
    dimension: str
    profit: ConcentrationSide
    loss: ConcentrationSide


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
    expectancy_r: str | None
    gross_profit: str
    gross_loss: str
    average_win: str | None
    average_loss: str | None
    payoff_ratio: str | None
    win_count: int
    loss_count: int
    breakeven_count: int
    active_day_count: int
    profitable_day_count: int
    profitable_day_rate: str
    best_day: str | None
    average_day: str | None
    recovery_factor: str | None
    current_streak_outcome: str | None
    current_streak_count: int
    longest_win_streak: int
    longest_loss_streak: int
    cumulative: list[CumulativePoint]
    per_trade: list[TradePerformancePoint]
    daily: list[DailyPerformance]
    by_strategy: list[StrategyPerformance]
    by_symbol: list[PerformanceBreakdown]
    by_direction: list[PerformanceBreakdown]
    concentration: list[ConcentrationBreakdown]


class DashboardService:
    def __init__(self, repository: SQLiteJournalRepository) -> None:
        self._repository = repository

    def build_report(
        self,
        *,
        account_id: int | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> DashboardReport:
        settings = self._repository.get_journal_settings()
        account_id = self._single_account_id(account_id)
        time_basis = settings.reporting_time_basis
        all_trades = self._repository.list_trade_performance(account_id)
        start = None if start_date is None else date.fromisoformat(start_date)
        end = None if end_date is None else date.fromisoformat(end_date)
        if (start is None) != (end is None):
            raise ValueError("Start date and end date must be provided together")
        trades = [
            trade
            for trade in all_trades
            if start is None or end is None or start <= self._trade_date(trade, time_basis) <= end
        ]

        # Every completed-trade fact uses the same current logical-trade
        # chronology. Regrouping therefore recalculates the complete report.
        pnl_total = sum((Decimal(trade.net_pnl) for trade in trades), Decimal("0"))
        r_values = [Decimal(trade.result_r) for trade in trades if trade.result_r is not None]
        r_total = sum(r_values, Decimal("0")) if r_values else None
        wins = sum(Decimal(trade.net_pnl) > 0 for trade in trades)
        losses = sum(Decimal(trade.net_pnl) < 0 for trade in trades)
        breakevens = len(trades) - wins - losses
        win_rate = Decimal(wins * 100) / Decimal(len(trades)) if trades else Decimal("0")
        winning_pnls = [Decimal(trade.net_pnl) for trade in trades if Decimal(trade.net_pnl) > 0]
        losing_pnls = [Decimal(trade.net_pnl) for trade in trades if Decimal(trade.net_pnl) < 0]
        gross_profit = sum(winning_pnls, Decimal("0"))
        gross_loss = -sum(losing_pnls, Decimal("0"))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else None
        expectancy = pnl_total / len(trades) if trades else None
        expectancy_r = r_total / len(r_values) if r_total is not None else None
        average_win = gross_profit / len(winning_pnls) if winning_pnls else None
        average_loss = sum(losing_pnls, Decimal("0")) / len(losing_pnls) if losing_pnls else None
        payoff_ratio = average_win / -average_loss if average_win is not None and average_loss is not None else None
        current_streak_outcome, current_streak_count, longest_win_streak, longest_loss_streak = self._streaks(trades)
        daily: dict[str, Decimal] = {}
        daily_r: dict[str, Decimal] = {}
        strategies: dict[str, tuple[Decimal, Decimal | None]] = {}
        profiles_by_name = {normalize_strategy_name(profile.name): profile for profile in self._repository.list_strategy_profiles()}
        for trade in trades:
            pnl = Decimal(trade.net_pnl)
            trade_date = self._trade_date(trade, time_basis).isoformat()
            daily[trade_date] = daily.get(trade_date, Decimal("0")) + pnl
            if trade.result_r is not None:
                daily_r[trade_date] = daily_r.get(trade_date, Decimal("0")) + Decimal(trade.result_r)
            strategy = trade.strategy or "Untagged"
            strategy_pnl, strategy_r = strategies.get(strategy, (Decimal("0"), None))
            next_r = strategy_r
            if trade.result_r is not None:
                next_r = (strategy_r or Decimal("0")) + Decimal(trade.result_r)
            strategies[strategy] = (strategy_pnl + pnl, next_r)

        concentration = [
            ConcentrationBreakdown(
                dimension="trade",
                profit=self._concentration_side(trades, lambda trade: f"LT-{trade.logical_trade_id} · {trade.display_label}", positive=True),
                loss=self._concentration_side(trades, lambda trade: f"LT-{trade.logical_trade_id} · {trade.display_label}", positive=False),
            ),
            ConcentrationBreakdown(
                dimension="symbol",
                profit=self._concentration_side(trades, lambda trade: trade.symbol, positive=True),
                loss=self._concentration_side(trades, lambda trade: trade.symbol, positive=False),
            ),
            ConcentrationBreakdown(
                dimension="strategy",
                profit=self._concentration_side(trades, lambda trade: trade.strategy or "Untagged", positive=True),
                loss=self._concentration_side(trades, lambda trade: trade.strategy or "Untagged", positive=False),
            ),
        ]

        cumulative: list[CumulativePoint] = []
        cumulative_pnl = Decimal("0")
        cumulative_r = Decimal("0")
        has_r = False
        account_baseline = self._repository.get_account_opening_balance(account_id)
        configured_starting_balance = None if account_baseline is None else Decimal(account_baseline)
        prior_pnl = sum(
            (Decimal(trade.net_pnl) for trade in all_trades if start is not None and self._trade_date(trade, time_basis) < start),
            Decimal("0"),
        )
        starting_balance = None if configured_starting_balance is None else configured_starting_balance + prior_pnl

        # Lifetime account drawdown is assessed at every logical-trade close.
        raw_cumulative_pnl = Decimal("0")
        raw_peak_pnl = Decimal("0")
        account_balance = starting_balance
        account_peak_balance = starting_balance
        account_max_drawdown = Decimal("0")
        account_max_drawdown_percent: Decimal | None = None
        account_current_drawdown = Decimal("0")
        account_current_drawdown_percent: Decimal | None = None
        for trade in trades:
            raw_cumulative_pnl += Decimal(trade.net_pnl)
            raw_peak_pnl = max(raw_peak_pnl, raw_cumulative_pnl)
            account_current_drawdown = raw_peak_pnl - raw_cumulative_pnl
            account_max_drawdown = max(account_max_drawdown, account_current_drawdown)
            if account_balance is not None and account_peak_balance is not None:
                account_balance += Decimal(trade.net_pnl)
                account_peak_balance = max(account_peak_balance, account_balance)
                account_current_drawdown_percent = account_current_drawdown * Decimal("100") / account_peak_balance
                account_max_drawdown_percent = max(account_max_drawdown_percent or Decimal("0"), account_current_drawdown_percent)

        per_trade: list[TradePerformancePoint] = []
        trade_cumulative_pnl = Decimal("0")
        trade_peak_pnl = Decimal("0")
        trade_balance = starting_balance
        trade_peak_balance = starting_balance
        for sequence, trade in enumerate(trades, start=1):
            trade_pnl = Decimal(trade.net_pnl)
            trade_cumulative_pnl += trade_pnl
            trade_peak_pnl = max(trade_peak_pnl, trade_cumulative_pnl)
            trade_drawdown = trade_peak_pnl - trade_cumulative_pnl
            trade_drawdown_percent = None
            if trade_balance is not None and trade_peak_balance is not None:
                trade_balance += trade_pnl
                trade_peak_balance = max(trade_peak_balance, trade_balance)
                trade_drawdown_percent = trade_drawdown * Decimal("100") / trade_peak_balance
            per_trade.append(
                TradePerformancePoint(
                    sequence=sequence,
                    logical_trade_id=trade.logical_trade_id,
                    display_label=trade.display_label,
                    position_ids=trade.position_ids,
                    position_count=trade.position_count,
                    exit_time=reporting_datetime(trade.exit_time, trade.server_utc_offset_minutes, time_basis).isoformat(),
                    position_id=trade.position_id,
                    symbol=trade.symbol,
                    direction=trade.direction,
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
        daily_current_drawdown = Decimal("0")
        daily_current_drawdown_percent: Decimal | None = None
        for trade_date, pnl in sorted(daily.items()):
            cumulative_pnl += pnl
            if trade_date in daily_r:
                cumulative_r += daily_r[trade_date]
                has_r = True
            peak_pnl = max(peak_pnl, cumulative_pnl)
            daily_current_drawdown = peak_pnl - cumulative_pnl
            balance = None if starting_balance is None else starting_balance + cumulative_pnl
            if balance is not None and peak_balance is not None:
                peak_balance = max(peak_balance, balance)
                daily_current_drawdown_percent = daily_current_drawdown * Decimal("100") / peak_balance
            cumulative.append(
                CumulativePoint(
                    trade_date,
                    _decimal_string(cumulative_pnl),
                    _decimal_string(cumulative_r) if has_r else None,
                    None if balance is None else _decimal_string(balance),
                    _decimal_string(daily_current_drawdown),
                    None if daily_current_drawdown_percent is None else _decimal_string(daily_current_drawdown_percent),
                )
            )

        ending_balance = None if starting_balance is None else starting_balance + pnl_total
        balance_growth_percent = None if starting_balance is None else pnl_total * Decimal("100") / starting_balance
        worst_day = min(daily.values()) if daily else None
        best_day = max(daily.values()) if daily else None
        average_day = pnl_total / len(daily) if daily else None
        profitable_day_count = sum(value > 0 for value in daily.values())
        profitable_day_rate = Decimal(profitable_day_count * 100) / Decimal(len(daily)) if daily else Decimal("0")
        recovery_factor = pnl_total / account_max_drawdown if account_max_drawdown > 0 else None

        return DashboardReport(
            trade_count=len(trades),
            net_pnl=_decimal_string(pnl_total),
            total_r=None if r_total is None else _decimal_string(r_total),
            r_trade_count=len(r_values),
            win_rate=_decimal_string(win_rate),
            starting_balance=None if starting_balance is None else _decimal_string(starting_balance),
            ending_balance=None if ending_balance is None else _decimal_string(ending_balance),
            balance_growth_percent=None if balance_growth_percent is None else _decimal_string(balance_growth_percent),
            max_drawdown=_decimal_string(account_max_drawdown),
            max_drawdown_percent=None if account_max_drawdown_percent is None else _decimal_string(account_max_drawdown_percent),
            current_drawdown=_decimal_string(account_current_drawdown),
            current_drawdown_percent=None if account_current_drawdown_percent is None else _decimal_string(account_current_drawdown_percent),
            worst_day=None if worst_day is None else _decimal_string(worst_day),
            profit_factor=None if profit_factor is None else _decimal_string(profit_factor),
            expectancy=None if expectancy is None else _decimal_string(expectancy),
            expectancy_r=None if expectancy_r is None else _decimal_string(expectancy_r),
            gross_profit=_decimal_string(gross_profit),
            gross_loss=_decimal_string(gross_loss),
            average_win=None if average_win is None else _decimal_string(average_win),
            average_loss=None if average_loss is None else _decimal_string(average_loss),
            payoff_ratio=None if payoff_ratio is None else _decimal_string(payoff_ratio),
            win_count=wins,
            loss_count=losses,
            breakeven_count=breakevens,
            active_day_count=len(daily),
            profitable_day_count=profitable_day_count,
            profitable_day_rate=_decimal_string(profitable_day_rate),
            best_day=None if best_day is None else _decimal_string(best_day),
            average_day=None if average_day is None else _decimal_string(average_day),
            recovery_factor=None if recovery_factor is None else _decimal_string(recovery_factor),
            current_streak_outcome=current_streak_outcome,
            current_streak_count=current_streak_count,
            longest_win_streak=longest_win_streak,
            longest_loss_streak=longest_loss_streak,
            cumulative=cumulative,
            per_trade=per_trade,
            daily=[DailyPerformance(day, _decimal_string(pnl), _decimal_string(daily_r[day]) if day in daily_r else None) for day, pnl in sorted(daily.items())],
            by_strategy=[
                StrategyPerformance(
                    strategy,
                    _decimal_string(pnl),
                    None if total_r is None else _decimal_string(total_r),
                    backtest_verified=profiles_by_name[normalize_strategy_name(strategy)].backtest_verified if normalize_strategy_name(strategy) in profiles_by_name else False,
                )
                for strategy, (pnl, total_r) in sorted(strategies.items())
            ],
            by_symbol=self._performance_breakdowns(trades, lambda trade: trade.symbol),
            by_direction=self._performance_breakdowns(trades, lambda trade: trade.direction),
            concentration=concentration,
        )

    @staticmethod
    def _streaks(trades: list[TradePerformanceItem]) -> tuple[str | None, int, int, int]:
        current_outcome: str | None = None
        current_count = 0
        win_streak = 0
        loss_streak = 0
        longest_win_streak = 0
        longest_loss_streak = 0
        for trade in trades:
            pnl = Decimal(trade.net_pnl)
            outcome = "win" if pnl > 0 else "loss" if pnl < 0 else "breakeven"
            if outcome == current_outcome:
                current_count += 1
            else:
                current_outcome = outcome
                current_count = 1
            if outcome == "win":
                win_streak += 1
                loss_streak = 0
                longest_win_streak = max(longest_win_streak, win_streak)
            elif outcome == "loss":
                loss_streak += 1
                win_streak = 0
                longest_loss_streak = max(longest_loss_streak, loss_streak)
            else:
                win_streak = 0
                loss_streak = 0
        return current_outcome, current_count, longest_win_streak, longest_loss_streak

    @staticmethod
    def _performance_breakdowns(
        trades: list[TradePerformanceItem],
        label_for: Callable[[TradePerformanceItem], str],
    ) -> list[PerformanceBreakdown]:
        grouped: dict[str, list[TradePerformanceItem]] = {}
        for trade in trades:
            grouped.setdefault(label_for(trade), []).append(trade)
        results: list[PerformanceBreakdown] = []
        for label, members in sorted(grouped.items(), key=lambda item: item[0].casefold()):
            pnls = [Decimal(item.net_pnl) for item in members]
            winning_pnls = [value for value in pnls if value > 0]
            losing_pnls = [value for value in pnls if value < 0]
            win_count = len(winning_pnls)
            loss_count = len(losing_pnls)
            breakeven_count = len(members) - win_count - loss_count
            gross_profit = sum(winning_pnls, Decimal("0"))
            gross_loss = -sum(losing_pnls, Decimal("0"))
            r_values = [Decimal(item.result_r) for item in members if item.result_r is not None]
            total_r = sum(r_values, Decimal("0")) if r_values else None
            results.append(
                PerformanceBreakdown(
                    label=label,
                    trade_count=len(members),
                    win_count=win_count,
                    loss_count=loss_count,
                    breakeven_count=breakeven_count,
                    win_rate=_decimal_string(Decimal(win_count * 100) / Decimal(len(members))),
                    net_pnl=_decimal_string(sum(pnls, Decimal("0"))),
                    total_r=None if total_r is None else _decimal_string(total_r),
                    r_trade_count=len(r_values),
                    expectancy_r=None if total_r is None else _decimal_string(total_r / len(r_values)),
                    profit_factor=None if gross_loss == 0 else _decimal_string(gross_profit / gross_loss),
                )
            )
        return results

    def earliest_trade_date(self, account_id: int | None = None) -> date | None:
        account_id = self._single_account_id(account_id)
        settings = self._repository.get_journal_settings()
        dates = [
            self._trade_date(trade, settings.reporting_time_basis)
            for trade in self._repository.list_trade_performance(account_id)
        ]
        return min(dates) if dates else None

    def current_report_date(self, account_id: int) -> date:
        """Return today in the same clock used to group this account's trades."""
        settings = self._repository.get_journal_settings()
        account = next((item for item in self._repository.list_mt5_accounts() if item.id == account_id), None)
        offset = 0 if account is None or account.latest_server_utc_offset_minutes is None else account.latest_server_utc_offset_minutes
        return reporting_datetime(datetime.now(timezone.utc).isoformat(), offset, settings.reporting_time_basis).date()

    @staticmethod
    def _concentration_side(
        trades: list[TradePerformanceItem],
        label_for: Callable[[TradePerformanceItem], str],
        *,
        positive: bool,
    ) -> ConcentrationSide:
        """Summarize gross profit or gross loss without using signed net P&L.

        A net-P&L denominator produces misleading Pareto shares when wins and
        losses offset each other. Each side instead uses its own gross total.
        """
        amounts: dict[str, Decimal] = {}
        counts: dict[str, int] = {}
        for trade in trades:
            pnl = Decimal(trade.net_pnl)
            if (positive and pnl <= 0) or (not positive and pnl >= 0):
                continue
            label = label_for(trade)
            amount = pnl if positive else -pnl
            amounts[label] = amounts.get(label, Decimal("0")) + amount
            counts[label] = counts.get(label, 0) + 1

        gross_amount = sum(amounts.values(), Decimal("0"))
        ordered = sorted(amounts.items(), key=lambda item: (-item[1], item[0].casefold()))
        cumulative = Decimal("0")
        target_group_count = 0
        items: list[ConcentrationItem] = []
        for index, (label, amount) in enumerate(ordered, start=1):
            cumulative += amount
            share = amount * Decimal("100") / gross_amount
            cumulative_share = cumulative * Decimal("100") / gross_amount
            if target_group_count == 0 and cumulative_share >= Decimal("80"):
                target_group_count = index
            items.append(
                ConcentrationItem(
                    label=label,
                    trade_count=counts[label],
                    amount=_decimal_string(amount),
                    share_percent=_decimal_string(share),
                    cumulative_share_percent=_decimal_string(cumulative_share),
                )
            )
        group_count = len(items)
        return ConcentrationSide(
            gross_amount=_decimal_string(gross_amount),
            group_count=group_count,
            target_group_count=target_group_count,
            target_group_percent=None if group_count == 0 else _decimal_string(Decimal(target_group_count * 100) / Decimal(group_count)),
            items=items,
        )

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
