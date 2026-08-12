---
description: Review the current diff against this repo's non-negotiable domain rules
---

Run `git diff` (staged and unstaged) and review the changes specifically against the "Domain conventions to preserve" section of `CLAUDE.md`. For each rule below, state explicitly whether the diff violates it, is unrelated to it, or correctly handles it — don't just give a general thumbs up:

- MT5 stays read-only: no new order/write path, no stored MT5 password.
- R-multiples only from known risk (recorded SL, real-loss estimate, or opt-in captured pre-trade balance) — never inferred from outcome, never silently defaulted.
- Hard-rule Clear/Fail results aren't recomputed retroactively when rules change.
- Assessment corrections create a new revision rather than mutating the original row.
- Daily P&L/balance/drawdown/risk-limit monitoring use raw positions, never the mutable logical-trade grouping.
- No cross-account currency aggregation or conversion.
- Desktop stays loopback-only.
- Any schema_version change updates both the `.mq5` exporter and `domain/models.py`.

If you find a violation, don't just flag it — propose the specific fix. If everything checks out, say so plainly and move on; don't manufacture concerns that aren't there.
