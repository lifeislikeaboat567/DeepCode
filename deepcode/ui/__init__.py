"""Deprecated Streamlit compatibility package.

The active Web UI is Reflex under ``deepcode_reflex``.
This package is retained only to provide explicit deprecation guidance.
"""

from __future__ import annotations

import warnings

warnings.warn(
	"deepcode.ui is deprecated and no longer used by runtime commands. "
	"Use Reflex UI via `deepcode ui`.",
	DeprecationWarning,
	stacklevel=2,
)
