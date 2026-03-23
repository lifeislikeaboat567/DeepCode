"""Deprecated Streamlit frontend entrypoint.

The active frontend is Reflex and can be launched with ``deepcode ui``.
"""

from __future__ import annotations


def main() -> None:
    raise RuntimeError(
        "Streamlit frontend has been deprecated and decoupled from runtime. "
        "Use Reflex UI via `deepcode ui`."
    )


if __name__ == "__main__":
    main()
