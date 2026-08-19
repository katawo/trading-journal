"""Shared Trade Compass branding assets for Streamlit surfaces."""

from __future__ import annotations

from html import escape

import streamlit as st


def render_trade_doctrine(label: str) -> None:
    """Render the compact editorial slogan used in the shared app header."""
    words = [escape(word.strip()) for word in label.split("·") if word.strip()]
    word_markup = ' <span class="trade-compass-doctrine-dot" aria-hidden="true">·</span> '.join(
        f'<span class="trade-compass-doctrine-word">{word}</span>' for word in words
    )
    st.html(
        f"""
        <div class="trade-compass-doctrine" role="note" aria-label="{escape(label)}">
            <span class="trade-compass-doctrine-rule" aria-hidden="true"></span>
            <div class="trade-compass-doctrine-words">{word_markup}</div>
            <span class="trade-compass-doctrine-rule" aria-hidden="true"></span>
        </div>
        <style>
        .trade-compass-doctrine {{ display: flex; align-items: center; justify-content: flex-start; gap: 1.1rem; margin: 0.15rem 0 1.5rem; padding-left: 3.5rem; color: var(--text-color); }}
        .trade-compass-doctrine-rule {{ display: none; }}
        .trade-compass-doctrine-words {{ display: flex; align-items: center; justify-content: center; gap: 0.45rem; flex-wrap: wrap; text-align: center; }}
        .trade-compass-doctrine-word {{ position: relative; isolation: isolate; padding: 0.1rem 0.36rem; font-size: 1.18rem; font-weight: 800; letter-spacing: 0.14em; line-height: 1.6; text-transform: uppercase; white-space: nowrap; }}
        .trade-compass-doctrine-word::before, .trade-compass-doctrine-word::after {{ content: ""; position: absolute; z-index: -1; left: 50%; bottom: 0.02em; width: 100%; height: 0.68em; background: rgba(244, 207, 104, 0.88); }}
        .trade-compass-doctrine-word::after {{ transform: translateX(-50%) rotate(-2deg); clip-path: polygon(0% 30%, 96% 0%, 100% 68%, 4% 100%); }}
        .trade-compass-doctrine-word::before {{ opacity: 0.5; transform: translateX(-50%) rotate(1.5deg) translateY(0.06em); clip-path: polygon(2% 10%, 100% 22%, 98% 88%, 0% 78%); }}
        .trade-compass-doctrine-word:nth-child(4n+3)::after {{ transform: translateX(-50%) rotate(2deg); clip-path: polygon(2% 0%, 100% 28%, 96% 100%, 0% 70%); }}
        .trade-compass-doctrine-word:nth-child(4n+3)::before {{ transform: translateX(-50%) rotate(-1.5deg) translateY(0.05em); clip-path: polygon(0% 22%, 98% 8%, 100% 82%, 2% 92%); }}
        .trade-compass-doctrine-dot {{ color: #0e9163; font-size: 1.3rem; font-weight: 850; }}
        @media (max-width: 640px) {{ .trade-compass-doctrine {{ gap: 0.4rem; padding-left: 0.5rem; }} .trade-compass-doctrine-words {{ gap: 0.18rem; }} .trade-compass-doctrine-word {{ font-size: 0.85rem; letter-spacing: 0.08em; }} .trade-compass-doctrine-dot {{ font-size: 1rem; }} }}
        </style>
        """
    )


TRADE_COMPASS_ICON = "data:image/svg+xml;base64,PHN2ZyB2aWV3Qm94PSIwIDAgMTAwIDEwMCIgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIj4gPGNpcmNsZSBjeD0iNTAiIGN5PSI1MCIgcj0iNDQiIGZpbGw9Im5vbmUiIHN0cm9rZT0iIzBlOTE2MyIgc3Ryb2tlLXdpZHRoPSI1Ii8+IDxjaXJjbGUgY3g9IjUwIiBjeT0iMTAiIHI9IjMuNiIgZmlsbD0iIzBlOTE2MyIvPiA8Y2lyY2xlIGN4PSI5MCIgY3k9IjUwIiByPSIzLjIiIGZpbGw9IiMwZTkxNjMiLz4gPGNpcmNsZSBjeD0iNTAiIGN5PSI5MCIgcj0iMy4yIiBmaWxsPSIjMGU5MTYzIi8+IDxjaXJjbGUgY3g9IjEwIiBjeT0iNTAiIHI9IjMuMiIgZmlsbD0iIzBlOTE2MyIvPiA8cGF0aCBkPSJNIDUwIDE2IEwgNDIgNTAgTCA1MCA0NCBMIDU4IDUwIFoiIGZpbGw9IiMxMGI5ODEiIG9wYWNpdHk9IjAuNSIgdHJhbnNmb3JtPSJyb3RhdGUoMTIwIDUwIDUwKSIvPiA8cGF0aCBkPSJNIDUwIDE2IEwgNDIgNTAgTCA1MCA0NCBMIDU4IDUwIFoiIGZpbGw9IiMxMGI5ODEiIG9wYWNpdHk9IjAuNSIgdHJhbnNmb3JtPSJyb3RhdGUoMjQwIDUwIDUwKSIvPiA8cGF0aCBkPSJNIDUwIDE2IEwgNDIgNTAgTCA1MCA0NCBMIDU4IDUwIFoiIGZpbGw9IiMxMGI5ODEiLz4gPGNpcmNsZSBjeD0iNTAiIGN5PSI1MCIgcj0iNy41IiBmaWxsPSIjMGQ3YTUyIi8+IDwvc3ZnPg=="
