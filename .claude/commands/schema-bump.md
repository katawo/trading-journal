---
description: Guided workflow for bumping the MT5 export schema_version
argument-hint: [new-fields-or-reason]
---

Bump the MT5 export schema, following CLAUDE.md's stated convention (no migration path — this is a greenfield schema bump, not a migration).

Context/reason for this bump: $ARGUMENTS

Do all of the following, in order, and don't skip any step even if it seems obvious:

1. Read `src/trading_journal/domain/models.py` and find `MT5PositionExport` and its current `schema_version`.
2. Read the relevant `.mq5` exporter (`mql5/TradingJournalExporter.mq5`) and find where it writes the schema version and the CSV columns.
3. Increment `schema_version` and add the new field(s) to *both* sides together — the Pydantic model and the MQL5 exporter — in the same change. Do not update only one side.
4. Check `import_mt5.py` (application layer) for any logic that branches on `schema_version` (e.g. gating which fields/evidence a given export version can carry) and update it for the new version.
5. State explicitly in your summary that this schema bump has no migration path: existing local databases will need `make reset-db CONFIRM_RESET=yes`, and call out that this is a destructive, user-initiated action — don't run it yourself.
6. Add/update tests in `tests/test_mt5_import.py` covering the new schema version, including how an *older*-version export should be handled (still parseable, or explicitly rejected — pick one and test it).
7. If this is a trading-correctness-sensitive field (affects R-multiples, risk gating, or Framework assessments), consult `.claude/skills/trading-app-engineer/references/framework-and-journal-domain.md` before finalizing.

Run `make check` at the end and report results.
