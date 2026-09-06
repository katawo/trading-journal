# Trade Compass Performance & Correctness Fixes — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the quadratic framework-scoring cost that makes the Bearings → Monitor page unusable at a few thousand reviewed trades, then close the verified correctness gaps around drawdown maths, import reporting, the hosted local calendar, and the stale domain documentation.

**Architecture:** Phase 1 is performance and is behaviour-preserving — every task pins current output with a characterization test first, then changes the implementation underneath it. The central change replaces repeated whole-history rescans in `FrameworkService` with a single-pass prefix accumulator. Phase 2 is correctness and does change behaviour, so each task states the old and new observable result. Nothing in this plan changes the MT5 export contract, so no `schema_version` bump and no database reset.

**Tech Stack:** Python 3, Streamlit, SQLAlchemy 2 + SQLite (WAL), pydantic v2, pytest. No new dependencies.

**Spec:** `docs/perf-and-correctness-audit-2026-09.md`

## Global Constraints

- No linter or formatter is configured. Match the surrounding file's style by hand — this codebase uses long descriptive names, `from __future__ import annotations`, and explanatory comments that say *why*, not *what*.
- Every user-facing string goes through `tr()` (`presentation/i18n.py`) with the English text as the literal dict key, and gets a matching entry in the `VI` table.
- **Do not grow `app.py`.** New page logic belongs in `presentation/<name>.py` with a thin `app_pages/<name>.py` shim. This plan only edits existing lines in `app.py`; it adds nothing new there.
- Additive **nullable** columns go in the `initialize()` migration block in `infrastructure/sqlite_repository.py`. Never add a column name to `_require_clean_framework_schema()`'s `expected_columns` — that forces an unnecessary `make reset-db`.
- Per-account currency only. No cross-account aggregation, no FX conversion.
- MT5 stays read-only. No order, modify, or cancel path may be introduced.
- Money is `Decimal` end to end. Never let a monetary value pass through `float`.
- Gate for every task: `make check` **and** `make test-web` both pass. `make check` is `pytest -q` (367 tests) plus `compileall`; `make test-web` runs the 53 `web`-marked Streamlit tests that `make check` deselects.
- One commit per task, message in the repo's existing Conventional Commits style (`perf:`, `fix:`, `refactor:`, `docs:`, `test:`).

## File Structure

| File | Responsibility | Phase |
|---|---|---|
| `tests/test_framework_scaling.py` | **new** — `perf`-marked scaling guards for framework scoring | 1 |
| `pyproject.toml` | register the `perf` marker and deselect it by default | 1 |
| `Makefile` | `test-perf` target, matching the existing `test-web` pattern | 1 |
| `src/trading_journal/application/framework.py` | prefix accumulator; drop dead `pnl_by_trade` | 1 |
| `src/trading_journal/infrastructure/sqlite_repository.py` | bounded incident lookup; `count_trades` aggregate; honest skip counts | 1, 2 |
| `src/trading_journal/application/reporting_time.py` | cache `detect_local_timezone` | 1 |
| `src/trading_journal/ingestion_api.py` | initialize each user's schema once per process | 1 |
| `src/trading_journal/application/dashboard.py` | guard non-positive starting balance; accept a `local_zone` | 2 |
| `app.py` | pass the browser zone into the cached dashboard report | 2 |
| `CLAUDE.md`, `docs/mt5-import-scale-audit.md`, `docs/three_pillar_framework_guide*.md` | documentation truth-up | 2 |

---

# Phase 1 — Performance

### Task 1: Scaling harness

Nothing here changes production code. It creates the measuring stick the next two tasks
are judged against, and wires it into the repo's existing marker convention so it never
destabilises `make check`.

**Files:**
- Create: `tests/test_framework_scaling.py`
- Modify: `pyproject.toml:31-37`
- Modify: `Makefile:47-52`

**Interfaces:**
- Produces: `build_scored_account(tmp_path, *, total: int, reviewed: int) -> tuple[SQLiteJournalRepository, int]` — returns a repository and the `account_id`, used by Tasks 2 and 3.

- [ ] **Step 1: Register the `perf` marker**

In `pyproject.toml`, extend the existing `[tool.pytest.ini_options]` block. Both edits matter — adding the marker without deselecting it would put a timing test into `make check`.

```toml
[tool.pytest.ini_options]
addopts = "-m 'not web and not browser and not perf'"
markers = [
  "bdd: scenarios organized explicitly as Given/When/Then behavior",
  "web: maintained Streamlit interaction regression scenarios",
  "browser: tests that execute client-side behavior in a real browser",
  "perf: scaling guards that time framework scoring; wall-clock, so never in the default run",
]
```

- [ ] **Step 2: Add the `make test-perf` target**

In `Makefile`, directly after the `test-browser` target, matching its shape exactly:

```makefile
test-perf: venv ## Run scaling guards for framework scoring.
	$(VENV_PYTHON) -m pytest -q -m perf
```

Then add `test-perf` to the `.PHONY` list on line 19, after `test-browser`.

- [ ] **Step 3: Write the fixture builder and the baseline scaling guard**

Create `tests/test_framework_scaling.py`. The helper reuses the existing fixture helpers from `tests/test_risk_baseline_dashboard.py` rather than inventing a second way to build an account.

```python
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
```

- [ ] **Step 4: Run it and record the pre-fix baseline**

Run: `make test-perf`

Expected: **FAIL**, with a ratio near `4.0` — this is the quadratic being caught. Write the observed ratio into the commit message; Task 3 has to move it under 2.8.

- [ ] **Step 5: Confirm the default run is untouched**

Run: `make check`

Expected: PASS, `367 passed, 55 deselected` — one more deselected than before, because the new `perf` test is excluded.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml Makefile tests/test_framework_scaling.py
git commit -m "test: add a perf-marked scaling guard for framework scoring"
```

---

### Task 2: Remove the dead `pnl_by_trade` plumbing

`_period_components` takes a `pnl_by_trade` argument and never reads it. Building it costs
a `Decimal` per trade per pillar per trend point. Measured: removing it alone took the
2,000-reviewed trend from 9.47s to 3.15s.

**Files:**
- Modify: `src/trading_journal/application/framework.py:1436-1437`, `:1470-1476`, `:983-989`
- Test: `tests/test_framework_scaling.py`

**Interfaces:**
- Consumes: `build_scored_account` from Task 1.
- Produces: `_period_components(self, pillar, sample, historical_events) -> tuple[tuple[str, Decimal | None], ...]` — one argument shorter. Task 3 calls the same signature.

- [ ] **Step 1: Write the characterization test**

This is a refactor, so the test that must exist first is "the output does not change". Append to `tests/test_framework_scaling.py`:

```python
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
```

- [ ] **Step 2: Generate the expected constants from the current implementation**

Do not hand-write these. Run the current code and paste its output:

```bash
.venv/bin/python - <<'PY'
import sys, tempfile
from pathlib import Path
sys.path.insert(0, "src"); sys.path.insert(0, "tests")
from test_framework_scaling import build_scored_account
from trading_journal.application.framework import FrameworkService

repository, account_id = build_scored_account(Path(tempfile.mkdtemp()), total=200, reviewed=60)
scores = {i.pillar: (i.score, i.raw_score, i.status, i.reviewed_total, i.sample_size,
                     i.unreviewed_total, i.automatic_evidence_total)
          for i in FrameworkService(repository).pillar_scores(account_id)}
trend = FrameworkService(repository).rolling_score_trend(account_id)
print("EXPECTED_PILLAR_SCORES =", repr(scores))
print("EXPECTED_TREND_HEAD =", repr(trend[:5]))
print("EXPECTED_TREND_TAIL =", repr(trend[-5:]))
PY
```

Paste the three printed lines verbatim as module-level constants near the top of `tests/test_framework_scaling.py`, above the test that reads them.

- [ ] **Step 3: Run the characterization test against the unchanged code**

Run: `.venv/bin/python -m pytest tests/test_framework_scaling.py::test_pillar_scores_are_unchanged_by_the_scoring_refactor -m perf -q`

Expected: PASS. If it fails now, the constants were generated wrong — fix them before touching production code.

- [ ] **Step 4: Drop the parameter from `_period_components`**

In `src/trading_journal/application/framework.py`, change the signature at `:1470`:

```python
    def _period_components(
        self,
        pillar: str,
        sample: list[TradeProcessScore],
        historical_events: dict[int, dict[str, object]] | None,
    ) -> tuple[tuple[str, Decimal | None], ...]:
```

- [ ] **Step 5: Delete both dead construction sites**

At `:1436-1437`, delete the `pnl_by_trade` line entirely and shorten the call:

```python
        components = self._period_components(pillar, sample, historical_events)
```

At `:983-989`, in `_focus_progress`, drop the fourth argument:

```python
            components = self._period_components(
                focus.pillar, sample,
                self._historical_risk_events(account_id) if focus.pillar == "risk" else None,
            )
```

- [ ] **Step 6: Verify behaviour is unchanged and measure the win**

Run: `make check && make test-web && make test-perf`

Expected: `make check` and `make test-web` pass unchanged. The characterization test passes — scores did not move. The scaling ratio test still **fails** (the quadratic is still there; only its constant factor shrank), but the absolute times in the output should be roughly a third of Task 1's.

- [ ] **Step 7: Commit**

```bash
git add src/trading_journal/application/framework.py tests/test_framework_scaling.py
git commit -m "perf(framework): drop the unused pnl_by_trade map from pillar scoring"
```

---

### Task 3: One-pass prefix totals

`_period_pillar_score` rescans the entire score prefix four times, and
`_pillar_scores_from_sample` calls it three times, and `rolling_score_trend` calls that
once per reviewed trade. Everything past `sample = reviewed[-window:]` is bounded by
`window`, so replacing the rescans with a single accumulating pass makes the whole trend
linear. It does not meaningfully change ordinary `pillar_scores()`: measured at 5,000
trades / 2,000 reviewed, `pillar_scores()` went 0.54s → 0.52s, because that call is
dominated by the linear `_account_trade_scores` database load, not by the three prefix
rescans this task removes.

**Files:**
- Modify: `src/trading_journal/application/framework.py:37`, `:466-476`, `:791-817`, `:1411-1441`
- Test: `tests/test_framework_scaling.py` (already written in Tasks 1–2)

**Interfaces:**
- Consumes: `_period_components(self, pillar, sample, historical_events)` from Task 2.
- Produces:
  - `_SampleTotals(sample: tuple[TradeProcessScore, ...], reviewed_count: int, automatic: int, unreviewed: int)` — frozen dataclass.
  - `_SampleAccumulator(window: int)` with `.add(item: TradeProcessScore) -> None` and `.totals() -> _SampleTotals`.
  - `_sample_totals(scores: tuple[TradeProcessScore, ...], window: int) -> _SampleTotals`.
  - `_period_pillar_score(self, pillar: str, totals: _SampleTotals, window: int, scope: str, historical_events=None) -> PillarScore` — second parameter changes from the score tuple to the totals.

- [ ] **Step 1: Run the characterization test to confirm the green starting point**

Run: `.venv/bin/python -m pytest tests/test_framework_scaling.py -m perf -q`

Expected: the characterization test PASSES, the scaling test FAILS near 4.0. That is the state this task changes.

- [ ] **Step 2: Add the automatic-kinds constant**

The set `{"auto_review", "approved_auto_review"}` is currently a literal inside the hot loop. Promote it next to `REVIEWED_KINDS` at `framework.py:37`:

```python
REVIEWED_KINDS = frozenset({"approved_auto_review", "manual_review"})
AUTOMATIC_KINDS = frozenset({"auto_review", "approved_auto_review"})
```

- [ ] **Step 3: Add the `deque` import**

At `framework.py:5`, extend the existing `collections` import:

```python
from collections import Counter, deque
```

- [ ] **Step 4: Add the accumulator, above `class FrameworkService`**

Place these immediately after the module's dataclass definitions, before the service class:

```python
@dataclass(frozen=True)
class _SampleTotals:
    """The prefix aggregates one pillar score needs, at one point in an account's history."""

    sample: tuple[TradeProcessScore, ...]
    reviewed_count: int
    automatic: int
    unreviewed: int


class _SampleAccumulator:
    """Roll the prefix aggregates forward one trade at a time.

    A pillar score needs the trailing rolling sample plus three whole-history
    counts. Recomputing those by rescanning the prefix — once per pillar, and
    again per point in the score trend — is what made rolling_score_trend
    quadratic. Accumulating instead keeps every caller linear.
    """

    def __init__(self, window: int) -> None:
        self._trailing: deque[TradeProcessScore] = deque(maxlen=window)
        self._reviewed_count = 0
        self._automatic = 0
        self._unreviewed = 0

    def add(self, item: TradeProcessScore) -> None:
        if item.review_kind in REVIEWED_KINDS and item.rubric_version == CURRENT_RUBRIC_VERSION:
            self._trailing.append(item)
            self._reviewed_count += 1
        if item.review_kind in AUTOMATIC_KINDS:
            self._automatic += 1
        if item.review_kind not in REVIEWED_KINDS:
            self._unreviewed += 1

    def totals(self) -> _SampleTotals:
        return _SampleTotals(tuple(self._trailing), self._reviewed_count, self._automatic, self._unreviewed)


def _sample_totals(scores: tuple[TradeProcessScore, ...], window: int) -> _SampleTotals:
    accumulator = _SampleAccumulator(window)
    for item in scores:
        accumulator.add(item)
    return accumulator.totals()
```

- [ ] **Step 5: Take totals instead of rescanning, in `_period_pillar_score`**

Replace the head of `_period_pillar_score` (`framework.py:1411-1437`) — the signature, the three rescans, and the empty-sample early return:

```python
    def _period_pillar_score(
        self,
        pillar: str,
        totals: _SampleTotals,
        window: int,
        scope: str,
        historical_events: dict[int, dict[str, object]] | None = None,
    ) -> PillarScore:
        # A reviewed trade is either a one-click approval of normalized MT5
        # evidence or a full Manual Review. Both use persisted criterion grades
        # and have equal weight in framework scoring and maturity gates.
        # The caller accumulates those counts in one pass; see _SampleAccumulator.
        sample = totals.sample
        if not sample:
            detail = "No Zone-aligned post-trade review evidence yet."
            return PillarScore(
                pillar, None, None, "incomplete", 0, 0, totals.unreviewed, totals.automatic,
                False, 0, (), detail, scope,
            )
        components = self._period_components(pillar, sample, historical_events)
```

Then in the `return PillarScore(...)` at the end of the method, replace `len(reviewed)` with `totals.reviewed_count`, `unreviewed` with `totals.unreviewed`, and `automatic` with `totals.automatic`:

```python
        return PillarScore(
            pillar, None if score is None else _decimal_text(score), None if raw is None else _decimal_text(raw),
            status, totals.reviewed_count, len(sample), totals.unreviewed, totals.automatic, hard_block, critical,
            formatted, detail, scope,
        )
```

Everything between those two edits — `values`, `available`, `hard_block`, `critical`, `settings`, `capped`, `score`, `status`, `formatted`, `detail` — is unchanged and already operates only on `sample`.

- [ ] **Step 6: Compute the totals once for all three pillars**

Replace `_pillar_scores_from_sample` (`framework.py:466-476`):

```python
    def _pillar_scores_from_sample(
        self,
        account_scores: tuple[TradeProcessScore, ...],
        historical_events: dict[int, dict[str, object]],
        window: int,
    ) -> tuple[PillarScore, ...]:
        totals = _sample_totals(account_scores, window)
        return (
            self._period_pillar_score("psychology", totals, window, "Selected account"),
            self._period_pillar_score("risk", totals, window, "Selected account", historical_events),
            self._period_pillar_score("system", totals, window, "Selected account"),
        )
```

- [ ] **Step 7: Walk the trend once instead of re-slicing per point**

Replace `rolling_score_trend` (`framework.py:791-817`). The accumulator takes every trade — including unreviewed ones, which still feed the `automatic` and `unreviewed` counts — but only reviewed trades emit a point, exactly as before:

```python
    def rolling_score_trend(
        self, account_id: int, *, window: int = 20
    ) -> tuple[tuple[str, str | None, str | None, str | None, str], ...]:
        """Historical rolling scores for the current Zone-aligned rubric."""
        account_scores, historical_events = self._account_trade_scores(account_id)
        time_basis = self._reporting_time_basis()
        accumulator = _SampleAccumulator(window)
        points: list[tuple[str, str | None, str | None, str | None, str]] = []
        for trade in account_scores:
            accumulator.add(trade)
            if trade.review_kind not in REVIEWED_KINDS:
                continue
            totals = accumulator.totals()
            closed = reporting_datetime(trade.exit_time, trade.server_utc_offset_minutes, time_basis).isoformat()
            points.append((
                closed,
                self._period_pillar_score("psychology", totals, window, "Selected account").score,
                self._period_pillar_score("risk", totals, window, "Selected account", historical_events).score,
                self._period_pillar_score("system", totals, window, "Selected account").score,
                trade.rubric_version or CURRENT_RUBRIC_VERSION,
            ))
        return tuple(points)
```

- [ ] **Step 8: Run the characterization test — scores must be byte-identical**

Run: `.venv/bin/python -m pytest tests/test_framework_scaling.py::test_pillar_scores_are_unchanged_by_the_scoring_refactor -m perf -q`

Expected: PASS. A failure here means the accumulator disagrees with the old rescan — most likely the `rubric_version == CURRENT_RUBRIC_VERSION` filter on `reviewed`, or adding to the accumulator after the `continue` instead of before it.

- [ ] **Step 9: Run the scaling guard — it must now pass**

Run: `make test-perf`

Expected: PASS, ratio near 2.0.

- [ ] **Step 10: Run the full suites**

Run: `make check && make test-web`

Expected: `367 passed, 55 deselected` and `53 passed`.

- [ ] **Step 11: Measure the headline number for the commit message**

```bash
.venv/bin/python - <<'PY'
import sys, tempfile, time
from pathlib import Path
sys.path.insert(0, "src"); sys.path.insert(0, "tests")
from test_framework_scaling import build_scored_account
from trading_journal.application.framework import FrameworkService

repository, account_id = build_scored_account(Path(tempfile.mkdtemp()), total=5000, reviewed=2000)
started = time.perf_counter()
FrameworkService(repository).rolling_score_trend(account_id)
print(f"rolling_score_trend, 2000 reviewed: {time.perf_counter() - started:.2f}s (was 9.47s)")
PY
```

- [ ] **Step 12: Commit**

```bash
git add src/trading_journal/application/framework.py
git commit -m "perf(framework): accumulate rolling-sample totals in one pass"
```

---

### Task 4: Bound the live-incident lookup

`record_live_incident_transitions` runs on every Ongoing render — every 5 seconds — and
loads every incident row the account has ever recorded, only to keep the newest row per
`incident_key`. The index `(mt5_account_id, incident_key, id)` already supports the
grouped form.

**Files:**
- Modify: `src/trading_journal/infrastructure/sqlite_repository.py:2273-2282`
- Test: `tests/test_live_positions.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: no signature change — `record_live_incident_transitions(self, account_id, active, *, occurred_at)` is unchanged.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_live_positions.py`. It asserts the *behaviour that must survive* — a resolved incident that later recurs must open a fresh row rather than being suppressed by a stale one — because that is what the "latest row per key" logic exists to get right.

```python
def test_a_recurring_incident_reopens_after_it_was_resolved(tmp_path: Path) -> None:
    """Only the newest row per key decides state, so a resolved alert can fire again."""
    repository = configured_repository(tmp_path)
    account = repository.find_active_mt5_account("123456", "DemoBroker-Live")
    assert account is not None
    unprotected = {"unprotected:5001": ("unprotected", "5001", "No protective stop.")}

    repository.record_live_incident_transitions(account.id, unprotected, occurred_at="2026-08-18T08:00:00+00:00")
    repository.record_live_incident_transitions(account.id, {}, occurred_at="2026-08-18T08:01:00+00:00")
    repository.record_live_incident_transitions(account.id, unprotected, occurred_at="2026-08-18T08:02:00+00:00")

    states = [item.state for item in repository.list_live_position_incidents(account.id)]
    assert states == ["opened", "resolved", "opened"]
```

- [ ] **Step 2: Run it against the current implementation**

Run: `.venv/bin/python -m pytest tests/test_live_positions.py::test_a_recurring_incident_reopens_after_it_was_resolved -q`

Expected: PASS. This is a characterization test — it pins behaviour the rewrite must not break. Confirming it green now is the point.

- [ ] **Step 3: Replace the full scan with a latest-per-key query**

In `sqlite_repository.py`, replace lines 2273-2282 (from `with self._sessions.begin() as session:` through the `latest.setdefault(...)` loop):

```python
        with self._sessions.begin() as session:
            # Only the newest row per key decides whether an alert is currently
            # open. Selecting just those keeps this off a full-history scan on a
            # path that runs every few seconds while the Ongoing page is open.
            newest_ids = (
                select(func.max(LivePositionIncident.id))
                .where(LivePositionIncident.mt5_account_id == account_id)
                .group_by(LivePositionIncident.incident_key)
            )
            latest: dict[str, LivePositionIncident] = {
                row.incident_key: row
                for row in session.scalars(
                    select(LivePositionIncident).where(LivePositionIncident.id.in_(newest_ids))
                ).all()
            }
```

`func` and `select` are already imported at `sqlite_repository.py:11`. Everything below — `open_keys`, both `for` loops — is unchanged.

- [ ] **Step 4: Verify**

Run: `make check && make test-web`

Expected: both pass, including the new test and the existing incident tests in `tests/test_live_positions.py`.

- [ ] **Step 5: Commit**

```bash
git add src/trading_journal/infrastructure/sqlite_repository.py tests/test_live_positions.py
git commit -m "perf(live): select only the newest incident per key"
```

---

### Task 5: Three small, contained wins

Independent one-liners that share a test cycle: they touch three different files, none
depends on another, and no reviewer would accept one and reject another.

**Files:**
- Modify: `src/trading_journal/infrastructure/sqlite_repository.py:3998` (docstring), `:4378-4380`
- Modify: `src/trading_journal/application/reporting_time.py:1-9`, `:50-66`
- Test: `tests/test_reporting_time.py`

**Interfaces:**
- Produces: `detect_local_timezone.cache_clear()` — available to any test that manipulates `TZ`.
- Unchanged: `SQLiteJournalRepository.list_trades()` keeps its signature. Step 5 explains why it survives despite having no production caller.

- [ ] **Step 1: Write the failing test for the timezone cache**

Append to `tests/test_reporting_time.py`:

```python
def test_detect_local_timezone_is_resolved_once_per_process(monkeypatch: pytest.MonkeyPatch) -> None:
    """The local zone is read per trade on a dashboard build; resolving it each time is a syscall."""
    from trading_journal.application import reporting_time

    reporting_time.detect_local_timezone.cache_clear()
    monkeypatch.delenv("TZ", raising=False)
    resolutions = 0
    original_resolve = Path.resolve

    def counting_resolve(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal resolutions
        if self == Path("/etc/localtime"):
            resolutions += 1
        return original_resolve(self, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", counting_resolve)
    for _ in range(50):
        reporting_time.detect_local_timezone()
    assert resolutions <= 1
```

Add `from pathlib import Path` and `import pytest` to the file's imports if they are not already there.

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_reporting_time.py::test_detect_local_timezone_is_resolved_once_per_process -q`

Expected: FAIL — either `AttributeError: 'function' object has no attribute 'cache_clear'`, or `assert 50 <= 1`.

- [ ] **Step 3: Cache the timezone resolution**

In `reporting_time.py`, add the import at line 5:

```python
from functools import cache
```

and decorate the function at line 50:

```python
@cache
def detect_local_timezone() -> tzinfo:
    """Use the host's IANA zone where available, without adding a dependency.

    Cached: dashboard builds call this once per trade, and the /etc/localtime
    fallback is a realpath syscall. Tests that change TZ must call cache_clear().
    """
```

The body is unchanged.

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_reporting_time.py -q`

Expected: PASS.

- [ ] **Step 5: Mark `list_trades` test-only instead of deleting it**

The audit called this dead code. It has no production caller — verify:

```bash
grep -rn "list_trades()" --include=*.py app.py app_pages src
```

Expected: no output. But it *is* the only coverage of `_standard_risk_for_trade`'s
`effective_risk` and `risk_source` labelling (`tests/test_risk_baseline_dashboard.py:163-197`,
`tests/test_strategy_profiles.py:323-388`), and `TradePerformanceItem` exposes neither
field. Deleting it would cost that coverage for no runtime gain, so keep it and make its
status explicit. Replace its (absent) docstring at `sqlite_repository.py:3998`:

```python
    def list_trades(self) -> list[TradeListItem]:
        """Every imported position across every account, with its risk-source label.

        No production caller: this exists for tests that assert the effective-risk
        and risk-source labelling directly. It is deliberately unscoped, so do not
        wire it into a page — per-account reporting goes through
        list_trade_performance(account_id) instead.
        """
```

- [ ] **Step 6: Replace the `count_trades` full scan**

At `sqlite_repository.py:4378`:

```python
    def count_trades(self) -> int:
        with self._sessions() as session:
            return session.scalar(select(func.count()).select_from(Trade)) or 0
```

- [ ] **Step 7: Verify**

Run: `make check && make test-web`

Expected: both pass.

- [ ] **Step 8: Commit**

```bash
git add src/trading_journal/application/reporting_time.py src/trading_journal/infrastructure/sqlite_repository.py tests/test_reporting_time.py
git commit -m "perf: cache the local timezone and count trades in SQL"
```

---

### Task 6: Initialize each user's schema once, not per request

Both ingestion endpoints build a repository and run the full migration sequence on every
HTTP call — measured at 8.8 ms per request, against one live-snapshot POST every 10
seconds per connected user. Each user's schema only needs bringing up once per process.

**Files:**
- Modify: `src/trading_journal/ingestion_api.py:29-30`, `:66-129`
- Test: `tests/test_ingestion_api.py`

**Interfaces:**
- Produces: `_user_repository(username: str) -> SQLiteJournalRepository` — returns a per-process cached, already-initialized repository. Callers must **not** call `.close()` on it.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_ingestion_api.py`, inside the existing `TestCompletedPositionIngestion` class (it already has the fixtures that configure a user, a token and an account):

```python
    def test_given_repeated_pushes_then_the_schema_is_initialized_once(self, monkeypatch, client, token) -> None:
        """initialize() runs the whole migration sequence; it belongs once per process, not per request."""
        from trading_journal.infrastructure.sqlite_repository import SQLiteJournalRepository

        initializations = 0
        original_initialize = SQLiteJournalRepository.initialize

        def counting_initialize(self) -> None:
            nonlocal initializations
            initializations += 1
            original_initialize(self)

        monkeypatch.setattr(SQLiteJournalRepository, "initialize", counting_initialize)
        for _ in range(3):
            client.post("/ingest/live-positions", json={"snapshot": EMPTY_SNAPSHOT},
                        headers={"Authorization": f"Bearer {token}"})
        assert initializations == 1
```

Reuse whatever the file already names for a valid empty snapshot payload; if there is no such constant, define `EMPTY_SNAPSHOT` at module level from the existing snapshot fixture with `"positions": []`.

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_ingestion_api.py -k schema_is_initialized_once -q`

Expected: FAIL with `assert 3 == 1`.

- [ ] **Step 3: Add the per-process repository cache**

In `ingestion_api.py`, after the `logger` line at `:30`:

```python
# One repository per user for the life of the process. initialize() runs the full
# schema-migration sequence inside a write transaction; at one live snapshot every
# ten seconds per connected user, doing that per request is pure overhead and
# serialises against concurrent imports. SQLAlchemy's engine pool is thread-safe,
# so a cached repository is safe to share across FastAPI's worker threads.
_repositories: dict[str, SQLiteJournalRepository] = {}
_repositories_lock = Lock()


def _user_repository(username: str) -> SQLiteJournalRepository:
    with _repositories_lock:
        repository = _repositories.get(username)
        if repository is None:
            database_path = user_database_path(username)
            database_path.parent.mkdir(parents=True, exist_ok=True)
            repository = SQLiteJournalRepository(database_path)
            repository.initialize()
            _repositories[username] = repository
        return repository
```

Add `from threading import Lock` to the imports at `:16`.

- [ ] **Step 4: Use it in both endpoints**

In `ingest` (`:66`), replace the four lines from `database_path = user_database_path(username)` through `repository.initialize()` with:

```python
    repository = _user_repository(username)
```

and delete the `finally: repository.close()` block — the repository now outlives the request.

Apply the identical change to `ingest_live_positions` (`:106`).

- [ ] **Step 5: Run the test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_ingestion_api.py -q`

Expected: PASS, including the existing tenant-isolation tests — the cache is keyed by username, so one user still cannot reach another's file.

- [ ] **Step 6: Verify**

Run: `make check && make test-web`

Expected: both pass.

- [ ] **Step 7: Commit**

```bash
git add src/trading_journal/ingestion_api.py tests/test_ingestion_api.py
git commit -m "perf(ingestion): reuse one initialized repository per user per process"
```

---

# Phase 2 — Correctness

### Task 7: Guard a non-positive starting balance

With a date range selected, `starting_balance = funded_capital + prior_pnl`. Funded capital
is validated `>= 0.01` but prior P&L is unbounded negative, so an account that lost its
capital before the window produces a zero or negative baseline. At exactly zero this raises
`decimal.DivisionByZero`; below zero it silently reports a negative drawdown percentage.
Unreachable from the UI today — the Dashboard passes no dates — but the parameters are
public and tested.

**Files:**
- Modify: `src/trading_journal/application/dashboard.py:184-190`, `:296-298`, `:358`
- Test: `tests/test_risk_baseline_dashboard.py`

**Interfaces:**
- Produces: no signature change. `DashboardReport.drawdown_percent`, `max_drawdown_percent`, `current_drawdown_percent`, `end_of_day_*_percent` and `balance_growth_percent` become `None` — rather than raising — when the baseline is not positive.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_risk_baseline_dashboard.py`:

```python
def test_a_window_that_starts_from_a_wiped_balance_reports_no_percentages(tmp_path: Path) -> None:
    """Prior losses can wipe out funded capital; percentages of a zero baseline are undefined, not a crash."""
    repository = configured_repository(tmp_path)
    account = repository.find_active_mt5_account("123456", "DemoBroker-Live")
    assert account is not None
    # Funded capital is 100 in this fixture. Lose all of it before the window opens.
    repository.upsert_mt5_positions(
        account.id,
        [
            position("7001", net_pnl="-100", exit_time="2026-08-01T09:00:00+00:00"),
            position("7002", net_pnl="-20", exit_time="2026-08-05T09:00:00+00:00"),
        ],
        "positions.csv",
        "wiped-hash",
    )

    report = DashboardService(repository).build_report(
        account_id=account.id, start_date="2026-08-05", end_date="2026-08-05"
    )

    assert report.starting_balance == "0"
    assert report.balance_growth_percent is None
    assert report.max_drawdown_percent is None
    assert report.current_drawdown_percent is None
    assert report.max_drawdown == "20"  # absolute money figures still reconcile
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_risk_baseline_dashboard.py -k wiped_balance -q`

Expected: FAIL with `decimal.DivisionByZero`.

- [ ] **Step 3: Make the drawdown tracker refuse a non-positive peak**

In `dashboard.py`, replace `_DrawdownTracker.advance`'s percentage block (`:184-190`):

```python
        drawdown_percent = None
        if self.balance is not None and self.peak_balance is not None:
            self.balance += pnl
            self.peak_balance = max(self.peak_balance, self.balance)
            # A window can open from a wiped or negative baseline when prior losses
            # exceed funded capital. A percentage of that peak is undefined, not zero.
            if self.peak_balance > 0:
                drawdown_percent = drawdown * Decimal("100") / self.peak_balance
                self.max_drawdown_percent = max(self.max_drawdown_percent or Decimal("0"), drawdown_percent)
        return drawdown, drawdown_percent
```

- [ ] **Step 4: Guard the growth percentage**

At `dashboard.py:358`:

```python
        balance_growth_percent = (
            None if starting_balance is None or starting_balance <= 0
            else pnl_total * Decimal("100") / starting_balance
        )
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_risk_baseline_dashboard.py -q`

Expected: PASS, including every existing drawdown-percentage test — those all use a positive baseline and are unaffected.

- [ ] **Step 6: Verify**

Run: `make check && make test-web`

Expected: both pass.

- [ ] **Step 7: Commit**

```bash
git add src/trading_journal/application/dashboard.py tests/test_risk_baseline_dashboard.py
git commit -m "fix(dashboard): report no percentage when the baseline balance is not positive"
```

---

### Task 8: Report unchanged rows as skipped

The EA re-exports the whole closed-position history whenever it appends a trade, so a
normal sync re-upserts everything. Every untouched row is counted as `updated` and
`skipped_count` is hardcoded `0`, so the toast tells a user "5,000 updated" when one trade
arrived.

**Files:**
- Modify: `src/trading_journal/infrastructure/sqlite_repository.py:4265-4358`
- Test: `tests/test_mt5_import.py`

**Interfaces:**
- Produces: `ImportResult.skipped_count` becomes meaningful — rows whose stored values all matched the export. `updated_count` now means "row actually changed".

- [ ] **Step 1: Write the failing test**

Append to `tests/test_mt5_import.py`:

```python
def test_reimporting_an_unchanged_position_reports_it_as_skipped(
    repository: SQLiteJournalRepository, tmp_path: Path
) -> None:
    """The EA re-exports the whole history on every append; unchanged rows are not updates."""
    export_path = tmp_path / "positions.csv"
    write_export(export_path, net_pnl="98.00")
    service = MT5ImportService(repository)
    first = service.import_csv(export_path)
    assert (first.created_count, first.updated_count, first.skipped_count) == (1, 0, 0)

    # Byte-identical re-export: nothing about the position changed.
    write_export(export_path, net_pnl="98.00")
    second = service.import_csv(export_path)

    assert (second.created_count, second.updated_count, second.skipped_count) == (0, 0, 1)
```

This uses the file's existing `repository` fixture and its `write_export(path, *, net_pnl=...)`
helper, which writes one position (`9001`) per call — the same shape as
`test_reimport_refreshes_execution_data` directly above it.

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_mt5_import.py -k unchanged_positions_reports -q`

Expected: FAIL — `(1, 2, 0) != (1, 0, 2)`.

- [ ] **Step 3: Count a skip when nothing actually changed**

In `upsert_mt5_positions`, add a counter beside the existing two at `:4195-4196`:

```python
        created = 0
        updated = 0
        skipped = 0
```

Then replace the `else` branch of the per-position loop (`:4335-4341`). `source_updated_at` is deliberately excluded from the comparison — it is a bookkeeping stamp, not exported evidence, and including it would mark every row changed:

```python
                else:
                    # source_updated_at is our own bookkeeping stamp, not exported
                    # evidence: comparing against it would mark every row changed on
                    # every safety re-export and drown a real update in the count.
                    evidence = {field: value for field, value in values.items() if field != "source_updated_at"}
                    changed = any(getattr(trade, field) != value for field, value in evidence.items())
                    if changed:
                        for field, value in values.items():
                            setattr(trade, field, value)
                        updated += 1
                    else:
                        skipped += 1
                    pending_member = pending_members_by_position_id.get(position.position_id)
                    if pending_member is None and trade.auto_risk_policy_id is None and active_policy is not None:
                        trade.auto_risk_policy_id = active_policy.id
```

- [ ] **Step 4: Record and return the real counts**

In the `MT5ImportRun(...)` constructed at `:4345`, replace `skipped_count=0` with `skipped_count=skipped`. Then change the return at `:4358`:

```python
        return ImportResult(created_count=created, updated_count=updated, skipped_count=skipped)
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_mt5_import.py -q`

Expected: PASS, with no other test needing changes. `test_reimport_refreshes_execution_data`
(`tests/test_mt5_import.py:372`) re-exports with `net_pnl` changed from `98.00` to `102.00`,
so its row genuinely changes and its `updated_count == 1` assertion still holds — verified
before this plan was written.

- [ ] **Step 6: Make the toast honest**

In `app.py:1411-1414`, the notice sums `created` and `updated` across imported results. Add skipped, and say nothing about it when it is zero:

```python
    created = sum(item.created_count for item in imported)
    updated = sum(item.updated_count for item in imported)
    skipped = sum(item.skipped_count for item in imported)
    unchanged = f", {skipped} unchanged" if skipped else ""
    st.session_state["auto_sync_notice"] = f"Auto-imported {created} created and {updated} updated MT5 position(s){unchanged}."
```

- [ ] **Step 7: Verify**

Run: `make check && make test-web`

Expected: both pass.

- [ ] **Step 8: Commit**

```bash
git add src/trading_journal/infrastructure/sqlite_repository.py app.py tests/test_mt5_import.py
git commit -m "fix(import): count unchanged re-imported positions as skipped, not updated"
```

---

### Task 9: Use the viewer's zone for the Dashboard's local calendar

`browser_timezone()` is threaded into the Ongoing and Monitor pages but not into
`DashboardService`, so on "Local Timezone" basis the hosted Dashboard groups trades by the
*container's* clock (UTC) while the rest of the app uses the viewer's. Two pages disagree
about which day a trade belongs to.

**Files:**
- Modify: `src/trading_journal/application/dashboard.py:193-216`, `:321`, `:506-513`, `:580-582`
- Modify: `app.py:1336-1348`, `:1394-1401`
- Test: `tests/test_reporting_time.py`

**Interfaces:**
- Produces: `DashboardService(repository, local_zone: tzinfo | None = None)` — matching `FrameworkService`'s existing constructor shape, so the two services are configured the same way.
- Produces: `_cached_dashboard_report(database_path, database_change_token, account_id, payload_shape, local_zone_name)` — gains a fifth argument so two viewers in different zones do not share one cache entry.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_reporting_time.py`:

```python
def test_local_basis_groups_dashboard_days_by_the_supplied_zone(tmp_path: Path) -> None:
    """A 22:00 UTC close is the next day in Asia/Ho_Chi_Minh; the Dashboard must agree with Ongoing."""
    repository = configured_repository(tmp_path)
    repository.configure_journal(reporting_time_basis="local")
    account = repository.find_active_mt5_account("123456", "DemoBroker-Live")
    assert account is not None
    repository.upsert_mt5_positions(
        account.id,
        [position("8001", net_pnl="10", exit_time="2026-08-10T22:00:00+00:00")],
        "positions.csv",
        "zone-hash",
    )

    report = DashboardService(repository, local_zone=ZoneInfo("Asia/Ho_Chi_Minh")).build_report(account_id=account.id)

    assert [item.date for item in report.daily] == ["2026-08-11"]
```

Import `ZoneInfo` from `zoneinfo`, and `configured_repository`/`position` from `test_risk_baseline_dashboard` if this file does not already have equivalents.

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_reporting_time.py -k groups_dashboard_days -q`

Expected: FAIL with `TypeError: __init__() got an unexpected keyword argument 'local_zone'`.

- [ ] **Step 3: Accept a zone on the service**

In `dashboard.py`, replace the constructor at `:193-195`:

```python
class DashboardService:
    def __init__(self, repository: SQLiteJournalRepository, *, local_zone: tzinfo | None = None) -> None:
        self._repository = repository
        # Hosted mode runs in the container's clock, which is not the viewer's.
        # FrameworkService takes the browser zone the same way; the two must agree
        # about which day a trade closed on.
        self._local_zone = local_zone
```

Add `tzinfo` to the `datetime` import at `:4`:

```python
from datetime import date, datetime, timezone, tzinfo
```

- [ ] **Step 4: Thread the zone through every reporting-calendar call**

`_trade_date` is currently a `@staticmethod`. Make it an instance method (`:580-582`):

```python
    def _trade_date(self, trade: TradePerformanceItem, reporting_time_basis: str) -> date:
        return reporting_date(
            trade.exit_time, trade.server_utc_offset_minutes, reporting_time_basis, local_zone=self._local_zone
        )
```

Its four call sites already use `self._trade_date(...)`, so they need no change. Then at `:321`, inside the `per_trade` loop:

```python
                    exit_time=reporting_datetime(
                        trade.exit_time, trade.server_utc_offset_minutes, time_basis, local_zone=self._local_zone
                    ).isoformat(),
```

and at `:520`, in `current_report_date`:

```python
        return reporting_datetime(
            datetime.now(timezone.utc).isoformat(), offset, settings.reporting_time_basis, local_zone=self._local_zone
        ).date()
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_reporting_time.py -q`

Expected: PASS.

- [ ] **Step 6: Pass the browser zone from the Dashboard page**

In `app.py`, add the zone to the cached function's key and its body (`:1336-1348`). The zone name — not the `tzinfo` object — goes in the signature, because `st.cache_data` hashes its arguments:

```python
@st.cache_data(ttl=_ANALYTICS_CACHE_TTL_SECONDS, max_entries=128, show_spinner=False)
def _cached_dashboard_report(
    database_path: str,
    database_change_token: tuple[int, int, int, int],
    account_id: int,
    payload_shape: str,
    local_zone_name: str | None,
) -> dict[str, object]:
    del database_change_token, payload_shape
    repo = SQLiteJournalRepository(database_path)
    try:
        local_zone = None if local_zone_name is None else ZoneInfo(local_zone_name)
        return asdict(DashboardService(repo, local_zone=local_zone).build_report(account_id=account_id))
    finally:
        repo.close()
```

Add `from zoneinfo import ZoneInfo` to `app.py`'s imports.

- [ ] **Step 7: Resolve the zone at the call site**

Replace `build_dashboard_report` (`:1394-1401`). It resolves the browser zone only on "local" basis, exactly as `_render_monitor` does at `presentation/framework.py:2047`:

```python
def build_dashboard_report(repo: SQLiteJournalRepository, *, account_id: int) -> DashboardReport:
    settings = repo.get_journal_settings()
    zone = browser_timezone() if settings.reporting_time_basis == "local" else None
    return _dashboard_report_from_cache_payload(
        _cached_dashboard_report(
            str(repo.database_path),
            _database_change_token(repo.database_path),
            account_id,
            _DASHBOARD_PAYLOAD_SHAPE,
            None if zone is None else str(zone),
        )
    )
```

Add `from trading_journal.presentation.browser_timezone import browser_timezone` to `app.py`'s imports if it is not already there.

- [ ] **Step 8: Verify**

Run: `make check && make test-web`

Expected: both pass. `make test-web` matters here — `browser_timezone()` renders a Streamlit component, so the AppTest suite is what proves the Dashboard still renders when the component has not reported a zone yet (it returns `None`, and the basis falls back to the server zone for that first run, as it already does on Monitor).

- [ ] **Step 9: Commit**

```bash
git add src/trading_journal/application/dashboard.py app.py tests/test_reporting_time.py
git commit -m "fix(dashboard): group local-basis days by the viewer's zone, not the server's"
```

---

### Task 10: Documentation truth-up

Three documents currently assert things the code does not do. Nothing here changes
behaviour; all of it changes what a future reader — human or agent — will believe.

**Files:**
- Modify: `CLAUDE.md` (the "Corrections version" bullet under "Domain conventions to preserve")
- Delete: `docs/mt5-import-scale-audit.md`
- Modify: `docs/three_pillar_framework_guide.md`, `docs/three_pillar_framework_guide.vi.md`
- Modify: `.claude/commands/framework-review.md`

- [ ] **Step 1: Correct the assessment-revision convention**

The single-current-revision design is intentional: one active `PostTradeAssessment` row per logical trade, superseded only when logical-trade membership changes. Replace the "Corrections version, they don't overwrite" bullet in `CLAUDE.md` with:

```markdown
- **One current assessment per logical trade; supersession is for regrouping**: editing a Framework review overwrites the single active `PostTradeAssessment` row (`sqlite_repository.py::save_post_trade_assessment`) — there is no per-edit revision history, and `post_trade_assessment_revisions` is deliberately dropped during migration (`sqlite_repository.py:1171`). What *is* preserved is regrouping: changing a logical trade's membership stamps `superseded_at`/`superseded_reason` on the old assessment and keeps it queryable via `list_superseded_post_trade_assessments_for_trade`. Don't reintroduce per-edit versioning without an explicit request; several tests pin the overwrite behaviour (`test_correction_overwrites_the_single_current_assessment`, `test_repeated_manual_assessment_save_keeps_one_row`).
```

- [ ] **Step 2: Retire the stale scale audit**

```bash
git rm docs/mt5-import-scale-audit.md
```

Then update the reference to it in `CLAUDE.md`'s "Docs worth reading before non-trivial changes" list:

```markdown
- `docs/perf-and-correctness-audit-2026-09.md` — measured import/query/scoring performance and the verified correctness gaps, with the numbers behind each. Replaces the earlier `mt5-import-scale-audit.md`, whose top-ranked hotspot had already been fixed.
```

- [ ] **Step 3: Fix the stale monitoring note in the review command**

`CLAUDE.md` already flags that `.claude/commands/framework-review.md` wrongly says monitoring uses raw positions rather than logical trades. Fix the command file itself so the flag can go: find the sentence claiming monitoring uses raw positions and replace it with logical trades in final-close order, matching CLAUDE.md's "Logical trades are the reporting unit" bullet. Then delete the parenthetical staleness warning from that bullet in `CLAUDE.md`.

- [ ] **Step 4: Document the DST caveat next to the affected bases**

**Decision gate.** The root fix — exporting the offset in effect at each trade's close — is an MT5 `schema_version` bump and therefore a database reset for every existing user. Until that is chosen, document the limitation. In `docs/three_pillar_framework_guide.md`, in the section describing the reporting time basis, add:

```markdown
**A note on UTC and Local basis.** The MT5 exporter stamps every row with the broker's
UTC offset *at export time*, not the offset that applied when each trade closed. For a
broker that observes DST, trades that closed on the other side of a clock change are
stored up to an hour away from their true UTC time. Server Timezone basis — the default —
is unaffected, because it re-applies the same stored offset and recovers the original
server wall clock. On UTC or Local basis, a trade that closed within an hour of midnight
may be attributed to the neighbouring day.
```

Mirror it in `docs/three_pillar_framework_guide.vi.md`. Match that file's existing register — it is a full translation, not a summary.

- [ ] **Step 5: Verify the Guide page still renders**

The Guide page renders these markdown files verbatim.

Run: `make check && make test-web`

Expected: both pass.

- [ ] **Step 6: Commit**

```bash
git add CLAUDE.md docs/ .claude/commands/framework-review.md
git commit -m "docs: correct the assessment-revision convention and retire the stale scale audit"
```

---

## Deferred, with rationale

Recorded so they are decisions rather than oversights.

**P3 — analytics cache invalidation.** `_database_change_token` includes the WAL, so the
live-snapshot write every ~10s invalidates the 15s dashboard and framework caches. A
content-hash guard does not help: `current_price` moves on nearly every tick while
positions are open, so the write is genuine. The real fix is decoupling the analytics cache
key from whole-database writes, which needs its own design pass. **Re-measure after Task 3**
— at 0.41s (dashboard) and 0.54s (pillar scores) for 5,000 trades, a cache miss may simply
not be user-visible, in which case this costs nothing to leave alone.

**P8 — MQL5 exporter O(n²).** `ContainsPosition` (`TradingJournalSync.mq5:700`),
`IsPositionIdentifierOpen` (`:730`) and `ContainsText` (`:515`) are each linear inside a
loop over every position or deal. Fixing them means hash sets in MQL5 and a compile
round-trip through Wine + MetaEditor, and it cannot be verified from this environment —
it needs a real MT5 terminal with a large history. Worth doing, but as its own task with a
tester who can run it. The duplicate risk arithmetic at `:773-819` vs `:894-911` should be
collapsed in the same pass.

**C1 — the DST root fix.** Exporting a per-trade historical offset requires
`MT5PositionExport.schema_version` 6, the matching `.mq5` change, and — because the `trades`
columns are guarded by `_require_clean_framework_schema` rather than the soft migrator — a
database reset for every existing user. `.claude/commands/schema-bump.md` walks the process.
Task 10 Step 4 documents the limitation instead. Reopen this if anyone actually reports the
UTC or Local basis being an hour out.

## Self-review

- **Spec coverage:** P1 → Tasks 2–3. P2 → Task 2. P3 → deferred with rationale. P4 → Task 4. P5 → Task 8 (reporting, which is the real defect). P6 → Task 5 (partially: `list_trades` is documented rather than deleted — see that task's Step 5 for the evidence). P7 → Task 6. P8 → deferred. C1 → Task 10 Step 4 + deferred root fix. C2 → Task 7. C3 → Task 8. C4 → Task 9. C5 → Task 10 Step 1. Stale scale audit → Task 10 Step 2. No spec item is unaccounted for.
- **Type consistency:** `_period_components(pillar, sample, historical_events)` is defined in Task 2 and called with that arity in Task 3. `_SampleTotals` field names (`sample`, `reviewed_count`, `automatic`, `unreviewed`) are used consistently in Steps 4, 5 and 6 of Task 3. `_sample_totals` and `_SampleAccumulator` are defined before their Task 3 call sites. `build_scored_account` is defined in Task 1 and imported by name in Tasks 2 and 3.
- **Assumptions resolved before publishing, not left to the executor:** `list_trades` is kept, not deleted — it is the only coverage of the `risk_source` labelling and `TradePerformanceItem` has no equivalent field (Task 5 Step 5). `test_reimport_refreshes_execution_data` changes `net_pnl` between exports, so Task 8 does not disturb it. Task 8's new test uses the real `write_export(path, *, net_pnl=...)` signature. One genuine unknown remains: Task 6 Step 1 needs an empty-snapshot payload constant, and `tests/test_ingestion_api.py` must be read to find or build one — it is the only place this plan asks the executor to discover a name.
