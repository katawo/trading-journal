from types import SimpleNamespace

import pytest

from trading_journal.application.framework import (
    AccountFrameworkSnapshot,
    CoachingFocusPlan,
    CoachingRecommendation,
    FrameworkService,
)
from trading_journal.domain.models import MT5PositionExport
from trading_journal.infrastructure.sqlite_repository import SQLiteJournalRepository


def _snapshot_stub(account_id: int, **values):
    snapshot = object.__new__(AccountFrameworkSnapshot)
    object.__setattr__(snapshot, "account_id", account_id)
    for name, value in values.items():
        object.__setattr__(snapshot, name, value)
    return snapshot


def test_account_framework_snapshot_loads_trade_history_once(monkeypatch, tmp_path):
    database_path = tmp_path / "journal.db"
    repository = SQLiteJournalRepository(database_path)
    repository.initialize()
    repository.register_mt5_account(
        display_name="Primary",
        login="snapshot-once",
        broker_server="DemoBroker-Live",
        account_currency="USD",
        export_file_path="",
    )
    account = repository.find_active_mt5_account("snapshot-once", "DemoBroker-Live")
    assert account is not None

    original = repository.list_closed_trades_for_review
    calls = 0

    def counted(account_id):
        nonlocal calls
        calls += 1
        return original(account_id)

    monkeypatch.setattr(repository, "list_closed_trades_for_review", counted)

    snapshot = FrameworkService(repository).account_snapshot(account.id)

    assert snapshot.account_id == account.id
    assert snapshot.review_queue_count == 0
    assert calls == 1


def test_account_framework_snapshots_never_mix_account_history(tmp_path):
    repository = SQLiteJournalRepository(tmp_path / "journal.db")
    repository.initialize()
    for name, login in (("Primary", "account-a"), ("Secondary", "account-b")):
        repository.register_mt5_account(
            display_name=name,
            login=login,
            broker_server="DemoBroker-Live",
            account_currency="USD",
            export_file_path="",
            opening_balance="1000" if login == "account-b" else None,
        )
    first = repository.find_active_mt5_account("account-a", "DemoBroker-Live")
    second = repository.find_active_mt5_account("account-b", "DemoBroker-Live")
    assert first is not None and second is not None
    repository.save_account_risk_policy(
        account_id=second.id,
        standard_risk_per_trade_percent="1",
        maximum_risk_per_trade_percent="1",
        daily_loss_limit_r="2",
        weekly_loss_limit_r="4",
        max_drawdown_percent="10",
        max_open_risk_r="2",
        max_consecutive_losses=3,
        minimum_rr="1.5",
        correlation_policy=None,
    )
    second_focus = repository.save_framework_focus(
        account_id=second.id,
        pillar="psychology",
        metric_kind="manual_evidence",
        metric_code=None,
        hypothesis="Collect account-specific evidence.",
        action_text="Review the next five trades.",
        baseline_value="0",
        target_value="5",
        target_reviews=5,
        starting_manual_reviews=0,
    )
    repository.upsert_mt5_positions(
        second.id,
        [
            MT5PositionExport(
                schema_version=1,
                account_login="account-b",
                broker_server="DemoBroker-Live",
                account_currency="USD",
                position_id="second-only",
                symbol="XAUUSD",
                direction="long",
                entry_time="2026-09-03T08:00:00+00:00",
                exit_time="2026-09-03T09:00:00+00:00",
                entry_price="3300",
                exit_price="3310",
                volume="0.01",
                gross_pnl="10",
                commission="0",
                swap="0",
                fees="0",
                net_pnl="10",
            )
        ],
        "positions.csv",
        "account-isolation-test",
    )

    first_snapshot = FrameworkService(repository).account_snapshot(first.id)
    second_snapshot = FrameworkService(repository).account_snapshot(second.id)

    assert first_snapshot.account_id == first.id
    assert first_snapshot.review_queue_count == 0
    assert second_snapshot.account_id == second.id
    assert second_snapshot.review_queue_count == 1
    assert first_snapshot.risk.configured is False
    assert second_snapshot.risk.configured is True
    assert "risk_unconfigured" in {alert.code for alert in first_snapshot.alerts}
    assert "risk_unconfigured" not in {alert.code for alert in second_snapshot.alerts}
    assert first_snapshot.focus is None
    assert second_snapshot.focus is not None
    assert second_snapshot.focus.id == second_focus.id
    assert {score.unreviewed_total for score in first_snapshot.pillar_scores} == {0}
    assert {score.unreviewed_total for score in second_snapshot.pillar_scores} == {1}
    assert first_snapshot.readiness == FrameworkService(repository).readiness(first.id)
    assert second_snapshot.readiness == FrameworkService(repository).readiness(second.id)


def test_account_framework_snapshot_rejects_another_account():
    snapshot = _snapshot_stub(2)

    with pytest.raises(ValueError, match="does not match account"):
        snapshot.require_account(1)


def test_account_framework_snapshot_rejects_a_coaching_plan_from_another_account():
    recommendation = CoachingRecommendation(
        pillar="psychology",
        metric_kind="manual_evidence",
        metric_code=None,
        hypothesis="Collect evidence.",
        action_text="Review trades.",
        baseline_value="0",
        target_value="5",
        target_reviews=5,
        reason="Build a reviewed sample.",
    )
    plan = CoachingFocusPlan(
        account_id=2,
        recommendation=recommendation,
        starting_manual_reviews=0,
    )

    with pytest.raises(ValueError, match="does not match framework snapshot account"):
        AccountFrameworkSnapshot(
            account_id=1,
            alerts=(),
            review_queue_count=0,
            focus=None,
            focus_progress=None,
            risk=SimpleNamespace(),
            pillar_scores=(),
            readiness=SimpleNamespace(),
            coaching_focus_plan=plan,
        )


def test_framework_renderers_reject_a_snapshot_from_another_account():
    import app as journal_app
    from trading_journal.presentation.framework import render_framework_dashboard

    account = SimpleNamespace(id=1, display_name="Primary")
    snapshot = _snapshot_stub(2)

    with pytest.raises(ValueError, match="does not match account"):
        journal_app.render_account_framework_alert_bubble(account, snapshot)
    with pytest.raises(ValueError, match="does not match account"):
        render_framework_dashboard(SimpleNamespace(), account, snapshot)


def test_dashboard_coaching_focus_skips_service_while_cached_plan_is_empty(monkeypatch):
    from trading_journal.presentation import framework as framework_presentation

    class UnexpectedFrameworkService:
        def __init__(self, repo):
            pytest.fail("Collapsed coaching with no plan must not build FrameworkService")

    fake_streamlit = SimpleNamespace(
        context=SimpleNamespace(theme=SimpleNamespace(type="light")),
        get_option=lambda key: "#ffffff",
        markdown=lambda *args, **kwargs: None,
        expander=lambda *args, **kwargs: SimpleNamespace(open=False),
    )
    snapshot = _snapshot_stub(7, coaching_focus_plan=None)
    monkeypatch.setattr(framework_presentation, "FrameworkService", UnexpectedFrameworkService)
    monkeypatch.setattr(framework_presentation, "st", fake_streamlit)

    framework_presentation.render_dashboard_coaching_focus(
        SimpleNamespace(),
        SimpleNamespace(id=7),
        snapshot,
    )


def test_dashboard_coaching_focus_applies_cached_plan_without_recomputing(monkeypatch):
    from trading_journal.presentation import framework as framework_presentation

    applied = []

    class StubFrameworkService:
        def __init__(self, repo):
            pass

        def apply_coaching_focus_plan(self, plan):
            applied.append(plan)

    fake_streamlit = SimpleNamespace(
        context=SimpleNamespace(theme=SimpleNamespace(type="light")),
        get_option=lambda key: "#ffffff",
        markdown=lambda *args, **kwargs: None,
        expander=lambda *args, **kwargs: SimpleNamespace(open=False),
    )
    plan = SimpleNamespace(account_id=7)
    snapshot = _snapshot_stub(7, coaching_focus_plan=plan)
    monkeypatch.setattr(framework_presentation, "FrameworkService", StubFrameworkService)
    monkeypatch.setattr(framework_presentation, "st", fake_streamlit)

    framework_presentation.render_dashboard_coaching_focus(
        SimpleNamespace(),
        SimpleNamespace(id=7),
        snapshot,
    )

    assert applied == [plan]


def test_dashboard_reuses_the_entrypoint_framework_snapshot(monkeypatch):
    import app as journal_app

    snapshot = _snapshot_stub(7)
    fake_streamlit = SimpleNamespace(
        session_state={journal_app._ACTIVE_FRAMEWORK_SNAPSHOT_KEY: snapshot}
    )
    monkeypatch.setattr(journal_app, "st", fake_streamlit)
    monkeypatch.setattr(
        journal_app,
        "build_account_framework_snapshot",
        lambda *args, **kwargs: pytest.fail("The Dashboard must reuse the entrypoint snapshot"),
    )

    assert journal_app._dashboard_framework_snapshot(SimpleNamespace(), account_id=7) is snapshot


def test_cached_dashboard_report_closes_its_temporary_repository(monkeypatch):
    import app as journal_app

    closed = []

    class StubRepository:
        def __init__(self, database_path):
            pass

        def close(self):
            closed.append(True)

    class StubDashboardService:
        def __init__(self, repo):
            pass

        def build_report(self, *, account_id):
            return account_id

    monkeypatch.setattr(journal_app, "SQLiteJournalRepository", StubRepository)
    monkeypatch.setattr(journal_app, "DashboardService", StubDashboardService)
    monkeypatch.setattr(journal_app, "asdict", lambda result: {"account_id": result})

    result = journal_app._cached_dashboard_report(
        "temporary-dashboard-repository",
        (1, 2, 3, 4),
        7,
    )

    assert result == {"account_id": 7}
    assert closed == [True]
