from trading_journal.domain.review_taxonomy import (
    HARD_RULE_CODES,
    REVIEW_MISTAKE_CODES,
    REVIEW_MISTAKES_BY_PILLAR,
    VIOLATION_CODES,
    VIOLATION_PILLARS,
    violation_sort_key,
)


def test_every_controlled_review_code_has_a_pillar() -> None:
    assert VIOLATION_CODES | HARD_RULE_CODES <= VIOLATION_PILLARS.keys()


def test_current_mistake_order_is_derived_from_the_three_pillars() -> None:
    expected = tuple(
        code
        for pillar in ("psychology", "risk", "system")
        for code in REVIEW_MISTAKES_BY_PILLAR[pillar]
    )

    assert REVIEW_MISTAKE_CODES == expected
    assert len(REVIEW_MISTAKE_CODES) == len(set(REVIEW_MISTAKE_CODES))


def test_issue_ties_use_hard_rule_then_taxonomy_order() -> None:
    assert sorted(
        ("overtrading", "fomo_or_chase", "stop_widened"),
        key=violation_sort_key,
    ) == ["stop_widened", "fomo_or_chase", "overtrading"]
