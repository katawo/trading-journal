# Account-scoped Streamlit performance investigation

**Date:** 2026-09-04

**Scope:** Working-tree changes in `app.py`, framework application and presentation services, and their tests.

**Decision:** Framework navigation, dashboard metrics, coaching state, and alerts remain strictly scoped to the active MT5 account. Cross-account aggregation is intentionally excluded.

## Executive summary

The implementation is safe to continue with: no blocking correctness defect or observed cross-account data leak remains. It replaces several independently computed framework values with one immutable, account-keyed snapshot and reuses that snapshot for navigation, alerts, and the Dashboard framework panel.

On a copy of the current local database, the final code reduced a cold AppTest render from approximately **3.14 s to 2.54 s** and a stable rerender from approximately **0.52-0.58 s to a 0.362 s median**. The snapshot itself took **69.3 ms and 24 SELECT statements**. Coaching maintenance is now decided inside that cached read and applied separately only when persistence is required; a collapsed Dashboard with no plan performs no additional framework query.

The account identity is part of the cache key, and both alert and Dashboard rendering reject a snapshot whose `account_id` differs from the supplied account. Database-wide invalidation remains conservative: unrelated writes can cause extra computation, but cannot cause account data to be shared.

## Review follow-up

### Resolved: coaching maintenance bypassed the cached snapshot

`AccountFrameworkSnapshot` now carries a pure `CoachingFocusPlan` when focus creation or safety supersession is required. `render_dashboard_coaching_focus()` performs no framework-service work while collapsed when that plan is empty. When a plan exists, it applies the explicit persistence action outside `st.cache_data`.

This preserves automatic coaching behavior without the previously measured **47.7 ms and 16 SELECT statements** on every stable Dashboard rerun.

### Resolved: repeated snapshot lookup and repository lifecycle

The entrypoint snapshot is stored in session state for the selected page in the same rerun. Dashboard reuses it, with a cached-function fallback when the page is executed standalone. `_cached_dashboard_report()` now closes its temporary repository in `finally`, matching the framework snapshot wrapper.

### Resolved: validation, tests, and alert presentation

- `AccountFrameworkSnapshot.require_account()` is the single account-identity guard used by both renderers.
- Pure snapshot, mismatch, coaching-plan, cache-reuse, and repository-lifecycle tests now live in the unmarked fast suite.
- The two-account fixture distinguishes risk configuration, alerts, focus state, review count, and pillar evidence in addition to account IDs.
- The active account is shown once in the alert panel heading instead of on every alert row.

### Accepted: invalidation is account-keyed but database-wide

`_cached_account_framework_snapshot()` keys cached values by database path, database change token, and account ID. The change token comes from the SQLite database and WAL file metadata, so a write for any account invalidates cached entries for every account in that database.

- Evidence: `app.py:1008-1047`.
- Correctness: safe; account IDs are still separate cache keys and render-boundary checks reject mismatches.
- Performance: inactive-account auto-sync activity can reduce cache hit rates for the active account.
- Recommendation: keep the conservative token until profiling shows it matters. A true per-account revision needs reliable mutation tracking across all framework source tables; an incomplete timestamp fingerprint would risk stale alerts and badges.

### Accepted: exact `now` values remain part of risk-cache identity

`FrameworkService.risk_snapshot()` keeps `(account_id, now)` as its cache key. This deduplicates the repeated `now=None` path inside account snapshot construction and remains correct for repeated calculations at an identical explicit as-of instant. `TodayService` requests one explicit instant once, so there is no duplicate Today calculation to eliminate.

## What changed

### Account snapshot

`AccountFrameworkSnapshot` is a frozen read model containing:

- account ID;
- framework alerts;
- review queue count;
- coaching focus and progress;
- risk snapshot;
- three pillar scores;
- readiness assessment;
- an optional coaching-focus persistence plan.

`FrameworkService.account_snapshot(account_id)` computes these values through one short-lived service. Its internal caches allow dependent calculations to reuse the same account trade history and risk timeline.

### Streamlit cache

`_cached_account_framework_snapshot(database_path, database_change_token, account_id)` uses `st.cache_data` with a 15-second TTL and a 128-entry limit. It opens a temporary repository, builds a serializable frozen snapshot, and closes the repository in `finally`.

The effective isolation key is:

```text
database path + SQLite/WAL change token + MT5 account ID
```

In multi-user mode, each application user already receives a distinct database path. Within one database, each MT5 account receives a distinct cache entry.

### Consumers

The same active-account snapshot now supplies:

- Review navigation count;
- Monitor alert count;
- coaching-focus-ready navigation state;
- warning/critical alert bubble;
- Dashboard risk, readiness, and pillar metrics.

The alert bubble no longer scans or presents other MT5 accounts.

## How Streamlit works technically in this app

Streamlit executes the Python entry script from top to bottom for each browser session. Most widget interactions request another script run; Streamlit compares the new element stream with the previous run and updates the browser. Ordinary local variables disappear after each run, while `st.session_state` retains session-specific UI state.

`st.navigation()` constructs the available pages during the entry-script run. `page.run()` then executes the selected page inside that run. This means navigation badge data is needed before the selected page renders, which is why framework analytics in `main()` can affect every page's rerun cost.

`st.cache_data` stores serialized return values by function identity and hashed arguments. Cached data can be reused across reruns and sessions in the same Streamlit process, but callers receive deserialized values rather than a shared mutable instance. In this implementation, database path and account ID prevent one user's or MT5 account's snapshot from satisfying another account's cache key.

`st.cache_resource` behaves differently: it retains a shared live object. The application uses it for bounded repository instances, while snapshots correctly use `st.cache_data` because they are immutable data rather than connections.

The coaching expander uses `on_change="rerun"`. Its `.open` state allows expensive detail rendering to be skipped while collapsed. The cached snapshot describes any required focus creation/supersession, while the actual database write remains an explicit uncached action. No extra rerun is forced because framework alerts and scores do not depend on the newly created focus; the database token refreshes the snapshot on the next natural run.

The SQLite/WAL metadata token is passed as a cache argument. A committed database change normally changes this token, producing a cache miss immediately instead of waiting for the TTL. The TTL is a bounded fallback, not the primary freshness mechanism.

## Bottleneck matrix

| Area | Trigger | Current cost/evidence | Account scope | Severity | Recommended direction |
|---|---|---:|---|---|---|
| Coaching-focus maintenance | Only when cached plan requests persistence | No extra read service on the common empty-plan path | One active account | Low | Keep persistence outside `st.cache_data` |
| Framework snapshot miss | DB/WAL change or 15 s expiry | 69.3 ms, 24 SELECTs | One requested account | Medium | Keep cache; later push rolling windows into SQL if history grows |
| Database-wide invalidation | Any write in the user's SQLite DB | Causes otherwise avoidable account cache misses | Results isolated; invalidation shared | Low | Add per-account revisions only with complete mutation coverage |
| Navigation precomputation | Every full Streamlit rerun | Cache hit is cheap; miss blocks navigation construction | Active account only | Low | Retain single snapshot; consider fragments only if navigation architecture changes |
| Full account history scoring | Snapshot or coaching cache miss | Loads/scans complete closed-trade history | One account | Scale risk | Add SQL-side/windowed reads after realistic large-history benchmarks |
| Dashboard report | Dashboard render | Separately cached; still uses full-history service paths on miss | One account | Scale risk | Preserve separate cache, then optimize source queries based on profiling |
| MT5 import/upsert | Changed export file | Existing scale audit identifies full-file parsing and row-wise upsert | Per imported account | Separate high-scale risk | See `docs/mt5-import-scale-audit.md` |

## Performance measurements

Measurements used a copied database, never the live journal file. The copy contained 3 configured accounts, 817 positions, 802 logical trades, and 221 assessments. AppTest timings include Python/Streamlit execution on this workstation and are comparative, not production service-level objectives.

| Phase | Cold render | Stable rerender |
|---|---:|---:|
| Before snapshot consolidation | ~3.14 s | ~0.52-0.58 s |
| Initial snapshot implementation before restoring automatic coaching | ~2.31 s | ~0.34-0.38 s |
| Automatic coaching restored with an unconditional service call | 2.552 s | 0.541, 0.409, 0.386, 0.682, 0.424 s; median **0.424 s** |
| Final cached coaching-plan implementation | 2.538 s | 0.540, 0.362, 0.351, 0.367, 0.351 s; median **0.362 s** |

Final component profile:

| Operation | Time | SQL statements |
|---|---:|---:|
| `FrameworkService.account_snapshot()` | 69.3 ms | 24 SELECTs |
| Collapsed coaching renderer with an empty cached plan | No repository/service call | 0 |

The final code remains faster than the baseline while preserving coaching behavior. Timing variance means these figures should be treated as directional; repeatable CI benchmarks would be required for regression thresholds.

## Pros and cons of the per-account approach

### Pros

- Matches the product rule: the trader sees only the active account's framework state.
- Removes cross-account alert scans from normal navigation reruns.
- Prevents currencies, risk policies, review queues, and readiness evidence from being combined.
- Makes cached data auditable through an explicit `account_id` field.
- Reduces repeated history loading by sharing one service snapshot across several consumers.
- Keeps the cached value immutable and serializable.

### Cons

- Switching accounts requires a different snapshot and usually another computation.
- Database-wide invalidation can evict useful account-specific results after unrelated account writes.
- Snapshot construction currently calculates all framework sections even when a page needs only one badge.
- A required coaching write still invalidates the next snapshot, as it should.
- Full-history calculation cost will still grow with the selected account's journal size.

## Validation

- `make check`: **338 passed, 53 deselected**.
- `make test-web`: **52 passed, 339 deselected**.
- Focused snapshot and follow-up regression tests: **9 passed**.
- Focus and coaching service scenarios: **16 passed**.
- `python -m compileall -q app.py src tests/test_framework_snapshot.py`: passed.
- `git diff --check`: passed.
- AppTest benchmark completed with zero rendered exceptions.
- No database migration or live journal-data modification was introduced.

## Disposition

There is no release-blocking correctness finding in the reviewed changes. The implementation satisfies strict per-account presentation, keeps persistence outside cached reads, and improves rerender performance. Database-wide invalidation remains the only accepted cache-granularity tradeoff; change it only after measurement and with complete per-account mutation tracking.
