# Dashboard changes review

**Date:** 2026-08-29  
**Scope:** All working-tree changes in `app.py`, dashboard and framework presentation helpers, localization, tests, and the dashboard design specification.

## Findings

All findings from the final working-tree review are resolved:

- Concentration bars use centered percentage bins with explicit widths, so boundary bars are not clipped.
- Direction edge matrices stack vertically below the mobile breakpoint instead of requiring a 52rem-wide horizontal canvas.
- New dashboard labels and Process & risk help text are present in the Vietnamese catalog.
- The implemented dashboard specification now reflects the five-block layout, compact Direction edge matrices, and removal of the Outcome mix and breakdown charts.

## Validation

- `tests/test_dashboard_charts.py`, `tests/test_i18n.py`, and `tests/test_three_pillar_framework.py`: **154 passed**.
- Changed dashboard integration scenarios from `tests/test_app.py`: **2 passed**.
- `python -m compileall -q app.py src`: **passed**.
- `git diff --check`: **passed**.
- `make check`: progressed without a failure through the changed dashboard scenarios but was interrupted during the slower unrelated suite. The complete repository-wide result remains unconfirmed.

## Review disposition

The reviewed findings are resolved. Perform the documented light/dark and narrow-width visual inspection before release.
