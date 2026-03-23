"""Failure classification and fallback reflection helpers."""

from __future__ import annotations

from deepcode.agents.task_state import ReflectionRecord


def classify_failure_output(output: str) -> str:
    """Classify a failure category from raw execution output."""
    text = (output or "").lower()
    if any(token in text for token in {"not found", "no such file", "path"}):
        return "path_issue"
    if any(token in text for token in {"permission", "denied", "forbidden"}):
        return "permission_issue"
    if any(token in text for token in {"timeout", "timed out", "network", "connection"}):
        return "network_issue"
    if any(token in text for token in {"invalid", "parameter", "argument", "unexpected keyword"}):
        return "wrong_parameters"
    if any(token in text for token in {"importerror", "module not found", "dependency", "package"}):
        return "missing_dependency"
    if any(token in text for token in {"environment", "interpreter", "executable"}):
        return "environment_issue"
    if any(token in text for token in {"traceback", "exception", "assert", "syntaxerror", "typeerror", "valueerror"}):
        return "logic_bug"
    if any(token in text for token in {"insufficient", "unclear", "missing context"}):
        return "insufficient_information"
    return "unknown"


def default_recovery(category: str) -> tuple[list[str], bool, bool, bool]:
    """Return fallback fixes and flow decisions for a failure category."""
    mapping = {
        "path_issue": (["Re-check file paths and working directory", "List the target directory before retrying"], True, False, False),
        "wrong_parameters": (["Inspect tool schema and retry with explicit arguments", "Reduce optional parameters and retry"], True, False, False),
        "permission_issue": (["Check risk policy and approval requirements", "Ask for explicit confirmation before retrying"], False, False, True),
        "missing_dependency": (["Install or avoid the missing dependency", "Add a local script fallback if possible"], True, False, False),
        "environment_issue": (["Inspect runtime environment and executable paths", "Retry with a simpler isolated script"], True, False, False),
        "logic_bug": (["Inspect stack trace and patch the generated code", "Add a narrower validation case and retry"], True, False, False),
        "insufficient_information": (["Observe more files and runtime context", "Ask the user for the missing boundary only if observation cannot resolve it"], False, True, False),
        "unknown": (["Gather more context from tool output", "Retry with a smaller, more explicit action"], True, False, False),
    }
    return mapping.get(category, mapping["unknown"])


def fallback_reflection(step_id: str, category: str, diagnosis: str) -> ReflectionRecord:
    """Build a deterministic reflection record when the LLM output is missing or weak."""
    fixes, should_retry, should_replan, requires_user_input = default_recovery(category)
    return ReflectionRecord(
        step_id=step_id,
        failure_category=category,
        diagnosis=diagnosis,
        proposed_fixes=fixes,
        selected_fix=fixes[0] if fixes else None,
        should_retry=should_retry,
        should_replan=should_replan,
        requires_user_input=requires_user_input,
    )
