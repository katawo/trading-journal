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


def _time_rolling_trend(tmp_path: Path, *, total: int, reviewed: int) -> float:
    repository, account_id = build_scored_account(tmp_path, total=total, reviewed=reviewed)
    service = FrameworkService(repository)
    started = time.perf_counter()
    service.rolling_score_trend(account_id)
    return time.perf_counter() - started


@pytest.mark.perf
def test_rolling_score_trend_scales_linearly_in_reviewed_trades(tmp_path: Path) -> None:
    """Doubling the reviewed history must not quadruple the work.

    Quadratic scoring lands near 4.0; linear scoring near 2.0. The 2.8 bound
    catches a regression while tolerating a noisy machine.
    """
    small = _time_rolling_trend(tmp_path / "small", total=1000, reviewed=400)
    large = _time_rolling_trend(tmp_path / "large", total=2000, reviewed=800)
    assert large / small < 2.8, f"rolling_score_trend scaled {large / small:.1f}x for 2x the reviewed trades"
