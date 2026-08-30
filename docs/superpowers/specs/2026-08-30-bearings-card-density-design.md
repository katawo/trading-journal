# Bearings: compact trade & period cards

**Status:** Implemented 2026-08-30. Revised from the original approved design after several rounds of feedback against the real running app — see "Changes from the original design" below.

## Intent

The Review register's trade cards and the Monitor period cards are read many times per session but used to spend a lot of vertical space on low-priority text (stacked P/R/S lines, a bare "Zone-aligned 12-criterion" caption, plain badges, raw ticket numbers) while the two things a reviewer actually scans for — the process score and the action to take — didn't stand out. This redesign compacts each card into fixed, table-like columns and gives the score and the "Review" action clear visual priority, reusing the existing theme tokens (`.streamlit/config.toml`'s primary/green/red/orange/blue/gray triples) already exposed via `st.badge`/`direction_tag`/`outcome_tag` — no new colors introduced.

Mockup (initial toggle, superseded by in-app iteration): artifact `https://claude.ai/code/artifact/c720c03c-f3b4-4396-b74a-4a8f74ca9045`.

## Scope

- `_render_review_register` (Bearings → Review trade register), `src/trading_journal/presentation/framework.py:1458-`.
- `_render_period_reviews` (Bearings → Monitor period reviews), `framework.py:2224-` — the "Ongoing periods" / "Latest completed periods" cards and the "Past periods requiring attention" backlog.
- `_format_execution_number` (`framework.py:371`) — presentation-only price/volume rounding, see below.
- No change to `FrameworkService`, scoring logic, or any data model. Presentation-only.

## Review register card: final layout

Each trade is one bordered card built from **8 fixed `st.columns()`** (not a single flexible/wrapping container — an earlier version used one wide flex row and it misaligned across rows and let grouped trades wrap onto extra lines):

`Select | Trade | Positions | P&L | Method | Score | Rules | Actions`

1. **Select** — the existing selection checkbox.
2. **Trade** — `LT-{id}` (bold), `trade.symbol`, a direction chip (↗ Long / ↘ Short, `direction_tag` colors), and — only if the user gave the group a real custom name (`trade.custom_label`) — that name. The raw MT5 ticket number and the auto-generated fallback label (`"{symbol} {direction} · {time}"`) are **not** shown here any more: they duplicated the direction chip/symbol and read as meaningless noise (a bare `#2251931295`) rather than a deliberate design choice.
3. **Positions** — a `"{n} pos"` badge (abbreviated; tooltip via `help=` lists the full MT5 ticket numbers) instead of enumerating every position ID inline.
4. **P&L** — the signed currency amount as a colored `st.badge` (green/red/gray from `outcome_tag`), not a separate "Profit"/"Loss" text badge next to a plain number — the color is the signal, the redundant label was removed, and it needed the same pill/background treatment as the other chips to look consistent rather than floating as bare colored text.
5. **Method** — the review-kind chip (Auto / Manual / Requires review / Awaiting approval), in its own column so it can never wrap onto a second line inside a crowded flex row (this was the direct cause of grouped trades rendering taller than single-position ones).
6. **Score** — the overall score as a color-tiered badge (green/orange/red from `score.quality_status`, reusing the existing `good`/`needs_improvement`/`bad` domain tiers — not new 80/50 bands), plus the Psychology/Risk/System breakdown condensed to bare numbers in a fixed `(P-R-S)` order, e.g. `(50-70-50)`; a hard-blocked pillar keeps an inline `⚠` on its number (e.g. `(100-100⚠-100)`) since the order is documented once in the column header's help popover.
7. **Rules** — the hard-rule Clear/Fail badge (unchanged).
8. **Actions** — a primary filled **Review** button, plus — only when applicable (`needs_approval`/`auto_review` kind, and/or `trade.is_group`) — a compact **⋮ "More actions"** `st.popover` holding Approve/Quick review and Ungroup. These used to be separate buttons that stacked vertically or wrapped; consolidating them keeps every card the same height regardless of how many secondary actions it has.

The execution-detail row (Opened/Entry/Closed/Exit/Duration/Size) keeps the same `st.caption`/`st.markdown` calls as before (needed for existing AppTest assertions) but is visually shrunk via scoped CSS (`div[class*="st-key-review-detail-"]`) rather than restructuring the widgets. The classification (e.g. "Needs improvement Win") is its own colored badge (same tier colors as the Score badge) next to a plain caption for the risk-evidence source and rubric name — it used to be plain text dot-chained together with the risk-evidence label, which read as an undifferentiated, confusing string.

### Price/volume precision fix

A grouped logical trade's entry/exit price is a notional-weighted average across its member positions, which can produce a `Decimal` with dozens of repeating digits (e.g. `4,466.67266666666666666667`). `_format_execution_number` now takes an optional `reference` price (a raw member position's price) and rounds to *that* value's decimal precision — the broker's real quoting precision for the symbol — instead of either showing the raw division result or an arbitrary fixed number of decimals.

## Monitor period cards

- "Ongoing weekly/monthly" and "Latest completed weekly/monthly" cards: status (`Reviewed`/`Pending review`/`Due`/`Up to date`/`Skipped`/`No activity`) is a colored chip instead of plain `st.metric`/text; the ongoing card's reviewed/closed/pending sentence is a compact `st.progress` bar with the counts as its label.
- "Past periods requiring attention" backlog: the `st.dataframe` table became one compact bordered row per backlog period (cadence, period range, reviewed/closed/pending counts, a Due/Trades-first status chip).
- The **selectbox ("Choose a period") + save/skip forms below the backlog are unchanged** — see "Changes from the original design".
- No change to `FrameworkService.period_review_backlog`/`save_period_review`; this only changes how the same data is laid out.

## Changes from the original design

The original design (see git history on this file) proposed two things this implementation deliberately does *not* do:

1. **A per-backlog-row "Review" button that opens the form directly**, replacing the selectbox. `test_monitor_tab_shows_early_estimate_not_incomplete_for_a_partial_sample` asserts the `"Choose a period"` selectbox exists when there are multiple backlog periods. Changing the interaction model would mean rewriting that test for a part of the request the user had flagged as secondary ("apply the same density/style principles" to Monitor, not "change how you act on it") — kept the existing selectbox/form flow and only restyled the display around it.
2. **A single wide flex container per trade row with inline badges**, instead of fixed columns. This was the first cut at the Review register and looked fine for a single-position trade, but broke down under real data: badge widths varied per row (different ticket-count text, Auto vs. Manual vs. nothing) so nothing lined up in a column, and a grouped trade with more badges than fit on one line wrapped onto a second line, making its card taller than a single-position trade's. Replaced with the 8-fixed-column layout described above.

Also fixed along the way (not part of the original design, found while implementing it): the exit-price float-formatting artifact flagged in the original design as a separate concern is now fixed (see "Price/volume precision fix" above), and a `Select` header-label wrap caused by an over-narrow column.

## Verification

No new business logic, so no new unit tests were needed beyond what already covers `_render_review_register`/`_render_period_reviews` inputs — one existing test (`test_register_flags_the_specific_hard_blocked_pillar`) was updated to match the new `(P-R-S)` score-caption format. `make test`: 365 passed. Verified visually in a running app (`make run` equivalent) against the real dev database: single-position trades, a 3-position grouped trade, a hard-blocked pillar, an unreviewed trade, and the Monitor period cards, in the browser.
