"""Deprecated compatibility module.

The Streamlit helper runtime was removed. Active Web UI code lives in ``deepcode_reflex``.
"""

from __future__ import annotations


def _deprecated(*_args, **_kwargs):
    raise RuntimeError(
        "deepcode.ui.helpers is deprecated and no longer part of active runtime. "
        "Use Reflex UI modules under deepcode_reflex."
    )


__all__ = ["_deprecated"]
