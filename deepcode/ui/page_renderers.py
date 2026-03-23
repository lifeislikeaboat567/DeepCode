"""Deprecated compatibility module.

The Streamlit page renderer runtime was removed. Active Web UI code lives in ``deepcode_reflex``.
"""

from __future__ import annotations


def render_page(_page: str) -> None:
    raise RuntimeError(
        "deepcode.ui.page_renderers is deprecated and no longer part of active runtime. "
        "Use Reflex UI modules under deepcode_reflex."
    )
