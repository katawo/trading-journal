"""Deterministic scoring and monitoring for post-trade three-pillar reviews."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from trading_journal.infrastructure.sqlite_repository import (
    AccountRiskPolicyView,
    ClosedTradeReviewItem,
    PostTradeAssessmentView,
    SQLiteJournalRepository,
    StrategyEvidenceSnapshot,
    StrategyProfileView,
)


PILLAR_NAMES = {"psychology": "Psychology", "risk": "Risk management", "system": "Trading system"}

ROADMAP_ITEMS: dict[str, dict[int, tuple[tuple[str, str], ...]]] = {
    "psychology": {
        1: (("triggers", "Name personal triggers and stop conditions"), ("behaviour_rules", "Define no-revenge and no-FOMO rules")),
        2: (("tracking", "Track psychology during structured practice"), ("patterns", "Document recurring triggers and corrections")),
        3: (("execution", "Complete 20 post-trade reviews with no critical behaviour breach"),),
        4: (("review", "Maintain 30 post-trade reviews"),),
        5: (("hypothesis", "Record one behavioural improvement hypothesis and result"),),
    },
    "risk": {
        1: (("policy", "Define account risk limits and position-sizing rule"), ("hard_rules", "Document stop and daily-stop rules")),
        2: (("simulation", "Test 20 risk calculations or simulated trades"),),
        3: (("execution", "Complete 20 post-trade reviews without a critical risk breach"),),
        4: (("review", "Maintain 30 reviewed trades with risk metrics"),),
        5: (("hypothesis", "Record one isolated risk-policy improvement and result"),),
    },
    "system": {
        1: (("rules", "Define market, context, entry, invalidation, target, and no-trade rules"), ("examples", "Document valid and invalid setup examples")),
        2: (("backtest", "Record 100+ backtest trades with positive expectancy after costs"),),
        3: (("execution", "Complete 20 post-trade reviews using only valid setups"),),
        4: (("review", "Maintain 30 reviewed trades with live metrics"),),
        5: (("hypothesis", "Record one isolated system-change hypothesis and result"),),
    },
}


def _decimal_text(value: Decimal) -> str:
    text = format(value.normalize(), "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


@dataclass(frozen=True)
class RiskSnapshot:
    configured: bool
    state: str
    daily_r: str | None
    weekly_r: str | None
    current_drawdown_percent: str | None
    max_drawdown_percent: str | None
    consecutive_losses: int | None
    message: str


@dataclass(frozen=True)
class PillarScore:
    pillar: str
    score: str | None
    reviewed_count: int
    unreviewed_count: int
    reviewed_total: int
    unreviewed_total: int
    auto_reviewed_count: int
    auto_reviewed_total: int
    hard_block: bool
    detail: str
    scope: str


@dataclass(frozen=True)
class PillarRoadmapStatus:
    pillar: str
    completed_items: int
    total_items: int
    current_level: int
    can_complete_current_level: bool
    gate: str


@dataclass(frozen=True)
class AutoRiskEvidence:
    state: str
    detail: str
    specific_preset_sl_amount: str | None
    real_loss_sl_amount: str | None
    live_account_balance_sl_amount: str | None
    risk_basis: str
    initial_reward_amount: str | None
    initial_rr: str | None
    observed_stop_widened: bool | None
    policy_version: int | None

    @property
    def source_amount(self) -> str | None:
        return self.specific_preset_sl_amount or self.real_loss_sl_amount or self.live_account_balance_sl_amount


@dataclass(frozen=True)
class TradeProcessScore:
    """Calculated evidence for one imported trade; only a saved review creates a full three-pillar score."""

    account_id: int
    trade_id: int
    exit_time: str
    assessment_state: str
    psychology_score: str | None
    risk_score: str | None
    system_score: str | None
    overall_score: str | None
    psychology_hard_block: bool
    risk_hard_block: bool
    system_hard_block: bool
    auto_risk: AutoRiskEvidence
    policy_risk_amount: str | None
    actual_risk_amount: str | None
    mapped_strategy: StrategyProfileView | None


class FrameworkService:
    """Advisory monitoring over imported closed trades and their post-trade reviews."""

    def __init__(self, repository: SQLiteJournalRepository) -> None:
        self._repository = repository

    def risk_snapshot(self, account_id: int, *, now: datetime | None = None) -> RiskSnapshot:
        policy = self._repository.get_active_risk_policy(account_id)
        if policy is None:
            return RiskSnapshot(False, "unconfigured", None, None, None, None, None, "Save an account risk policy to monitor this account.")
        funded_capital = self._repository.get_account_funded_capital(account_id)
        if funded_capital is None:
            return RiskSnapshot(False, "unconfigured", None, None, None, None, None, "Set this account's funded capital to monitor risk.")
        live_account_balance = self._repository.get_latest_mt5_balance(account_id)

        timestamp = now or datetime.now(timezone.utc)
        try:
            reporting_timezone = ZoneInfo(self._repository.get_journal_settings().reporting_timezone)
        except (RuntimeError, ZoneInfoNotFoundError):
            reporting_timezone = timezone.utc
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        current_date = timestamp.astimezone(reporting_timezone).date()
        week_start = current_date - timedelta(days=current_date.weekday())

        reviews = {item.assessment.trade_id: item.assessment for item in self._repository.list_post_trade_assessment_outcomes(account_id)}
        trades = sorted(
            self._repository.list_closed_trades_for_review(account_id),
            key=lambda item: (self._trade_datetime(item.exit_time, reporting_timezone), item.id),
        )
        policy_ids = {assessment.risk_policy_id for assessment in reviews.values() if assessment.risk_policy_id is not None}
        policy_ids.update(item.auto_risk_policy_id for item in trades if item.auto_risk_policy_id is not None)
        policies = {policy_id: saved for policy_id in policy_ids if (saved := self._repository.get_risk_policy(policy_id)) is not None}
        scored = []
        for item in trades:
            assessment = reviews.get(item.id)
            risk_policy = self._risk_policy_for_trade(assessment, item, policies, policy)
            risk_amount = self._risk_amount(
                assessment,
                item,
                self._standard_risk_amount(funded_capital, risk_policy),
                live_account_balance,
            )
            scored.append(
                (
                    self._trade_date(item.exit_time, reporting_timezone),
                    Decimal(item.net_pnl),
                    Decimal(item.net_pnl) / risk_amount,
                )
            )
        daily_r = sum((result for trade_date, _, result in scored if trade_date == current_date), Decimal("0"))
        weekly_r = sum((result for trade_date, _, result in scored if week_start <= trade_date <= current_date), Decimal("0"))

        balance = Decimal(funded_capital)
        peak = balance
        current_drawdown_percent = Decimal("0")
        max_drawdown_percent = Decimal("0")
        for _, net_pnl, _ in scored:
            balance += net_pnl
            peak = max(peak, balance)
            current_drawdown_percent = Decimal("0") if peak <= 0 else max((peak - balance) / peak * Decimal("100"), Decimal("0"))
            max_drawdown_percent = max(max_drawdown_percent, current_drawdown_percent)
        consecutive_losses = 0
        for _, net_pnl, _ in reversed(scored):
            if net_pnl < 0:
                consecutive_losses += 1
            else:
                break

        daily_usage = max(-daily_r, Decimal("0")) / Decimal(policy.daily_loss_limit_r)
        weekly_usage = max(-weekly_r, Decimal("0")) / Decimal(policy.weekly_loss_limit_r)
        drawdown_usage = max_drawdown_percent / Decimal(policy.max_drawdown_percent)
        loss_usage = Decimal(consecutive_losses) / Decimal(policy.max_consecutive_losses)
        highest_usage = max(daily_usage, weekly_usage, drawdown_usage, loss_usage)
        pending = len(trades) - len(reviews)
        suffix = (
            f" {pending} closed position(s) still need a full review; their R uses Specific preset SL, then Real-loss SL, then Live-account-balance SL when available, otherwise the account's standard 1R."
            if pending
            else ""
        )
        if highest_usage >= 1:
            state = "stop"
            message = "A completed-trade risk hard limit is reached. Stop and complete a review." + suffix
        elif highest_usage >= Decimal("0.8"):
            state = "caution"
            message = "Completed-trade risk is in the yellow zone. Review performance before the next session." + suffix
        else:
            state = "clear"
            message = "Completed-trade risk is within the configured limits." + suffix
        return RiskSnapshot(
            True,
            state,
            _decimal_text(daily_r),
            _decimal_text(weekly_r),
            _decimal_text(current_drawdown_percent),
            _decimal_text(max_drawdown_percent),
            consecutive_losses,
            message,
        )

    def trade_process_scores(self, account_id: int) -> tuple[TradeProcessScore, ...]:
        """Return imported evidence, Risk-only auto-reviews, and saved full reviews."""
        reviews = {item.assessment.trade_id: item.assessment for item in self._repository.list_post_trade_assessment_outcomes(account_id)}
        trades = self._repository.list_closed_trades_for_review(account_id)
        active_policy = self._repository.get_active_risk_policy(account_id)
        policy_ids = {review.risk_policy_id for review in reviews.values() if review.risk_policy_id is not None}
        policy_ids.update(trade.auto_risk_policy_id for trade in trades if trade.auto_risk_policy_id is not None)
        if active_policy is not None:
            policy_ids.add(active_policy.id)
        policies = {policy_id: policy for policy_id in policy_ids if (policy := self._repository.get_risk_policy(policy_id)) is not None}
        strategies_by_magic = {
            magic_number: profile
            for profile in self._repository.list_strategy_profiles()
            for magic_number in profile.magic_numbers
        }
        funded_capital = self._repository.get_account_funded_capital(account_id)
        live_account_balance = self._repository.get_latest_mt5_balance(account_id)
        scores = [
            self._trade_process_score(
                account_id,
                trade,
                reviews.get(trade.id),
                policies,
                strategies_by_magic,
                funded_capital,
                active_policy,
                live_account_balance,
            )
            for trade in trades
        ]
        return tuple(sorted(scores, key=lambda item: (item.exit_time, item.trade_id)))

    def pillar_scores(self, account_id: int) -> tuple[PillarScore, ...]:
        trader_scores = self._trader_trade_process_scores()
        account_scores = self.trade_process_scores(account_id)
        return (
            self._aggregate_pillar_score("psychology", trader_scores, "Trader-wide"),
            self._aggregate_pillar_score("risk", account_scores, "Selected account", account_id=account_id),
            self._aggregate_pillar_score("system", trader_scores, "Trader-wide"),
        )

    def roadmap_status(self, account_id: int) -> tuple[PillarRoadmapStatus, ...]:
        evidence = {(item.pillar, item.level, item.item_key): item for item in self._repository.list_pillar_roadmap_evidence(account_id)}
        scores = {item.pillar: item for item in self.pillar_scores(account_id)}
        statuses: list[PillarRoadmapStatus] = []
        for pillar, levels in ROADMAP_ITEMS.items():
            total = sum(len(items) for items in levels.values())
            completed = sum(
                1
                for level, items in levels.items()
                for key, _ in items
                if evidence.get((pillar, level, key)) and evidence[(pillar, level, key)].completed
            )
            current_level = 1
            for level, items in levels.items():
                complete = all(evidence.get((pillar, level, key)) and evidence[(pillar, level, key)].completed for key, _ in items)
                if not complete:
                    current_level = level
                    break
                current_level = min(level + 1, 5)
            score = scores[pillar]
            if completed == total:
                can_complete_current_level = False
                gate = "All roadmap evidence is complete. Continue collecting reviewed evidence."
            elif current_level == 3 and (score.reviewed_total < 20 or score.hard_block):
                can_complete_current_level = False
                gate = "Needs 20 post-trade reviews and no critical breach."
            elif current_level == 4 and (score.reviewed_total < 30 or score.hard_block):
                can_complete_current_level = False
                gate = "Needs 30 post-trade reviews for measurable rolling evidence."
            else:
                can_complete_current_level = True
                gate = "Complete the current level evidence items."
            statuses.append(PillarRoadmapStatus(pillar, completed, total, current_level, can_complete_current_level, gate))
        return tuple(statuses)

    def _trader_trade_process_scores(self) -> tuple[TradeProcessScore, ...]:
        scores = [score for account in self._repository.list_mt5_accounts() for score in self.trade_process_scores(account.id)]
        return tuple(sorted(scores, key=lambda item: (item.exit_time, item.trade_id)))

    def _aggregate_pillar_score(
        self,
        pillar: str,
        scores: tuple[TradeProcessScore, ...],
        scope: str,
        *,
        account_id: int | None = None,
    ) -> PillarScore:
        recent_imports = scores[-20:]
        all_reviewed = [score for score in scores if score.assessment_state == "reviewed"]
        # Automatic SL sources prove only a Risk amount. They never substitute for
        # a full three-pillar assessment or roadmap evidence.
        all_auto_reviewed = [score for score in scores if score.assessment_state == "auto_reviewed"] if pillar == "risk" else []
        reviewed_sample = all_reviewed[-20:]
        reviewed_count = sum(score.assessment_state == "reviewed" for score in recent_imports)
        auto_reviewed_count = sum(score.assessment_state == "auto_reviewed" for score in recent_imports) if pillar == "risk" else 0
        unreviewed_count = len(recent_imports) - reviewed_count - auto_reviewed_count
        if not scores:
            return PillarScore(pillar, None, 0, 0, 0, 0, 0, 0, False, "No imported closed trades yet.", scope)
        if not reviewed_sample:
            detail = "No reviewed evidence yet. Imported MT5 facts are shown separately and never become a process score on their own."
            if all_auto_reviewed:
                detail += f" {len(all_auto_reviewed)} trade(s) are auto-reviewed for Risk sizing only; they do not create Process or roadmap evidence."
            return PillarScore(
                pillar,
                None,
                reviewed_count,
                unreviewed_count,
                0,
                len(scores) - len(all_auto_reviewed),
                auto_reviewed_count,
                len(all_auto_reviewed),
                False,
                detail,
                scope,
            )
        score_values = [Decimal(getattr(score, f"{pillar}_score")) for score in reviewed_sample]
        hard_block = any(getattr(score, f"{pillar}_hard_block") for score in reviewed_sample)
        if pillar == "risk" and account_id is not None:
            hard_block = hard_block or self.risk_snapshot(account_id).state == "stop"
        detail = f"Latest {len(reviewed_sample)} reviewed trade(s); latest imports: {reviewed_count} reviewed, {unreviewed_count} awaiting review."
        if auto_reviewed_count:
            detail += f" {auto_reviewed_count} Risk-only auto-review(s)."
        if len(all_reviewed) < 10:
            detail += " Roadmap evidence still requires 10+ reviewed trades."
        return PillarScore(
            pillar,
            _decimal_text(sum(score_values, Decimal("0")) / len(score_values)),
            reviewed_count,
            unreviewed_count,
            len(all_reviewed),
            len(scores) - len(all_reviewed) - len(all_auto_reviewed),
            auto_reviewed_count,
            len(all_auto_reviewed),
            hard_block,
            detail,
            scope,
        )

    def _trade_process_score(
        self,
        account_id: int,
        trade: ClosedTradeReviewItem,
        assessment: PostTradeAssessmentView | None,
        policies: dict[int, AccountRiskPolicyView],
        strategies_by_magic: dict[str, StrategyProfileView],
        funded_capital: str | None,
        active_policy: AccountRiskPolicyView | None,
        live_account_balance: str | None,
    ) -> TradeProcessScore:
        auto_risk = self._auto_risk_evidence(trade, policies, funded_capital, active_policy, live_account_balance)
        risk_policy = self._risk_policy_for_trade(assessment, trade, policies, active_policy)
        policy_risk_amount = (
            _decimal_text(self._maximum_risk_amount(funded_capital, risk_policy))
            if funded_capital is not None and risk_policy is not None
            else None
        )
        actual_risk_amount = (
            assessment.declared_actual_risk_amount
            if assessment is not None and assessment.declared_actual_risk_amount is not None
            else auto_risk.source_amount
        )
        mapped_strategy = strategies_by_magic.get(trade.entry_magic_number or "")
        if assessment is None:
            if auto_risk.risk_basis in {"specific_preset_sl", "real_loss_sl", "live_account_balance_sl"} and auto_risk.state in {
                "within_policy",
                "over_policy",
            }:
                automatic_risk_score = (
                    "100"
                    if auto_risk.state == "within_policy"
                    else "0"
                    if auto_risk.state == "over_policy"
                    else None
                )
                return TradeProcessScore(
                    account_id,
                    trade.id,
                    trade.exit_time,
                    "auto_reviewed",
                    None,
                    automatic_risk_score,
                    None,
                    None,
                    False,
                    False,
                    False,
                    auto_risk,
                    policy_risk_amount,
                    actual_risk_amount,
                    mapped_strategy,
                )
            return TradeProcessScore(
                account_id,
                trade.id,
                trade.exit_time,
                "not_scored",
                None,
                None,
                None,
                None,
                False,
                False,
                False,
                auto_risk,
                policy_risk_amount,
                actual_risk_amount,
                mapped_strategy,
            )

        psychology_score, psychology_hard_block = self._reviewed_psychology_score(assessment)
        risk_score, risk_hard_block = self._reviewed_risk_score(
            assessment,
            trade,
            policies,
            funded_capital,
            live_account_balance,
            active_policy,
        )
        system_score, system_hard_block = self._reviewed_system_score(assessment)
        overall_score = (psychology_score + risk_score + system_score) / Decimal("3")
        return TradeProcessScore(
            account_id,
            trade.id,
            trade.exit_time,
            "reviewed",
            _decimal_text(psychology_score),
            _decimal_text(risk_score),
            _decimal_text(system_score),
            _decimal_text(overall_score),
            psychology_hard_block,
            risk_hard_block,
            system_hard_block,
            auto_risk,
            policy_risk_amount,
            actual_risk_amount,
            mapped_strategy,
        )

    def _reviewed_psychology_score(self, assessment: PostTradeAssessmentView) -> tuple[Decimal, bool]:
        breach = assessment.impulse_violation or assessment.revenge_violation or assessment.emotional_size_violation
        behaviour = Decimal("0") if breach else Decimal("100")
        correction = Decimal("100") if not breach or assessment.corrective_action else Decimal("0")
        return behaviour * Decimal("0.7") + correction * Decimal("0.3"), assessment.revenge_violation or assessment.emotional_size_violation

    def _reviewed_risk_score(
        self,
        assessment: PostTradeAssessmentView,
        trade: ClosedTradeReviewItem,
        policies: dict[int, AccountRiskPolicyView],
        funded_capital: str | None,
        live_account_balance: str | None,
        active_policy: AccountRiskPolicyView | None,
    ) -> tuple[Decimal, bool]:
        policy = self._risk_policy_for_trade(assessment, trade, policies, active_policy)
        fallback_risk = self._standard_risk_amount(funded_capital, policy)
        risk_amount = self._risk_amount(assessment, trade, fallback_risk, live_account_balance)
        size_compliant = bool(
            policy
            and funded_capital is not None
            and risk_amount > 0
            and risk_amount <= self._maximum_risk_amount(funded_capital, policy)
        )
        size_score = Decimal("100") if size_compliant else Decimal("0")
        stop_score = Decimal("0") if assessment.stop_widened_violation else Decimal("100")
        return size_score * Decimal("0.7") + stop_score * Decimal("0.3"), assessment.stop_widened_violation

    @staticmethod
    def _risk_policy_for_trade(
        assessment: PostTradeAssessmentView | None,
        trade: ClosedTradeReviewItem,
        policies: dict[int, AccountRiskPolicyView],
        active_policy: AccountRiskPolicyView | None,
    ) -> AccountRiskPolicyView | None:
        policy_id = (assessment.risk_policy_id if assessment is not None else None) or trade.auto_risk_policy_id
        return policies.get(policy_id) if policy_id is not None and policy_id in policies else active_policy

    @staticmethod
    def _standard_risk_amount(funded_capital: str | None, policy: AccountRiskPolicyView | None) -> Decimal:
        if funded_capital is None or policy is None:
            return Decimal("0")
        return Decimal(funded_capital) * Decimal(policy.standard_risk_per_trade_percent) / Decimal("100")

    @staticmethod
    def _maximum_risk_amount(funded_capital: str | None, policy: AccountRiskPolicyView | None) -> Decimal:
        if funded_capital is None or policy is None:
            return Decimal("0")
        return Decimal(funded_capital) * Decimal(policy.maximum_risk_per_trade_percent) / Decimal("100")

    def _reviewed_system_score(self, assessment: PostTradeAssessmentView) -> tuple[Decimal, bool]:
        valid_score = Decimal("100") if assessment.system_confirmed else Decimal("0")
        evidence_score = Decimal("100") if self._strategy_eligible(assessment.strategy_snapshot) else Decimal("0")
        return valid_score * Decimal("0.7") + evidence_score * Decimal("0.3"), not assessment.system_confirmed

    @staticmethod
    def _strategy_eligible(strategy: StrategyProfileView | StrategyEvidenceSnapshot | None) -> bool:
        return bool(
            strategy
            and strategy.description
            and strategy.backtest_start_date
            and strategy.backtest_end_date
            and strategy.backtest_trade_count is not None
            and strategy.backtest_trade_count >= 100
            and strategy.backtest_expectancy_r is not None
            and Decimal(strategy.backtest_expectancy_r) > 0
        )

    @staticmethod
    def _risk_amount(
        assessment,
        trade: ClosedTradeReviewItem,
        fallback: Decimal,
        live_account_balance: str | None,
    ) -> Decimal:  # type: ignore[no-untyped-def]
        if assessment is not None and assessment.declared_actual_risk_amount is not None:
            return Decimal(assessment.declared_actual_risk_amount)
        if (specific_preset_sl_amount := FrameworkService._specific_preset_sl_amount(trade)) is not None:
            return Decimal(specific_preset_sl_amount)
        if (real_loss_sl_amount := FrameworkService._real_loss_sl_amount(trade)) is not None:
            return Decimal(real_loss_sl_amount)
        if (live_account_balance_sl_amount := FrameworkService._live_account_balance_sl_amount(trade, live_account_balance)) is not None:
            return Decimal(live_account_balance_sl_amount)
        return fallback

    @staticmethod
    def _real_loss_sl_amount(trade: ClosedTradeReviewItem) -> str | None:
        """Use a realised loss as a derived Real-loss SL only when MT5 has no initial risk."""
        if FrameworkService._specific_preset_sl_amount(trade) is not None:
            return None
        net_pnl = Decimal(trade.net_pnl)
        return _decimal_text(-net_pnl) if net_pnl < 0 else None

    @staticmethod
    def _specific_preset_sl_amount(trade: ClosedTradeReviewItem) -> str | None:
        """Return usable initial risk, treating malformed legacy values as unavailable."""
        if trade.initial_risk_amount is None:
            return None
        try:
            risk_amount = Decimal(trade.initial_risk_amount)
        except ArithmeticError:
            return None
        return _decimal_text(risk_amount) if risk_amount.is_finite() and risk_amount > 0 else None

    @staticmethod
    def _live_account_balance_sl_amount(trade: ClosedTradeReviewItem, live_account_balance: str | None) -> str | None:
        """Use current MT5 balance only for a profitable trade that had no recorded entry SL."""
        if (
            FrameworkService._specific_preset_sl_amount(trade) is not None
            or trade.entry_stop_price is not None
            or Decimal(trade.net_pnl) <= 0
            or live_account_balance is None
        ):
            return None
        balance = Decimal(live_account_balance)
        return _decimal_text(balance) if balance > 0 else None

    def _auto_risk_evidence(
        self,
        trade: ClosedTradeReviewItem,
        policies: dict[int, AccountRiskPolicyView],
        funded_capital: str | None,
        active_policy: AccountRiskPolicyView | None,
        live_account_balance: str | None,
    ) -> AutoRiskEvidence:
        observed_stop_widened = self._observed_stop_widened(trade)
        specific_preset_sl_amount = self._specific_preset_sl_amount(trade)
        initial_rr = None
        if specific_preset_sl_amount is not None and trade.initial_reward_amount is not None:
            reward_amount = Decimal(trade.initial_reward_amount)
            if reward_amount.is_finite() and reward_amount > 0:
                initial_rr = _decimal_text(reward_amount / Decimal(specific_preset_sl_amount))
        # Schema-v2 imports retain the policy active at import time. Older
        # exports have no policy snapshot, so evaluate automatic Risk evidence against
        # today's account policy and make that policy version visible.
        policy = policies.get(trade.auto_risk_policy_id) or active_policy
        real_loss_sl_amount = self._real_loss_sl_amount(trade)
        live_account_balance_sl_amount = self._live_account_balance_sl_amount(trade, live_account_balance)
        risk_amount = specific_preset_sl_amount or real_loss_sl_amount or live_account_balance_sl_amount
        risk_basis = (
            "specific_preset_sl"
            if specific_preset_sl_amount is not None
            else "real_loss_sl"
            if real_loss_sl_amount is not None
            else "live_account_balance_sl"
            if live_account_balance_sl_amount is not None
            else "unavailable"
        )
        if risk_amount is None:
            return AutoRiskEvidence(
                "unavailable",
                "No usable SL source is available. MT5 needs a calculable preset SL, a realised loss, or a live account-balance snapshot for a profitable no-SL trade.",
                None,
                None,
                None,
                risk_basis,
                trade.initial_reward_amount,
                initial_rr,
                observed_stop_widened,
                None if policy is None else policy.version,
            )
        if policy is None or funded_capital is None:
            return AutoRiskEvidence(
                "unavailable",
                "Set funded capital and save a Risk policy before automatic risk compliance can be evaluated.",
                specific_preset_sl_amount,
                real_loss_sl_amount,
                live_account_balance_sl_amount,
                risk_basis,
                trade.initial_reward_amount,
                initial_rr,
                observed_stop_widened,
                None if policy is None else policy.version,
            )
        allowed_risk = self._maximum_risk_amount(funded_capital, policy)
        state = "within_policy" if Decimal(risk_amount) <= allowed_risk else "over_policy"
        source = {
            "specific_preset_sl": "Specific preset SL (MT5-calculated initial risk)",
            "real_loss_sl": "Real-loss SL (derived from realised loss; not an MT5-recorded initial stop)",
            "live_account_balance_sl": "Live-account-balance SL (current MT5 balance; not an MT5-recorded stop)",
        }[risk_basis]
        comparison = "is within" if state == "within_policy" else "exceeds"
        detail = f"{source} {risk_amount} {comparison} policy v{policy.version} limit {_decimal_text(allowed_risk)}."
        return AutoRiskEvidence(
            state,
            detail,
            specific_preset_sl_amount,
            real_loss_sl_amount,
            live_account_balance_sl_amount,
            risk_basis,
            trade.initial_reward_amount,
            initial_rr,
            observed_stop_widened,
            policy.version,
        )

    @staticmethod
    def _observed_stop_widened(trade: ClosedTradeReviewItem) -> bool | None:
        if trade.entry_stop_price is None or trade.close_stop_price is None:
            return None
        if trade.direction == "long":
            return Decimal(trade.close_stop_price) < Decimal(trade.entry_stop_price)
        return Decimal(trade.close_stop_price) > Decimal(trade.entry_stop_price)

    @staticmethod
    def _trade_date(value: str, reporting_timezone: ZoneInfo) -> date:
        return FrameworkService._trade_datetime(value, reporting_timezone).date()

    @staticmethod
    def _trade_datetime(value: str, reporting_timezone: ZoneInfo) -> datetime:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            return timestamp.replace(tzinfo=reporting_timezone)
        return timestamp.astimezone(reporting_timezone)
