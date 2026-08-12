---
description: Run this repo's real test/compile checks and fix any regressions
---

Run `make check` (which runs `pytest -q` then `python -m compileall` on `app.py` and `src/`).

Report the results plainly: what passed, what failed, and for any failure, whether it's caused by the current change or pre-existing. If it's caused by the current change, fix it and re-run `make check` until clean. If a failure looks pre-existing and unrelated, say so explicitly rather than silently ignoring it or trying to fix unrelated code.
