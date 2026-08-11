"""Deterministic three-pillar scoring for completed, imported MT5 trades."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from trading_journal.application.reporting_time import reporting_date, reporting_datetime

from trading_journal.infrastructure.sqlite_repository import (
    PSYCHOLOGY_CRITERIA,
    RISK_CRITERIA,
    SYSTEM_CRITERIA,
    AccountRiskPolicyView,
    ClosedTradeReviewItem,
    FrameworkRuleSettingsView,
    PostTradeAssessmentView,
    SQLiteJournalRepository,
    StrategyEvidenceSnapshot,
    StrategyProfileView,
)


PILLAR_NAMES = {"psychology": "Psychology", "risk": "Risk management", "system": "Trading system"}
GRADE_VALUES = {"pass": Decimal("100"), "partial": Decimal("50"), "fail": Decimal("0")}
TRADE_WEIGHTS = {
    "psychology": (("rule_adherence", Decimal("0.35")), ("impulse_control", Decimal("0.25")), ("emotional_control", Decimal("0.20")), ("patience_discipline", Decimal("0.20"))),
    "risk": (("policy_adherence", Decimal("0.35")), ("position_size_accuracy", Decimal("0.20")), ("stop_discipline", Decimal("0.25")), ("exposure_limit_compliance", Decimal("0.20"))),
    "system": (("setup_validity", Decimal("0.30")), ("context_alignment", Decimal("0.20")), ("entry_fidelity", Decimal("0.20")), ("invalidation_fidelity", Decimal("0.15")), ("management_exit_fidelity", Decimal("0.15"))),
}
PERIOD_WEIGHTS = {
    "psychology": (Decimal("0.35"), Decimal("0.25"), Decimal("0.20"), Decimal("0.20")),
    "risk": (Decimal("0.35"), Decimal("0.25"), Decimal("0.25"), Decimal("0.15")),
    "system": (Decimal("0.20"), Decimal("0.20"), Decimal("0.15"), Decimal("0.20"), Decimal("0.25")),
}
CRITICAL_VIOLATIONS = {
    "psychology": frozenset({"revenge", "emotional_sizing", "post_loss_reset", "oversized_revenge"}),
    "risk": frozenset({"daily_limit", "weekly_limit", "drawdown_limit", "open_exposure", "correlation_exposure", "oversized_revenge", "stop_widened", "shutdown_breach"}),
    "system": frozenset({"mandatory_setup_absent"}),
}

ROADMAP_ITEMS: dict[str, dict[int, tuple[tuple[str, str], ...]]] = {
    "psychology": {
        1: (("triggers", "Document triggers and stop conditions"), ("behaviour_rules", "Document no-revenge and no-chase rules")),
        2: (("practice", "Record structured practice and recurring patterns"),),
        3: (("execution", "20 full reviews, score at least 70, no active hard failure"),),
        4: (("measure", "30 full reviews, current period review, score at least 80"),),
        5: (("hypothesis", "Record one behavioural hypothesis, result, and keep/reject decision"),),
    },
    "risk": {
        1: (("policy", "Define account risk policy and hard limits"), ("sizing", "Document the position-sizing method")),
        2: (("test", "Record risk-calculation or simulation evidence"),),
        3: (("execution", "20 full reviews, score at least 70, no active hard failure"),),
        4: (("measure", "30 full reviews, current period review, score at least 80"),),
        5: (("hypothesis", "Record one risk-policy hypothesis, result, and keep/reject decision"),),
    },
    "system": {
        1: (("rules", "Define context, entry, invalidation, exit, and no-trade rules"), ("examples", "Document valid and invalid examples")),
        2: (("backtest", "Record 100+ backtest trades with positive expectancy after costs"),),
        3: (("execution", "20 full reviews, score at least 70, no active hard failure"),),
        4: (("measure", "30 full reviews, current period review, score at least 80"),),
        5: (("hypothesis", "Record one system hypothesis, result, and keep/reject decision"),),
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
class AutoRiskEvidence:
    state: str
    detail: str
    specific_preset_sl_amount: str | None
    real_loss_sl_amount: str | None
    live_account_balance_sl_amount: str | None
    risk_basis: str
    confidence: str
    initial_reward_amount: str | None
    initial_rr: str | None
    observed_stop_widened: bool | None
    policy_version: int | None

    @property
    def source_amount(self) -> str | None:
        return self.specific_preset_sl_amount or self.real_loss_sl_amount or self.live_account_balance_sl_amount


@dataclass(frozen=True)
class TradeProcessScore:
    account_id: int
    trade_id: int
    exit_time: str
    server_utc_offset_minutes: int
    assessment_state: str
    psychology_score: str | None
    risk_score: str | None
    system_score: str | None
    overall_score: str | None
    process_status: str | None
    classification: str | None
    psychology_hard_block: bool
    risk_hard_block: bool
    system_hard_block: bool
    hard_rule_codes: tuple[str, ...]
    violation_codes: tuple[str, ...]
    auto_risk: AutoRiskEvidence
    policy_risk_amount: str | None
    actual_risk_amount: str | None
    mapped_strategy: StrategyProfileView | None


@dataclass(frozen=True)
class PillarScore:
    pillar: str
    score: str | None
    raw_score: str | None
    status: str
    reviewed_total: int
    sample_size: int
    unreviewed_total: int
    automatic_evidence_total: int
    hard_block: bool
    critical_count: int
    component_scores: tuple[tuple[str, str | None], ...]
    detail: str
    scope: str


@dataclass(frozen=True)
class ReadinessAssessment:
    score: str | None
    status: str
    window: int
    detail: str


@dataclass(frozen=True)
class FrameworkAlert:
    severity: str
    code: str
    message: str


@dataclass(frozen=True)
class PeriodReviewStatus:
    cadence: str
    period_start: str
    period_end: str
    due: bool
    reviewed_trades: int


@dataclass(frozen=True)
class PillarRoadmapStatus:
    pillar: str
    completed_items: int
    total_items: int
    current_level: int
    can_complete_current_level: bool
    gate: str


class FrameworkService:
    """Purely advisory calculations over persisted closed-trade reviews."""

    def __init__(self, repository: SQLiteJournalRepository) -> None:
        self._repository = repository

    def trade_process_scores(self, account_id: int) -> tuple[TradeProcessScore, ...]:
        account_scores, _ = self._account_trade_scores(account_id)
        return account_scores

    def pillar_scores(self, account_id: int, *, window: int = 20, as_of: date | None = None) -> tuple[PillarScore, ...]:
        trader_scores = self._scores_through(self._trader_trade_process_scores(), as_of)
        all_account_scores, historical_events = self._account_trade_scores(account_id)
        account_scores = self._scores_through(all_account_scores, as_of)
        return (
            self._period_pillar_score("psychology", trader_scores, window, "Trader-wide"),
            self._period_pillar_score("risk", account_scores, window, "Selected account", historical_events),
            self._period_pillar_score("system", trader_scores, window, "Trader-wide"),
        )

    def readiness(self, account_id: int, *, window: int = 20, as_of: date | None = None) -> ReadinessAssessment:
        scores = self.pillar_scores(account_id, window=window, as_of=as_of)
        if any(score.score is None or score.status == "incomplete" for score in scores):
            return ReadinessAssessment(None, "incomplete", window, f"Needs {window} complete reviews and measurable evidence in every pillar.")
        if any(score.hard_block for score in scores):
            score = min(Decimal(item.score) for item in scores if item.score is not None)
            return ReadinessAssessment(_decimal_text(score), "fail", window, "A hard-rule failure overrides readiness in the selected rolling sample.")
        return ReadinessAssessment(_decimal_text(min(Decimal(score.score) for score in scores if score.score is not None)), "ready", window, "Readiness is the weakest complete pillar.")

    def framework_alerts(
        self,
        account_id: int,
        *,
        now: datetime | None = None,
        include_period_review_due: bool = True,
    ) -> tuple[FrameworkAlert, ...]:
        alerts: list[FrameworkAlert] = []
        snapshot = self.risk_snapshot(account_id, now=now)
        if snapshot.state == "stop":
            alerts.append(FrameworkAlert("critical", "risk_stop", snapshot.message))
        elif snapshot.state == "caution":
            alerts.append(FrameworkAlert("warning", "risk_caution", snapshot.message))
        elif not snapshot.configured:
            alerts.append(FrameworkAlert("info", "risk_unconfigured", snapshot.message))
        as_of = None if now is None else self._current_report_date(now, account_id)
        scores = self.pillar_scores(account_id, as_of=as_of)
        for score in scores:
            if score.hard_block:
                alerts.append(FrameworkAlert("critical", f"{score.pillar}_hard_rule", f"{PILLAR_NAMES[score.pillar]} has a hard-rule failure in the rolling sample."))
            elif score.score is not None and Decimal(score.score) < 70:
                alerts.append(FrameworkAlert("warning", f"{score.pillar}_developing", f"{PILLAR_NAMES[score.pillar]} is below 70 in the rolling sample."))
        if include_period_review_due:
            for cadence in ("weekly", "monthly"):
                status = self.period_review_status(account_id, cadence, now=now)
                if status.due:
                    alerts.append(FrameworkAlert("warning", f"{cadence}_review_due", f"{cadence.capitalize()} review due for {status.period_start} to {status.period_end}."))
        return tuple(alerts)

    def period_review_status(self, account_id: int, cadence: str, *, now: datetime | None = None) -> PeriodReviewStatus:
        current = self._current_report_date(now or datetime.now(timezone.utc), account_id)
        if cadence == "weekly":
            end = current - timedelta(days=current.weekday() + 1)
            start = end - timedelta(days=6)
        elif cadence == "monthly":
            first = current.replace(day=1)
            end = first - timedelta(days=1)
            start = end.replace(day=1)
        else:
            raise ValueError("Period review cadence must be weekly or monthly")
        reviewed = [item for item in self._repository.list_post_trade_assessment_outcomes(account_id) if start <= self._trade_date(item.trade.exit_time, item.trade.server_utc_offset_minutes) <= end]
        existing = self._repository.list_framework_period_reviews(account_id, cadence)
        saved = any(item.period_start == start.isoformat() and item.period_end == end.isoformat() for item in existing)
        return PeriodReviewStatus(cadence, start.isoformat(), end.isoformat(), bool(reviewed) and not saved, len(reviewed))

    def save_period_review(
        self,
        *,
        account_id: int,
        cadence: str,
        review_note: str,
        priority_action: str,
        now: datetime | None = None,
    ) -> None:
        status = self.period_review_status(account_id, cadence, now=now)
        if not status.reviewed_trades:
            raise ValueError("A period review requires at least one complete post-trade assessment in that period")
        period_end = date.fromisoformat(status.period_end)
        scores = self.pillar_scores(account_id, as_of=period_end)
        readiness = self.readiness(account_id, as_of=period_end)
        recurring = self.recurring_issues(account_id, as_of=period_end)
        alerts = self.framework_alerts(
            account_id,
            now=datetime.combine(period_end, datetime.max.time(), tzinfo=timezone.utc),
            include_period_review_due=False,
        )
        self._repository.save_framework_period_review(
            account_id=account_id,
            cadence=cadence,
            period_start=status.period_start,
            period_end=status.period_end,
            psychology_score=next(item.score for item in scores if item.pillar == "psychology"),
            risk_score=next(item.score for item in scores if item.pillar == "risk"),
            system_score=next(item.score for item in scores if item.pillar == "system"),
            readiness_score=readiness.score,
            alert_codes=tuple(item.code for item in alerts),
            recurring_issues=tuple(issue for issue, _ in recurring),
            review_note=review_note,
            priority_action=priority_action,
        )

    def recurring_issues(self, account_id: int, *, window: int = 20, as_of: date | None = None) -> tuple[tuple[str, int], ...]:
        scored = [item for item in self._scores_through(self.trade_process_scores(account_id), as_of) if item.assessment_state == "reviewed"][-window:]
        counter: Counter[str] = Counter(code for item in scored for code in set(item.violation_codes) | set(item.hard_rule_codes))
        return tuple(counter.most_common())

    def rolling_score_trend(self, account_id: int, *, window: int = 20) -> tuple[tuple[str, str | None, str | None, str | None], ...]:
        """Selected-account trend; readiness cards keep the documented wider scopes."""
        reviewed = [item for item in self.trade_process_scores(account_id) if item.assessment_state == "reviewed"]
        points: list[tuple[str, str | None, str | None, str | None]] = []
        for index in range(1, len(reviewed) + 1):
            sample = reviewed[max(0, index - window) : index]
            if not sample:
                continue
            values = []
            for pillar in ("psychology", "risk", "system"):
                numeric = [Decimal(getattr(item, f"{pillar}_score")) for item in sample if getattr(item, f"{pillar}_score") is not None]
                values.append(_decimal_text(sum(numeric, Decimal("0")) / len(numeric)) if numeric else None)
            trade = reviewed[index - 1]
            closed = reporting_datetime(trade.exit_time, trade.server_utc_offset_minutes, self._reporting_time_basis()).isoformat()
            points.append((closed, *values))
        return tuple(points)

    def roadmap_status(self, account_id: int) -> tuple[PillarRoadmapStatus, ...]:
        evidence = {(item.pillar, item.level, item.item_key): item for item in self._repository.list_pillar_roadmap_evidence(account_id)}
        scores = {item.pillar: item for item in self.pillar_scores(account_id)}
        period_reviews = self._repository.list_framework_period_reviews(account_id)
        statuses: list[PillarRoadmapStatus] = []
        for pillar, levels in ROADMAP_ITEMS.items():
            total = sum(len(items) for items in levels.values())
            completed = sum(1 for level, items in levels.items() for key, _ in items if (item := evidence.get((pillar, level, key))) and item.completed)
            current_level = next((level for level, items in levels.items() if not all((item := evidence.get((pillar, level, key))) and item.completed for key, _ in items)), 5)
            score = scores[pillar]
            if completed == total:
                allowed, gate = False, "All framework evidence is complete; continue monitoring the current sample."
            elif current_level == 3:
                allowed = score.reviewed_total >= 20 and score.score is not None and Decimal(score.score) >= 70 and not score.hard_block
                gate = "Needs 20 full reviews, a score of at least 70, and no active hard failure."
            elif current_level == 4:
                allowed = score.reviewed_total >= 30 and score.score is not None and Decimal(score.score) >= 80 and not score.hard_block and bool(period_reviews)
                gate = "Needs 30 full reviews, a score of at least 80, a saved period review, and no active hard failure."
            else:
                allowed, gate = True, "Complete the current evidence item with a note."
            statuses.append(PillarRoadmapStatus(pillar, completed, total, current_level, allowed, gate))
        return tuple(statuses)

    def risk_snapshot(self, account_id: int, *, now: datetime | None = None) -> RiskSnapshot:
        policy = self._repository.get_active_risk_policy(account_id)
        funded = self._repository.get_account_funded_capital(account_id)
        if policy is None or funded is None:
            return RiskSnapshot(False, "unconfigured", None, None, None, None, None, "Set funded capital and save an account Risk policy to monitor this account.")
        events = self._historical_risk_events(account_id)
        current = (now or datetime.now(timezone.utc))
        today = self._current_report_date(current, account_id)
        week_start = today - timedelta(days=today.weekday())
        entries = [entry for entry in events.values() if entry["date"] <= today]
        daily_r = sum((entry["result_r"] for entry in entries if entry["date"] == today), Decimal("0"))
        weekly_r = sum((entry["result_r"] for entry in entries if week_start <= entry["date"] <= today), Decimal("0"))
        last = entries[-1] if entries else None
        drawdown = Decimal("0") if last is None else Decimal(last["drawdown"])
        current_streak = 0 if last is None else int(last["streak"])
        caution = (
            daily_r <= -(Decimal(policy.daily_loss_limit_r) * Decimal("0.8"))
            or weekly_r <= -(Decimal(policy.weekly_loss_limit_r) * Decimal("0.8"))
            or drawdown >= Decimal(policy.max_drawdown_percent) * Decimal("0.8")
            or Decimal(current_streak) >= Decimal(policy.max_consecutive_losses) * Decimal("0.8")
        )
        # Daily and weekly limits expire with their reporting periods. Drawdown
        # and loss streaks remain active only while their current values are at
        # the configured limit. Historical breaches stay in the review record;
        # they must not keep the live advisory state at STOP indefinitely.
        stop = (
            daily_r <= -Decimal(policy.daily_loss_limit_r)
            or weekly_r <= -Decimal(policy.weekly_loss_limit_r)
            or drawdown >= Decimal(policy.max_drawdown_percent)
            or Decimal(current_streak) >= Decimal(policy.max_consecutive_losses)
        )
        state = "stop" if stop else "caution" if caution else "clear"
        pending = sum(item.assessment_state != "reviewed" for item in self._scores_through(self.trade_process_scores(account_id), today))
        message = (
            "Risk limits are clear." if state == "clear" else
            "A completed-trade Risk limit is approaching its threshold." if state == "caution" else
            "A completed-trade Risk limit needs review."
        )
        if pending:
            message += f" {pending} closed position(s) still need a full review."
        return RiskSnapshot(
            True,
            state,
            _decimal_text(daily_r),
            _decimal_text(weekly_r),
            None if last is None else _decimal_text(last["drawdown"]),
            None if last is None else _decimal_text(max(entry["drawdown"] for entry in entries)),
            None if last is None else last["streak"],
            message,
        )

    def _trader_trade_process_scores(self) -> tuple[TradeProcessScore, ...]:
        scores = [score for account in self._repository.list_mt5_accounts() for score in self.trade_process_scores(account.id)]
        return tuple(sorted(scores, key=lambda item: (item.exit_time, item.trade_id)))

    def _scores_through(self, scores: tuple[TradeProcessScore, ...], as_of: date | None) -> tuple[TradeProcessScore, ...]:
        if as_of is None:
            return scores
        return tuple(score for score in scores if self._trade_date(score.exit_time, score.server_utc_offset_minutes) <= as_of)

    def _account_trade_scores(self, account_id: int) -> tuple[tuple[TradeProcessScore, ...], dict[int, dict[str, object]]]:
        trades = sorted(self._repository.list_closed_trades_for_review(account_id), key=lambda item: (item.exit_time, item.id))
        assessments = {item.assessment.trade_id: item.assessment for item in self._repository.list_post_trade_assessment_outcomes(account_id)}
        policies = self._policies_for(trades, assessments, account_id)
        active_policy = self._repository.get_active_risk_policy(account_id)
        funded = self._repository.get_account_funded_capital(account_id)
        live_balance = self._repository.get_latest_mt5_balance(account_id)
        settings = self._repository.get_framework_rule_settings()
        strategies = {magic: profile for profile in self._repository.list_strategy_profiles() for magic in profile.magic_numbers}
        events = self._historical_risk_events_from_context(trades, assessments, policies, active_policy, funded, live_balance)
        scores = tuple(
            self._trade_process_score(account_id, trade, assessments.get(trade.id), policies, strategies, funded, active_policy, live_balance, settings, tuple(events[trade.id]["events"]))
            for trade in trades
        )
        return scores, events

    def _period_pillar_score(
        self,
        pillar: str,
        scores: tuple[TradeProcessScore, ...],
        window: int,
        scope: str,
        historical_events: dict[int, dict[str, object]] | None = None,
    ) -> PillarScore:
        reviewed = [item for item in scores if item.assessment_state == "reviewed"]
        sample = reviewed[-window:]
        automatic = sum(item.assessment_state == "automatic_evidence" for item in scores)
        unreviewed = sum(item.assessment_state == "not_scored" for item in scores)
        if not sample:
            return PillarScore(pillar, None, None, "incomplete", 0, 0, unreviewed, automatic, False, 0, (), "No complete post-trade review evidence yet.", scope)
        components = self._period_components(pillar, sample, historical_events)
        values = [value for _, value in components]
        raw = None if any(value is None for value in values) else sum(
            (value * weight for value, weight in zip(values, PERIOD_WEIGHTS[pillar], strict=True) if value is not None), Decimal("0")
        )
        hard_block = any(getattr(item, f"{pillar}_hard_block") for item in sample)
        critical = sum(1 for item in sample if self._is_critical_violation(pillar, item))
        settings = self._repository.get_framework_rule_settings()
        capped = critical >= settings.repeated_critical_threshold and not self._review_after_last_critical(pillar, sample)
        score = None if raw is None else min(raw, Decimal("59")) if capped else raw
        status = "fail" if hard_block else "caution" if capped else "incomplete" if len(sample) < window else "ready"
        formatted = tuple((name, None if value is None else _decimal_text(Decimal(value))) for name, value in components)
        detail = f"{len(sample)} of {window} complete review(s) in this rolling sample."
        if hard_block:
            detail += " A hard-rule failure overrides the numeric score."
        elif capped:
            detail += " Repeated critical violations cap this pillar at 59 until a period review is saved."
        return PillarScore(pillar, None if score is None else _decimal_text(score), None if raw is None else _decimal_text(raw), status, len(reviewed), len(sample), unreviewed, automatic, hard_block, critical, formatted, detail, scope)

    def _period_components(self, pillar: str, sample: list[TradeProcessScore], historical_events: dict[int, dict[str, object]] | None) -> tuple[tuple[str, Decimal | None], ...]:
        outcomes = self._repository.list_post_trade_assessment_outcomes()
        assessments = {item.assessment.trade_id: item.assessment for item in outcomes}
        pnl_by_trade = {item.trade.id: Decimal(item.trade.net_pnl) for item in outcomes}
        if pillar == "psychology":
            rules = self._average_grade((assessments[item.trade_id] for item in sample), "rule_adherence")
            impulse = self._average_grade((assessments[item.trade_id] for item in sample), "impulse_control")
            emotion = self._average_grade((assessments[item.trade_id] for item in sample), "emotional_control")
            after_loss = self._post_loss_discipline(sample, assessments, pnl_by_trade)
            return (("Rule adherence", rules), ("Impulse control", impulse), ("Emotional control", emotion), ("Post-loss discipline", after_loss))
        if pillar == "risk":
            policy = self._average_grade((assessments[item.trade_id] for item in sample), "policy_adherence")
            stop = self._average_grade((assessments[item.trade_id] for item in sample), "stop_discipline")
            exposure = self._average_grade((assessments[item.trade_id] for item in sample), "exposure_limit_compliance")
            limits = self._risk_limit_component(sample, historical_events)
            return (("Policy adherence", policy), ("Stop discipline", stop), ("Limit compliance", limits), ("Exposure control", exposure))
        setup = self._average_grade((assessments[item.trade_id] for item in sample), "setup_validity")
        execution_values = []
        for item in sample:
            grades = assessments[item.trade_id].criterion_grades
            execution_values.extend(GRADE_VALUES[grades[key]] for key in ("entry_fidelity", "invalidation_fidelity", "management_exit_fidelity"))
        execution = sum(execution_values, Decimal("0")) / len(execution_values) if execution_values else None
        context = self._average_grade((assessments[item.trade_id] for item in sample), "context_alignment")
        evidence_quality = self._strategy_evidence_component(sample, assessments, edge=False)
        edge = self._strategy_evidence_component(sample, assessments, edge=True)
        return (("Setup validity", setup), ("Execution fidelity", execution), ("Context alignment", context), ("Evidence quality", evidence_quality), ("Edge evidence", edge))

    @staticmethod
    def _average_grade(assessment_iter, criterion: str) -> Decimal | None:  # type: ignore[no-untyped-def]
        values = [GRADE_VALUES[assessment.criterion_grades[criterion]] for assessment in assessment_iter]
        return sum(values, Decimal("0")) / len(values) if values else Decimal("100")

    @staticmethod
    def _post_loss_discipline(
        sample: list[TradeProcessScore], assessments: dict[int, PostTradeAssessmentView], pnl_by_trade: dict[int, Decimal]
    ) -> Decimal | None:
        values = []
        ordered = sorted(sample, key=lambda item: (item.exit_time, item.trade_id))
        for previous, current in zip(ordered, ordered[1:], strict=False):
            if previous.account_id != current.account_id or pnl_by_trade.get(previous.trade_id, Decimal("0")) >= 0:
                continue
            grade = assessments[current.trade_id].criterion_grades["impulse_control"]
            values.append(Decimal("0") if "post_loss_reset" in assessments[current.trade_id].violation_codes else GRADE_VALUES[grade])
        return sum(values, Decimal("0")) / len(values) if values else Decimal("100")

    @staticmethod
    def _risk_limit_component(sample: list[TradeProcessScore], events: dict[int, dict[str, object]] | None) -> Decimal | None:
        if events is None:
            return None
        values = [Decimal("0") if events.get(item.trade_id, {}).get("events") else Decimal("100") for item in sample]
        return sum(values, Decimal("0")) / len(values) if values else None

    @staticmethod
    def _strategy_evidence_component(sample: list[TradeProcessScore], assessments: dict[int, PostTradeAssessmentView], *, edge: bool) -> Decimal | None:
        values = []
        for item in sample:
            strategy = assessments[item.trade_id].strategy_snapshot
            documented = bool(strategy.description and strategy.backtest_start_date and strategy.backtest_end_date)
            count = strategy.backtest_trade_count or 0
            expectancy = Decimal(strategy.backtest_expectancy_r) if strategy.backtest_expectancy_r is not None else None
            if edge:
                values.append(Decimal("100") if count >= 100 and expectancy is not None and expectancy > 0 else Decimal("50") if count >= 50 and expectancy is not None and expectancy > 0 else Decimal("0"))
            else:
                values.append(Decimal("100") if documented and count >= 100 else Decimal("50") if documented else Decimal("0"))
        return sum(values, Decimal("0")) / len(values) if values else None

    def _review_after_last_critical(self, pillar: str, sample: list[TradeProcessScore]) -> bool:
        last_critical = max((item.exit_time for item in sample if self._is_critical_violation(pillar, item)), default=None)
        if last_critical is None:
            return True
        account_ids = {sample[-1].account_id} if pillar == "risk" else {item.id for item in self._repository.list_mt5_accounts()}
        return any(
            review.created_at > last_critical
            for account_id in account_ids
            for review in self._repository.list_framework_period_reviews(account_id)
        )

    @staticmethod
    def _is_critical_violation(pillar: str, score: TradeProcessScore) -> bool:
        return bool(CRITICAL_VIOLATIONS[pillar] & (set(score.violation_codes) | set(score.hard_rule_codes)))

    def _trade_process_score(
        self,
        account_id: int,
        trade: ClosedTradeReviewItem,
        assessment: PostTradeAssessmentView | None,
        policies: dict[int, AccountRiskPolicyView],
        strategies_by_magic: dict[str, StrategyProfileView],
        funded: str | None,
        active_policy: AccountRiskPolicyView | None,
        live_balance: str | None,
        settings: FrameworkRuleSettingsView,
        automatic_hard_events: tuple[str, ...],
    ) -> TradeProcessScore:
        auto_risk = self._auto_risk_evidence(trade, policies, funded, active_policy, live_balance)
        policy = self._risk_policy_for_trade(assessment, trade, policies, active_policy)
        policy_risk = _decimal_text(self._maximum_risk_amount(funded, policy)) if funded is not None and policy is not None else None
        actual_risk = assessment.declared_actual_risk_amount if assessment and assessment.declared_actual_risk_amount else auto_risk.source_amount
        mapped = strategies_by_magic.get(trade.entry_magic_number or "")
        if assessment is None:
            state = "automatic_evidence" if auto_risk.state in {"within_policy", "over_policy"} else "not_scored"
            return TradeProcessScore(account_id, trade.id, trade.exit_time, trade.server_utc_offset_minutes, state, None, None, None, None, None, None, False, False, False, (), (), auto_risk, policy_risk, actual_risk, mapped)
        psychology = self._trade_pillar_score(assessment, "psychology")
        risk = self._trade_pillar_score(assessment, "risk")
        system = self._trade_pillar_score(assessment, "system")
        hard_rules = self._effective_hard_rules(assessment.hard_rule_codes, settings) | set(automatic_hard_events)
        psychology_hard = "oversized_revenge" in hard_rules
        risk_hard = bool({"oversized_revenge", "stop_widened", "shutdown_breach", "daily_limit", "weekly_limit", "drawdown_limit", "loss_streak"} & hard_rules)
        system_hard = "mandatory_setup_absent" in hard_rules
        status = "FAIL" if hard_rules else "PASS"
        classification = self._classification(Decimal(trade.net_pnl), status)
        overall = (psychology + risk + system) / Decimal("3")
        return TradeProcessScore(
            account_id, trade.id, trade.exit_time, trade.server_utc_offset_minutes, "reviewed", _decimal_text(psychology), _decimal_text(risk), _decimal_text(system), _decimal_text(overall), status, classification,
            psychology_hard, risk_hard, system_hard, tuple(sorted(hard_rules)), assessment.violation_codes, auto_risk, policy_risk, actual_risk, mapped,
        )

    @staticmethod
    def _trade_pillar_score(assessment: PostTradeAssessmentView, pillar: str) -> Decimal:
        return sum((GRADE_VALUES[assessment.criterion_grades[key]] * weight for key, weight in TRADE_WEIGHTS[pillar]), Decimal("0"))

    @staticmethod
    def _effective_hard_rules(codes: tuple[str, ...], settings: FrameworkRuleSettingsView) -> set[str]:
        enabled = {
            "oversized_revenge": settings.oversized_revenge_hard,
            "mandatory_setup_absent": settings.mandatory_setup_hard,
            "stop_widened": settings.stop_widened_hard,
            "shutdown_breach": settings.shutdown_breach_hard,
        }
        return {code for code in codes if enabled.get(code, False)}

    @staticmethod
    def _classification(net_pnl: Decimal, status: str) -> str:
        quality = "Good" if status == "PASS" else "Bad"
        outcome = "Win" if net_pnl > 0 else "Loss" if net_pnl < 0 else "Breakeven"
        return f"{quality} {outcome}"

    def _historical_risk_events(self, account_id: int) -> dict[int, dict[str, object]]:
        _, events = self._account_trade_scores_without_events(account_id)
        return events

    def _account_trade_scores_without_events(self, account_id: int) -> tuple[tuple[ClosedTradeReviewItem, ...], dict[int, dict[str, object]]]:
        trades = tuple(sorted(self._repository.list_closed_trades_for_review(account_id), key=lambda item: (item.exit_time, item.id)))
        assessments = {item.assessment.trade_id: item.assessment for item in self._repository.list_post_trade_assessment_outcomes(account_id)}
        policies = self._policies_for(trades, assessments, account_id)
        return trades, self._historical_risk_events_from_context(trades, assessments, policies, self._repository.get_active_risk_policy(account_id), self._repository.get_account_funded_capital(account_id), self._repository.get_latest_mt5_balance(account_id))

    def _historical_risk_events_from_context(self, trades, assessments, policies, active_policy, funded, live_balance):  # type: ignore[no-untyped-def]
        events: dict[int, dict[str, object]] = {}
        if funded is None:
            return {trade.id: {"date": self._trade_date(trade.exit_time, trade.server_utc_offset_minutes), "result_r": Decimal("0"), "drawdown": Decimal("0"), "streak": 0, "events": ()} for trade in trades}
        capital = Decimal(funded)
        balance = capital
        peak = capital
        daily: dict[date, Decimal] = {}
        weekly: dict[date, Decimal] = {}
        streak = 0
        for trade in trades:
            assessed = assessments.get(trade.id)
            policy = self._risk_policy_for_trade(assessed, trade, policies, active_policy)
            fallback = self._standard_risk_amount(funded, policy)
            risk_amount = self._risk_amount(assessed, trade, fallback, live_balance)
            result_r = Decimal(trade.net_pnl) / risk_amount if risk_amount > 0 else Decimal("0")
            trade_day = self._trade_date(trade.exit_time, trade.server_utc_offset_minutes)
            week_start = trade_day - timedelta(days=trade_day.weekday())
            daily[trade_day] = daily.get(trade_day, Decimal("0")) + result_r
            weekly[week_start] = weekly.get(week_start, Decimal("0")) + result_r
            balance += Decimal(trade.net_pnl)
            peak = max(peak, balance)
            drawdown = (peak - balance) / peak * Decimal("100") if peak > 0 else Decimal("0")
            streak = streak + 1 if Decimal(trade.net_pnl) < 0 else 0
            breach: set[str] = set()
            if policy is not None:
                if daily[trade_day] <= -Decimal(policy.daily_loss_limit_r): breach.add("daily_limit")
                if weekly[week_start] <= -Decimal(policy.weekly_loss_limit_r): breach.add("weekly_limit")
                if drawdown >= Decimal(policy.max_drawdown_percent): breach.add("drawdown_limit")
                if streak >= policy.max_consecutive_losses: breach.add("loss_streak")
            events[trade.id] = {"date": trade_day, "result_r": result_r, "drawdown": drawdown, "streak": streak, "events": tuple(sorted(breach))}
        return events

    def _policies_for(self, trades, assessments, account_id: int):  # type: ignore[no-untyped-def]
        ids = {item.auto_risk_policy_id for item in trades if item.auto_risk_policy_id is not None}
        ids.update(item.risk_policy_id for item in assessments.values() if item.risk_policy_id is not None)
        active = self._repository.get_active_risk_policy(account_id)
        if active is not None:
            ids.add(active.id)
        return {policy_id: policy for policy_id in ids if (policy := self._repository.get_risk_policy(policy_id)) is not None}

    @staticmethod
    def _risk_policy_for_trade(assessment, trade, policies, active_policy):  # type: ignore[no-untyped-def]
        policy_id = (assessment.risk_policy_id if assessment is not None else None) or trade.auto_risk_policy_id
        return policies.get(policy_id) if policy_id is not None else active_policy

    @staticmethod
    def _standard_risk_amount(funded: str | None, policy: AccountRiskPolicyView | None) -> Decimal:
        return Decimal("0") if funded is None or policy is None else Decimal(funded) * Decimal(policy.standard_risk_per_trade_percent) / Decimal("100")

    @staticmethod
    def _maximum_risk_amount(funded: str | None, policy: AccountRiskPolicyView | None) -> Decimal:
        return Decimal("0") if funded is None or policy is None else Decimal(funded) * Decimal(policy.maximum_risk_per_trade_percent) / Decimal("100")

    @staticmethod
    def _risk_amount(assessment, trade, fallback: Decimal, live_balance: str | None) -> Decimal:  # type: ignore[no-untyped-def]
        if assessment is not None and assessment.declared_actual_risk_amount is not None:
            return Decimal(assessment.declared_actual_risk_amount)
        if (value := FrameworkService._specific_preset_sl_amount(trade)) is not None:
            return Decimal(value)
        if (value := FrameworkService._real_loss_sl_amount(trade)) is not None:
            return Decimal(value)
        if (value := FrameworkService._live_account_balance_sl_amount(trade, live_balance)) is not None:
            return Decimal(value)
        return fallback

    @staticmethod
    def _specific_preset_sl_amount(trade: ClosedTradeReviewItem) -> str | None:
        try:
            amount = Decimal(trade.initial_risk_amount) if trade.initial_risk_amount is not None else Decimal("0")
        except ArithmeticError:
            return None
        return _decimal_text(amount) if amount.is_finite() and amount > 0 else None

    @staticmethod
    def _real_loss_sl_amount(trade: ClosedTradeReviewItem) -> str | None:
        if FrameworkService._specific_preset_sl_amount(trade) is not None:
            return None
        return _decimal_text(-Decimal(trade.net_pnl)) if Decimal(trade.net_pnl) < 0 else None

    @staticmethod
    def _live_account_balance_sl_amount(trade: ClosedTradeReviewItem, live_balance: str | None) -> str | None:
        if FrameworkService._specific_preset_sl_amount(trade) is not None or trade.entry_stop_price is not None or Decimal(trade.net_pnl) <= 0 or live_balance is None:
            return None
        amount = Decimal(live_balance)
        return _decimal_text(amount) if amount > 0 else None

    def _auto_risk_evidence(self, trade, policies, funded, active_policy, live_balance):  # type: ignore[no-untyped-def]
        specific = self._specific_preset_sl_amount(trade)
        real_loss = self._real_loss_sl_amount(trade)
        live = self._live_account_balance_sl_amount(trade, live_balance)
        amount = specific or real_loss or live
        basis = "specific_preset_sl" if specific else "real_loss_sl" if real_loss else "live_account_balance_sl" if live else "unavailable"
        confidence = "verified" if basis == "specific_preset_sl" else "inferred" if basis == "real_loss_sl" else "conservative" if basis == "live_account_balance_sl" else "unavailable"
        observed_stop = self._observed_stop_widened(trade)
        initial_rr = _decimal_text(Decimal(trade.initial_reward_amount) / Decimal(specific)) if specific and trade.initial_reward_amount and Decimal(trade.initial_reward_amount) > 0 else None
        policy = policies.get(trade.auto_risk_policy_id) or active_policy
        if amount is None:
            return AutoRiskEvidence("unavailable", "No usable automatic risk source is available.", specific, real_loss, live, basis, confidence, trade.initial_reward_amount, initial_rr, observed_stop, None if policy is None else policy.version)
        if policy is None or funded is None:
            return AutoRiskEvidence("unavailable", "Set funded capital and save a Risk policy to compare this evidence.", specific, real_loss, live, basis, confidence, trade.initial_reward_amount, initial_rr, observed_stop, None if policy is None else policy.version)
        limit = self._maximum_risk_amount(funded, policy)
        state = "within_policy" if Decimal(amount) <= limit else "over_policy"
        source = {"specific_preset_sl": "Specific preset SL", "real_loss_sl": "Real-loss estimate", "live_account_balance_sl": "Live-account-balance estimate"}[basis]
        return AutoRiskEvidence(state, f"{source} {amount} is {'within' if state == 'within_policy' else 'over'} policy v{policy.version} limit {_decimal_text(limit)}.", specific, real_loss, live, basis, confidence, trade.initial_reward_amount, initial_rr, observed_stop, policy.version)

    @staticmethod
    def _observed_stop_widened(trade: ClosedTradeReviewItem) -> bool | None:
        if trade.entry_stop_price is None or trade.close_stop_price is None:
            return None
        return Decimal(trade.close_stop_price) < Decimal(trade.entry_stop_price) if trade.direction == "long" else Decimal(trade.close_stop_price) > Decimal(trade.entry_stop_price)

    def _reporting_time_basis(self) -> str:
        return self._repository.get_journal_settings().reporting_time_basis

    def _trade_date(self, value: str, server_utc_offset_minutes: int) -> date:
        return reporting_date(value, server_utc_offset_minutes, self._reporting_time_basis())

    def _current_report_date(self, value: datetime, account_id: int) -> date:
        timestamp = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
        account = next((item for item in self._repository.list_mt5_accounts() if item.id == account_id), None)
        offset = 0 if account is None or account.latest_server_utc_offset_minutes is None else account.latest_server_utc_offset_minutes
        return reporting_datetime(timestamp.isoformat(), offset, self._reporting_time_basis()).date()
