"""Scaling guards for framework scoring.

Marked `perf` and deselected from the default run: these assert on wall-clock
ratios, which is the only cheap way to catch a reintroduced quadratic. Run with
`make test-perf`. See docs/perf-and-correctness-audit-2026-09.md.
"""

from __future__ import annotations

from pathlib import Path
import time

import pytest

from test_risk_baseline_dashboard import configured_repository, position
from trading_journal.application.framework import FrameworkService
from trading_journal.infrastructure.sqlite_repository import (
    ASSESSMENT_CRITERIA,
    SQLiteJournalRepository,
)


def build_scored_account(tmp_path: Path, *, total: int, reviewed: int) -> tuple[SQLiteJournalRepository, int]:
    """An account with `total` imported positions, the first `reviewed` of them approved."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    repository = configured_repository(tmp_path)
    account = repository.find_active_mt5_account("123456", "DemoBroker-Live")
    assert account is not None
    repository.upsert_mt5_positions(
        account.id,
        [
            position(
                str(2000 + index),
                net_pnl="1" if index % 2 else "-1",
                exit_time=f"2026-08-{(index % 27) + 1:02d}T09:00:00+00:00",
            )
            for index in range(total)
        ],
        "positions.csv",
        "scaling-hash",
    )
    policy = repository.get_active_risk_policy(account.id)
    assert policy is not None
    grades = {criterion: "pass" for criterion in ASSESSMENT_CRITERIA}
    for trade in repository.list_closed_trades_for_review(account.id)[:reviewed]:
        repository.approve_auto_review(
            account_id=account.id,
            trade_id=trade.id,
            risk_policy_id=policy.id,
            risk_evidence_source="preset_stop",
            risk_policy_state="within_policy",
            actual_risk_amount="10",
            criterion_grades=grades,
        )
    return repository, account.id


def _time_warm_rolling_trend(tmp_path: Path, *, total: int, reviewed: int) -> float:
    """Time a second `rolling_score_trend` call on an already-warmed service.

    The first call populates `FrameworkService._account_score_cache`, which
    holds the linear database load and score construction. Timing only the
    second call isolates the scoring loop itself from that cache-fill cost —
    a cold measurement dilutes the quadratic term enough to pass even with
    the rescan bug present.
    """
    repository, account_id = build_scored_account(tmp_path, total=total, reviewed=reviewed)
    service = FrameworkService(repository)
    service.rolling_score_trend(account_id)
    started = time.perf_counter()
    service.rolling_score_trend(account_id)
    return time.perf_counter() - started


@pytest.mark.perf
def test_rolling_score_trend_scales_linearly_in_reviewed_trades(tmp_path: Path) -> None:
    """Tripling the reviewed history must not scale the work by ~9x.

    Quadratic scoring predicts ~9x for a 3x input increase; linear scoring
    predicts ~3x. The 4.0 bound sits between the two, so it fails against the
    quadratic implementation while comfortably tolerating a noisy machine
    around the linear result.
    """
    small = _time_warm_rolling_trend(tmp_path / "small", total=1000, reviewed=300)
    large = _time_warm_rolling_trend(tmp_path / "large", total=3000, reviewed=900)
    assert large / small < 4.0, f"rolling_score_trend scaled {large / small:.1f}x for 3x the reviewed trades"


EXPECTED_PILLAR_SCORES = {'psychology': ('100', '100', 'ready', 60, 20, 142, 131), 'risk': ('100', '100', 'ready', 60, 20, 142, 131), 'system': ('70', '70', 'ready', 60, 20, 142, 131)}
EXPECTED_TREND_HEAD = (('2026-08-01T09:00:00+00:00', '100', '100', '70', 'zone_v2'), ('2026-08-01T09:00:00+00:00', '100', '100', '70', 'zone_v2'), ('2026-08-01T09:00:00+00:00', '100', '100', '70', 'zone_v2'), ('2026-08-01T09:00:00+00:00', '100', '100', '70', 'zone_v2'), ('2026-08-02T09:00:00+00:00', '100', '100', '70', 'zone_v2'))
EXPECTED_TREND_TAIL = (('2026-08-25T09:00:00+00:00', '100', '100', '70', 'zone_v2'), ('2026-08-26T09:00:00+00:00', '100', '100', '70', 'zone_v2'), ('2026-08-26T09:00:00+00:00', '100', '100', '70', 'zone_v2'), ('2026-08-27T09:00:00+00:00', '100', '100', '70', 'zone_v2'), ('2026-08-27T09:00:00+00:00', '100', '100', '70', 'zone_v2'))


@pytest.mark.perf
def test_pillar_scores_are_unchanged_by_the_scoring_refactor(tmp_path: Path) -> None:
    """Pin the exact scored output so Tasks 2 and 3 cannot drift it.

    The expected values are generated from the pre-refactor implementation in the
    next step. If this ever fails, the refactor changed a score — that is a bug,
    not a baseline to regenerate.
    """
    repository, account_id = build_scored_account(tmp_path, total=200, reviewed=60)
    scores = {
        item.pillar: (
            item.score, item.raw_score, item.status, item.reviewed_total,
            item.sample_size, item.unreviewed_total, item.automatic_evidence_total,
        )
        for item in FrameworkService(repository).pillar_scores(account_id)
    }
    trend = FrameworkService(repository).rolling_score_trend(account_id)

    assert scores == EXPECTED_PILLAR_SCORES
    assert len(trend) == 60
    assert trend[:5] == EXPECTED_TREND_HEAD
    assert trend[-5:] == EXPECTED_TREND_TAIL
