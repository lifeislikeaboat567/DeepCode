"""Regression tests for preflight behavior across command targets."""

from __future__ import annotations

from deepcode.config import Settings
from deepcode.preflight import run_preflight


def _check_map(target: str, settings: Settings) -> dict[str, str]:
    checks = run_preflight(target=target, settings=settings)
    return {check.name: check.status for check in checks}


def test_ui_preflight_warns_when_openai_key_missing(tmp_path):
    settings = Settings(
        llm_provider="openai",
        llm_api_key="",
        data_dir=tmp_path,
    )

    statuses = _check_map(target="ui", settings=settings)

    assert statuses["llm_api_key"] == "warn"


def test_run_preflight_fails_when_openai_key_missing(tmp_path):
    settings = Settings(
        llm_provider="openai",
        llm_api_key="",
        data_dir=tmp_path,
    )

    statuses = _check_map(target="run", settings=settings)

    assert statuses["llm_api_key"] == "fail"


def test_ui_preflight_passes_mock_provider_without_key(tmp_path):
    settings = Settings(
        llm_provider="mock",
        llm_api_key="",
        data_dir=tmp_path,
    )

    statuses = _check_map(target="ui", settings=settings)

    assert statuses["llm_provider"] == "pass"
    assert "llm_api_key" not in statuses