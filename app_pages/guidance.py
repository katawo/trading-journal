"""Read-only operating guide for the three-pillar framework."""

from pathlib import Path

import streamlit as st

from trading_journal.presentation.i18n import language, tr

suffix = ".vi" if language() == "vi" else ""
guide_path = Path(__file__).parents[1] / "docs" / f"three_pillar_framework_guide{suffix}.md"

try:
    guide = guide_path.read_text(encoding="utf-8")
except OSError:
    st.error(tr("The three-pillar operating guide could not be loaded."))
else:
    st.markdown(guide)
