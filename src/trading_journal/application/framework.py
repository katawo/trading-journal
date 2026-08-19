"""Deterministic three-pillar scoring for completed, imported MT5 trades."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from trading_journal.application.reporting_time import reporting_date, reporting_datetime

from trading_journal.infrastructure.sqlite_repository import (
    PSYCHOLOGY_CRITERIA,
    RISK_CRITERIA,
    SYSTEM_CRITERIA,
    AccountRiskPolicyView,
    ClosedTradeReviewItem,
    PillarRoadmapEvidenceView,
    PostTradeAssessmentView,
    FrameworkFocusView,
    SQLiteJournalRepository,
    StrategyEvidenceSnapshot,
    StrategyProfileView,
)


PILLAR_NAMES = {"psychology": "Psychology", "risk": "Risk management", "system": "Trading system"}
GRADE_VALUES = {"pass": Decimal("100"), "partial": Decimal("50"), "fail": Decimal("0")}
GOOD_PROCESS_SCORE = Decimal("70")
REVIEWED_KINDS = frozenset({"approved_auto_review", "manual_review"})
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
    "risk": frozenset({"daily_limit", "weekly_limit", "drawdown_limit", "open_exposure", "correlation_exposure", "oversized_revenge", "stop_widened", "shutdown_breach", "no_stop_loss"}),
    "system": frozenset({"mandatory_setup_absent"}),
}
COMPONENT_CODES = {
    "rule_adherence": "Rule adherence", "impulse_control": "Impulse control", "emotional_control": "Emotional control", "post_loss_discipline": "Post-loss discipline",
    "policy_adherence": "Policy adherence", "stop_discipline": "Stop discipline", "limit_compliance": "Limit compliance", "exposure_control": "Exposure control",
    "setup_validity": "Setup validity", "execution_fidelity": "Execution fidelity", "context_alignment": "Context alignment", "evidence_quality": "Evidence quality", "edge_evidence": "Edge evidence",
}

ROADMAP_ITEMS: dict[str, dict[int, tuple[tuple[str, str], ...]]] = {
    "psychology": {
        1: (("triggers", "Document triggers and stop conditions"), ("behaviour_rules", "Document no-revenge and no-chase rules")),
        2: (("practice", "Record structured practice and recurring patterns"),),
        3: (("execution", "20 full reviews, score at least 70, no active hard failure"),),
        4: (("measure", "30 full reviews, current period review, score at least 80"),),
        5: (("hypothesis", "Record one behavioural hypothesis, baseline, result, and keep/reject decision"),),
    },
    "risk": {
        1: (("policy_and_sizing", "Define account risk policy, hard limits, and position sizing"),),
        2: (("test", "Record risk-calculation or simulation evidence"),),
        3: (("execution", "20 full reviews, score at least 70, no active hard failure"),),
        4: (("measure", "30 full reviews, current period review, score at least 80"),),
        5: (("hypothesis", "Record one risk-policy hypothesis, baseline, result, and keep/reject decision"),),
    },
    "system": {
        1: (("rules", "Define context, entry, invalidation, exit, and no-trade rules"), ("examples", "Document valid and invalid examples")),
        2: (("backtest", "Mark the strategy's backtest as verified"),),
        3: (("execution", "20 full reviews, score at least 70, no active hard failure"),),
        4: (("measure", "30 full reviews, current period review, score at least 80"),),
        5: (("hypothesis", "Record one system hypothesis, baseline, result, and keep/reject decision"),),
    },
}

ROADMAP_LEVEL_NAMES: dict[int, str] = {1: "Define", 2: "Test", 3: "Execute", 4: "Measure", 5: "Optimize"}

# Items with no equivalent structured data anywhere in the app; the trader must self-certify these.
_MANUAL_ROADMAP_ITEM_KEYS = frozenset({"triggers", "behaviour_rules", "practice", "test"})


def _decimal_text(value: Decimal) -> str:
    text = format(value.normalize(), "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _rounded_score_text(value: str | None) -> str:
    """Round a stored score string for display in generated gate text (never for comparisons)."""
    return "—" if value is None else f"{Decimal(value):.0f}"


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
    pretrade_account_balance_sl_amount: str | None
    risk_basis: str
    confidence: str
    initial_reward_amount: str | None
    initial_rr: str | None
    observed_stop_widened: bool | None
    policy_version: int | None

    @property
    def source_amount(self) -> str | None:
        # The imported pre-trade balance is one account-level fallback. It
        # is never added again for each position in a scaled trade.
        if self.pretrade_account_balance_sl_amount is not None:
            return self.pretrade_account_balance_sl_amount
        total = sum(
            (
                Decimal(value)
                for value in (
                    self.specific_preset_sl_amount,
                    self.real_loss_sl_amount,
                )
                if value is not None
            ),
            Decimal("0"),
        )
        return None if total <= 0 else _decimal_text(total)


@dataclass(frozen=True)
class TradeProcessScore:
    account_id: int
    trade_id: int
    net_pnl: str
    exit_time: str
    server_utc_offset_minutes: int
    assessment_state: str
    review_kind: str
    criterion_grades: dict[str, str] | None
    psychology_score: str | None
    risk_score: str | None
    system_score: str | None
    overall_score: str | None
    process_status: str | None
    quality_status: str | None
    classification: str | None
    psychology_hard_block: bool
    risk_hard_block: bool
    system_hard_block: bool
    hard_rule_codes: tuple[str, ...]
    violation_codes: tuple[str, ...]
    automatic_risk_event_codes: tuple[str, ...]
    shutdown_candidate_codes: tuple[str, ...]
    auto_risk: AutoRiskEvidence
    policy_risk_amount: str | None
    actual_risk_amount: str | None
    risk_policy_state: str
    risk_evidence_source: str
    # Live magic-number mapping for auto/not-yet-scored trades (no strategy is
    # attached yet); the strategy *snapshot* attached at save time for a
    # manually-reviewed trade, so later edits to a strategy's backtest fields
    # never retroactively change an already-reviewed trade's Monitor score.
    mapped_strategy: "StrategyProfileView | StrategyEvidenceSnapshot | None"
    setup_snapshot: str | None = None
    session_snapshot: str | None = None
    regime_snapshot: str | None = None
    direction: str = "long"


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
class RiskEvidenceCoverage:
    total: int
    approved: int
    pending: int
    over_policy: int
    unavailable: int


@dataclass(frozen=True)
class ContextBreakdown:
    label: str
    review_count: int
    average_process_score: str | None
    win_rate: str
    average_r: str | None


@dataclass(frozen=True)
class MonitorAnalysisPoint:
    """One selected-account logical trade for descriptive Monitor analysis."""

    trade_id: int
    closed: str
    direction: str
    outcome: str
    review_kind: str
    overall_score: str | None
    psychology_score: str | None
    risk_score: str | None
    system_score: str | None
    classification: str | None
    result_r: str | None
    strategy: str
    risk_policy_state: str
    violation_codes: tuple[str, ...]
    hard_rule_codes: tuple[str, ...]
    setup: str | None
    session: str | None
    regime: str | None


@dataclass(frozen=True)
class MonitorBreakdown:
    label: str
    count: int
    average_process_score: str | None = None
    win_rate: str | None = None
    average_r: str | None = None


@dataclass(frozen=True)
class MonitorInsight:
    severity: str
    message: str


@dataclass(frozen=True)
class MonitorAnalysisReport:
    start_date: str
    end_date: str
    points: tuple[MonitorAnalysisPoint, ...]
    reviewed_points: tuple[MonitorAnalysisPoint, ...]
    lifecycle: tuple[MonitorBreakdown, ...]
    policy_states: tuple[MonitorBreakdown, ...]
    classifications: tuple[MonitorBreakdown, ...]
    issues: tuple[MonitorBreakdown, ...]
    strategies: tuple[MonitorBreakdown, ...]
    contexts: dict[str, tuple[MonitorBreakdown, ...]]
    insights: tuple[MonitorInsight, ...]


@dataclass(frozen=True)
class FrameworkFocusProgress:
    focus_id: int
    reviews_completed: int
    target_reviews: int
    current_value: str | None
    ready_to_evaluate: bool


@dataclass(frozen=True)
class CoachingRecommendation:
    pillar: str
    metric_kind: str
    metric_code: str | None
    hypothesis: str
    action_text: str
    baseline_value: str | None
    target_value: str
    target_reviews: int
    reason: str
    safety: bool = False


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
    closed_trades: int


@dataclass(frozen=True)
class RoadmapItemStatus:
    item_key: str
    label: str
    level: int
    is_auto: bool
    completed: bool
    # Auto items: a live-computed description of the detected evidence (or None if not yet detected).
    # Manual items: the saved evidence note (or None if never saved).
    evidence_summary: str | None


@dataclass(frozen=True)
class PillarRoadmapStatus:
    pillar: str
    completed_items: int
    total_items: int
    current_level: int
    items: tuple[RoadmapItemStatus, ...]


class FrameworkService:
    """Purely advisory calculations over persisted closed-trade reviews."""

    def __init__(self, repository: SQLiteJournalRepository) -> None:
        self._repository = repository
        # The service is short-lived: one Streamlit render. Cache its read
        # model only for that render so live imports and saved reviews are
        # never served stale on a later rerun.
        self._account_score_cache: dict[int, tuple[tuple[TradeProcessScore, ...], dict[int, dict[str, object]]]] = {}
        self._raw_risk_event_cache: dict[int, dict[int, dict[str, object]]] = {}
        self._pillar_score_cache: dict[tuple[int, int, date | None], tuple[PillarScore, ...]] = {}
        self._reporting_time_basis_cache: str | None = None

    def trade_process_scores(self, account_id: int) -> tuple[TradeProcessScore, ...]:
        account_scores, _ = self._account_trade_scores(account_id)
        return account_scores

    def pillar_scores(self, account_id: int, *, window: int = 20, as_of: date | None = None) -> tuple[PillarScore, ...]:
        cache_key = (account_id, window, as_of)
        if cache_key in self._pillar_score_cache:
            return self._pillar_score_cache[cache_key]
        trader_scores = self._scores_through(self._trader_trade_process_scores(), as_of)
        all_account_scores, historical_events = self._account_trade_scores(account_id)
        account_scores = self._scores_through(all_account_scores, as_of)
        scores = (
            self._period_pillar_score("psychology", trader_scores, window, "Trader-wide"),
            self._period_pillar_score("risk", account_scores, window, "Selected account", historical_events),
            self._period_pillar_score("system", account_scores, window, "Selected account"),
        )
        self._pillar_score_cache[cache_key] = scores
        return scores

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
        # trade_process_scores() sees every closed trade regardless of review status (unlike
        # list_post_trade_assessment_outcomes(), which only sees already-reviewed ones) - so a
        # single pass over it distinguishes "nothing closed this period" from "closed trades
        # are sitting unreviewed," using the same REVIEWED_KINDS the rest of the app already
        # uses for review-status (Review-tab badge, coaching recommendations, etc.).
        in_period = [item for item in self.trade_process_scores(account_id) if start <= self._trade_date(item.exit_time, item.server_utc_offset_minutes) <= end]
        reviewed = [item for item in in_period if item.review_kind in REVIEWED_KINDS]
        existing = self._repository.list_framework_period_reviews(account_id, cadence)
        saved = any(item.period_start == start.isoformat() and item.period_end == end.isoformat() for item in existing)
        return PeriodReviewStatus(cadence, start.isoformat(), end.isoformat(), bool(reviewed) and not saved, len(reviewed), len(in_period))

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
        scored = [item for item in self._scores_through(self.trade_process_scores(account_id), as_of) if item.review_kind in REVIEWED_KINDS][-window:]
        counter: Counter[str] = Counter(code for item in scored for code in set(item.violation_codes) | set(item.hard_rule_codes))
        return tuple(counter.most_common())

    @staticmethod
    def _recurring_issues_from_scores(scores: tuple[TradeProcessScore, ...], *, window: int) -> tuple[tuple[str, int], ...]:
        reviewed = [item for item in scores if item.review_kind in REVIEWED_KINDS][-window:]
        counter: Counter[str] = Counter(
            code for item in reviewed for code in set(item.violation_codes) | set(item.hard_rule_codes)
        )
        return tuple(counter.most_common())

    def rolling_score_trend(self, account_id: int, *, window: int = 20) -> tuple[tuple[str, str | None, str | None, str | None], ...]:
        """Historical card-equivalent scores, retaining documented pillar scopes."""
        reviewed = [item for item in self.trade_process_scores(account_id) if item.review_kind in REVIEWED_KINDS]
        points: list[tuple[str, str | None, str | None, str | None]] = []
        for trade in reviewed:
            as_of = self._trade_date(trade.exit_time, trade.server_utc_offset_minutes)
            values_by_pillar = {item.pillar: item.score for item in self.pillar_scores(account_id, window=window, as_of=as_of)}
            closed = reporting_datetime(trade.exit_time, trade.server_utc_offset_minutes, self._reporting_time_basis()).isoformat()
            points.append((closed, values_by_pillar["psychology"], values_by_pillar["risk"], values_by_pillar["system"]))
        return tuple(points)

    def risk_evidence_coverage(self, account_id: int, *, window: int = 20) -> RiskEvidenceCoverage:
        scores = self.trade_process_scores(account_id)[-window:]
        return RiskEvidenceCoverage(
            total=len(scores),
            approved=sum(item.review_kind in {"approved_auto_review", "manual_review"} for item in scores),
            pending=sum(item.review_kind in {"needs_approval", "auto_review"} for item in scores),
            over_policy=sum(item.risk_policy_state == "over_policy" for item in scores),
            unavailable=sum(item.risk_policy_state == "unavailable" for item in scores),
        )

    def context_breakdown(self, account_id: int, *, dimension: str, window: int = 20) -> tuple[ContextBreakdown, ...]:
        if dimension not in {"setup", "session", "regime"}:
            raise ValueError("Context dimension must be setup, session, or regime")
        attribute = f"{dimension}_snapshot"
        manual = [item for item in self.trade_process_scores(account_id) if item.review_kind == "manual_review"][-window:]
        buckets: dict[str, list[TradeProcessScore]] = {}
        for item in manual:
            label = getattr(item, attribute, None) or "Unspecified"
            buckets.setdefault(label, []).append(item)
        rows: list[ContextBreakdown] = []
        for label, items in sorted(buckets.items()):
            scores = [Decimal(item.overall_score) for item in items if item.overall_score is not None]
            wins = sum(Decimal(item.net_pnl) > 0 for item in items)
            # Normalised R uses the attached account policy evidence where available.
            r_values = [Decimal(item.net_pnl) / Decimal(item.policy_risk_amount) for item in items if item.policy_risk_amount and Decimal(item.policy_risk_amount) > 0]
            rows.append(ContextBreakdown(
                label, len(items), None if not scores else _decimal_text(sum(scores, Decimal("0")) / len(scores)),
                _decimal_text(Decimal(wins * 100) / len(items)),
                None if not r_values else _decimal_text(sum(r_values, Decimal("0")) / len(r_values)),
            ))
        return tuple(rows)

    def monitor_analysis(self, account_id: int, *, start_date: date, end_date: date, window: int = 20) -> MonitorAnalysisReport:
        """Return descriptive selected-account evidence without changing score gates.

        Outcome R is intentionally joined from the Performance-dashboard read model so
        every Monitor outcome chart uses the journal's standard 1R convention.
        """
        if start_date > end_date:
            raise ValueError("Start date must be on or before end date")
        performance = {item.logical_trade_id: item for item in self._repository.list_trade_performance(account_id)}
        scores = [
            item for item in self.trade_process_scores(account_id)
            if start_date <= self._trade_date(item.exit_time, item.server_utc_offset_minutes) <= end_date
        ]
        points = tuple(
            MonitorAnalysisPoint(
                trade_id=item.trade_id,
                closed=reporting_datetime(item.exit_time, item.server_utc_offset_minutes, self._reporting_time_basis()).isoformat(),
                direction=item.direction,
                outcome="profit" if Decimal(item.net_pnl) > 0 else "loss" if Decimal(item.net_pnl) < 0 else "breakeven",
                review_kind=item.review_kind,
                overall_score=item.overall_score,
                psychology_score=item.psychology_score,
                risk_score=item.risk_score,
                system_score=item.system_score,
                classification=item.classification,
                result_r=performance[item.trade_id].result_r if item.trade_id in performance else None,
                strategy=(item.mapped_strategy.name if item.mapped_strategy is not None else "Untagged"),
                risk_policy_state=item.risk_policy_state,
                violation_codes=item.violation_codes,
                hard_rule_codes=item.hard_rule_codes,
                setup=item.setup_snapshot,
                session=item.session_snapshot,
                regime=item.regime_snapshot,
            )
            for item in scores
        )
        reviewed = tuple(item for item in points if item.review_kind in REVIEWED_KINDS)

        def counts(values: list[str], order: tuple[str, ...] = ()) -> tuple[MonitorBreakdown, ...]:
            counter = Counter(values)
            labels = [*order, *(key for key in counter if key not in order)]
            return tuple(MonitorBreakdown(label, counter[label]) for label in labels if counter[label])

        lifecycle = counts([item.review_kind for item in points], ("manual_review", "approved_auto_review", "auto_review", "needs_approval"))
        policy_states = counts([item.risk_policy_state for item in points], ("within_policy", "over_policy", "unavailable"))
        classifications = counts([item.classification for item in reviewed if item.classification is not None])
        issues = tuple(MonitorBreakdown(label, count) for label, count in Counter(
            code for item in reviewed for code in set(item.violation_codes) | set(item.hard_rule_codes)
        ).most_common())

        def grouped(items: tuple[MonitorAnalysisPoint, ...], attribute: str) -> tuple[MonitorBreakdown, ...]:
            buckets: dict[str, list[MonitorAnalysisPoint]] = {}
            for item in items:
                label = getattr(item, attribute) or "Unspecified"
                buckets.setdefault(label, []).append(item)
            rows: list[MonitorBreakdown] = []
            for label, bucket in sorted(buckets.items()):
                quality = [Decimal(item.overall_score) for item in bucket if item.overall_score is not None]
                r_values = [Decimal(item.result_r) for item in bucket if item.result_r is not None]
                wins = sum(value > 0 for value in r_values)
                rows.append(MonitorBreakdown(
                    label=label,
                    count=len(bucket),
                    average_process_score=None if not quality else _decimal_text(sum(quality, Decimal("0")) / len(quality)),
                    win_rate=None if not r_values else _decimal_text(Decimal(wins * 100) / len(r_values)),
                    average_r=None if not r_values else _decimal_text(sum(r_values, Decimal("0")) / len(r_values)),
                ))
            return tuple(rows)

        strategies = grouped(reviewed, "strategy")
        manual = tuple(item for item in reviewed if item.review_kind == "manual_review")
        scores_now = self.pillar_scores(account_id, window=window)
        insight_rows: list[MonitorInsight] = []
        for score in scores_now:
            if score.hard_block:
                insight_rows.append(MonitorInsight("critical", f"{PILLAR_NAMES[score.pillar]} has a hard-rule failure in the rolling sample."))
            elif score.status == "caution":
                insight_rows.append(MonitorInsight("warning", f"{PILLAR_NAMES[score.pillar]} is capped after repeated critical violations."))
            elif score.score is not None and Decimal(score.score) < Decimal("70"):
                insight_rows.append(MonitorInsight("warning", f"{PILLAR_NAMES[score.pillar]} is below 70 in the rolling sample."))
        pending = sum(item.review_kind in {"auto_review", "needs_approval"} for item in points)
        if pending:
            insight_rows.append(MonitorInsight("info", f"{pending} trade(s) in this period still need review approval before they can contribute to scoring."))
        if issues and issues[0].count >= 2:
            insight_rows.append(MonitorInsight("info", f"Most frequent reviewed issue: {issues[0].label} ({issues[0].count} trade(s) in this period)."))
        return MonitorAnalysisReport(
            start_date=start_date.isoformat(), end_date=end_date.isoformat(), points=points, reviewed_points=reviewed,
            lifecycle=lifecycle, policy_states=policy_states, classifications=classifications, issues=issues,
            strategies=strategies, contexts={dimension: grouped(manual, dimension) for dimension in ("setup", "session", "regime")},
            insights=tuple(insight_rows[:3]),
        )

    def focus_progress(self, account_id: int) -> tuple[FrameworkFocusView | None, FrameworkFocusProgress | None]:
        focus = self._repository.get_active_framework_focus()
        if focus is None:
            return None, None
        if focus.pillar in {"risk", "system"}:
            if focus.account_id is None:
                raise ValueError("Risk and Trading system focuses require an account")
            focus_account = focus.account_id
            focus_scores = self.trade_process_scores(focus_account)
        else:
            focus_account = account_id
            focus_scores = self._trader_trade_process_scores()
        reviewed = [item for item in focus_scores if item.review_kind in REVIEWED_KINDS]
        completed = max(0, len(reviewed) - focus.starting_manual_reviews)
        sample = reviewed[focus.starting_manual_reviews:focus.starting_manual_reviews + focus.target_reviews]
        current: str | None = None
        if focus.metric_kind == "manual_evidence":
            current = str(completed)
        elif focus.metric_kind == "criterion" and sample:
            values = [GRADE_VALUES[item.criterion_grades[focus.metric_code]] for item in sample if item.criterion_grades and focus.metric_code]
            current = _decimal_text(sum(values, Decimal("0")) / len(values)) if values else None
        elif focus.metric_kind == "violation" and sample:
            current = str(sum((focus.metric_code or "") in item.violation_codes for item in sample))
        elif focus.metric_kind == "component" and sample and focus.metric_code:
            components = self._period_components(
                focus.pillar, sample,
                self._historical_risk_events(focus_account) if focus.pillar == "risk" else None,
                {item.trade_id: Decimal(item.net_pnl) for item in sample},
            )
            current = next((_decimal_text(value) for name, value in components if name == COMPONENT_CODES.get(focus.metric_code) and value is not None), None)
        return focus, FrameworkFocusProgress(focus.id, completed, focus.target_reviews, current, completed >= focus.target_reviews)

    def coaching_recommendation(self, account_id: int) -> CoachingRecommendation | None:
        """Choose one auditable, post-trade coaching experiment from current evidence."""
        scores = self.pillar_scores(account_id, window=20)
        reviewed = min(item.reviewed_total for item in scores)
        for pillar in PILLAR_NAMES:
            recent = [item for item in self._pillar_trade_process_scores(account_id, pillar) if item.review_kind in REVIEWED_KINDS][-20:]
            latest_code = next((code for item in reversed(recent) for code in item.hard_rule_codes if code in CRITICAL_VIOLATIONS[pillar]), None)
            if latest_code:
                return replace(self._coaching_recommendation(
                    pillar, "violation", latest_code, "0", 5,
                    "Hard-rule safety focus.", safety=True,
                ), baseline_value="1")
        if reviewed < 5:
            return CoachingRecommendation(
                "psychology", "manual_evidence", None,
                "A small reviewed sample cannot yet distinguish a stable pattern from noise.",
                "Review each closed trade promptly and record one factual lesson.", str(reviewed), "5", 5,
                "Build a first reviewed sample.",
            )
        for pillar in PILLAR_NAMES:
            eligible_codes = (
                (CRITICAL_VIOLATIONS["risk"] | CRITICAL_VIOLATIONS["system"]) - CRITICAL_VIOLATIONS["psychology"]
                if pillar == "psychology"
                else CRITICAL_VIOLATIONS[pillar]
            )
            recurring = next(
                (
                    (code, count)
                    for code, count in self._recurring_issues_from_scores(
                        self._pillar_trade_process_scores(account_id, pillar), window=10
                    )
                    if count >= 2 and (code not in eligible_codes if pillar == "psychology" else code in eligible_codes)
                ),
                None,
            )
            if recurring:
                code, count = recurring
                return replace(self._coaching_recommendation(pillar, "violation", code, "0", 10, "Repeated reviewed issue."), baseline_value=str(count))
        weak = min((item for item in scores if item.score is not None), key=lambda item: Decimal(item.score), default=None)
        if weak is not None and Decimal(weak.score) < 70:
            component = min(((name, value) for name, value in weak.component_scores if value is not None), key=lambda item: Decimal(item[1]), default=None)
            code = next((key for key, label in COMPONENT_CODES.items() if component and label == component[0]), None)
            if code is not None:
                return replace(self._coaching_recommendation(weak.pillar, "component", code, "80", 10, "Weakest current pillar component."), baseline_value=component[1])
        return None

    def _recently_resolved_same_recommendation(self, account_id: int, recommendation: CoachingRecommendation) -> FrameworkFocusView | None:
        """A previously resolved coach focus matching this recommendation, if no fresh
        reviewed evidence has landed since - the anti-recycling guard ensure_coaching_focus()
        uses to avoid reopening a new tracked window before any new data exists to justify one.
        """
        focus_account_id = account_id if recommendation.pillar in {"risk", "system"} else None
        completed = [
            item for item in self._repository.list_framework_focuses()
            if item.source == "coach" and item.status in {"completed", "abandoned"}
            and item.pillar == recommendation.pillar and item.metric_kind == recommendation.metric_kind and item.metric_code == recommendation.metric_code
            and item.account_id == focus_account_id
        ]
        if not completed:
            return None
        latest = completed[0]
        reviewed_total = next(item.reviewed_total for item in self.pillar_scores(account_id) if item.pillar == recommendation.pillar)
        return latest if reviewed_total <= latest.starting_manual_reviews + latest.target_reviews else None

    def pending_coaching_reason(self, account_id: int) -> str | None:
        """Why "Today focus" is showing nothing new right after a resolution: the top
        recommendation is the same one just resolved, with no fresh reviewed evidence yet to
        justify reopening it. Lets the UI say "still working on X" instead of the misleading
        "on track" when a real weakness is simply waiting on more evidence.
        """
        recommendation = self.coaching_recommendation(account_id)
        if recommendation is None:
            return None
        latest = self._recently_resolved_same_recommendation(account_id, recommendation)
        if latest is None:
            return None
        reviewed_total = next(item.reviewed_total for item in self.pillar_scores(account_id) if item.pillar == recommendation.pillar)
        remaining = latest.starting_manual_reviews + latest.target_reviews - reviewed_total + 1
        return (
            f"Still tracking {PILLAR_NAMES[recommendation.pillar]}: {recommendation.reason} "
            f"Resolved, but {remaining} more reviewed trade(s) are needed before a new focus reopens on it."
        )

    def ensure_coaching_focus(self, account_id: int) -> FrameworkFocusView | None:
        active = self._repository.get_active_framework_focus()
        recommendation = self.coaching_recommendation(account_id)
        if active is not None and not (recommendation and recommendation.safety):
            return active
        if active is not None and recommendation is not None and active.metric_kind == recommendation.metric_kind and active.metric_code == recommendation.metric_code:
            return active
        if active is None and recommendation is not None and self._recently_resolved_same_recommendation(account_id, recommendation) is not None:
            return None
        if active is not None and recommendation is not None:
            self._repository.resolve_framework_focus(
                focus_id=active.id, outcome="superseded",
                resolution_note=f"Superseded by safety coaching: {recommendation.reason}",
            )
        if recommendation is None:
            return None
        try:
            return self._repository.save_framework_focus(
                account_id=account_id if recommendation.pillar in {"risk", "system"} else None,
                pillar=recommendation.pillar, metric_kind=recommendation.metric_kind, metric_code=recommendation.metric_code,
                hypothesis=recommendation.hypothesis, action_text=recommendation.action_text,
                baseline_value=recommendation.baseline_value, target_value=recommendation.target_value,
                target_reviews=recommendation.target_reviews,
                starting_manual_reviews=next(item.reviewed_total for item in self.pillar_scores(account_id) if item.pillar == recommendation.pillar),
                source="coach", coach_reason=recommendation.reason,
            )
        except ValueError:
            return self._repository.get_active_framework_focus()

    @staticmethod
    def _coaching_recommendation(
        pillar: str, metric_kind: str, metric_code: str, target_value: str, target_reviews: int, reason: str, *, safety: bool = False,
    ) -> CoachingRecommendation:
        label = metric_code.replace("_", " ")
        actions = {
            "revenge": "After any loss, pause before the next order and re-check the written setup.",
            "stop_widened": "Keep the original invalidation level; do not widen the stop after entry.",
            "rule_adherence": "Before entry, state the rule that authorizes the trade; skip it if you cannot.",
            "impulse_control": "Wait for the documented trigger, then take one breath before placing the order.",
            "policy_adherence": "Calculate the planned risk before entry and reduce size when it exceeds policy.",
            "setup_validity": "Name the documented setup and its invalidation before entering; otherwise stand aside.",
            "post_loss_discipline": "After a loss, pause and confirm the next setup meets the written plan before re-entering.",
            "limit_compliance": "Check the daily and weekly loss limits before taking additional risk.",
            "execution_fidelity": "Use the documented entry, invalidation, and exit sequence without improvising.",
            "evidence_quality": "Record the setup evidence and review it before changing the system.",
            "edge_evidence": "Keep the system unchanged while you collect enough representative reviewed evidence.",
        }
        action = actions.get(metric_code, f"Before the next trade, verify {label} against the written plan and stand aside if it is not met.")
        return CoachingRecommendation(pillar, metric_kind, metric_code, f"Practising {label} consistently will improve {PILLAR_NAMES[pillar]}.", action, None, target_value, target_reviews, reason, safety)

    def _auto_roadmap_item_evaluation(
        self,
        pillar: str,
        item_key: str,
        account_id: int,
        *,
        current_period_review_account: bool = False,
        current_period_review_trader_wide: bool = False,
    ) -> tuple[bool, str | None] | None:
        """Live-computed completion for an auto-detected roadmap item, or None if this item is manual."""
        if item_key in _MANUAL_ROADMAP_ITEM_KEYS:
            return None
        if item_key == "execution":
            score = {item.pillar: item for item in self.pillar_scores(account_id, window=20)}[pillar]
            reviews_ok = score.reviewed_total >= 20
            score_ok = score.score is not None and Decimal(score.score) >= 70
            no_hard_block = not score.hard_block
            summary = "\n".join((
                f"- Reviews: {score.reviewed_total} (need 20 or more) {'✓' if reviews_ok else '✗'}",
                f"- Score: {_rounded_score_text(score.score)} (need 70 or more) {'✓' if score_ok else '✗'}",
                f"- Hard failure: {'none' if no_hard_block else 'active'} {'✓' if no_hard_block else '✗'}",
            ))
            return reviews_ok and score_ok and no_hard_block, summary
        if item_key == "measure":
            score = {item.pillar: item for item in self.pillar_scores(account_id, window=30)}[pillar]
            current_period_review = current_period_review_trader_wide if pillar == "psychology" else current_period_review_account
            reviews_ok = score.reviewed_total >= 30
            score_ok = score.score is not None and Decimal(score.score) >= 80
            no_hard_block = not score.hard_block
            summary = "\n".join((
                f"- Reviews: {score.reviewed_total} (need 30 or more) {'✓' if reviews_ok else '✗'}",
                f"- Score: {_rounded_score_text(score.score)} (need 80 or more) {'✓' if score_ok else '✗'}",
                f"- Period review: {'saved' if current_period_review else 'missing'} {'✓' if current_period_review else '✗'}",
                f"- Hard failure: {'none' if no_hard_block else 'active'} {'✓' if no_hard_block else '✗'}",
            ))
            return reviews_ok and score_ok and no_hard_block and current_period_review, summary
        if item_key == "hypothesis":
            focus_account_id = account_id if pillar in {"risk", "system"} else None
            resolved = [
                item for item in self._repository.list_framework_focuses()
                if item.pillar == pillar and item.status in {"completed", "abandoned"} and item.account_id == focus_account_id
            ]
            if not resolved:
                return False, None
            latest = resolved[0]
            return True, f"{latest.hypothesis} → {latest.resolution_note or '—'}"
        if pillar == "risk" and item_key == "policy_and_sizing":
            policy = self._repository.get_active_risk_policy(account_id)
            if policy is None:
                return False, None
            return True, (
                f"Risk {policy.standard_risk_per_trade_percent}%/trade, max {policy.maximum_risk_per_trade_percent}%, "
                f"daily {policy.daily_loss_limit_r}R, weekly {policy.weekly_loss_limit_r}R."
            )
        if pillar == "system" and item_key == "rules":
            profile = self._repository.get_account_strategy(account_id)
            if not (profile.description or "").strip():
                return False, None
            return True, f"{profile.name} has documented rules."
        if pillar == "system" and item_key == "examples":
            profile = self._repository.get_account_strategy(account_id)
            if any((setup.description or "").strip() for setup in self._repository.list_strategy_setups(profile.id)):
                return True, f"{profile.name} has a documented setup example."
            return False, None
        if pillar == "system" and item_key == "backtest":
            profile = self._repository.get_account_strategy(account_id)
            if profile.backtest_verified:
                return True, "Backtest verified."
            return False, None
        return None

    def roadmap_status(self, account_id: int, *, now: datetime | None = None) -> tuple[PillarRoadmapStatus, ...]:
        evidence = {(item.pillar, item.level, item.item_key): item for item in self._repository.list_pillar_roadmap_evidence(account_id)}
        # Psychology is trader-wide; Risk and System are evaluated on the selected account.
        current_period_review_account = self._has_current_period_review(account_id, now=now)
        current_period_review_trader_wide = self._has_current_period_review(account_id, now=now, trader_wide=True)
        statuses: list[PillarRoadmapStatus] = []
        for pillar, levels in ROADMAP_ITEMS.items():
            items: list[RoadmapItemStatus] = []
            for level, entries in levels.items():
                for item_key, label in entries:
                    auto_result = self._auto_roadmap_item_evaluation(
                        pillar, item_key, account_id,
                        current_period_review_account=current_period_review_account,
                        current_period_review_trader_wide=current_period_review_trader_wide,
                    )
                    if auto_result is not None:
                        completed, summary = auto_result
                        items.append(RoadmapItemStatus(item_key, label, level, True, completed, summary))
                    else:
                        saved = evidence.get((pillar, level, item_key))
                        items.append(RoadmapItemStatus(item_key, label, level, False, bool(saved and saved.completed), saved.evidence_note if saved else None))
            total = len(items)
            completed_count = sum(1 for item in items if item.completed)
            current_level = next((item.level for item in items if not item.completed), 5)
            statuses.append(PillarRoadmapStatus(pillar, completed_count, total, current_level, tuple(items)))
        return tuple(statuses)

    def save_pillar_roadmap_evidence(
        self,
        *,
        account_id: int | None,
        pillar: str,
        level: int,
        item_key: str,
        completed: bool,
        evidence_note: str | None,
    ) -> PillarRoadmapEvidenceView:
        """Defense-in-depth: re-validate the item is manual and unlocked here, not only in the presentation layer."""
        if account_id is not None and self._auto_roadmap_item_evaluation(pillar, item_key, account_id) is not None:
            raise ValueError("This roadmap item is auto-detected and cannot be saved manually.")
        if completed and account_id is not None:
            status = next(item for item in self.roadmap_status(account_id) if item.pillar == pillar)
            if level != status.current_level:
                raise ValueError("This roadmap item is not yet unlocked.")
        return self._repository.save_pillar_roadmap_evidence(
            account_id=account_id, pillar=pillar, level=level, item_key=item_key, completed=completed, evidence_note=evidence_note,
        )

    def _has_current_period_review(self, account_id: int, *, now: datetime | None = None, trader_wide: bool = False) -> bool:
        """Require reflection on the latest completed eligible week or month.

        Psychology must not be gated by whichever single account happens to be
        active — a period review saved against any account satisfies it. Risk
        and System remain account-scoped.
        """
        account_ids = tuple(item.id for item in self._repository.list_mt5_accounts()) if trader_wide else (account_id,)
        for candidate_account_id in account_ids:
            for cadence in ("weekly", "monthly"):
                status = self.period_review_status(candidate_account_id, cadence, now=now)
                if any(
                    review.period_start == status.period_start and review.period_end == status.period_end
                    for review in self._repository.list_framework_period_reviews(candidate_account_id, cadence)
                ):
                    return True
        return False

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
            message += f" {pending} logical trade(s) still need a full review."
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

    def _pillar_trade_process_scores(self, account_id: int, pillar: str) -> tuple[TradeProcessScore, ...]:
        """Return the evidence scope used by a pillar's rolling score and coaching."""
        return self._trader_trade_process_scores() if pillar == "psychology" else self.trade_process_scores(account_id)

    def _scores_through(self, scores: tuple[TradeProcessScore, ...], as_of: date | None) -> tuple[TradeProcessScore, ...]:
        if as_of is None:
            return scores
        return tuple(score for score in scores if self._trade_date(score.exit_time, score.server_utc_offset_minutes) <= as_of)

    def _account_trade_scores(self, account_id: int) -> tuple[tuple[TradeProcessScore, ...], dict[int, dict[str, object]]]:
        if account_id in self._account_score_cache:
            return self._account_score_cache[account_id]
        trades = sorted(self._repository.list_closed_trades_for_review(account_id), key=lambda item: (item.exit_time, item.id))
        assessments = {item.trade_id: item for item in self._repository.list_active_post_trade_assessments(account_id)}
        raw_positions = self._repository.list_imported_positions_for_risk(account_id)
        policies = self._policies_for(raw_positions, assessments, account_id)
        active_policy = self._repository.get_active_risk_policy(account_id)
        funded = self._repository.get_account_funded_capital(account_id)
        account_strategy = self._repository.get_account_strategy(account_id)
        raw_events = self._historical_risk_events(account_id)
        events = {
            trade.id: self._combine_member_risk_events(trade, raw_events)
            for trade in trades
        }
        scores = tuple(
            self._trade_process_score(
                account_id,
                trade,
                assessments.get(trade.id),
                policies,
                account_strategy,
                funded,
                active_policy,
                tuple(events[trade.id]["events"]),
                tuple(events[trade.id]["shutdown_candidates"]),
            )
            for trade in trades
        )
        result = (scores, events)
        self._account_score_cache[account_id] = result
        return result

    def _period_pillar_score(
        self,
        pillar: str,
        scores: tuple[TradeProcessScore, ...],
        window: int,
        scope: str,
        historical_events: dict[int, dict[str, object]] | None = None,
    ) -> PillarScore:
        # A reviewed trade is either a one-click approval of normalized MT5
        # evidence or a full Manual Review. Both use persisted criterion grades
        # and have equal weight in framework scoring and maturity gates.
        reviewed = [item for item in scores if item.review_kind in REVIEWED_KINDS]
        sample = reviewed[-window:]
        automatic = sum(item.review_kind in {"auto_review", "approved_auto_review"} for item in scores)
        unreviewed = sum(item.review_kind not in REVIEWED_KINDS for item in scores)
        if not sample:
            return PillarScore(pillar, None, None, "incomplete", 0, 0, unreviewed, automatic, False, 0, (), "No complete post-trade review evidence yet.", scope)
        pnl_by_trade = {item.trade_id: Decimal(item.net_pnl) for item in scores}
        components = self._period_components(pillar, sample, historical_events, pnl_by_trade)
        values = [value for _, value in components]
        # Exclude any component that couldn't be computed and renormalize over what's
        # available, rather than nulling the whole pillar the moment one is missing.
        # No current component can actually return None here (each has an unconditional
        # fallback given a non-empty sample), but this keeps a future one degrading
        # gracefully instead of silently blanking the pillar.
        available = [(value, weight) for value, weight in zip(values, PERIOD_WEIGHTS[pillar], strict=True) if value is not None]
        weight_total = sum((weight for _, weight in available), Decimal("0"))
        raw = None if not available else sum((value * weight for value, weight in available), Decimal("0")) / weight_total
        hard_block = any(getattr(item, f"{pillar}_hard_block") for item in sample)
        critical = sum(1 for item in sample if self._is_critical_violation(pillar, item))
        settings = self._repository.get_framework_rule_settings()
        reviewed_after_critical, last_critical_date = self._review_after_last_critical(pillar, sample)
        capped = critical >= settings.repeated_critical_threshold and not reviewed_after_critical
        score = None if raw is None else min(raw, Decimal("59")) if capped else raw
        status = "fail" if hard_block else "caution" if capped else "incomplete" if len(sample) < window else "ready"
        formatted = tuple((name, None if value is None else _decimal_text(Decimal(value))) for name, value in components)
        detail = f"{len(sample)} of {window} complete review(s) in this rolling sample."
        if hard_block:
            detail += " A hard-rule failure overrides the numeric score."
        elif capped:
            detail += (
                f" Repeated critical violations cap this pillar at 59 until a period review is saved after the last one on {last_critical_date}."
                if last_critical_date is not None
                else " Repeated critical violations cap this pillar at 59 until a period review is saved."
            )
        return PillarScore(pillar, None if score is None else _decimal_text(score), None if raw is None else _decimal_text(raw), status, len(reviewed), len(sample), unreviewed, automatic, hard_block, critical, formatted, detail, scope)

    def _period_components(
        self,
        pillar: str,
        sample: list[TradeProcessScore],
        historical_events: dict[int, dict[str, object]] | None,
        pnl_by_trade: dict[int, Decimal],
    ) -> tuple[tuple[str, Decimal | None], ...]:
        grades = {item.trade_id: item.criterion_grades for item in sample if item.criterion_grades is not None}
        if pillar == "psychology":
            rules = self._average_grade((grades[item.trade_id] for item in sample), "rule_adherence")
            impulse = self._average_grade((grades[item.trade_id] for item in sample), "impulse_control")
            emotion = self._average_grade((grades[item.trade_id] for item in sample), "emotional_control")
            after_loss = self._post_loss_discipline(sample, grades, pnl_by_trade)
            return (("Rule adherence", rules), ("Impulse control", impulse), ("Emotional control", emotion), ("Post-loss discipline", after_loss))
        if pillar == "risk":
            policy = self._average_grade((grades[item.trade_id] for item in sample), "policy_adherence")
            stop = self._average_grade((grades[item.trade_id] for item in sample), "stop_discipline")
            exposure = self._average_grade((grades[item.trade_id] for item in sample), "exposure_limit_compliance")
            limits = self._risk_limit_component(sample, historical_events)
            return (("Policy adherence", policy), ("Stop discipline", stop), ("Limit compliance", limits), ("Exposure control", exposure))
        setup = self._average_grade((grades[item.trade_id] for item in sample), "setup_validity")
        execution_values = []
        for item in sample:
            execution_values.extend(GRADE_VALUES[grades[item.trade_id][key]] for key in ("entry_fidelity", "invalidation_fidelity", "management_exit_fidelity"))
        execution = sum(execution_values, Decimal("0")) / len(execution_values) if execution_values else None
        context = self._average_grade((grades[item.trade_id] for item in sample), "context_alignment")
        evidence_quality = self._strategy_evidence_component(sample)
        edge = evidence_quality
        return (("Setup validity", setup), ("Execution fidelity", execution), ("Context alignment", context), ("Evidence quality", evidence_quality), ("Edge evidence", edge))

    @staticmethod
    def _average_grade(assessment_iter, criterion: str) -> Decimal | None:  # type: ignore[no-untyped-def]
        values = [GRADE_VALUES[assessment[criterion]] for assessment in assessment_iter]
        return sum(values, Decimal("0")) / len(values) if values else Decimal("100")

    @staticmethod
    def _post_loss_discipline(
        sample: list[TradeProcessScore], assessments: dict[int, dict[str, str]], pnl_by_trade: dict[int, Decimal]
    ) -> Decimal | None:
        values = []
        ordered = sorted(sample, key=lambda item: (item.exit_time, item.trade_id))
        for previous, current in zip(ordered, ordered[1:], strict=False):
            if pnl_by_trade.get(previous.trade_id, Decimal("0")) >= 0:
                continue
            if "post_loss_reset" in current.violation_codes:
                values.append(Decimal("0"))
                continue
            grade = assessments[current.trade_id]["impulse_control"]
            values.append(GRADE_VALUES[grade])
        return sum(values, Decimal("0")) / len(values) if values else Decimal("100")

    @staticmethod
    def _risk_limit_component(sample: list[TradeProcessScore], events: dict[int, dict[str, object]] | None) -> Decimal | None:
        if events is None:
            return None
        values = [Decimal("0") if events.get(item.trade_id, {}).get("events") else Decimal("100") for item in sample]
        return sum(values, Decimal("0")) / len(values) if values else None

    @staticmethod
    def _strategy_evidence_component(sample: list[TradeProcessScore]) -> Decimal | None:
        values = []
        for item in sample:
            strategy = item.mapped_strategy
            if strategy is None:
                values.append(Decimal("100") if item.review_kind == "manual_review" else Decimal("50"))
                continue
            values.append(Decimal("100") if strategy.backtest_verified else Decimal("0"))
        return sum(values, Decimal("0")) / len(values) if values else None

    def _review_after_last_critical(self, pillar: str, sample: list[TradeProcessScore]) -> tuple[bool, str | None]:
        """Return whether a period review has been saved since the last critical violation, and that violation's date."""
        critical_items = [item for item in sample if self._is_critical_violation(pillar, item)]
        last_critical_item = max(critical_items, key=lambda item: item.exit_time, default=None)
        if last_critical_item is None:
            return True, None
        last_critical = last_critical_item.exit_time
        last_critical_date = self._trade_date(last_critical, last_critical_item.server_utc_offset_minutes).isoformat()
        account_ids = {item.id for item in self._repository.list_mt5_accounts()} if pillar == "psychology" else {sample[-1].account_id}
        reviewed_after = any(
            review.created_at > last_critical
            for account_id in account_ids
            for review in self._repository.list_framework_period_reviews(account_id)
        )
        return reviewed_after, last_critical_date

    @staticmethod
    def _is_critical_violation(pillar: str, score: TradeProcessScore) -> bool:
        return bool(CRITICAL_VIOLATIONS[pillar] & (set(score.violation_codes) | set(score.hard_rule_codes)))

    def _trade_process_score(
        self,
        account_id: int,
        trade: ClosedTradeReviewItem,
        assessment: PostTradeAssessmentView | None,
        policies: dict[int, AccountRiskPolicyView],
        account_strategy: StrategyProfileView,
        funded: str | None,
        active_policy: AccountRiskPolicyView | None,
        automatic_risk_events: tuple[str, ...],
        shutdown_candidates: tuple[str, ...],
    ) -> TradeProcessScore:
        auto_risk = self._auto_risk_evidence(trade, policies, funded, active_policy)
        policy = self._risk_policy_for_trade(assessment, trade, policies, active_policy)
        policy_risk = _decimal_text(self._maximum_risk_amount(funded, policy)) if funded is not None and policy is not None else None
        # Only a manual assessment's declared risk is a human-reviewed override — an "auto" row's
        # declared_actual_risk_amount is just an approval-time audit snapshot, not evidence to display.
        manual_declared_risk = assessment.declared_actual_risk_amount if assessment is not None and assessment.method == "manual" else None
        actual_risk = manual_declared_risk if manual_declared_risk else auto_risk.source_amount
        risk_evidence_source = "reviewed_actual_risk" if manual_declared_risk else auto_risk.risk_basis
        risk_policy_state = self._risk_policy_state(actual_risk, policy_risk)
        mapped = account_strategy
        if assessment is None or assessment.method == "auto":
            if assessment is not None:
                state, kind, grades = "reviewed", "approved_auto_review", assessment.criterion_grades
            elif auto_risk.state == "within_policy":
                state, kind, grades = "not_scored", "auto_review", None
            else:
                state, kind, grades = "not_scored", "needs_approval", None
            if grades is not None:
                psychology = self._pillar_score_from_grades(grades, "psychology")
                risk = self._pillar_score_from_grades(grades, "risk")
                system = self._pillar_score_from_grades(grades, "system")
                overall = (psychology + risk + system) / Decimal("3")
                quality = self._quality_status(overall, "PASS")
                classification = self._classification(Decimal(trade.net_pnl), quality)
            else:
                psychology = risk = system = overall = None
                quality = classification = None
            return TradeProcessScore(
                account_id,
                trade.id,
                trade.net_pnl,
                trade.exit_time,
                trade.server_utc_offset_minutes,
                state,
                kind,
                grades,
                None if psychology is None else _decimal_text(psychology),
                None if risk is None else _decimal_text(risk),
                None if system is None else _decimal_text(system),
                None if overall is None else _decimal_text(overall),
                "PASS" if grades is not None else None,
                quality,
                classification,
                False,
                False,
                False,
                (),
                (),
                automatic_risk_events,
                shutdown_candidates,
                auto_risk,
                policy_risk,
                actual_risk,
                risk_policy_state,
                risk_evidence_source,
                mapped,
                None,
                None,
                None,
                direction=trade.direction,
            )
        psychology = self._trade_pillar_score(assessment, "psychology")
        risk = self._trade_pillar_score(assessment, "risk")
        system = self._trade_pillar_score(assessment, "system")
        # Closed-position limits are retrospective monitoring evidence. They
        # show when a limit was reached, but cannot prove that this individual
        # logical trade was deliberately taken after a shutdown. The repository
        # persists only enabled hard-rule events at save time, making a saved
        # review's hard status stable when framework settings later change.
        hard_rules = set(assessment.hard_rule_codes)
        psychology_hard = "oversized_revenge" in hard_rules
        risk_hard = bool({"oversized_revenge", "stop_widened", "shutdown_breach"} & hard_rules)
        system_hard = "mandatory_setup_absent" in hard_rules
        status = "FAIL" if hard_rules else "PASS"
        overall = (psychology + risk + system) / Decimal("3")
        quality_status = self._quality_status(overall, status)
        classification = self._classification(Decimal(trade.net_pnl), quality_status)
        return TradeProcessScore(
            account_id, trade.id, trade.net_pnl, trade.exit_time, trade.server_utc_offset_minutes, "reviewed", "manual_review", assessment.criterion_grades, _decimal_text(psychology), _decimal_text(risk), _decimal_text(system), _decimal_text(overall), status, quality_status, classification,
            psychology_hard,
            risk_hard,
            system_hard,
            tuple(sorted(hard_rules)),
            assessment.violation_codes,
            automatic_risk_events,
            shutdown_candidates,
            auto_risk,
            policy_risk,
            actual_risk,
            risk_policy_state,
            risk_evidence_source,
            assessment.strategy_snapshot,
            assessment.setup_snapshot,
            assessment.session_snapshot,
            assessment.regime_snapshot,
            direction=trade.direction,
        )

    @staticmethod
    def _automatic_review_grades(risk_policy_state: str) -> dict[str, str]:
        grades = {criterion: "partial" for criterion in PSYCHOLOGY_CRITERIA + RISK_CRITERIA + SYSTEM_CRITERIA}
        if risk_policy_state == "within_policy":
            grades["policy_adherence"] = "pass"
        elif risk_policy_state == "over_policy":
            grades["policy_adherence"] = "fail"
        return grades

    @staticmethod
    def _trade_pillar_score(assessment: PostTradeAssessmentView, pillar: str) -> Decimal:
        return sum((GRADE_VALUES[assessment.criterion_grades[key]] * weight for key, weight in TRADE_WEIGHTS[pillar]), Decimal("0"))

    @staticmethod
    def _pillar_score_from_grades(grades: dict[str, str], pillar: str) -> Decimal:
        return sum((GRADE_VALUES[grades[key]] * weight for key, weight in TRADE_WEIGHTS[pillar]), Decimal("0"))

    @staticmethod
    def _quality_status(overall: Decimal, hard_rule_status: str) -> str:
        if hard_rule_status == "FAIL":
            return "bad"
        return "good" if overall >= GOOD_PROCESS_SCORE else "needs_improvement"

    @staticmethod
    def _classification(net_pnl: Decimal, quality_status: str) -> str:
        quality = {
            "good": "Good",
            "needs_improvement": "Needs improvement",
            "bad": "Bad",
        }[quality_status]
        outcome = "Win" if net_pnl > 0 else "Loss" if net_pnl < 0 else "Breakeven"
        return f"{quality} {outcome}"

    @staticmethod
    def _risk_policy_state(amount: str | None, policy_limit: str | None) -> str:
        if amount is None or policy_limit is None:
            return "unavailable"
        return "within_policy" if Decimal(amount) <= Decimal(policy_limit) else "over_policy"

    def _historical_risk_events(self, account_id: int) -> dict[int, dict[str, object]]:
        if account_id in self._raw_risk_event_cache:
            return self._raw_risk_event_cache[account_id]
        trades = tuple(self._repository.list_imported_positions_for_risk(account_id))
        policies = self._policies_for(trades, {}, account_id)
        events = self._historical_risk_events_from_context(
            trades,
            {},
            policies,
            self._repository.get_active_risk_policy(account_id),
            self._repository.get_account_funded_capital(account_id),
        )
        self._raw_risk_event_cache[account_id] = events
        return events

    @staticmethod
    def _combine_member_risk_events(trade: ClosedTradeReviewItem, raw_events: dict[int, dict[str, object]]) -> dict[str, object]:
        members = [raw_events[member.id] for member in trade.members]
        latest = max(members, key=lambda item: item["date"])
        return {
            "date": latest["date"],
            "result_r": sum((Decimal(item["result_r"]) for item in members), Decimal("0")),
            "drawdown": latest["drawdown"],
            "streak": latest["streak"],
            "events": tuple(sorted({event for item in members for event in item["events"]})),
            "shutdown_candidates": tuple(
                sorted({event for item in members for event in item["shutdown_candidates"]})
            ),
        }

    def _historical_risk_events_from_context(self, trades, assessments, policies, active_policy, funded):  # type: ignore[no-untyped-def]
        events: dict[int, dict[str, object]] = {}
        if funded is None:
            return {
                trade.id: {
                    "date": self._trade_date(trade.exit_time, trade.server_utc_offset_minutes),
                    "result_r": Decimal("0"),
                    "drawdown": Decimal("0"),
                    "streak": 0,
                    "events": (),
                    "shutdown_candidates": (),
                }
                for trade in trades
            }
        capital = Decimal(funded)
        balance = capital
        peak = capital
        daily: dict[date, Decimal] = {}
        weekly: dict[date, Decimal] = {}
        streak = 0
        daily_limit_reached_at: dict[date, datetime] = {}
        weekly_limit_reached_at: dict[date, datetime] = {}
        drawdown_limit_reached_at: datetime | None = None
        loss_streak_limit_reached_at: datetime | None = None
        for trade in trades:
            entry_at = self._as_utc_datetime(trade.entry_time)
            assessed = assessments.get(trade.id)
            policy = self._risk_policy_for_trade(assessed, trade, policies, active_policy)
            standard_risk_amount = self._standard_risk_amount(funded, policy)
            # Daily and weekly limits are expressed in policy-standard R. Keep
            # that denominator stable across every position; trade-specific
            # actual risk belongs to execution/adherence review and would make
            # the account-level loss limit change meaning from trade to trade.
            result_r = (
                Decimal(trade.net_pnl) / standard_risk_amount
                if standard_risk_amount > 0
                else Decimal("0")
            )
            trade_day = self._trade_date(trade.exit_time, trade.server_utc_offset_minutes)
            week_start = trade_day - timedelta(days=trade_day.weekday())
            entry_day = self._trade_date(trade.entry_time, trade.server_utc_offset_minutes)
            entry_week_start = entry_day - timedelta(days=entry_day.weekday())
            shutdown_candidates: set[str] = set()
            if (reached_at := daily_limit_reached_at.get(entry_day)) is not None and entry_at > reached_at:
                shutdown_candidates.add("daily_limit")
            if (reached_at := weekly_limit_reached_at.get(entry_week_start)) is not None and entry_at > reached_at:
                shutdown_candidates.add("weekly_limit")
            if drawdown_limit_reached_at is not None and entry_at > drawdown_limit_reached_at:
                shutdown_candidates.add("drawdown_limit")
            if loss_streak_limit_reached_at is not None and entry_at > loss_streak_limit_reached_at:
                shutdown_candidates.add("loss_streak")
            daily[trade_day] = daily.get(trade_day, Decimal("0")) + result_r
            weekly[week_start] = weekly.get(week_start, Decimal("0")) + result_r
            balance += Decimal(trade.net_pnl)
            peak = max(peak, balance)
            drawdown = (peak - balance) / peak * Decimal("100") if peak > 0 else Decimal("0")
            streak = streak + 1 if Decimal(trade.net_pnl) < 0 else 0
            breach: set[str] = set()
            exited_at = self._as_utc_datetime(trade.exit_time)
            if policy is not None:
                if daily[trade_day] <= -Decimal(policy.daily_loss_limit_r) and trade_day not in daily_limit_reached_at:
                    breach.add("daily_limit")
                    daily_limit_reached_at[trade_day] = exited_at
                if weekly[week_start] <= -Decimal(policy.weekly_loss_limit_r) and week_start not in weekly_limit_reached_at:
                    breach.add("weekly_limit")
                    weekly_limit_reached_at[week_start] = exited_at
                if drawdown >= Decimal(policy.max_drawdown_percent):
                    if drawdown_limit_reached_at is None:
                        breach.add("drawdown_limit")
                        drawdown_limit_reached_at = exited_at
                else:
                    # A recovered balance ends the advisory drawdown shutdown.
                    # Historical breach evidence remains attached to the
                    # threshold trade, but later entries are no longer
                    # candidates until a new drawdown reaches the limit.
                    drawdown_limit_reached_at = None
                if streak >= policy.max_consecutive_losses and loss_streak_limit_reached_at is None:
                    breach.add("loss_streak")
                    loss_streak_limit_reached_at = exited_at
            if Decimal(trade.net_pnl) >= 0:
                loss_streak_limit_reached_at = None
            events[trade.id] = {
                "date": trade_day,
                "result_r": result_r,
                "drawdown": drawdown,
                "streak": streak,
                "events": tuple(sorted(breach)),
                "shutdown_candidates": tuple(sorted(shutdown_candidates)),
            }
        return events

    @staticmethod
    def _as_utc_datetime(value: str) -> datetime:
        parsed = datetime.fromisoformat(value)
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)

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
    def _risk_amount(assessment, trade, fallback: Decimal, policy: AccountRiskPolicyView | None) -> Decimal:  # type: ignore[no-untyped-def]
        if assessment is not None and assessment.method == "manual" and assessment.declared_actual_risk_amount is not None:
            return Decimal(assessment.declared_actual_risk_amount)
        if (value := FrameworkService._specific_preset_sl_amount(trade)) is not None:
            return Decimal(value)
        if (value := FrameworkService._real_loss_sl_amount(trade)) is not None:
            return Decimal(value)
        if (value := FrameworkService._pretrade_account_balance_sl_amount(trade, policy)) is not None:
            return Decimal(value)
        return fallback

    @staticmethod
    def _specific_preset_sl_amount(trade) -> str | None:  # type: ignore[no-untyped-def]
        try:
            amount = Decimal(trade.initial_risk_amount) if trade.initial_risk_amount is not None else Decimal("0")
        except ArithmeticError:
            return None
        return _decimal_text(amount) if amount.is_finite() and amount > 0 else None

    @staticmethod
    def _real_loss_sl_amount(trade) -> str | None:  # type: ignore[no-untyped-def]
        if FrameworkService._specific_preset_sl_amount(trade) is not None:
            return None
        return _decimal_text(-Decimal(trade.net_pnl)) if Decimal(trade.net_pnl) < 0 else None

    @staticmethod
    def _pretrade_account_balance_sl_amount(trade, policy: AccountRiskPolicyView | None) -> str | None:  # type: ignore[no-untyped-def]
        if (
            policy is None
            or not policy.pretrade_balance_auto_evidence_enabled
            or FrameworkService._specific_preset_sl_amount(trade) is not None
            or trade.entry_stop_price is not None
            or Decimal(trade.net_pnl) <= 0
            or trade.pretrade_account_balance is None
        ):
            return None
        amount = Decimal(trade.pretrade_account_balance)
        return _decimal_text(amount) if amount > 0 else None

    def _auto_risk_evidence(self, trade, policies, funded, active_policy):  # type: ignore[no-untyped-def]
        members = trade.members
        member_sources: list[tuple[str, Decimal]] = []
        unavailable = 0
        specific_total = Decimal("0")
        real_loss_total = Decimal("0")
        pretrade_balance_amount: Decimal | None = None
        reward_total = Decimal("0")
        all_specific = True
        all_rewards = True
        observed_stops: list[bool | None] = []
        earliest_member = min(members, key=lambda item: (item.entry_time, item.id))
        earliest_policy = policies.get(earliest_member.auto_risk_policy_id) or active_policy
        group_pretrade = self._pretrade_account_balance_sl_amount(earliest_member, earliest_policy)
        needs_group_pretrade = False
        for member in members:
            specific = self._specific_preset_sl_amount(member)
            real_loss = self._real_loss_sl_amount(member)
            observed_stops.append(self._observed_stop_widened(member))
            if specific is not None:
                amount = Decimal(specific)
                specific_total += amount
                member_sources.append(("specific_preset_sl", amount))
                if member.initial_reward_amount is not None and Decimal(member.initial_reward_amount) > 0:
                    reward_total += Decimal(member.initial_reward_amount)
                else:
                    all_rewards = False
            elif real_loss is not None:
                all_specific = False
                amount = Decimal(real_loss)
                real_loss_total += amount
                member_sources.append(("real_loss_sl", amount))
                all_rewards = False
            else:
                all_specific = False
                all_rewards = False
                needs_group_pretrade = True
        if needs_group_pretrade and group_pretrade is not None:
            # A grouped idea has one opening balance: the snapshot captured for
            # its earliest member. Never add it once for every scaled entry.
            pretrade_balance_amount = Decimal(group_pretrade)
            member_sources.append(("pretrade_account_balance_sl", pretrade_balance_amount))
        elif needs_group_pretrade:
            unavailable += 1
        specific = _decimal_text(specific_total) if specific_total > 0 else None
        real_loss = _decimal_text(real_loss_total) if real_loss_total > 0 else None
        pretrade = None if pretrade_balance_amount is None else _decimal_text(pretrade_balance_amount)
        amount = pretrade_balance_amount if pretrade_balance_amount is not None else specific_total + real_loss_total
        bases = {basis for basis, _ in member_sources}
        basis = next(iter(bases)) if len(bases) == 1 else "mixed_sources" if bases else "unavailable"
        confidence = "verified" if all_specific and member_sources else "conservative" if pretrade_balance_amount is not None else "inferred" if basis == "real_loss_sl" else "mixed" if bases else "unavailable"
        observed_stop = True if any(value is True for value in observed_stops) else False if observed_stops and all(value is False for value in observed_stops) else None
        initial_reward = _decimal_text(reward_total) if all_rewards and reward_total > 0 else None
        initial_rr = _decimal_text(reward_total / specific_total) if all_specific and all_rewards and specific_total > 0 else None
        policy = policies.get(trade.auto_risk_policy_id) or active_policy
        if not member_sources:
            return AutoRiskEvidence("unavailable", "No usable automatic risk source is available.", specific, real_loss, pretrade, basis, confidence, initial_reward, initial_rr, observed_stop, None if policy is None else policy.version)
        source_description = {
            "specific_preset_sl": "Specific preset SL",
            "real_loss_sl": "Real-loss estimate",
            "pretrade_account_balance_sl": "Pre-trade-balance estimate",
            "mixed_sources": "Mixed automatic estimates",
        }[basis]
        amount_text = _decimal_text(amount)
        pretrade_balance_note = (
            " The pre-trade-balance fallback is applied once for the logical trade, not once per position."
            if pretrade_balance_amount is not None and trade.position_count > 1
            else ""
        )
        if unavailable:
            return AutoRiskEvidence(
                "unavailable",
                f"{source_description} totals {amount_text} across {len(member_sources)} of {trade.position_count} position(s); policy compliance is unavailable until every member has risk evidence or you enter Actual risk.{pretrade_balance_note}",
                specific,
                real_loss,
                pretrade,
                basis,
                confidence,
                initial_reward,
                initial_rr,
                observed_stop,
                None if policy is None else policy.version,
            )
        if policy is None or funded is None:
            return AutoRiskEvidence("unavailable", f"{source_description} totals {amount_text}. Set funded capital and save a Risk policy to compare it.{pretrade_balance_note}", specific, real_loss, pretrade, basis, confidence, initial_reward, initial_rr, observed_stop, None if policy is None else policy.version)
        limit = self._maximum_risk_amount(funded, policy)
        state = "within_policy" if amount <= limit else "over_policy"
        return AutoRiskEvidence(state, f"{source_description} total {amount_text} is {'within' if state == 'within_policy' else 'over'} policy v{policy.version} limit {_decimal_text(limit)} across {trade.position_count} position(s).{pretrade_balance_note}", specific, real_loss, pretrade, basis, confidence, initial_reward, initial_rr, observed_stop, policy.version)

    @staticmethod
    def _observed_stop_widened(trade) -> bool | None:  # type: ignore[no-untyped-def]
        if trade.entry_stop_price is None or trade.close_stop_price is None:
            return None
        return Decimal(trade.close_stop_price) < Decimal(trade.entry_stop_price) if trade.direction == "long" else Decimal(trade.close_stop_price) > Decimal(trade.entry_stop_price)

    def _reporting_time_basis(self) -> str:
        if self._reporting_time_basis_cache is None:
            self._reporting_time_basis_cache = self._repository.get_journal_settings().reporting_time_basis
        return self._reporting_time_basis_cache

    def _trade_date(self, value: str, server_utc_offset_minutes: int) -> date:
        return reporting_date(value, server_utc_offset_minutes, self._reporting_time_basis())

    def _current_report_date(self, value: datetime, account_id: int) -> date:
        timestamp = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
        account = next((item for item in self._repository.list_mt5_accounts() if item.id == account_id), None)
        offset = 0 if account is None or account.latest_server_utc_offset_minutes is None else account.latest_server_utc_offset_minutes
        return reporting_datetime(timestamp.isoformat(), offset, self._reporting_time_basis()).date()
