"""Controlled post-trade mistake taxonomy shared across journal layers."""

from __future__ import annotations


REVIEW_MISTAKES_BY_PILLAR = {
    "psychology": (
        "fomo_or_chase",
        "revenge",
        "overtrading",
        "overconfidence_streak",
        "fear_hesitation",
        "forced_trade",
        "post_loss_reset",
    ),
    "risk": (
        "position_size_too_large",
        "overtrading_positions",
        "correlation_exposure",
        "no_stop_loss",
        "stop_widened",
        "loss_limit_exceeded",
        "shutdown_breach",
    ),
    "system": (
        "mandatory_setup_absent",
        "context_misread",
        "entry_timing",
        "premature_exit",
        "held_loser_too_long",
        "exit_plan_deviation",
    ),
}
REVIEW_MISTAKE_CODES = tuple(
    code
    for pillar in ("psychology", "risk", "system")
    for code in REVIEW_MISTAKES_BY_PILLAR[pillar]
)
LEGACY_VIOLATION_CODES = frozenset(
    {
        "emotional_sizing",
        "ignored_trade_plan",
        "daily_limit",
        "weekly_limit",
        "drawdown_limit",
        "open_exposure",
    }
)
VIOLATION_CODES = frozenset(REVIEW_MISTAKE_CODES) | LEGACY_VIOLATION_CODES
HARD_RULE_CODE_ORDER = (
    "oversized_revenge",
    "mandatory_setup_absent",
    "stop_widened",
    "shutdown_breach",
)
HARD_RULE_CODES = frozenset(HARD_RULE_CODE_ORDER)
VIOLATION_PILLARS = {
    **{
        code: frozenset({pillar})
        for pillar, codes in REVIEW_MISTAKES_BY_PILLAR.items()
        for code in codes
    },
    "emotional_sizing": frozenset({"psychology"}),
    "ignored_trade_plan": frozenset({"system"}),
    "daily_limit": frozenset({"risk"}),
    "weekly_limit": frozenset({"risk"}),
    "drawdown_limit": frozenset({"risk"}),
    "open_exposure": frozenset({"risk"}),
    "oversized_revenge": frozenset({"psychology", "risk"}),
}
_ANALYTICS_ALIASES = {
    "daily_limit": "loss_limit_exceeded",
    "weekly_limit": "loss_limit_exceeded",
    "drawdown_limit": "loss_limit_exceeded",
}
_ISSUE_PRIORITY = {
    code: index
    for index, code in enumerate(
        dict.fromkeys((*HARD_RULE_CODE_ORDER, *REVIEW_MISTAKE_CODES, *sorted(LEGACY_VIOLATION_CODES)))
    )
}


def canonical_violation_code(code: str) -> str:
    """Return the current analytics code for a stored legacy or current code."""

    return _ANALYTICS_ALIASES.get(code, code)


def violation_pillars(code: str) -> frozenset[str]:
    """Return the framework pillars to which a mistake or hard rule belongs."""

    return VIOLATION_PILLARS.get(code, frozenset())


def violation_sort_key(code: str) -> tuple[int, str]:
    """Return a stable safety-and-taxonomy order for equally frequent issues."""

    return (_ISSUE_PRIORITY.get(code, len(_ISSUE_PRIORITY)), code)
