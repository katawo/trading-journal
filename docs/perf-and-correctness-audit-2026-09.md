# Performance & correctness audit — September 2026

**Status:** measured, not speculative. Every figure below was produced on this machine
against the real code (SQLite 3.46.1, `.venv`), using synthetic accounts built from the
`tests/test_risk_baseline_dashboard.py` fixture helpers. This document is the spec for
`docs/superpowers/plans/2026-09-06-perf-and-correctness-fixes.md`.

It supersedes `docs/mt5-import-scale-audit.md`, which is stale — see "Corrections to
earlier notes" below.

## Baseline

```
make check      367 passed, 54 deselected   20s
make test-web    53 passed                  88s
```

Measured at 5,000 logical trades on one account:

| Operation | Time |
|---|---|
| First import of 5,000 positions | 1.78s |
| Append 1 trade → re-upsert of the whole 5,001-row export | 0.25s |
| `DashboardService.build_report` | 0.41s |
| `FrameworkService.pillar_scores` (2,000 reviewed) | 0.54s |

All indexes referenced by the hot queries exist (`ix_trades_account_exit`,
`ix_trades_logical_trade`, `ix_live_incidents_account_key_id`, the assessment indexes).

## Performance findings

### P1 — `rolling_score_trend` is quadratic (the one that matters)

`src/trading_journal/application/framework.py:791`

`rolling_score_trend` walks every score and, for each *reviewed* trade, calls
`_pillar_scores_from_sample(account_scores[:index + 1], …)`. That slice is an O(i) copy,
and `_period_pillar_score` (`framework.py:1411`) then rescans the whole prefix four
times — `reviewed`, `automatic`, `unreviewed`, `pnl_by_trade` — three times over (once
per pillar). Everything after `sample = reviewed[-window:]` is bounded by `window`, so
the prefix rescans are the entire cost.

Measured, uncached, on the Bearings → Monitor page:

| Reviewed trades | `rolling_score_trend` |
|---|---|
| 300 | 0.27s |
| 1,000 | 2.34s |
| 2,000 | **9.47s** |

`presentation/framework.py:2143` filters the result to the selected date range *after*
computing all of it, so narrowing "Last 90 days" saves nothing. `window` is a user-facing
slider (10–100), so every slider move recomputes.

### P2 — a dead dictionary accounts for a third of P1

`framework.py:1436` builds `pnl_by_trade = {item.trade_id: Decimal(item.net_pnl) for item
in scores}` across the entire prefix and passes it to `_period_components`, whose body
(`framework.py:1470-1503`) never references the parameter. A second dead construction
sits at `framework.py:987`.

Verified by stubbing the dict to `{}` and re-running the 2,000-reviewed case:
**9.47s → 3.15s.** One line, ~3×.

### P3 — the 15s analytics caches are invalidated faster than they fill

`_cached_dashboard_report` and the framework snapshot key on `_database_change_token`
(`app.py:1206`), which includes the **`-wal`** file's mtime and size. The 5s auto-sync
fragment (`app.py:1423`) re-imports the live snapshot whenever its mtime/size moved
(`auto_sync.py:128`) — the EA rewrites that file every 10s — and `replace_live_positions`
(`sqlite_repository.py:1939`) unconditionally DELETEs and re-INSERTs every live row.

**Deferred deliberately.** A content-hash guard does not help: `current_price` moves on
almost every tick while positions are open, so the rewrite is genuinely necessary and the
WAL genuinely moves. Fixing this properly means decoupling the analytics cache key from
whole-database writes, which is its own design pass. After P1/P2 the cached paths are
fast enough (0.41s / 0.54s at 5k) that a cache miss is not user-visible. Re-measure before
spending anything here.

### P4 — unbounded read on a 5-second write path

`sqlite_repository.py:2274` — `record_live_incident_transitions` loads *every* incident
row ever recorded for the account, on every Ongoing render, only to keep the latest row
per `incident_key`. The table grows monotonically (one row per open, one per resolve).
The supporting index `(mt5_account_id, incident_key, id)` already exists.

### P5 — re-import UPDATEs every unchanged row

`sqlite_repository.py:4268` — `source_updated_at: now` sits in the always-applied `values`
dict, so every existing `Trade` is dirtied even when nothing else changed. Measured: a
byte-identical re-import of 400 rows issues **400 UPDATE statements**. Bounded in practice
by the auto-sync hash guard and only 0.25s at 5,000 rows, so this is a reporting problem
(see C2) rather than a speed problem.

### P6 — small, safe wins

- `sqlite_repository.py:4378` — `count_trades()` is `len(session.scalars(select(Trade)).all())`: full ORM hydration to produce an integer.
- `sqlite_repository.py:3998` — `list_trades()` loads every trade across **every** account with no filter and has zero callers outside tests. Dead code, and a cross-account leak if anyone ever used it.
- `reporting_time.py:58` — `detect_local_timezone()` runs `Path("/etc/localtime").resolve()` on every call with no cache. On "Local Timezone" basis that is roughly three `realpath` syscalls per trade per dashboard build.

### P7 — the ingestion API re-migrates the schema on every request

`ingestion_api.py:71` and `:111` construct a repository and call `initialize()` per HTTP
request: `_require_clean_framework_schema`, `create_all`, ~30 `PRAGMA table_info`/`ALTER`
probes, index and trigger creation, and an `UPDATE account_risk_policies …`, all inside a
write transaction. Measured **8.8 ms per request** on a 2,000-trade database, against one
live-snapshot POST every 10s per connected user.

### P8 — the MQL5 exporter is O(n²)

`mql5/TradingJournalSync.mq5`. Code-reading only; no MT5 terminal available here.

- `:700` `ContainsPosition` linear-scans the accumulating array inside the loop over every deal.
- `:730` `IsPositionIdentifierOpen` re-scans all open positions once per historical position.
- `:515` `ContainsText(acked_ids, …)` is linear per position.

At ~10,000 positions / ~30,000 deals this is hundreds of millions of string and integer
comparisons on the MT5 timer thread, every 60 seconds.

Also `:773-819` `AppendLivePositionJson` duplicates the stop-validity and risk arithmetic
already written inline at `:894-911` — two copies that must stay in sync by hand.

## Correctness findings

### C1 — broker DST corrupts stored UTC times

The EA stamps every exported row with the offset in effect **at export time**
(`ServerUtcOffsetMinutes()`, `TradingJournalSync.mq5:717`), not the offset that applied at
each trade's close. `upsert_mt5_positions` normalizes to UTC only when a row is first
created (`sqlite_repository.py:4294`), so the first import wins permanently.

Demonstrated:

```
close 2026-01-15 23:30 server time (EET winter, +120) → true UTC 21:30Z
first imported in July (offset +180)                  → stored  20:30Z   (1h early)
  server basis: 2026-01-15T23:30+03:00   correct wall clock
  utc    basis: 2026-01-15T20:30+00:00   1h off
  local  basis: 2026-01-16T03:30+07:00   1h off
```

Server basis — the default (`sqlite_repository.py:1078`) — round-trips correctly, so most
users never see this. On UTC or Local basis a near-midnight close can land on the wrong
reporting day, which feeds the daily and weekly loss-limit replay.

**Open decision.** The root fix is exporting the historical offset per trade, which is an
MT5 `schema_version` bump and therefore a database reset for existing users. The
alternative is documenting the caveat next to the UTC and Local basis options. The plan
carries the documentation option and marks the root fix as gated.

### C2 — `DivisionByZero` on a wiped account (latent)

`dashboard.py:294` computes `starting_balance = funded_capital + prior_pnl`. Funded capital
is validated `>= 0.01` (`sqlite_repository.py:1527`), but `prior_pnl` — the P&L of every
trade before a selected `start_date` — is unbounded negative.

Reproduced:

- `starting_balance == 0` → `_DrawdownTracker.advance` raises `decimal.DivisionByZero` (`dashboard.py:188`), and so does `balance_growth_percent` (`dashboard.py:358`).
- `starting_balance < 0` → no crash, nonsense output: a `-500` start reports a `-20%` drawdown.

**Not reachable today.** The Dashboard page calls `build_report(account_id=…)` with no
date range (`app.py:1346`). The parameters exist and the test suite exercises them, so
this fires the day a date filter is wired into the UI.

### C3 — misleading import counts

`ImportResult.skipped_count` is hardcoded `0` (`sqlite_repository.py:4354`) and every
unchanged row counts as `updated`, so the toast at `app.py:1414` tells a user "5,000
updated" when a single new trade arrived. Confirmed by the repo's own passing test
`test_reimport_refreshes_execution_data`.

### C4 — hosted "Local Timezone" is the server's zone, not the viewer's

`browser_timezone()` (`presentation/browser_timezone.py`) is threaded into the Ongoing
page (`ongoing.py:417`) and the Monitor page (`presentation/framework.py:2048`), but
`DashboardService` never passes `local_zone` (`dashboard.py:321`, `:582`). In the Docker
deployment (container clock = UTC) the Dashboard's local calendar therefore disagrees with
the other pages' for every user outside UTC.

### C5 — CLAUDE.md documents an audit trail the code does not keep

CLAUDE.md's "Domain conventions to preserve" states *"Corrections version, they don't
overwrite … see `PostTradeAssessment`/`PostTradeAssessmentRevision`"*. No such model
exists; `post_trade_assessment_revisions` is explicitly dropped during migration
(`sqlite_repository.py:1171`). Editing a review overwrites the single active row, pinned by
passing tests (`test_correction_overwrites_the_single_current_assessment`,
`test_repeated_manual_assessment_save_keeps_one_row`). Supersession fires only when
logical-trade membership changes.

**Resolved:** the single-current-revision design is intentional. The document is wrong and
is what changes.

## Corrections to earlier notes

`docs/mt5-import-scale-audit.md` is stale and should be retired:

- Its hotspot #1 (one `SELECT` per row in the upsert) **has been fixed** — the bulk pre-fetch is at `sqlite_repository.py:4219-4231`.
- Its line references (`2826-2911`, `2679-2698`) point at code that has since moved.
- Its ranking is wrong. Measured, the import path (1.78s at 5,000) and the dashboard (0.41s) are fine; the framework scoring it ranks third is the only path that actually degrades.

## Checked and cleared

Not findings — recorded so nobody re-investigates them:

- **Historical R recalculating against a new policy version is deliberate**, tested (`test_account_policy_supplies_r_and_preserves_imported_policy_context`) and gated behind explicit confirmation copy (`presentation/framework.py:2891-2918`).
- **Live snapshot times really are UTC** — `ServerTime(TimeGMT())`, `TradingJournalSync.mq5:878` — so the two-interval staleness detection is sound.
- **`record_live_incident_transitions` is genuinely transition-only**, so the write on a render path is idempotent.
- **The multi-user cookie key fails closed** (`presentation/multiuser_auth.py:33-49`).
- Import idempotency, tenant isolation, hard-rule snapshot immutability and the timezone/day-boundary replay all have real test coverage.
- Unbounded `.in_()` lists at `sqlite_repository.py:3801` and `:4225` are safe to ~32,000 rows on SQLite 3.46's variable limit.
