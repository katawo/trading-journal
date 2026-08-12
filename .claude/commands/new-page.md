---
description: Scaffold a new Streamlit page following this repo's thin-shim convention
argument-hint: [page-name] [one-line description]
---

Add a new page: $ARGUMENTS

Follow the existing convention exactly (see CLAUDE.md's Architecture section):

1. Create the real page logic as a render function in `presentation/` (a new module, or an existing one if it clearly belongs there) — not in `app_pages/`.
2. Create a thin `app_pages/<name>.py` file whose only job is to define an `st.Page` and call into the `presentation/` render function. Look at an existing file under `app_pages/` first and match its shape exactly.
3. Wire the new page into `st.navigation()` in `app.py`'s `main()`.
4. Route every user-facing string through `tr()` from `presentation/i18n.py`, and add the new English strings to the `VI` table in the same change — don't leave Vietnamese untranslated as a follow-up.
5. If the page reads or displays trading data (positions, R-multiples, Framework assessments), check whether it needs to respect per-account currency scoping and the raw-vs-logical-trade distinction — see `.claude/skills/trading-app-engineer/references/framework-and-journal-domain.md` if unsure.
6. Add a basic test if the page's render function has any non-trivial logic worth testing outside Streamlit.

Run `make check` when done.
