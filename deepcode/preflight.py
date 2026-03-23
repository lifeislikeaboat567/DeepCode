"""Preflight checks and environment diagnostics for DeepCode CLI."""

from __future__ import annotations

import platform
import shutil
import sys
from dataclasses import asdict, dataclass
from importlib.util import find_spec
from pathlib import Path
from typing import Any, Literal

from deepcode import __version__
from deepcode.config import Settings, get_settings

CheckStatus = Literal["pass", "warn", "fail"]

_IMPLEMENTED_PROVIDERS = {"openai", "ollama", "gemini", "github_copilot", "mock"}


@dataclass
class CheckResult:
    """Single preflight check result."""

    name: str
    status: CheckStatus
    detail: str
    fix: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        return asdict(self)


def _result(name: str, status: CheckStatus, detail: str, fix: str = "") -> CheckResult:
    return CheckResult(name=name, status=status, detail=detail, fix=fix)


def run_preflight(target: str = "all", settings: Settings | None = None) -> list[CheckResult]:
    """Run baseline checks for the requested target."""
    cfg = settings or get_settings()
    checks: list[CheckResult] = []

    python_ok = sys.version_info >= (3, 11)
    checks.append(
        _result(
            "python_version",
            "pass" if python_ok else "fail",
            f"Python {sys.version.split()[0]}",
            "Use Python 3.11 or newer." if not python_ok else "",
        )
    )

    deepcode_bin = shutil.which("deepcode")
    checks.append(
        _result(
            "command_entry",
            "pass" if deepcode_bin else "warn",
            f"deepcode executable: {deepcode_bin or 'not found in PATH'}",
            "Run with 'python -m deepcode' or install via 'pip install -e .'." if not deepcode_bin else "",
        )
    )

    try:
        cfg.ensure_data_dir()
        data_dir = cfg.data_dir.expanduser().resolve()
        checks.append(_result("data_dir", "pass", f"Writable data dir: {data_dir}"))
    except Exception as exc:
        checks.append(
            _result(
                "data_dir",
                "fail",
                f"Cannot initialize data dir: {exc}",
                "Set DEEPCODE_DATA_DIR to a writable directory.",
            )
        )

    provider = cfg.llm_provider
    if provider not in _IMPLEMENTED_PROVIDERS:
        checks.append(
            _result(
                "llm_provider",
                "fail",
                f"Configured provider '{provider}' is not implemented.",
                "Set DEEPCODE_LLM_PROVIDER to openai, ollama, gemini, github_copilot, or mock.",
            )
        )
    else:
        checks.append(_result("llm_provider", "pass", f"Provider '{provider}' is supported."))

    if provider in {"openai", "gemini", "github_copilot"} and not cfg.llm_api_key:
        missing_key_status: CheckStatus = "warn" if target in ("all", "ui") else "fail"
        checks.append(
            _result(
                "llm_api_key",
                missing_key_status,
                f"DEEPCODE_LLM_API_KEY is empty for provider={provider}.",
                "Set DEEPCODE_LLM_API_KEY in environment/.env, or open the Web UI and configure it in Model Studio.",
            )
        )
    elif provider == "ollama" and not cfg.llm_base_url:
        checks.append(
            _result(
                "ollama_base_url",
                "warn",
                "DEEPCODE_LLM_BASE_URL not set; using default http://localhost:11434/v1.",
                "Set DEEPCODE_LLM_BASE_URL if your Ollama endpoint is different.",
            )
        )
    else:
        checks.append(_result("llm_credentials", "pass", "Provider credential configuration looks valid."))

    if target in ("all", "serve"):
        for pkg in ("fastapi", "uvicorn"):
            checks.append(
                _result(
                    f"dependency_{pkg}",
                    "pass" if find_spec(pkg) else "fail",
                    f"Dependency '{pkg}' {'is installed' if find_spec(pkg) else 'is missing'}.",
                    f"Install with 'pip install {pkg}' or reinstall project dependencies." if not find_spec(pkg) else "",
                )
            )

    if target in ("all", "ui"):
        reflex_available = find_spec("reflex") is not None
        checks.append(
            _result(
                "dependency_reflex",
                "pass" if reflex_available else "fail",
                "Dependency 'reflex' is installed." if reflex_available else "Dependency 'reflex' is missing.",
                "Install with 'pip install reflex' or reinstall project dependencies." if not reflex_available else "",
            )
        )

        node_available = shutil.which("node") is not None
        checks.append(
            _result(
                "dependency_node",
                "pass" if node_available else "warn",
                "Node.js runtime is available." if node_available else "Node.js runtime is not found in PATH.",
                "Install Node.js 18+ for better Reflex dev experience, or run Reflex in environments where Node is available."
                if not node_available
                else "",
            )
        )

    return checks


def has_failures(checks: list[CheckResult]) -> bool:
    """Return True when any check failed."""
    return any(c.status == "fail" for c in checks)


def environment_snapshot(settings: Settings | None = None) -> dict[str, Any]:
    """Collect environment information used by doctor reports."""
    cfg = settings or get_settings()
    cwd = Path.cwd()
    return {
        "deepcode_version": __version__,
        "python_version": sys.version.split()[0],
        "python_executable": sys.executable,
        "platform": platform.platform(),
        "cwd": str(cwd),
        "data_dir": str(cfg.data_dir.expanduser()),
        "db_url": cfg.resolved_db_url,
        "llm_provider": cfg.llm_provider,
        "llm_model": cfg.llm_model,
        "deepcode_executable": shutil.which("deepcode") or "",
        "module_entry": "python -m deepcode",
    }
