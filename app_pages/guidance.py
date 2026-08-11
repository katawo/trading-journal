"""Read-only operating guide for the three-pillar framework."""

from pathlib import Path

import streamlit as st


guide_path = Path(__file__).parents[1] / "docs" / "three_pillar_framework_guide.md"

try:
    guide = guide_path.read_text(encoding="utf-8")
except OSError:
    st.error("The three-pillar operating guide could not be loaded.")
else:
    st.markdown(guide)
