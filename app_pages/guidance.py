"""Read-only operating guide for the three-pillar framework."""

from pathlib import Path

import streamlit as st

from trading_journal.presentation.i18n import language

suffix = ".vi" if language() == "vi" else ""
guide_path = Path(__file__).parents[1] / "docs" / f"three_pillar_framework_guide{suffix}.md"

try:
    guide = guide_path.read_text(encoding="utf-8")
except OSError:
    st.error("The three-pillar operating guide could not be loaded." if language() == "en" else "Không thể tải hướng dẫn vận hành ba trụ cột.")
else:
    st.markdown(guide)
