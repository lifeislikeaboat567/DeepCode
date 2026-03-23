"""State and service layer for the DeepCode Reflex Web UI."""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
import shutil
import traceback
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from time import perf_counter
from typing import Any, TypedDict
from urllib.parse import urlparse
from uuid import uuid4

import reflex as rx

from deepcode import __version__
from deepcode.agents.base import BaseAgent
from deepcode.agents.orchestrator import OrchestratorAgent
from deepcode.api.napcat_inbound_listener import (
    get_napcat_inbound_listener_status,
    start_napcat_inbound_listener,
    stop_napcat_inbound_listener,
)
from deepcode.api.platform_inbound_debug import PlatformInboundDebugStore
from deepcode.chat_runtime import (
    complete_agent_response,
    complete_chat_response,
    normalize_chat_mode,
    stream_chat_response,
    stream_agent_events,
)
from deepcode.config import (
    apply_chat_bridge_runtime_overrides,
    get_settings,
    load_chat_bridge_runtime_overrides,
    save_chat_bridge_runtime_overrides,
)
from deepcode.extensions import (
    HookEvent,
    HookRule,
    HookRuleStore,
    MCPRegistry,
    MCPServerConfig,
    SkillRegistry,
    SkillToggleStore,
    get_clawhub_skill_details,
    install_mcp_from_remote,
    install_skill_archive_bytes,
    install_skill_from_clawhub,
    install_skills_from_remote,
    resolve_clawhub_skill_slug,
    search_clawhub_skills,
)
from deepcode.governance import AuditLogger, PolicyRule, PolicyStore
from deepcode.llm.base import BaseLLMClient, LLMMessage
from deepcode.llm.factory import create_llm_client
from deepcode.memory import TaskMemoryStore
from deepcode.storage import Message, SessionStore, TaskRecord, TaskStore
from deepcode.tools import build_default_tools
from deepcode.web_shared.constants import (
    MCP_SITE_CATALOG,
    MCP_TEMPLATE_CATALOG,
    MODEL_PROVIDER_OPTIONS,
    NAV_ITEMS,
    SKILL_SITE_CATALOG,
)
from deepcode.web_shared.translations import I18N

NAV_ICON_MAP = {
    "space_dashboard": "layout_dashboard",
    "chat": "message_circle",
    "lan": "network",
    "folder_open": "folder_open",
    "inventory_2": "package",
    "extension": "puzzle",
    "tune": "sliders_horizontal",
    "verified_user": "shield_check",
    "info": "info",
}


class ChatSessionItem(TypedDict):
    id: str
    name: str
    messages: str
    updated_at: str
    is_pinned: str


class ChatSessionGroup(TypedDict):
    key: str
    title: str
    count: str
    collapsed: str
    items: list[ChatSessionItem]


class TaskDialogItem(TypedDict):
    id: str
    task: str
    status: str
    artifacts: str
    updated_at: str
    is_pinned: str


class TaskDialogGroup(TypedDict):
    key: str
    title: str
    count: str
    collapsed: str
    items: list[TaskDialogItem]


def _iso(dt: datetime | None) -> str:
    if dt is None:
        return ""
    return dt.astimezone().strftime("%Y-%m-%d %H:%M:%S")


@lru_cache(maxsize=1)
def _session_store() -> SessionStore:
    return SessionStore()


@lru_cache(maxsize=1)
def _task_store() -> TaskStore:
    return TaskStore()


@lru_cache(maxsize=1)
def _task_memory_store() -> TaskMemoryStore:
    return TaskMemoryStore()


@lru_cache(maxsize=1)
def _mcp_registry() -> MCPRegistry:
    return MCPRegistry()


@lru_cache(maxsize=1)
def _skill_toggle_store() -> SkillToggleStore:
    return SkillToggleStore()


@lru_cache(maxsize=1)
def _hook_rule_store() -> HookRuleStore:
    return HookRuleStore()


@lru_cache(maxsize=1)
def _policy_store() -> PolicyStore:
    return PolicyStore()


@lru_cache(maxsize=1)
def _audit_logger() -> AuditLogger:
    return AuditLogger()


@lru_cache(maxsize=1)
def _platform_inbound_debug_store() -> PlatformInboundDebugStore:
    return PlatformInboundDebugStore()


@lru_cache(maxsize=1)
def _chat_agent() -> BaseAgent:
    llm = create_llm_client()
    tools = build_default_tools()
    return BaseAgent(llm=llm, tools=tools)


@lru_cache(maxsize=1)
def _chat_client() -> BaseLLMClient:
    return create_llm_client()


@lru_cache(maxsize=1)
def _orchestrator() -> OrchestratorAgent:
    llm = create_llm_client()
    tools = build_default_tools()
    return OrchestratorAgent(llm=llm, tools=tools)


def _clear_agent_cache() -> None:
    _chat_client.cache_clear()
    _chat_agent.cache_clear()
    _orchestrator.cache_clear()


def _chat_message(
    role: str,
    content: str,
    *,
    status: str = "done",
    trace_reason: str = "",
    trace_function_call: str = "",
    trace_observation: str = "",
    trace_elapsed: str = "",
    trace_collapsed: str = "1",
    trace_context_collapsed: str = "1",
    trace_intent: str = "",
    trace_plan: str = "",
    trace_skills: str = "",
    trace_mcp: str = "",
) -> dict[str, str]:
    return {
        "id": str(uuid4()),
        "role": role,
        "content": content,
        "created_at": _iso(datetime.now()),
        "status": status,
        "trace_reason": trace_reason,
        "trace_function_call": trace_function_call,
        "trace_observation": trace_observation,
        "trace_elapsed": trace_elapsed,
        "trace_collapsed": trace_collapsed,
        "trace_context_collapsed": trace_context_collapsed,
        "trace_intent": trace_intent,
        "trace_plan": trace_plan,
        "trace_skills": trace_skills,
        "trace_mcp": trace_mcp,
    }


def _format_elapsed_label(seconds: float, language: str) -> str:
    elapsed = max(0.0, float(seconds))
    if language == "en":
        return f"Thought for {elapsed:.1f}s"
    return f"已思考（用时{elapsed:.1f}秒）"


def _format_trace_function_call(payload: dict[str, Any]) -> str:
    step = payload.get("step")
    step_prefix = f"Step {step}: " if step else ""
    name = str(payload.get("name", ""))
    arguments = payload.get("arguments")
    if isinstance(arguments, dict) and arguments:
        try:
            args_text = json.dumps(arguments, ensure_ascii=False)
        except (TypeError, ValueError):
            args_text = str(arguments)
        return f"{step_prefix}{name} {args_text}".strip()
    return f"{step_prefix}{name}".strip()


def _format_trace_observation(payload: dict[str, Any]) -> str:
    step = payload.get("step")
    step_prefix = f"Step {step}: " if step else ""
    tool_name = str(payload.get("tool_name", ""))
    success = payload.get("success")
    status_text = "ok" if success is True else "failed" if success is False else ""
    content = str(payload.get("content", "")).strip()
    prefix_parts = [part for part in [step_prefix.strip(), tool_name, status_text] if part]
    prefix = " | ".join(prefix_parts)
    if prefix and content:
        return f"{prefix}: {content}"
    if prefix:
        return prefix
    return content


def _chunk_text(text: str, chunk_size: int = 20) -> list[str]:
    if not text:
        return []
    return [text[index : index + chunk_size] for index in range(0, len(text), chunk_size)]


def _estimate_text_tokens(text: str) -> int:
    raw = str(text or "")
    if not raw.strip():
        return 0
    cjk_count = len(re.findall(r"[\u4e00-\u9fff]", raw))
    latin_words = len(re.findall(r"[A-Za-z0-9_]+", raw))
    symbol_count = len(re.findall(r"[^\sA-Za-z0-9_\u4e00-\u9fff]", raw))
    heuristic = cjk_count + int(latin_words * 1.3) + int(symbol_count * 0.5)
    length_based = max(1, int(len(raw) / 4))
    return max(heuristic, length_based)


def _estimate_session_tokens(messages: list[Message]) -> int:
    total = 0
    for item in messages:
        content = str(getattr(item, "content", "") or "").strip()
        if not content:
            continue
        total += _estimate_text_tokens(content) + 4
    return total + 12 if total > 0 else 0


def _fallback_context_summary(messages: list[Message], language: str) -> str:
    rows: list[str] = []
    for item in messages:
        role = str(getattr(item, "role", "user") or "user").strip().lower()
        content = str(getattr(item, "content", "") or "").strip()
        if not content:
            continue
        preview = content if len(content) <= 180 else (content[:180] + "...")
        rows.append(f"- {role}: {preview}")
    if not rows:
        return "(empty)"
    header = "历史上下文摘要" if language == "zh" else "Context summary"
    return f"{header}:\n" + "\n".join(rows[:16])


async def _summarize_context_block(llm: BaseLLMClient, messages: list[Message], language: str) -> str:
    transcript_rows: list[str] = []
    for item in messages:
        role = str(getattr(item, "role", "user") or "user").strip().lower()
        content = str(getattr(item, "content", "") or "").strip()
        if not content:
            continue
        transcript_rows.append(f"{role}: {content}")

    if not transcript_rows:
        return ""

    transcript = "\n".join(transcript_rows)
    if len(transcript) > 12000:
        transcript = transcript[-12000:]

    if language == "zh":
        instruction = (
            "请将以下对话压缩成一段可继续推理的上下文摘要。"
            "输出要点：目标、已完成动作、关键约束、未解决问题。"
            "保持简洁，不超过220字。"
        )
    else:
        instruction = (
            "Compress the conversation into a short context summary for continued reasoning. "
            "Include goal, completed actions, key constraints, and unresolved items. "
            "Keep it concise under 140 words."
        )

    try:
        response = await llm.complete(
            [
                LLMMessage.system("You summarize long chat context into compact continuation context."),
                LLMMessage.user(f"{instruction}\n\nConversation:\n{transcript}"),
            ]
        )
    except Exception:
        return _fallback_context_summary(messages, language)

    summary = str(getattr(response, "content", "") or "").strip()
    if summary:
        return summary
    return _fallback_context_summary(messages, language)


async def _compress_session_context_if_needed(
    session: Any,
    llm: BaseLLMClient,
    *,
    token_threshold: int,
    keep_recent_messages: int,
    language: str,
) -> bool:
    messages = list(getattr(session, "messages", []) or [])
    keep_recent = max(int(keep_recent_messages), 3)
    if len(messages) <= keep_recent + 2:
        return False

    before_tokens = _estimate_session_tokens(messages)
    if before_tokens <= max(int(token_threshold), 200):
        return False

    head_messages = messages[:-keep_recent]
    tail_messages = messages[-keep_recent:]
    if not head_messages or not tail_messages:
        return False

    summary_text = await _summarize_context_block(llm, head_messages, language)
    if not summary_text:
        return False

    prefix = "[历史摘要] " if language == "zh" else "[Context Summary] "
    summary_message = Message(role="system", content=f"{prefix}{summary_text}")
    session.messages = [summary_message, *tail_messages]

    metadata = dict(getattr(session, "metadata", {}) or {})
    summaries = metadata.get("context_summaries")
    if not isinstance(summaries, list):
        summaries = []
    after_tokens = _estimate_session_tokens(session.messages)
    summaries.append(
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "compressed_message_count": len(head_messages),
            "before_tokens": before_tokens,
            "after_tokens": after_tokens,
            "summary": summary_text,
        }
    )
    metadata["context_summaries"] = summaries[-30:]
    session.metadata = metadata
    return True


def _build_memory_process_summary(
    *,
    plan: list[str],
    execution_results: list[dict[str, Any]] | None = None,
) -> str:
    plan_rows = [str(item).strip() for item in plan if str(item).strip()]
    exec_rows = execution_results if isinstance(execution_results, list) else []

    fragments: list[str] = []
    if plan_rows:
        fragments.append("Plan: " + " | ".join(plan_rows[:6]))

    if exec_rows:
        success_count = sum(1 for row in exec_rows if isinstance(row, dict) and bool(row.get("success", False)))
        fragments.append(f"Execution steps: {success_count}/{len(exec_rows)} successful")

        tool_fragments: list[str] = []
        for row in exec_rows[:4]:
            if not isinstance(row, dict):
                continue
            tool_events = row.get("tool_events", [])
            if not isinstance(tool_events, list):
                continue
            for event in tool_events[:2]:
                if not isinstance(event, dict):
                    continue
                action = str(event.get("action", "")).strip()
                if not action:
                    continue
                status = "ok" if bool(event.get("tool_success", False)) else "failed"
                tool_fragments.append(f"{action}:{status}")
        if tool_fragments:
            fragments.append("Tools: " + ", ".join(tool_fragments[:8]))

    return "\n".join(fragments).strip()


def _format_exception_detail(stage: str, exc: Exception, *, include_traceback: bool = False) -> str:
    title = f"[{stage}] {exc.__class__.__name__}"
    message = str(exc or "").strip() or "(empty message)"
    lines = [title, f"Message: {message}"]
    if include_traceback:
        lines.append("Traceback:")
        lines.append(traceback.format_exc().strip())
    return "\n".join(line for line in lines if line.strip())


def _parse_local_chat_command(prompt: str) -> tuple[str, str] | None:
    raw = str(prompt or "").strip()
    if not raw.startswith("/"):
        return None

    parts = raw.split()
    if not parts:
        return None

    command = parts[0].lower()
    if command != "/skills":
        return None

    if len(parts) == 1:
        return ("skills_list", "")

    subcommand = parts[1].lower()
    if subcommand in {"show", "详情"}:
        return ("skills_show", " ".join(parts[2:]).strip())

    return ("skills_help", " ".join(parts[1:]).strip())


def _session_message_id(message: Message) -> str:
    return f"{message.role}-{message.created_at.isoformat()}"


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "enabled", "on"}:
            return True
        if normalized in {"0", "false", "no", "disabled", "off"}:
            return False
        return default
    if value is None:
        return default
    return bool(value)


def _extract_latest_user_message(messages: list[Message]) -> str:
    for item in reversed(messages):
        if str(item.role).strip().lower() == "user":
            text = str(item.content or "").strip()
            if text:
                return text
    return ""


def _agent_context_trace_fields(agent_context: dict[str, Any]) -> tuple[str, str, str, str]:
    payload = agent_context if isinstance(agent_context, dict) else {}
    intent_route = payload.get("intent_route") if isinstance(payload.get("intent_route"), dict) else {}
    decomposed = payload.get("decomposed_task") if isinstance(payload.get("decomposed_task"), dict) else {}
    relevant_skills = payload.get("relevant_skills") if isinstance(payload.get("relevant_skills"), list) else []
    relevant_mcp = payload.get("relevant_mcp_servers") if isinstance(payload.get("relevant_mcp_servers"), list) else []

    intent_name = str(intent_route.get("intent", "")).strip()
    rationale = str(intent_route.get("rationale", "")).strip()
    preferred_tools = intent_route.get("preferred_tools") if isinstance(intent_route.get("preferred_tools"), list) else []
    preferred_text = ", ".join(str(item).strip() for item in preferred_tools if str(item).strip())
    intent_lines: list[str] = []
    if intent_name:
        intent_lines.append(f"Intent: {intent_name}")
    if rationale:
        intent_lines.append(f"Rationale: {rationale}")
    if preferred_text:
        intent_lines.append(f"Preferred tools: {preferred_text}")

    goal = str(decomposed.get("goal", "")).strip()
    subtasks = decomposed.get("subtasks") if isinstance(decomposed.get("subtasks"), list) else []
    if not subtasks:
        subtasks = intent_route.get("subtasks") if isinstance(intent_route.get("subtasks"), list) else []
    plan_lines: list[str] = []
    if goal:
        plan_lines.append(f"Goal: {goal}")
    plan_lines.extend(
        f"{index}. {str(item).strip()}"
        for index, item in enumerate(subtasks, 1)
        if str(item).strip()
    )

    skill_lines = [
        (
            f"- {str(item.get('name', '')).strip()}: {str(item.get('description', '')).strip()}"
            if str(item.get("description", "")).strip()
            else f"- {str(item.get('name', '')).strip()}"
        )
        for item in relevant_skills
        if isinstance(item, dict) and str(item.get("name", "")).strip()
    ]

    mcp_lines: list[str] = []
    for item in relevant_mcp:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        if not name:
            continue
        transport = str(item.get("transport", "")).strip()
        description = str(item.get("description", "")).strip()
        line = f"- {name}"
        if transport:
            line += f" ({transport})"
        if description:
            line += f": {description}"
        mcp_lines.append(line)

    return (
        "\n".join(intent_lines).strip(),
        "\n".join(plan_lines).strip(),
        "\n".join(skill_lines).strip(),
        "\n".join(mcp_lines).strip(),
    )


def _append_chat_agent_run_metadata(
    session: Any,
    *,
    assistant_message_id: str,
    user_message: str,
    assistant_message: str,
    plan_only: bool,
    agent_context: dict[str, Any],
    trace_reason: str,
    trace_function_call: str,
    trace_observation: str,
    trace_elapsed: str,
    trace_collapsed: str,
    trace_context_collapsed: str,
    trace_intent: str,
    trace_plan: str,
    trace_skills: str,
    trace_mcp: str,
) -> None:
    metadata = dict(getattr(session, "metadata", {}) or {})
    runs = metadata.get("agent_runs")
    if not isinstance(runs, list):
        runs = []
    runs.append(
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "assistant_message_id": assistant_message_id,
            "user_message": user_message,
            "assistant_message": assistant_message,
            "plan_only": bool(plan_only),
            "agent_context": agent_context,
            "trace_reason": trace_reason,
            "trace_function_call": trace_function_call,
            "trace_observation": trace_observation,
            "trace_elapsed": trace_elapsed,
            "trace_collapsed": trace_collapsed,
            "trace_context_collapsed": trace_context_collapsed,
            "trace_intent": trace_intent,
            "trace_plan": trace_plan,
            "trace_skills": trace_skills,
            "trace_mcp": trace_mcp,
        }
    )
    metadata["agent_runs"] = runs[-50:]
    session.metadata = metadata


async def _persist_chat_agent_task_snapshot(
    *,
    session_id: str,
    user_message: str,
    assistant_message: str,
    plan_only: bool,
    agent_context: dict[str, Any],
) -> None:
    decomposed = agent_context.get("decomposed_task") if isinstance(agent_context, dict) else {}
    intent = agent_context.get("intent_route") if isinstance(agent_context, dict) else {}

    goal = str((decomposed or {}).get("goal") or user_message).strip() or user_message
    subtasks = (decomposed or {}).get("subtasks")
    if not isinstance(subtasks, list):
        subtasks = []
    plan = [str(item).strip() for item in subtasks if str(item).strip()]

    task = await _task_store().create(
        task=goal,
        session_id=session_id,
        metadata={
            "origin": "chat_agent",
            "plan_only": bool(plan_only),
            "intent": str((intent or {}).get("intent", "")),
        },
    )
    await _task_store().set_status(
        task.id,
        "completed",
        plan=plan,
        summary=assistant_message,
        task_state={"chat_agent_context": agent_context},
        observations=[
            {
                "source": "chat_agent",
                "summary": f"Chat agent {'planned' if plan_only else 'executed'} request",
                "raw_ref": user_message[:1000],
            }
        ],
    )

    intent_name = str((intent or {}).get("intent", "")).strip()
    tags = [intent_name] if intent_name else []
    process_summary = _build_memory_process_summary(plan=plan)
    with contextlib.suppress(Exception):
        _task_memory_store().record(
            session_id=session_id,
            task_id=task.id,
            source="chat_agent",
            status="planned" if bool(plan_only) else "completed",
            user_request=user_message,
            outcome_summary=assistant_message,
            process_summary=process_summary,
            tags=tags,
            metadata={
                "plan_only": bool(plan_only),
                "intent": intent_name,
            },
        )


def _model_config_path() -> Path:
    settings = get_settings()
    settings.ensure_data_dir()
    return settings.data_dir / "ui_model_config.json"


def _load_model_config() -> dict[str, Any] | None:
    path = _model_config_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def _build_default_model_profile(settings: Any, profile_id: str, name: str) -> dict[str, Any]:
    return {
        "id": profile_id,
        "name": name,
        "llm_provider": settings.llm_provider,
        "llm_model": settings.llm_model,
        "llm_base_url": settings.llm_base_url,
        "llm_temperature": float(settings.llm_temperature),
        "llm_max_tokens": int(settings.llm_max_tokens),
        "llm_enable_thinking": bool(settings.llm_enable_thinking),
        "persist_api_key": False,
        "llm_api_key": "",
    }


def _normalize_model_profile(raw: dict[str, Any], fallback: dict[str, Any], index: int) -> dict[str, Any]:
    profile_id = str(raw.get("id", "")).strip() or f"profile-{index + 1}"
    profile_name = str(raw.get("name", "")).strip() or f"配置 {index + 1}"

    provider = str(raw.get("llm_provider", fallback["llm_provider"]))
    provider = provider.strip().lower()
    if provider not in MODEL_PROVIDER_OPTIONS:
        provider = fallback["llm_provider"]

    try:
        temperature = float(raw.get("llm_temperature", fallback["llm_temperature"]))
    except (TypeError, ValueError):
        temperature = float(fallback["llm_temperature"])

    try:
        max_tokens = int(raw.get("llm_max_tokens", fallback["llm_max_tokens"]))
    except (TypeError, ValueError):
        max_tokens = int(fallback["llm_max_tokens"])

    persist_key = _coerce_bool(raw.get("persist_api_key", False))
    enable_thinking = _coerce_bool(
        raw.get("llm_enable_thinking", fallback.get("llm_enable_thinking", False)),
        default=bool(fallback.get("llm_enable_thinking", False)),
    )
    api_key = str(raw.get("llm_api_key", "")).strip() if persist_key else ""

    return {
        "id": profile_id,
        "name": profile_name,
        "llm_provider": provider,
        "llm_model": str(raw.get("llm_model", fallback["llm_model"])).strip() or fallback["llm_model"],
        "llm_base_url": str(raw.get("llm_base_url", fallback["llm_base_url"])).strip(),
        "llm_temperature": max(0.0, min(2.0, temperature)),
        "llm_max_tokens": max(128, max_tokens),
        "llm_enable_thinking": enable_thinking,
        "persist_api_key": persist_key,
        "llm_api_key": api_key,
    }


def _model_profiles_from_payload(payload: dict[str, Any] | None) -> tuple[list[dict[str, Any]], str]:
    settings = get_settings()
    fallback = _build_default_model_profile(settings, profile_id="profile-default", name="默认配置")

    if not payload:
        return [fallback], fallback["id"]

    rows = payload.get("profiles")
    if isinstance(rows, list) and rows:
        profiles = [
            _normalize_model_profile(item, fallback=fallback, index=index)
            for index, item in enumerate(rows)
            if isinstance(item, dict)
        ]
        if not profiles:
            profiles = [fallback]
        active_profile_id = str(payload.get("active_profile_id", "")).strip()
        ids = {str(profile["id"]) for profile in profiles}
        if active_profile_id not in ids:
            active_profile_id = str(profiles[0]["id"])
        return profiles, active_profile_id

    # Backward compatibility: legacy single-profile payload.
    if any(key in payload for key in ("llm_provider", "llm_model", "llm_base_url", "llm_api_key")):
        legacy_profile = _normalize_model_profile(
            {
                "id": "profile-legacy",
                "name": "默认配置",
                "llm_provider": payload.get("llm_provider", fallback["llm_provider"]),
                "llm_model": payload.get("llm_model", fallback["llm_model"]),
                "llm_base_url": payload.get("llm_base_url", fallback["llm_base_url"]),
                "llm_temperature": payload.get("llm_temperature", fallback["llm_temperature"]),
                "llm_max_tokens": payload.get("llm_max_tokens", fallback["llm_max_tokens"]),
                "llm_enable_thinking": payload.get("llm_enable_thinking", fallback["llm_enable_thinking"]),
                "persist_api_key": payload.get("persist_api_key", False),
                "llm_api_key": payload.get("llm_api_key", ""),
            },
            fallback=fallback,
            index=0,
        )
        return [legacy_profile], legacy_profile["id"]

    return [fallback], fallback["id"]


def _save_model_profiles(profiles: list[dict[str, Any]], active_profile_id: str) -> None:
    path = _model_config_path()
    normalized_rows: list[dict[str, Any]] = []
    for profile in profiles:
        persist_raw = profile.get("persist_api_key", False)
        persist = str(persist_raw).strip().lower() in {"true", "1", "yes", "enabled"}
        normalized_rows.append(
            {
                "id": str(profile.get("id", "")).strip(),
                "name": str(profile.get("name", "")).strip(),
                "llm_provider": str(profile.get("llm_provider", "openai")).strip().lower(),
                "llm_model": str(profile.get("llm_model", "gpt-4o-mini")).strip(),
                "llm_base_url": str(profile.get("llm_base_url", "")).strip(),
                "llm_temperature": float(profile.get("llm_temperature", 0.0)),
                "llm_max_tokens": int(profile.get("llm_max_tokens", 4096)),
                "llm_enable_thinking": _coerce_bool(profile.get("llm_enable_thinking", False)),
                "persist_api_key": persist,
                "llm_api_key": str(profile.get("llm_api_key", "")).strip() if persist else "",
            }
        )

    payload = {
        "active_profile_id": active_profile_id,
        "profiles": normalized_rows,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _save_model_config(config: dict[str, Any]) -> None:
    # Compatibility entrypoint for legacy callers.
    settings = get_settings()
    fallback = _build_default_model_profile(settings, profile_id="profile-default", name="默认配置")
    profile = _normalize_model_profile({"id": "profile-default", "name": "默认配置", **config}, fallback, 0)
    _save_model_profiles([profile], active_profile_id=str(profile["id"]))


def _delete_model_config() -> None:
    path = _model_config_path()
    if path.exists():
        path.unlink()


def _apply_runtime_model_config(config: dict[str, Any]) -> None:
    settings = get_settings()
    provider = str(config.get("llm_provider", settings.llm_provider)).strip().lower()
    if provider in MODEL_PROVIDER_OPTIONS:
        settings.llm_provider = provider  # type: ignore[assignment]

    settings.llm_model = str(config.get("llm_model", settings.llm_model)).strip() or settings.llm_model
    settings.llm_base_url = str(config.get("llm_base_url", settings.llm_base_url)).strip()

    try:
        settings.llm_temperature = float(config.get("llm_temperature", settings.llm_temperature))
    except (TypeError, ValueError):
        pass

    try:
        settings.llm_max_tokens = int(config.get("llm_max_tokens", settings.llm_max_tokens))
    except (TypeError, ValueError):
        pass

    settings.llm_enable_thinking = _coerce_bool(
        config.get("llm_enable_thinking", settings.llm_enable_thinking),
        default=settings.llm_enable_thinking,
    )

    settings.llm_api_key = str(config.get("llm_api_key", settings.llm_api_key)).strip()


def _apply_saved_model_overrides() -> None:
    payload = _load_model_config()
    profiles, active_profile_id = _model_profiles_from_payload(payload)
    target = next((profile for profile in profiles if profile["id"] == active_profile_id), profiles[0])
    _apply_runtime_model_config(target)


def _ui_runtime_flags_path() -> Path:
    settings = get_settings()
    settings.ensure_data_dir()
    return settings.data_dir / "ui_runtime_flags.json"


def _load_ui_runtime_flags() -> dict[str, Any]:
    path = _ui_runtime_flags_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def _save_ui_runtime_flags(flags: dict[str, Any]) -> None:
    path = _ui_runtime_flags_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(flags, indent=2), encoding="utf-8")


def _extension_sources_path() -> Path:
    settings = get_settings()
    settings.ensure_data_dir()
    return settings.data_dir / "extension_sources.json"


def _load_extension_sources() -> list[dict[str, Any]]:
    path = _extension_sources_path()
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    rows = payload.get("sources", []) if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def _save_extension_sources(rows: list[dict[str, Any]]) -> None:
    path = _extension_sources_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"sources": rows}, indent=2), encoding="utf-8")


def _upsert_extension_source(source: dict[str, Any]) -> None:
    rows = _load_extension_sources()
    for idx, item in enumerate(rows):
        if item.get("url") == source.get("url"):
            rows[idx] = source
            _save_extension_sources(rows)
            return
    rows.append(source)
    _save_extension_sources(rows)


def _remove_extension_source(url: str) -> bool:
    rows = _load_extension_sources()
    remaining = [item for item in rows if item.get("url") != url]
    if len(remaining) == len(rows):
        return False
    _save_extension_sources(remaining)
    return True


def _task_to_row(task: Any) -> dict[str, Any]:
    return {
        "id": str(task.id),
        "task": str(task.task),
        "status": str(task.status),
        "artifacts": str(len(task.code_artifacts)),
        "updated_at": str(_iso(task.updated_at)),
    }


def _task_draft_title(ui_language: str) -> str:
    return "新任务" if ui_language == "zh" else "New Task"


def _task_metadata_bool(metadata: dict[str, Any], key: str) -> bool:
    raw_value = metadata.get(key, False)
    if isinstance(raw_value, str):
        return raw_value.strip().lower() in {"1", "true", "yes", "enabled"}
    return bool(raw_value)


def _is_task_draft(record: TaskRecord) -> bool:
    title = record.task.strip()
    metadata = record.metadata or {}
    return _task_metadata_bool(metadata, "draft") or not title or title in {"新任务", "New Task"}


def _build_task_followup_input(record: TaskRecord, prompt: str, ui_language: str) -> str:
    plan_text = "\n".join(f"- {step}" for step in record.plan) if record.plan else "暂无"
    artifact_names = ", ".join(
        str(item.get("filename", "")) for item in record.code_artifacts if str(item.get("filename", "")).strip()
    ) or "暂无"
    summary_text = record.summary.strip() or "暂无"

    if ui_language == "zh":
        return (
            "你正在继续迭代一个已有任务。请结合当前任务上下文处理本次追加指令。\n\n"
            f"原始任务：\n{record.task}\n\n"
            f"追加指令：\n{prompt}\n\n"
            f"当前摘要：\n{summary_text}\n\n"
            f"已有计划：\n{plan_text}\n\n"
            f"已有工件：\n{artifact_names}"
        )

    return (
        "You are continuing an existing task. Use the current task context to process the new follow-up instruction.\n\n"
        f"Original task:\n{record.task}\n\n"
        f"Follow-up instruction:\n{prompt}\n\n"
        f"Current summary:\n{summary_text}\n\n"
        f"Current plan:\n{plan_text}\n\n"
        f"Existing artifacts:\n{artifact_names}"
    )


async def _create_draft_task(store: TaskStore, ui_language: str) -> TaskRecord:
    return await store.create(
        task=_task_draft_title(ui_language),
        metadata={"origin": "web_reflex", "draft": True, "task_history": []},
    )


async def _prepare_task_record_for_run(
    store: TaskStore,
    selected_task_id: str,
    task_text: str,
    ui_language: str,
) -> tuple[TaskRecord, str]:
    record: TaskRecord | None = None
    if selected_task_id:
        try:
            record = await store.get(selected_task_id)
        except Exception:
            record = None

    if record is None:
        record = await _create_draft_task(store, ui_language)

    metadata = dict(record.metadata or {})
    history = metadata.get("task_history", [])
    if not isinstance(history, list):
        history = []
    history.append(task_text)

    metadata["task_history"] = history[-20:]
    metadata["last_prompt"] = task_text
    metadata["origin"] = metadata.get("origin") or "web_reflex"

    if _is_task_draft(record):
        record.task = task_text
        execution_input = task_text
    else:
        execution_input = _build_task_followup_input(record, task_text, ui_language)

    metadata["draft"] = False
    record.metadata = metadata
    await store.update(record)
    return record, execution_input


def _session_to_row(session: Any) -> dict[str, Any]:
    return {
        "id": str(session.id),
        "name": str(session.name),
        "messages": str(len(session.messages)),
        "updated_at": str(_iso(session.updated_at)),
    }


def _looks_like_http_url(value: str) -> bool:
    parsed = urlparse(value.strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _resolve_clawhub_direct_query(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""

    if text.lower().startswith("slug:"):
        return resolve_clawhub_skill_slug(text.split(":", 1)[1].strip())

    if _looks_like_http_url(text):
        return resolve_clawhub_skill_slug(text)

    if any(char.isspace() for char in text):
        return ""

    if any(marker in text for marker in ("-", "_", "/", ".")):
        return resolve_clawhub_skill_slug(text)

    return ""


def _guess_language(filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    mapping = {
        ".py": "python",
        ".js": "javascript",
        ".ts": "typescript",
        ".tsx": "tsx",
        ".jsx": "jsx",
        ".json": "json",
        ".md": "markdown",
        ".yml": "yaml",
        ".yaml": "yaml",
        ".toml": "toml",
        ".html": "html",
        ".css": "css",
        ".sh": "bash",
        ".ps1": "powershell",
        ".sql": "sql",
    }
    return mapping.get(suffix, "text")


def _resolve_nav_icon(icon: str) -> str:
    if icon.startswith(":material/") and icon.endswith(":"):
        icon_name = icon[len(":material/") : -1]
        return NAV_ICON_MAP.get(icon_name, "circle")
    return icon


def _parse_task_updated_at(value: str) -> datetime | None:
    stamp = value.strip()
    if not stamp:
        return None

    for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(stamp, pattern)
        except ValueError:
            continue
    return None


def _time_bucket_key_title(updated_at: datetime | None, language: str, now: datetime | None = None) -> tuple[str, str]:
    current = now or datetime.now()
    use_zh = language == "zh"
    if updated_at is None:
        return ("earlier", "更早" if use_zh else "Earlier")

    day_gap = (current.date() - updated_at.date()).days
    if day_gap == 0:
        return ("today", "今天" if use_zh else "Today")
    if day_gap == 1:
        return ("yesterday", "昨天" if use_zh else "Yesterday")
    if day_gap <= 7:
        return ("last7", "最近7天" if use_zh else "Last 7 Days")
    if day_gap <= 30:
        return ("last30", "30天内" if use_zh else "Last 30 Days")
    month = updated_at.strftime("%Y-%m")
    return (f"month:{month}", month)


def _shift_month(base: datetime, offset: int) -> datetime:
    month_index = (base.year * 12) + (base.month - 1) + offset
    year = month_index // 12
    month = (month_index % 12) + 1
    return datetime(year, month, 1)


def _coerce_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _select_best_clawhub_candidate(candidates: list[dict[str, object]], query: str) -> dict[str, object]:
    query_text = query.strip().lower()
    query_slug = resolve_clawhub_skill_slug(query)

    def _rank(item: dict[str, object]) -> tuple[int, int, int, int, float]:
        slug = resolve_clawhub_skill_slug(str(item.get("slug", "")))
        name = str(item.get("name", "")).strip().lower()
        exact_slug = 1 if query_slug and slug == query_slug else 0
        exact_name = 1 if query_text and name == query_text else 0
        starts_with = 1 if query_text and (slug.startswith(query_text) or name.startswith(query_text)) else 0
        contains = 1 if query_text and (query_text in slug or query_text in name) else 0
        score = _coerce_float(item.get("score"), 0.0)
        return (exact_slug, exact_name, starts_with, contains, score)

    return max(candidates, key=_rank)


class UIState(rx.State):
    """Global state for the Reflex Web UI."""

    selected_page: str = "dashboard"
    sidebar_collapsed: bool = False
    ui_language: str = "zh"
    busy: bool = False
    notice: str = ""
    error_message: str = ""

    sessions: list[dict[str, str]] = []
    tasks: list[dict[str, str]] = []
    task_page: int = 1
    task_page_size: str = "8"
    dashboard_trend_range: str = "week"
    chat_messages: list[dict[str, str]] = []
    chat_agent_traces: dict[str, dict[str, str]] = {}

    selected_session_id: str = ""
    selected_session_name: str = ""
    selected_task_id: str = ""
    session_action_open_id: str = ""
    session_rename_id: str = ""
    session_rename_value: str = ""
    session_delete_confirm_id: str = ""
    session_delete_confirm_group_key: str = ""
    session_delete_confirm_group_title: str = ""
    session_delete_confirm_group_count: int = 0
    pinned_session_ids: list[str] = []
    collapsed_session_group_keys: list[str] = []
    pinned_task_ids: list[str] = []
    collapsed_task_group_keys: list[str] = []
    task_action_open_id: str = ""
    task_rename_id: str = ""
    task_rename_value: str = ""
    task_delete_confirm_id: str = ""
    task_delete_confirm_group_key: str = ""
    task_delete_confirm_group_title: str = ""
    task_delete_confirm_group_count: int = 0

    task_plan: list[str] = []
    task_artifacts: list[dict[str, str]] = []
    task_summary: str = ""
    task_error: str = ""
    task_review_score: str = ""
    task_review_issues: list[str] = []
    task_execution_timeline: list[dict[str, str]] = []
    task_agent_intent: str = ""
    task_agent_plan: str = ""
    task_agent_skills: str = ""
    task_agent_mcp: str = ""
    task_runtime_steps: list[dict[str, str]] = []
    task_runtime_cursor: str = "..."
    task_runtime_phase: str = ""
    task_busy: bool = False
    task_stop_requested: bool = False

    chat_prompt: str = ""
    chat_mode: str = "ask"
    chat_plan_only: str = "disabled"
    heartbeat_enabled: str = "enabled"
    chat_stop_requested: bool = False
    chat_edit_message_id: str = ""
    chat_edit_prompt: str = ""
    task_prompt: str = ""

    mcp_servers: list[dict[str, str]] = []
    hook_rules: list[dict[str, str]] = []
    policy_rules: list[dict[str, str]] = []
    audit_events: list[dict[str, str]] = []
    skills: list[dict[str, str]] = []
    extension_sources: list[dict[str, str]] = []

    extension_tab: str = "skills"
    skill_search_query: str = ""
    skill_page: int = 1
    skill_page_size: str = "8"
    skill_sort_by: str = "installed_at"
    clawhub_query: str = ""
    clawhub_source_url: str = "https://clawhub.ai"
    clawhub_selected_slug: str = ""
    clawhub_preview_query: str = ""
    clawhub_preview_candidate_count: str = ""
    clawhub_preview_name: str = ""
    clawhub_preview_version: str = ""
    clawhub_preview_score: str = ""
    clawhub_preview_summary: str = ""
    clawhub_preview_package_name: str = ""
    clawhub_preview_install_dir: str = ""
    clawhub_preview_text: str = ""
    clawhub_panel_hint: str = ""
    mcp_search_query: str = ""
    selected_skill_path: str = ""
    skill_delete_confirm_path: str = ""
    skill_delete_confirm_name: str = ""
    selected_mcp_name: str = ""
    extension_detail_kind: str = ""

    market_query: str = ""
    market_filter: str = "all"

    mcp_name: str = ""
    mcp_transport: str = "stdio"
    mcp_command: str = ""
    mcp_args: str = ""
    mcp_description: str = ""
    mcp_enabled: str = "enabled"

    hook_name: str = ""
    hook_event: str = HookEvent.BEFORE_LLM.value
    hook_handler_type: str = "command"
    hook_handler_value: str = ""
    hook_description: str = ""
    hook_enabled: str = "enabled"

    source_name: str = ""
    source_kind: str = "skill_site"
    source_url: str = ""
    source_description: str = ""
    source_tags: str = ""

    policy_name: str = ""
    policy_scope: str = "global"
    policy_target: str = "*"
    policy_decision: str = "ask"
    policy_description: str = ""
    policy_enabled: str = "enabled"
    audit_limit: str = "60"
    audit_query: str = ""
    audit_status_filter: str = "all"

    llm_provider: str = "openai"
    llm_model: str = "gpt-4o-mini"
    llm_base_url: str = ""
    llm_temperature: str = "0.0"
    llm_max_tokens: str = "4096"
    llm_enable_thinking: str = "disabled"
    llm_api_key: str = ""
    persist_api_key: str = "disabled"
    model_profile_name: str = "默认配置"
    model_profiles: list[dict[str, str]] = []
    active_model_profile_id: str = ""
    model_defaults: dict[str, Any] = {}

    platform_bridge_enabled: str = "enabled"
    platform_bridge_verify_token: str = ""
    platform_bridge_default_mode: str = "ask"
    platform_bridge_allowed_platforms: str = "generic,qq,wechat,feishu"
    platform_bridge_signature_ttl_seconds: str = "300"
    platform_bridge_event_id_ttl_seconds: str = "86400"
    platform_bridge_feishu_encrypt_key: str = ""
    platform_bridge_wechat_token: str = ""
    platform_bridge_qq_signing_secret: str = ""
    platform_bridge_callback_delivery_enabled: str = "disabled"
    platform_bridge_callback_timeout_seconds: str = "12"
    platform_bridge_feishu_api_base_url: str = "https://open.feishu.cn"
    platform_bridge_feishu_app_id: str = ""
    platform_bridge_feishu_app_secret: str = ""
    platform_bridge_wechat_delivery_mode: str = "auto"
    platform_bridge_wechat_work_api_base_url: str = "https://qyapi.weixin.qq.com"
    platform_bridge_wechat_work_corp_id: str = ""
    platform_bridge_wechat_work_corp_secret: str = ""
    platform_bridge_wechat_work_agent_id: str = ""
    platform_bridge_wechat_official_api_base_url: str = "https://api.weixin.qq.com"
    platform_bridge_wechat_official_app_id: str = ""
    platform_bridge_wechat_official_app_secret: str = ""
    platform_bridge_qq_api_base_url: str = "https://api.sgroup.qq.com"
    platform_bridge_qq_delivery_mode: str = "auto"
    platform_bridge_qq_bot_app_id: str = ""
    platform_bridge_qq_bot_token: str = ""
    platform_bridge_qq_napcat_api_base_url: str = "http://127.0.0.1:3000"
    platform_bridge_qq_napcat_access_token: str = ""
    platform_bridge_qq_napcat_webhook_token: str = ""
    platform_bridge_inbound_enabled: str = "enabled"
    platform_bridge_inbound_port: str = "8000"
    platform_bridge_inbound_debug: str = "disabled"
    platform_bridge_inbound_logs: list[dict[str, str]] = []
    platform_bridge_inbound_listener_status: str = "unknown"
    platform_bridge_inbound_listener_pid: str = ""
    platform_bridge_inbound_listener_message: str = ""
    platform_bridge_inbound_listener_updated_at: str = ""

    @rx.var
    def i18n(self) -> dict[str, str]:
        return I18N.get(self.ui_language, I18N["zh"])

    @rx.var
    def nav_rows(self) -> list[dict[str, str]]:
        rows: list[dict[str, str]] = []
        catalog = I18N.get(self.ui_language, I18N["zh"])
        for item in NAV_ITEMS:
            rows.append(
                {
                    "id": item["id"],
                    "label": catalog.get(item["label_key"], item["id"]),
                    "icon": _resolve_nav_icon(str(item["icon"])),
                }
            )
        return rows

    @rx.var
    def platform_bridge_inbound_callback_url(self) -> str:
        port = str(self.platform_bridge_inbound_port or "8000").strip() or "8000"
        return f"http://127.0.0.1:{port}/api/v1/platforms/qq/events"

    @rx.var
    def total_sessions(self) -> int:
        return len(self.sessions)

    @rx.var
    def chat_send_disabled(self) -> bool:
        return self.busy or not bool(self.chat_prompt.strip())

    @rx.var
    def chat_context_token_estimate(self) -> int:
        total = 0
        for row in self.chat_messages:
            content = str(row.get("content", "")).strip()
            if not content:
                continue
            total += _estimate_text_tokens(content) + 4
        prompt = self.chat_prompt.strip()
        if prompt:
            total += _estimate_text_tokens(prompt) + 4
        return total + 12 if total > 0 else 0

    @rx.var
    def chat_context_token_label(self) -> str:
        estimate = self.chat_context_token_estimate
        if self.ui_language == "en":
            return f"Context ~{estimate} tokens"
        return f"上下文 ~{estimate} tokens"

    @rx.var
    def task_send_disabled(self) -> bool:
        return self.task_busy or not bool(self.task_prompt.strip())

    @rx.var
    def task_rows(self) -> list[dict[str, str]]:
        pinned_ids = set(self.pinned_task_ids)

        def _sort_stamp(row: dict[str, str]) -> int:
            dt = _parse_task_updated_at(str(row.get("updated_at", "")))
            if dt is None:
                dt = datetime(1970, 1, 1)
            return (dt.toordinal() * 86400) + (dt.hour * 3600) + (dt.minute * 60) + dt.second

        ordered = sorted(
            self.tasks,
            key=lambda row: (
                0 if str(row.get("id", "")) in pinned_ids else 1,
                -_sort_stamp(row),
            ),
        )

        rows: list[dict[str, str]] = []
        for row in ordered:
            row_id = str(row.get("id", ""))
            rows.append(
                {
                    "id": row_id,
                    "task": str(row.get("task", "")),
                    "status": str(row.get("status", "pending")),
                    "artifacts": str(row.get("artifacts", "0")),
                    "updated_at": str(row.get("updated_at", "")),
                    "is_pinned": "1" if row_id in pinned_ids else "0",
                }
            )
        return rows

    @rx.var
    def chat_session_groups(self) -> list[ChatSessionGroup]:
        now = datetime.now()
        pinned_ids = set(self.pinned_session_ids)
        collapsed_keys = set(self.collapsed_session_group_keys)

        def _sort_stamp(row: dict[str, str]) -> int:
            dt = _parse_task_updated_at(str(row.get("updated_at", "")))
            if dt is None:
                dt = datetime(1970, 1, 1)
            return (dt.toordinal() * 86400) + (dt.hour * 3600) + (dt.minute * 60) + dt.second

        rows = sorted(
            self.sessions,
            key=lambda row: (
                0 if str(row.get("id", "")) in pinned_ids else 1,
                -_sort_stamp(row),
            ),
        )

        groups: list[ChatSessionGroup] = []
        indexes: dict[str, int] = {}

        for row in rows:
            updated_at = _parse_task_updated_at(str(row.get("updated_at", "")))
            key, title = _time_bucket_key_title(updated_at, self.ui_language, now)

            if key not in indexes:
                indexes[key] = len(groups)
                groups.append(
                    {
                        "key": key,
                        "title": title,
                        "count": "0",
                        "collapsed": "1" if key in collapsed_keys else "0",
                        "items": [],
                    }
                )

            item: ChatSessionItem = {
                "id": str(row.get("id", "")),
                "name": str(row.get("name", "")),
                "messages": str(row.get("messages", "0")),
                "updated_at": str(row.get("updated_at", "")),
                "is_pinned": "1" if str(row.get("id", "")) in pinned_ids else "0",
            }
            groups[indexes[key]]["items"].append(item)

        for group in groups:
            group["count"] = str(len(group["items"]))

        return groups

    @rx.var
    def task_groups(self) -> list[TaskDialogGroup]:
        now = datetime.now()
        grouped: list[TaskDialogGroup] = []
        indexes: dict[str, int] = {}
        collapsed_keys = set(self.collapsed_task_group_keys)

        for row in self.task_rows:
            updated_at = _parse_task_updated_at(str(row.get("updated_at", "")))
            key, title = _time_bucket_key_title(updated_at, self.ui_language, now)
            if key not in indexes:
                indexes[key] = len(grouped)
                grouped.append(
                    {
                        "key": key,
                        "title": title,
                        "count": "0",
                        "collapsed": "1" if key in collapsed_keys else "0",
                        "items": [],
                    }
                )
            grouped[indexes[key]]["items"].append(row)

        for group in grouped:
            group["count"] = str(len(group["items"]))
        return grouped

    @rx.var
    def total_tasks(self) -> int:
        return len(self.tasks)

    @rx.var
    def failed_tasks(self) -> int:
        return sum(1 for row in self.tasks if row.get("status") == "failed")

    @rx.var
    def pending_tasks(self) -> int:
        return sum(1 for row in self.tasks if row.get("status") == "pending")

    @rx.var
    def task_page_size_value(self) -> int:
        try:
            return max(int(self.task_page_size or "8"), 1)
        except ValueError:
            return 8

    @rx.var
    def task_total_pages(self) -> int:
        if not self.tasks:
            return 1
        page_size = self.task_page_size_value
        return max((len(self.tasks) + page_size - 1) // page_size, 1)

    @rx.var
    def task_page_label(self) -> str:
        catalog = I18N.get(self.ui_language, I18N["zh"])
        if not self.tasks:
            return "Page 0 / 1" if self.ui_language == "en" else "第 0 / 1 页"
        return (
            f"Page {self.task_page} / {self.task_total_pages}"
            if self.ui_language == "en"
            else f"第 {self.task_page} / {self.task_total_pages} 页"
        )

    @rx.var
    def paginated_tasks(self) -> list[dict[str, str]]:
        if not self.tasks:
            return []
        page_size = self.task_page_size_value
        current_page = min(max(self.task_page, 1), self.task_total_pages)
        start = (current_page - 1) * page_size
        end = start + page_size
        return self.tasks[start:end]

    @rx.var
    def completed_tasks(self) -> int:
        return sum(1 for row in self.tasks if row.get("status") == "completed")

    @rx.var
    def running_tasks(self) -> int:
        return sum(1 for row in self.tasks if row.get("status") == "running")

    @rx.var
    def total_artifacts(self) -> int:
        return sum(int(row.get("artifacts", 0)) for row in self.tasks)

    @rx.var
    def success_rate(self) -> str:
        if not self.tasks:
            return "0.0%"
        ratio = self.completed_tasks / max(len(self.tasks), 1)
        return f"{ratio * 100:.1f}%"

    @rx.var
    def current_page_title(self) -> str:
        catalog = I18N.get(self.ui_language, I18N["zh"])
        mapping = {
            "dashboard": "dashboard.title",
            "chat": "chat.title",
            "task_center": "task.title",
            "session_center": "session.title",
            "artifact_center": "artifact.title",
            "platform_bridge": "platform_bridge.title",
            "extensions": "extensions.title",
            "model_studio": "model.title",
            "governance": "governance.title",
            "about": "about.title",
        }
        key = mapping.get(self.selected_page)
        if key is None:
            return "DeepCode Reflex"
        return str(catalog.get(key, "DeepCode Reflex"))

    @rx.var
    def current_page_subtitle(self) -> str:
        catalog = I18N.get(self.ui_language, I18N["zh"])
        mapping = {
            "dashboard": "dashboard.caption",
            "chat": "chat.caption",
            "task_center": "task.caption",
            "session_center": "session.caption",
            "artifact_center": "artifact.caption",
            "platform_bridge": "platform_bridge.caption",
            "extensions": "extensions.caption",
            "model_studio": "model.caption",
            "governance": "governance.caption",
        }
        key = mapping.get(self.selected_page)
        if key is None:
            if self.selected_page == "about":
                return "Project overview and version details." if self.ui_language == "en" else "项目说明与版本信息。"
            return ""
        return str(catalog.get(key, ""))

    @rx.var
    def runtime_stamp(self) -> str:
        return datetime.now().strftime("在线 · %Y-%m-%d %H:%M")

    @rx.var
    def dashboard_metric_cards(self) -> list[dict[str, str]]:
        catalog = I18N.get(self.ui_language, I18N["zh"])
        total = max(len(self.tasks), 1)
        success = int(round((self.completed_tasks / total) * 100)) if self.tasks else 0
        return [
            {
                "title": str(catalog.get("dashboard.kpi.success_rate", "Task Success Rate")),
                "value": f"{success}%",
                "delta": (
                    f"Completed {self.completed_tasks}/{len(self.tasks)}"
                    if self.ui_language == "en"
                    else f"完成 {self.completed_tasks}/{len(self.tasks)}"
                ),
                "pill_class": "dc-kpi-pill dc-kpi-positive",
            },
            {
                "title": str(catalog.get("status.running", "Running")),
                "value": str(self.running_tasks),
                "delta": "Live" if self.ui_language == "en" else "实时执行",
                "pill_class": "dc-kpi-pill dc-kpi-neutral",
            },
            {
                "title": str(catalog.get("status.failed", "Failed")),
                "value": str(self.failed_tasks),
                "delta": "Needs attention" if self.ui_language == "en" else "需关注",
                "pill_class": "dc-kpi-pill dc-kpi-negative",
            },
            {
                "title": str(catalog.get("dashboard.metric.artifacts", "Artifacts")),
                "value": str(self.total_artifacts),
                "delta": "Generated code" if self.ui_language == "en" else "代码产出",
                "pill_class": "dc-kpi-pill dc-kpi-neutral",
            },
        ]

    @rx.var
    def dashboard_trend_range_label(self) -> str:
        catalog = I18N.get(self.ui_language, I18N["zh"])
        mapping = {
            "week": "Last 7 days" if self.ui_language == "en" else "最近一周",
            "month": "Last 30 days" if self.ui_language == "en" else "最近一月",
            "year": "Last 12 months" if self.ui_language == "en" else "最近一年",
        }
        return mapping.get(self.dashboard_trend_range, str(catalog.get("common.select", "Select")))

    @rx.var
    def dashboard_trend_range_options(self) -> list[str]:
        return [
            "Last 7 days" if self.ui_language == "en" else "最近一周",
            "Last 30 days" if self.ui_language == "en" else "最近一月",
            "Last 12 months" if self.ui_language == "en" else "最近一年",
        ]

    @rx.var
    def dashboard_filtered_tasks(self) -> list[dict[str, str]]:
        now = datetime.now()
        if self.dashboard_trend_range == "year":
            threshold = _shift_month(datetime(now.year, now.month, 1), -11)
        elif self.dashboard_trend_range == "month":
            threshold = datetime(now.year, now.month, now.day) - timedelta(days=29)
        else:
            threshold = datetime(now.year, now.month, now.day) - timedelta(days=6)

        rows: list[dict[str, str]] = []
        for row in self.tasks:
            dt = _parse_task_updated_at(str(row.get("updated_at", "")))
            if dt is None:
                continue
            if dt >= threshold:
                rows.append(row)
        return rows

    @rx.var
    def dashboard_filtered_total(self) -> int:
        return len(self.dashboard_filtered_tasks)

    @rx.var
    def dashboard_filtered_completed(self) -> int:
        return sum(1 for row in self.dashboard_filtered_tasks if row.get("status") == "completed")

    @rx.var
    def dashboard_filtered_running(self) -> int:
        return sum(1 for row in self.dashboard_filtered_tasks if row.get("status") == "running")

    @rx.var
    def dashboard_filtered_failed(self) -> int:
        return sum(1 for row in self.dashboard_filtered_tasks if row.get("status") == "failed")

    @rx.var
    def dashboard_filtered_pending(self) -> int:
        return sum(1 for row in self.dashboard_filtered_tasks if row.get("status") == "pending")

    @rx.var
    def dashboard_trend_points(self) -> list[dict[str, Any]]:
        now = datetime.now()

        if self.dashboard_trend_range == "year":
            current_month = datetime(now.year, now.month, 1)
            month_starts = [_shift_month(current_month, offset) for offset in range(-11, 1)]
            buckets = {(item.year, item.month): 0 for item in month_starts}

            for row in self.dashboard_filtered_tasks:
                dt = _parse_task_updated_at(str(row.get("updated_at", "")))
                if dt is None:
                    continue
                key = (dt.year, dt.month)
                if key in buckets:
                    buckets[key] += 1

            return [
                {
                    "label": item.strftime("%y-%m"),
                    "count": buckets[(item.year, item.month)],
                }
                for item in month_starts
            ]

        lookback_days = 7 if self.dashboard_trend_range == "week" else 30
        start_day = (now - timedelta(days=lookback_days - 1)).date()
        day_points = [start_day + timedelta(days=offset) for offset in range(lookback_days)]
        buckets = {item: 0 for item in day_points}

        for row in self.dashboard_filtered_tasks:
            dt = _parse_task_updated_at(str(row.get("updated_at", "")))
            if dt is None:
                continue
            day = dt.date()
            if day in buckets:
                buckets[day] += 1

        return [
            {
                "label": item.strftime("%m-%d"),
                "count": buckets[item],
            }
            for item in day_points
        ]

    @rx.var
    def dashboard_trend_rows(self) -> list[dict[str, str]]:
        max_count = max((int(item["count"]) for item in self.dashboard_trend_points), default=0)
        denominator = max(max_count, 1)
        rows: list[dict[str, str]] = []
        for item in self.dashboard_trend_points:
            count = int(item["count"])
            width = "0%"
            if count > 0:
                width = f"{max(int(round((count / denominator) * 100)), 8)}%"
            rows.append({"label": str(item["label"]), "count": str(count), "width": width})
        return rows

    @rx.var
    def dashboard_status_rows(self) -> list[dict[str, str]]:
        catalog = I18N.get(self.ui_language, I18N["zh"])
        total = max(self.dashboard_filtered_total, 1)
        specs = [
            (str(catalog.get("status.completed", "Completed")), self.dashboard_filtered_completed, "#22C55E"),
            (str(catalog.get("status.running", "Running")), self.dashboard_filtered_running, "#3D5AFE"),
            (str(catalog.get("status.failed", "Failed")), self.dashboard_filtered_failed, "#FF4757"),
            (str(catalog.get("status.pending", "Pending")), self.dashboard_filtered_pending, "#D1D5DB"),
        ]

        rows: list[dict[str, str]] = []
        for label, count, color in specs:
            pct = int(round((count / total) * 100)) if self.dashboard_filtered_tasks else 0
            width = "0%"
            if count > 0:
                width = f"{max(pct, 7)}%"
            rows.append(
                {
                    "label": label,
                    "count": str(count),
                    "pct": f"{pct}%",
                    "width": width,
                    "color": color,
                }
            )
        return rows

    @rx.var
    def dashboard_donut_background(self) -> str:
        total = max(self.dashboard_filtered_total, 1)
        completed = int(round((self.dashboard_filtered_completed / total) * 100)) if self.dashboard_filtered_tasks else 0
        running = int(round((self.dashboard_filtered_running / total) * 100)) if self.dashboard_filtered_tasks else 0
        failed = int(round((self.dashboard_filtered_failed / total) * 100)) if self.dashboard_filtered_tasks else 0
        used = completed + running + failed
        pending = max(100 - used, 0)

        c2 = completed
        c3 = c2 + running
        c4 = c3 + failed
        c5 = c4 + pending

        return (
            "conic-gradient("
            f"#22C55E 0% {c2}%, "
            f"#3D5AFE {c2}% {c3}%, "
            f"#FF4757 {c3}% {c4}%, "
            f"#E2E8F0 {c4}% {c5}%"
            ")"
        )

    @rx.var
    def session_select_options(self) -> list[dict[str, str]]:
        return [
            {
                "id": row["id"],
                "label": f"{row['name']} ({row['id'][:8]})",
            }
            for row in self.sessions
        ]

    @rx.var
    def task_select_options(self) -> list[dict[str, str]]:
        return [
            {
                "id": row["id"],
                "label": f"{row['id'][:8]} · {row['task'][:48]}",
            }
            for row in self.tasks
        ]

    @rx.var
    def skill_install_path(self) -> str:
        settings = get_settings()
        settings.ensure_data_dir()
        return str(settings.data_dir / "skills")

    @rx.var
    def filtered_skill_rows(self) -> list[dict[str, str]]:
        query = self.skill_search_query.strip().lower()
        filtered: list[dict[str, str]] = []
        for row in self.skills:
            text = " ".join(
                [
                    str(row.get("name", "")),
                    str(row.get("description", "")),
                    str(row.get("tags", "")),
                    str(row.get("path", "")),
                    str(row.get("installed_at", "")),
                ]
            ).lower()
            if not query or query in text:
                filtered.append(row)

        def _installed_stamp(row: dict[str, str]) -> int:
            stamp = _parse_task_updated_at(str(row.get("installed_at", "")))
            if stamp is None:
                return 0
            return int(stamp.timestamp())

        sort_by = str(self.skill_sort_by or "installed_at").strip().lower()
        if sort_by == "name":
            return sorted(
                filtered,
                key=lambda row: (
                    str(row.get("name", "")).lower(),
                    -_installed_stamp(row),
                ),
            )
        return sorted(
            filtered,
            key=lambda row: (
                -_installed_stamp(row),
                str(row.get("name", "")).lower(),
            ),
        )

    @rx.var
    def skill_page_size_value(self) -> int:
        try:
            return max(int(self.skill_page_size or "8"), 1)
        except ValueError:
            return 8

    @rx.var
    def skill_total_pages(self) -> int:
        rows = self.filtered_skill_rows
        if not rows:
            return 1
        page_size = self.skill_page_size_value
        return max((len(rows) + page_size - 1) // page_size, 1)

    @rx.var
    def skill_page_label(self) -> str:
        current_page = min(max(self.skill_page, 1), self.skill_total_pages)
        if not self.filtered_skill_rows:
            return "Page 0 / 1" if self.ui_language == "en" else "第 0 / 1 页"
        return f"Page {current_page} / {self.skill_total_pages}" if self.ui_language == "en" else f"第 {current_page} / {self.skill_total_pages} 页"

    @rx.var
    def skill_sort_label(self) -> str:
        return "按名称" if self.skill_sort_by == "name" else "按安装时间"

    @rx.var
    def paginated_skill_rows(self) -> list[dict[str, str]]:
        rows = self.filtered_skill_rows
        if not rows:
            return []
        page_size = self.skill_page_size_value
        current_page = min(max(self.skill_page, 1), self.skill_total_pages)
        start = (current_page - 1) * page_size
        end = start + page_size
        return rows[start:end]

    @rx.var
    def filtered_mcp_rows(self) -> list[dict[str, str]]:
        query = self.mcp_search_query.strip().lower()
        rows = sorted(
            self.mcp_servers,
            key=lambda row: (
                0 if str(row.get("enabled", "disabled")) == "enabled" else 1,
                str(row.get("name", "")).lower(),
            ),
        )
        if not query:
            return rows

        filtered: list[dict[str, str]] = []
        for row in rows:
            text = " ".join(
                [
                    str(row.get("name", "")),
                    str(row.get("description", "")),
                    str(row.get("transport", "")),
                    str(row.get("command", "")),
                    str(row.get("args", "")),
                ]
            ).lower()
            if query in text:
                filtered.append(row)
        return filtered

    @rx.var
    def selected_skill_detail(self) -> dict[str, str]:
        fallback = {
            "name": "",
            "path": "",
            "description": "",
            "tags": "",
            "installed_at": "",
            "enabled": "disabled",
        }
        rows = self.filtered_skill_rows if self.filtered_skill_rows else self.skills
        if not rows:
            return fallback

        if self.selected_skill_path:
            for row in rows:
                if str(row.get("path", "")) == self.selected_skill_path:
                    return row

        return rows[0]

    @rx.var
    def selected_skill_markdown(self) -> str:
        raw_path = str(self.selected_skill_detail.get("path", "")).strip()
        if not raw_path:
            return ""
        path = Path(raw_path)
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return "未找到 Skill 说明文件。" if self.ui_language == "zh" else "Skill markdown file could not be loaded."

    @rx.var
    def selected_mcp_detail(self) -> dict[str, str]:
        fallback = {
            "name": "",
            "transport": "",
            "command": "",
            "args": "",
            "description": "",
            "enabled": "disabled",
            "env": "",
        }
        rows = self.filtered_mcp_rows if self.filtered_mcp_rows else self.mcp_servers
        if not rows:
            return fallback

        if self.selected_mcp_name:
            for row in rows:
                if str(row.get("name", "")) == self.selected_mcp_name:
                    return row

        return rows[0]

    @rx.var
    def skill_resource_sites(self) -> list[dict[str, str]]:
        use_zh = self.ui_language == "zh"
        return [
            {
                "id": str(item.get("id", "")),
                "name": str(item.get("name", "")),
                "url": str(item.get("url", "")),
                "description": str(item.get("description_zh", item.get("description", ""))) if use_zh else str(item.get("description", "")),
            }
            for item in SKILL_SITE_CATALOG
        ]

    @rx.var
    def mcp_resource_sites(self) -> list[dict[str, str]]:
        use_zh = self.ui_language == "zh"
        return [
            {
                "id": str(item.get("id", "")),
                "name": str(item.get("name", "")),
                "url": str(item.get("url", "")),
                "description": str(item.get("description_zh", item.get("description", ""))) if use_zh else str(item.get("description", "")),
            }
            for item in MCP_SITE_CATALOG
        ]

    @rx.var
    def filtered_market_items(self) -> list[dict[str, str]]:
        rows: list[dict[str, str]] = []
        use_zh = self.ui_language == "zh"

        for item in SKILL_SITE_CATALOG:
            rows.append(
                {
                    "id": item["id"],
                    "kind": "skill_site",
                    "name": item.get("name_zh", item["name"]) if use_zh else item["name"],
                    "description": item.get("description_zh", item["description"])
                    if use_zh
                    else item["description"],
                    "url": item["url"],
                    "tags": ", ".join(item.get("tags", [])),
                }
            )

        for item in MCP_SITE_CATALOG:
            rows.append(
                {
                    "id": item["id"],
                    "kind": "mcp_site",
                    "name": item.get("name_zh", item["name"]) if use_zh else item["name"],
                    "description": item.get("description_zh", item["description"])
                    if use_zh
                    else item["description"],
                    "url": item["url"],
                    "tags": ", ".join(item.get("tags", [])),
                }
            )

        query = self.market_query.strip().lower()
        selected_kind = self.market_filter.strip().lower()
        filtered: list[dict[str, str]] = []
        for row in rows:
            if selected_kind in {"skill_site", "mcp_site"} and row["kind"] != selected_kind:
                continue
            text = " ".join(
                [
                    str(row.get("name", "")),
                    str(row.get("description", "")),
                    str(row.get("url", "")),
                    str(row.get("tags", "")),
                ]
            ).lower()
            if query and query not in text:
                continue
            filtered.append(row)

        return filtered

    @rx.var
    def mcp_templates(self) -> list[dict[str, str]]:
        use_zh = self.ui_language == "zh"
        rows: list[dict[str, str]] = []
        for item in MCP_TEMPLATE_CATALOG:
            name = item.get("name_zh", item["name"]) if use_zh else item["name"]
            description = item.get("description_zh", item["description"]) if use_zh else item["description"]
            command = str(item.get("command", "")).strip()
            args = [str(arg).strip() for arg in item.get("args", []) if str(arg).strip()]
            rows.append(
                {
                    "id": str(item.get("id", "")),
                    "name": str(name),
                    "description": str(description),
                    "command_line": " ".join([command] + args).strip(),
                }
            )
        return rows

    @rx.var
    def filtered_audit_events(self) -> list[dict[str, str]]:
        query = self.audit_query.strip().lower()
        status = self.audit_status_filter.strip().lower()
        rows: list[dict[str, str]] = []

        for row in self.audit_events:
            row_status = str(row.get("status", "")).lower()
            if status != "all" and row_status != status:
                continue

            if query:
                text = " ".join(
                    [
                        str(row.get("event", "")),
                        str(row.get("actor", "")),
                        str(row.get("resource", "")),
                        str(row.get("metadata", "")),
                    ]
                ).lower()
                if query not in text:
                    continue

            rows.append(row)

        return rows

    @rx.var
    def mcp_form_valid(self) -> bool:
        name = self.mcp_name.strip()
        command = self.mcp_command.strip()
        if not name or not command:
            return False
        if self.mcp_transport in {"http", "sse"}:
            return _looks_like_http_url(command)
        return True

    @rx.var
    def mcp_form_hint(self) -> str:
        if not self.mcp_name.strip():
            return "请填写 MCP 名称"
        if not self.mcp_command.strip():
            return "请填写 MCP 命令或 Endpoint"
        if self.mcp_transport in {"http", "sse"} and not _looks_like_http_url(self.mcp_command):
            return "HTTP/SSE 模式下，命令字段应为 http(s) URL"
        return "MCP 表单校验通过"

    @rx.var
    def hook_form_valid(self) -> bool:
        name = self.hook_name.strip()
        value = self.hook_handler_value.strip()
        if not name or not value:
            return False
        if self.hook_handler_type == "http":
            return _looks_like_http_url(value)
        return True

    @rx.var
    def hook_form_hint(self) -> str:
        if not self.hook_name.strip():
            return "请填写 Hook 规则名称"
        if not self.hook_handler_value.strip():
            return "请填写处理器值"
        if self.hook_handler_type == "http" and not _looks_like_http_url(self.hook_handler_value):
            return "HTTP 处理器必须是 http(s) URL"
        return "Hook 表单校验通过"

    @rx.var
    def source_form_valid(self) -> bool:
        name = self.source_name.strip()
        url = self.source_url.strip()
        if not name or not url:
            return False
        return _looks_like_http_url(url)

    @rx.var
    def source_form_hint(self) -> str:
        if not self.source_name.strip():
            return "请填写来源名称"
        if not self.source_url.strip():
            return "请填写来源 URL"
        if not _looks_like_http_url(self.source_url):
            return "来源 URL 必须是 http(s) 地址"
        return "来源表单校验通过"

    @rx.var
    def model_provider_options(self) -> list[str]:
        return MODEL_PROVIDER_OPTIONS

    @rx.var
    def active_model_profile_name(self) -> str:
        for profile in self.model_profiles:
            if profile.get("id") == self.active_model_profile_id:
                return str(profile.get("name", ""))
        if self.model_profiles:
            return str(self.model_profiles[0].get("name", ""))
        return ""

    @rx.var
    def model_profile_names(self) -> list[str]:
        return [str(profile.get("name", "")) for profile in self.model_profiles if str(profile.get("name", "")).strip()]

    @rx.var
    def model_base_url_hint(self) -> str:
        hints = {
            "openai": "OpenAI 默认地址可留空。",
            "ollama": "留空将使用 http://localhost:11434/v1",
            "gemini": "留空将使用 Gemini OpenAI 兼容地址。",
            "github_copilot": "留空将使用 GitHub Models 兼容地址。",
            "mock": "Mock 模式无需 Base URL。",
        }
        return str(hints.get(self.llm_provider, "可选字段，留空使用默认值。"))

    @rx.var
    def model_env_preview(self) -> str:
        return (
            f"DEEPCODE_LLM_PROVIDER={self.llm_provider}\n"
            f"DEEPCODE_LLM_MODEL={self.llm_model}\n"
            f"DEEPCODE_LLM_BASE_URL={self.llm_base_url}\n"
            f"DEEPCODE_LLM_TEMPERATURE={self.llm_temperature}\n"
            f"DEEPCODE_LLM_MAX_TOKENS={self.llm_max_tokens}\n"
            f"DEEPCODE_LLM_ENABLE_THINKING={'true' if self.llm_enable_thinking == 'enabled' else 'false'}"
        )

    def _toast_success(self, message: str) -> rx.event.EventSpec:
        return rx.toast(message, level="success", position="bottom-right", duration=3200)

    def _toast_error(self, message: str) -> rx.event.EventSpec:
        return rx.toast(message, level="error", position="bottom-right", duration=4200)

    def set_selected_page(self, page: str) -> None:
        if page == "session_center":
            page = "chat"
        self.selected_page = page

    def toggle_sidebar(self) -> None:
        self.sidebar_collapsed = not self.sidebar_collapsed

    def set_language(self, language: str) -> None:
        if language in I18N:
            self.ui_language = language

    def set_chat_prompt(self, value: str) -> None:
        self.chat_prompt = value

    def _resolve_local_chat_command(self, prompt: str) -> dict[str, str] | None:
        parsed = _parse_local_chat_command(prompt)
        if parsed is None:
            return None

        command, argument = parsed
        use_zh = self.ui_language == "zh"
        discovered = SkillRegistry().discover()
        toggle_map = _skill_toggle_store().load()

        skill_rows: list[dict[str, str]] = []
        for item in discovered:
            skill_rows.append(
                {
                    "name": str(item.name),
                    "path": str(item.path),
                    "description": str(item.description or "").strip(),
                    "tags": ", ".join(item.tags) if isinstance(item.tags, list) else "",
                    "enabled": "1" if bool(toggle_map.get(str(item.path), True)) else "0",
                }
            )

        skill_rows = sorted(skill_rows, key=lambda row: row["name"].lower())

        if command == "skills_help":
            content = (
                "本地指令帮助\n\n"
                "- `/skills`：列出所有技能与加载状态\n"
                "- `/skills show 技能名`：查看技能详情\n"
            )
            return {
                "content": content,
                "trace_intent": "本地指令 /skills",
                "trace_plan": f"Command: {prompt.strip()}",
            }

        if command == "skills_list":
            if not skill_rows:
                content = "当前没有可用技能。" if use_zh else "No skills are currently available."
            else:
                header = (
                    "| 技能名称 | 加载状态 | 描述 |\n| --- | --- | --- |"
                    if use_zh
                    else "| Skill | Loaded | Description |\n| --- | --- | --- |"
                )
                lines = [header]
                for row in skill_rows:
                    status = "已加载" if row["enabled"] == "1" else "已禁用"
                    if not use_zh:
                        status = "enabled" if row["enabled"] == "1" else "disabled"
                    description = row["description"] or "-"
                    lines.append(f"| {row['name']} | {status} | {description} |")

                hint = (
                    "\n\n使用 `/skills show 技能名` 查看技能详情。"
                    if use_zh
                    else "\n\nUse `/skills show <name>` to view skill details."
                )
                content = "\n".join(lines) + hint

            return {
                "content": content,
                "trace_intent": "本地指令 /skills",
                "trace_plan": f"Command: {prompt.strip()}",
            }

        target_name = argument.strip()
        if not target_name:
            content = (
                "请提供技能名，例如：`/skills show browser`"
                if use_zh
                else "Please provide a skill name, e.g. `/skills show browser`."
            )
            return {
                "content": content,
                "trace_intent": "本地指令 /skills show",
                "trace_plan": f"Command: {prompt.strip()}",
            }

        lowered = target_name.lower()
        selected: dict[str, str] | None = None
        for row in skill_rows:
            if row["name"].lower() == lowered:
                selected = row
                break
        if selected is None:
            for row in skill_rows:
                if row["name"].lower().startswith(lowered):
                    selected = row
                    break

        if selected is None:
            names = ", ".join(row["name"] for row in skill_rows[:12])
            content = (
                f"未找到技能：{target_name}\n\n可用技能：{names or '（无）'}"
                if use_zh
                else f"Skill not found: {target_name}\n\nAvailable skills: {names or '(none)'}"
            )
            return {
                "content": content,
                "trace_intent": "本地指令 /skills show",
                "trace_plan": f"Command: {prompt.strip()}",
            }

        status_text = "已加载" if selected["enabled"] == "1" else "已禁用"
        if not use_zh:
            status_text = "enabled" if selected["enabled"] == "1" else "disabled"

        markdown_text = ""
        try:
            markdown_text = Path(selected["path"]).read_text(encoding="utf-8").strip()
        except OSError as exc:
            markdown_text = f"读取技能文件失败: {exc}" if use_zh else f"Failed to read skill file: {exc}"

        if use_zh:
            content = (
                f"### 技能详情：{selected['name']}\n"
                f"- 状态：{status_text}\n"
                f"- 路径：{selected['path']}\n"
                f"- 标签：{selected['tags'] or '-'}\n"
                f"- 描述：{selected['description'] or '-'}\n\n"
                f"---\n\n{markdown_text}"
            )
        else:
            content = (
                f"### Skill Details: {selected['name']}\n"
                f"- Status: {status_text}\n"
                f"- Path: {selected['path']}\n"
                f"- Tags: {selected['tags'] or '-'}\n"
                f"- Description: {selected['description'] or '-'}\n\n"
                f"---\n\n{markdown_text}"
            )

        return {
            "content": content,
            "trace_intent": "本地指令 /skills show",
            "trace_plan": f"Command: {prompt.strip()}",
        }

    def set_chat_mode(self, value: str) -> None:
        self.chat_mode = normalize_chat_mode(value)

    @rx.var
    def chat_plan_only_label(self) -> str:
        return "仅输出计划" if self.chat_plan_only == "enabled" else "完整回复"

    def set_chat_plan_only(self, value: str) -> None:
        raw = str(value).strip().lower()
        self.chat_plan_only = "enabled" if raw in {"enabled", "true", "1", "仅输出计划"} else "disabled"

    def set_skill_page_size(self, size: str) -> None:
        self.skill_page_size = size if str(size).strip() in {"8", "12", "20"} else "8"
        self.skill_page = 1

    def set_skill_sort_by(self, value: str) -> None:
        raw = str(value).strip()
        mapping = {
            "name": "name",
            "installed_at": "installed_at",
            "按名称": "name",
            "按安装时间": "installed_at",
        }
        self.skill_sort_by = mapping.get(raw, "installed_at")
        self.skill_page = 1

    def set_chat_edit_prompt(self, value: str) -> None:
        self.chat_edit_prompt = value

    def stop_task_generation(self) -> rx.event.EventSpec | None:
        if not self.task_busy:
            return None
        self.task_stop_requested = True
        return None

    def toggle_task_step_fold(self, step_id: str) -> None:
        def _toggle(rows: list[dict[str, str]]) -> list[dict[str, str]]:
            next_rows: list[dict[str, str]] = []
            for step in rows:
                row = dict(step)
                if row.get("id") == step_id:
                    row["collapsed"] = "0" if row.get("collapsed") == "1" else "1"
                next_rows.append(row)
            return next_rows

        self.task_runtime_steps = _toggle(self.task_runtime_steps)
        self.task_execution_timeline = _toggle(self.task_execution_timeline)

    def notify_copy_success(self) -> rx.event.EventSpec:
        message = "复制成功" if self.ui_language == "zh" else "Copied"
        return rx.toast(message, level="success", position="bottom-right", duration=1800)

    def stop_chat_generation(self) -> rx.event.EventSpec | None:
        if not self.busy:
            return None
        self.chat_stop_requested = True
        return None

    def cancel_edit_message(self) -> rx.event.EventSpec | None:
        self.chat_edit_message_id = ""
        self.chat_edit_prompt = ""
        return None

    async def start_edit_user_message(self, message_id: str) -> rx.event.EventSpec | None:
        if self.busy or not self.selected_session_id:
            return None

        session = await _session_store().get(self.selected_session_id)
        target_index = next(
            (
                index
                for index, message in enumerate(session.messages)
                if message.role == "user" and _session_message_id(message) == message_id
            ),
            None,
        )
        if target_index is None:
            return None

        self.chat_edit_message_id = message_id
        self.chat_edit_prompt = str(session.messages[target_index].content)
        return None

    def toggle_session_actions(self, session_id: str) -> None:
        self.session_action_open_id = "" if self.session_action_open_id == session_id else session_id
        self.session_delete_confirm_id = ""
        self.session_rename_id = ""

    def start_rename_session(self, session_id: str, current_name: str) -> None:
        self.session_rename_id = session_id
        self.session_rename_value = current_name
        self.session_action_open_id = ""
        self.session_delete_confirm_id = ""

    def set_session_rename_value(self, value: str) -> None:
        self.session_rename_value = value

    def cancel_session_rename(self) -> None:
        self.session_rename_id = ""
        self.session_rename_value = ""

    def request_delete_session(self, session_id: str) -> None:
        self.session_delete_confirm_id = session_id
        self.session_delete_confirm_group_key = ""
        self.session_delete_confirm_group_title = ""
        self.session_delete_confirm_group_count = 0
        self.session_action_open_id = ""
        self.session_rename_id = ""
        self.session_rename_value = ""

    def toggle_session_group_fold(self, group_key: str) -> None:
        if group_key in self.collapsed_session_group_keys:
            self.collapsed_session_group_keys = [key for key in self.collapsed_session_group_keys if key != group_key]
        else:
            self.collapsed_session_group_keys = [*self.collapsed_session_group_keys, group_key]

    def request_delete_session_group(self, group_key: str) -> None:
        for group in self.chat_session_groups:
            if str(group.get("key", "")) != group_key:
                continue
            self.session_delete_confirm_group_key = group_key
            self.session_delete_confirm_group_title = str(group.get("title", ""))
            try:
                self.session_delete_confirm_group_count = int(str(group.get("count", "0")))
            except ValueError:
                self.session_delete_confirm_group_count = len(group.get("items", []))
            break
        else:
            self.session_delete_confirm_group_key = ""
            self.session_delete_confirm_group_title = ""
            self.session_delete_confirm_group_count = 0
        self.session_delete_confirm_id = ""
        self.session_action_open_id = ""
        self.session_rename_id = ""
        self.session_rename_value = ""

    def cancel_delete_session(self) -> None:
        self.session_delete_confirm_id = ""
        self.session_delete_confirm_group_key = ""
        self.session_delete_confirm_group_title = ""
        self.session_delete_confirm_group_count = 0

    async def delete_requested_session(self) -> rx.event.EventSpec | None:
        if self.session_delete_confirm_group_key:
            return await self.delete_session_group_by_key(self.session_delete_confirm_group_key)
        return await self.delete_session_by_id(self.session_delete_confirm_id)

    def toggle_pin_session(self, session_id: str) -> None:
        if session_id in self.pinned_session_ids:
            self.pinned_session_ids = [item for item in self.pinned_session_ids if item != session_id]
        else:
            self.pinned_session_ids = [session_id, *self.pinned_session_ids]
        self.session_action_open_id = ""

    def toggle_task_actions(self, task_id: str) -> None:
        self.task_action_open_id = "" if self.task_action_open_id == task_id else task_id
        self.task_delete_confirm_id = ""
        self.task_delete_confirm_group_key = ""
        self.task_delete_confirm_group_title = ""
        self.task_delete_confirm_group_count = 0
        self.task_rename_id = ""

    def toggle_task_group_fold(self, group_key: str) -> None:
        if group_key in self.collapsed_task_group_keys:
            self.collapsed_task_group_keys = [key for key in self.collapsed_task_group_keys if key != group_key]
        else:
            self.collapsed_task_group_keys = [*self.collapsed_task_group_keys, group_key]

    def start_rename_task(self, task_id: str, current_name: str) -> None:
        self.task_rename_id = task_id
        self.task_rename_value = current_name
        self.task_action_open_id = ""
        self.task_delete_confirm_id = ""

    def set_task_rename_value(self, value: str) -> None:
        self.task_rename_value = value

    def cancel_task_rename(self) -> None:
        self.task_rename_id = ""
        self.task_rename_value = ""

    def request_delete_task(self, task_id: str) -> None:
        self.task_delete_confirm_id = task_id
        self.task_delete_confirm_group_key = ""
        self.task_delete_confirm_group_title = ""
        self.task_delete_confirm_group_count = 0
        self.task_action_open_id = ""
        self.task_rename_id = ""
        self.task_rename_value = ""

    def request_delete_task_group(self, group_key: str) -> None:
        for group in self.task_groups:
            if str(group.get("key", "")) != group_key:
                continue
            self.task_delete_confirm_group_key = group_key
            self.task_delete_confirm_group_title = str(group.get("title", ""))
            try:
                self.task_delete_confirm_group_count = int(str(group.get("count", "0")))
            except ValueError:
                self.task_delete_confirm_group_count = len(group.get("items", []))
            break
        else:
            self.task_delete_confirm_group_key = ""
            self.task_delete_confirm_group_title = ""
            self.task_delete_confirm_group_count = 0
        self.task_delete_confirm_id = ""
        self.task_action_open_id = ""
        self.task_rename_id = ""
        self.task_rename_value = ""

    def cancel_delete_task(self) -> None:
        self.task_delete_confirm_id = ""
        self.task_delete_confirm_group_key = ""
        self.task_delete_confirm_group_title = ""
        self.task_delete_confirm_group_count = 0

    async def delete_requested_task(self) -> rx.event.EventSpec | None:
        if self.task_delete_confirm_group_key:
            return await self.delete_task_group_by_key(self.task_delete_confirm_group_key)
        return await self.delete_task_by_id(self.task_delete_confirm_id)

    def toggle_pin_task(self, task_id: str) -> None:
        if task_id in self.pinned_task_ids:
            self.pinned_task_ids = [item for item in self.pinned_task_ids if item != task_id]
        else:
            self.pinned_task_ids = [task_id, *self.pinned_task_ids]
        self.task_action_open_id = ""

    def set_task_prompt(self, value: str) -> None:
        self.task_prompt = value

    async def create_empty_task(self) -> rx.event.EventSpec | None:
        try:
            created = await _create_draft_task(_task_store(), self.ui_language)
            self.selected_task_id = created.id
            self.task_prompt = ""
            self.task_runtime_steps = []
            self.task_runtime_phase = ""
            self.task_runtime_cursor = ""
            await self._refresh_tasks_only()
            return self._toast_success("已新建空任务" if self.ui_language == "zh" else "Empty task created")
        except Exception as exc:
            self.error_message = str(exc)
            return self._toast_error(f"新建任务失败: {exc}" if self.ui_language == "zh" else f"Failed to create task: {exc}")

    def set_task_page_size(self, size: str) -> None:
        self.task_page_size = size
        self.task_page = 1

    def set_dashboard_trend_range(self, value: str) -> None:
        mapping = {
            "week": "week",
            "month": "month",
            "year": "year",
            "最近一周": "week",
            "最近一月": "month",
            "最近一年": "year",
            "Last 7 days": "week",
            "Last 30 days": "month",
            "Last 12 months": "year",
        }
        normalized = mapping.get(value)
        if normalized is not None:
            self.dashboard_trend_range = normalized

    def set_extension_tab(self, tab: str) -> None:
        self.extension_tab = tab if tab in {"skills", "mcp"} else "skills"

    def set_skill_search_query(self, value: str) -> None:
        self.skill_search_query = value
        self.skill_page = 1

    def next_skill_page(self) -> None:
        if self.skill_page < self.skill_total_pages:
            self.skill_page += 1

    def previous_skill_page(self) -> None:
        if self.skill_page > 1:
            self.skill_page -= 1

    def _clear_clawhub_preview(self) -> None:
        self.clawhub_selected_slug = ""
        self.clawhub_preview_query = ""
        self.clawhub_preview_candidate_count = ""
        self.clawhub_preview_name = ""
        self.clawhub_preview_version = ""
        self.clawhub_preview_score = ""
        self.clawhub_preview_summary = ""
        self.clawhub_preview_package_name = ""
        self.clawhub_preview_install_dir = ""
        self.clawhub_preview_text = ""
        self.clawhub_panel_hint = ""

    def set_clawhub_query(self, value: str) -> None:
        next_query = str(value or "")
        if next_query != self.clawhub_query:
            self._clear_clawhub_preview()
        self.clawhub_query = next_query

    def set_clawhub_source_url(self, value: str) -> None:
        next_source = str(value or "").strip()
        if not next_source:
            next_source = "https://clawhub.ai"
        if next_source != self.clawhub_source_url:
            self._clear_clawhub_preview()
        self.clawhub_source_url = next_source

    def set_mcp_search_query(self, value: str) -> None:
        self.mcp_search_query = value

    def request_delete_skill(self, path: str, name: str) -> None:
        self.skill_delete_confirm_path = str(path or "").strip()
        self.skill_delete_confirm_name = str(name or "").strip()

    def cancel_delete_skill(self) -> None:
        self.skill_delete_confirm_path = ""
        self.skill_delete_confirm_name = ""

    def select_skill(self, path: str) -> None:
        self.selected_skill_path = path

    def select_mcp(self, name: str) -> None:
        self.selected_mcp_name = name

    def open_skill_detail(self, path: str) -> None:
        self.selected_skill_path = path
        self.extension_detail_kind = "skill"

    def open_mcp_detail(self, name: str) -> None:
        self.selected_mcp_name = name
        self.extension_detail_kind = "mcp"

    def close_extension_detail(self) -> None:
        self.extension_detail_kind = ""

    def set_mcp_name(self, value: str) -> None:
        self.mcp_name = value

    def set_mcp_transport(self, value: str) -> None:
        self.mcp_transport = value

    def set_mcp_command(self, value: str) -> None:
        self.mcp_command = value

    def set_mcp_args(self, value: str) -> None:
        self.mcp_args = value

    def set_mcp_description(self, value: str) -> None:
        self.mcp_description = value

    def set_mcp_enabled(self, value: str) -> None:
        self.mcp_enabled = value

    def set_market_query(self, value: str) -> None:
        self.market_query = value

    def set_market_filter(self, value: str) -> None:
        self.market_filter = value

    def set_source_name(self, value: str) -> None:
        self.source_name = value

    def set_source_kind(self, value: str) -> None:
        self.source_kind = value

    def set_source_url(self, value: str) -> None:
        self.source_url = value

    def set_source_description(self, value: str) -> None:
        self.source_description = value

    def set_source_tags(self, value: str) -> None:
        self.source_tags = value

    def set_hook_name(self, value: str) -> None:
        self.hook_name = value

    def set_hook_event(self, value: str) -> None:
        self.hook_event = value

    def set_hook_handler_type(self, value: str) -> None:
        self.hook_handler_type = value

    def set_hook_handler_value(self, value: str) -> None:
        self.hook_handler_value = value

    def set_hook_description(self, value: str) -> None:
        self.hook_description = value

    def set_hook_enabled(self, value: str) -> None:
        self.hook_enabled = value

    def set_llm_provider(self, value: str) -> None:
        self.llm_provider = value

    def set_llm_model(self, value: str) -> None:
        self.llm_model = value

    def set_llm_base_url(self, value: str) -> None:
        self.llm_base_url = value

    def set_llm_temperature(self, value: str) -> None:
        self.llm_temperature = value

    def set_llm_max_tokens(self, value: str) -> None:
        self.llm_max_tokens = value

    def set_llm_enable_thinking(self, value: str) -> None:
        self.llm_enable_thinking = value

    def set_heartbeat_enabled(self, value: str) -> None:
        raw = str(value).strip().lower()
        self.heartbeat_enabled = "enabled" if raw in {"enabled", "true", "1", "on", "开启"} else "disabled"

    def set_llm_api_key(self, value: str) -> None:
        self.llm_api_key = value

    def set_persist_api_key(self, value: str) -> None:
        self.persist_api_key = value

    def set_platform_bridge_enabled(self, value: str) -> None:
        raw = str(value).strip().lower()
        self.platform_bridge_enabled = "enabled" if raw in {"enabled", "true", "1", "yes", "on"} else "disabled"

    def set_platform_bridge_verify_token(self, value: str) -> None:
        self.platform_bridge_verify_token = value

    def set_platform_bridge_default_mode(self, value: str) -> None:
        normalized = str(value).strip().lower()
        self.platform_bridge_default_mode = "agent" if normalized == "agent" else "ask"

    def set_platform_bridge_allowed_platforms(self, value: str) -> None:
        self.platform_bridge_allowed_platforms = value

    def set_platform_bridge_signature_ttl_seconds(self, value: str) -> None:
        self.platform_bridge_signature_ttl_seconds = value

    def set_platform_bridge_event_id_ttl_seconds(self, value: str) -> None:
        self.platform_bridge_event_id_ttl_seconds = value

    def set_platform_bridge_feishu_encrypt_key(self, value: str) -> None:
        self.platform_bridge_feishu_encrypt_key = value

    def set_platform_bridge_wechat_token(self, value: str) -> None:
        self.platform_bridge_wechat_token = value

    def set_platform_bridge_qq_signing_secret(self, value: str) -> None:
        self.platform_bridge_qq_signing_secret = value

    def set_platform_bridge_callback_delivery_enabled(self, value: str) -> None:
        raw = str(value).strip().lower()
        self.platform_bridge_callback_delivery_enabled = (
            "enabled" if raw in {"enabled", "true", "1", "yes", "on"} else "disabled"
        )

    def set_platform_bridge_callback_timeout_seconds(self, value: str) -> None:
        self.platform_bridge_callback_timeout_seconds = value

    def set_platform_bridge_feishu_api_base_url(self, value: str) -> None:
        self.platform_bridge_feishu_api_base_url = value

    def set_platform_bridge_feishu_app_id(self, value: str) -> None:
        self.platform_bridge_feishu_app_id = value

    def set_platform_bridge_feishu_app_secret(self, value: str) -> None:
        self.platform_bridge_feishu_app_secret = value

    def set_platform_bridge_wechat_delivery_mode(self, value: str) -> None:
        normalized = str(value).strip().lower()
        if normalized not in {"auto", "work", "official"}:
            normalized = "auto"
        self.platform_bridge_wechat_delivery_mode = normalized

    def set_platform_bridge_wechat_work_api_base_url(self, value: str) -> None:
        self.platform_bridge_wechat_work_api_base_url = value

    def set_platform_bridge_wechat_work_corp_id(self, value: str) -> None:
        self.platform_bridge_wechat_work_corp_id = value

    def set_platform_bridge_wechat_work_corp_secret(self, value: str) -> None:
        self.platform_bridge_wechat_work_corp_secret = value

    def set_platform_bridge_wechat_work_agent_id(self, value: str) -> None:
        self.platform_bridge_wechat_work_agent_id = value

    def set_platform_bridge_wechat_official_api_base_url(self, value: str) -> None:
        self.platform_bridge_wechat_official_api_base_url = value

    def set_platform_bridge_wechat_official_app_id(self, value: str) -> None:
        self.platform_bridge_wechat_official_app_id = value

    def set_platform_bridge_wechat_official_app_secret(self, value: str) -> None:
        self.platform_bridge_wechat_official_app_secret = value

    def set_platform_bridge_qq_api_base_url(self, value: str) -> None:
        self.platform_bridge_qq_api_base_url = value

    def set_platform_bridge_qq_delivery_mode(self, value: str) -> None:
        normalized = str(value).strip().lower()
        if normalized not in {"auto", "official", "napcat"}:
            normalized = "auto"
        self.platform_bridge_qq_delivery_mode = normalized

    def set_platform_bridge_qq_bot_app_id(self, value: str) -> None:
        self.platform_bridge_qq_bot_app_id = value

    def set_platform_bridge_qq_bot_token(self, value: str) -> None:
        self.platform_bridge_qq_bot_token = value

    def set_platform_bridge_qq_napcat_api_base_url(self, value: str) -> None:
        self.platform_bridge_qq_napcat_api_base_url = value

    def set_platform_bridge_qq_napcat_access_token(self, value: str) -> None:
        self.platform_bridge_qq_napcat_access_token = value

    def set_platform_bridge_qq_napcat_webhook_token(self, value: str) -> None:
        self.platform_bridge_qq_napcat_webhook_token = value

    def set_platform_bridge_inbound_enabled(self, value: str) -> None:
        normalized = str(value).strip().lower()
        self.platform_bridge_inbound_enabled = "enabled" if normalized == "enabled" else "disabled"

    def set_platform_bridge_inbound_port(self, value: str) -> None:
        self.platform_bridge_inbound_port = value

    def set_platform_bridge_inbound_debug(self, value: str) -> None:
        normalized = str(value).strip().lower()
        self.platform_bridge_inbound_debug = "enabled" if normalized == "enabled" else "disabled"

    def set_model_profile_name(self, value: str) -> None:
        self.model_profile_name = value

    def set_policy_name(self, value: str) -> None:
        self.policy_name = value

    def set_policy_scope(self, value: str) -> None:
        self.policy_scope = value

    def set_policy_target(self, value: str) -> None:
        self.policy_target = value

    def set_policy_decision(self, value: str) -> None:
        self.policy_decision = value

    def set_policy_description(self, value: str) -> None:
        self.policy_description = value

    def set_policy_enabled(self, value: str) -> None:
        self.policy_enabled = value

    def set_audit_limit(self, value: str) -> None:
        self.audit_limit = value

    def set_audit_query(self, value: str) -> None:
        self.audit_query = value

    def set_audit_status_filter(self, value: str) -> None:
        self.audit_status_filter = value

    def next_task_page(self) -> None:
        if self.task_page < self.task_total_pages:
            self.task_page += 1

    def previous_task_page(self) -> None:
        if self.task_page > 1:
            self.task_page -= 1

    async def bootstrap(self) -> None:
        _apply_saved_model_overrides()
        apply_chat_bridge_runtime_overrides()
        settings = get_settings()
        self.model_defaults = {
            "llm_provider": settings.llm_provider,
            "llm_model": settings.llm_model,
            "llm_base_url": settings.llm_base_url,
            "llm_api_key": settings.llm_api_key,
            "llm_temperature": settings.llm_temperature,
            "llm_max_tokens": settings.llm_max_tokens,
            "llm_enable_thinking": settings.llm_enable_thinking,
        }
        runtime_flags = _load_ui_runtime_flags()
        heartbeat_flag = runtime_flags.get("heartbeat_enabled")
        if isinstance(heartbeat_flag, bool):
            self.heartbeat_enabled = "enabled" if heartbeat_flag else "disabled"
        else:
            self.heartbeat_enabled = "enabled" if bool(settings.ui_heartbeat_enabled) else "disabled"
        await self.refresh_data()

    async def refresh_data(self) -> None:
        self.busy = True
        self.error_message = ""
        try:
            sessions = await _session_store().list_all()
            tasks = await _task_store().list_all(limit=200)

            self.sessions = [_session_to_row(row) for row in sessions]
            self.tasks = [_task_to_row(row) for row in tasks]

            if self.sessions and self.selected_session_id not in {s["id"] for s in self.sessions}:
                self.selected_session_id = self.sessions[0]["id"]
            if not self.sessions:
                self.selected_session_id = ""
            if self.tasks and self.selected_task_id not in {t["id"] for t in self.tasks}:
                self.selected_task_id = self.tasks[0]["id"]

            valid_session_ids = {row["id"] for row in self.sessions}
            self.pinned_session_ids = [sid for sid in self.pinned_session_ids if sid in valid_session_ids]
            valid_session_group_keys = {
                _time_bucket_key_title(_parse_task_updated_at(str(row.get("updated_at", ""))), self.ui_language)[0]
                for row in self.sessions
            }
            self.collapsed_session_group_keys = [
                key for key in self.collapsed_session_group_keys if key in valid_session_group_keys
            ]
            if self.session_action_open_id not in valid_session_ids:
                self.session_action_open_id = ""
            if self.session_delete_confirm_id not in valid_session_ids:
                self.session_delete_confirm_id = ""
            if self.session_delete_confirm_group_key not in valid_session_group_keys:
                self.session_delete_confirm_group_key = ""
                self.session_delete_confirm_group_title = ""
                self.session_delete_confirm_group_count = 0
            if self.session_rename_id not in valid_session_ids:
                self.session_rename_id = ""
                self.session_rename_value = ""

            valid_task_ids = {row["id"] for row in self.tasks}
            self.pinned_task_ids = [tid for tid in self.pinned_task_ids if tid in valid_task_ids]
            valid_task_group_keys = {
                _time_bucket_key_title(_parse_task_updated_at(str(row.get("updated_at", ""))), self.ui_language)[0]
                for row in self.tasks
            }
            self.collapsed_task_group_keys = [
                key for key in self.collapsed_task_group_keys if key in valid_task_group_keys
            ]
            if self.task_action_open_id not in valid_task_ids:
                self.task_action_open_id = ""
            if self.task_delete_confirm_id not in valid_task_ids:
                self.task_delete_confirm_id = ""
            if self.task_delete_confirm_group_key not in valid_task_group_keys:
                self.task_delete_confirm_group_key = ""
                self.task_delete_confirm_group_title = ""
                self.task_delete_confirm_group_count = 0
            if self.task_rename_id not in valid_task_ids:
                self.task_rename_id = ""
                self.task_rename_value = ""

            await self._load_selected_session_messages()
            await self._load_selected_task_detail()

            self.mcp_servers = [
                {
                    "name": str(item.name),
                    "transport": str(item.transport),
                    "command": str(item.command),
                    "args": " ".join([str(arg) for arg in item.args if str(arg).strip()]),
                    "env": json.dumps(item.env, ensure_ascii=False) if item.env else "",
                    "enabled": "enabled" if item.enabled else "disabled",
                    "description": str(item.description),
                }
                for item in _mcp_registry().load()
            ]
            self.hook_rules = [
                {
                    "id": str(item.id),
                    "name": str(item.name),
                    "event": str(item.event.value),
                    "handler_type": str(item.handler_type),
                    "handler_value": str(item.handler_value),
                    "enabled": "enabled" if item.enabled else "disabled",
                    "updated_at": str(_iso(item.updated_at)),
                }
                for item in _hook_rule_store().list_all()
            ]
            self.policy_rules = [
                {
                    "id": str(item.id),
                    "name": str(item.name),
                    "scope": str(item.scope),
                    "target": str(item.target),
                    "decision": str(item.decision),
                    "enabled": "enabled" if item.enabled else "disabled",
                    "updated_at": str(_iso(item.updated_at)),
                }
                for item in _policy_store().list_all()
            ]
            self.extension_sources = [
                {
                    "name": str(item.get("name", "")),
                    "kind": str(item.get("kind", "")),
                    "url": str(item.get("url", "")),
                    "description": str(item.get("description", "")),
                    "tags": ", ".join(
                        [str(tag) for tag in item.get("tags", [])] if isinstance(item.get("tags", []), list) else []
                    ),
                }
                for item in _load_extension_sources()
            ]
            skill_enabled = _skill_toggle_store().load()
            self.skills = [
                {
                    "name": str(item.name),
                    "path": str(item.path),
                    "description": str(item.description),
                    "tags": ", ".join(item.tags),
                    "installed_at": _iso(datetime.fromtimestamp(Path(item.path).stat().st_mtime)),
                    "enabled": "enabled" if bool(skill_enabled.get(str(item.path), True)) else "disabled",
                }
                for item in SkillRegistry().discover()
            ]

            if self.skill_page > self.skill_total_pages:
                self.skill_page = self.skill_total_pages

            valid_skill_paths = {row["path"] for row in self.skills}
            if self.selected_skill_path not in valid_skill_paths:
                self.selected_skill_path = next(iter(valid_skill_paths), "")

            valid_mcp_names = {row["name"] for row in self.mcp_servers}
            if self.selected_mcp_name not in valid_mcp_names:
                self.selected_mcp_name = next(iter(valid_mcp_names), "")
            if self.extension_detail_kind == "skill" and not self.selected_skill_path:
                self.extension_detail_kind = ""
            if self.extension_detail_kind == "mcp" and not self.selected_mcp_name:
                self.extension_detail_kind = ""

            limit = max(int(self.audit_limit or "60"), 1)
            self.audit_events = [
                {
                    "timestamp": _iso(item.timestamp),
                    "event": str(item.event),
                    "actor": str(item.actor),
                    "status": str(item.status),
                    "resource": str(item.resource),
                    "metadata": json.dumps(item.metadata, ensure_ascii=False),
                }
                for item in _audit_logger().list_recent(limit=limit)
            ]

            self._refresh_model_fields()
            self._refresh_platform_bridge_fields()
            self._refresh_platform_bridge_inbound_logs()
        except Exception as exc:
            self.error_message = str(exc)
        finally:
            self.busy = False

    async def refresh_data_with_feedback(self) -> rx.event.EventSpec | None:
        await self.refresh_data()
        if self.error_message:
            return None
        return rx.toast("数据已刷新", level="success")

    async def _load_selected_session_messages(self) -> None:
        if not self.selected_session_id:
            self.chat_messages = []
            self.selected_session_name = ""
            return

        try:
            session = await _session_store().get(self.selected_session_id)
        except Exception:
            self.chat_messages = []
            self.selected_session_name = ""
            return

        self.selected_session_name = session.name
        trace_map = dict(getattr(self, "chat_agent_traces", {}))
        metadata = dict(session.metadata or {})
        persisted_runs = metadata.get("agent_runs")
        if isinstance(persisted_runs, list):
            for item in persisted_runs:
                if not isinstance(item, dict):
                    continue
                assistant_message_id = str(item.get("assistant_message_id", "")).strip()
                if not assistant_message_id:
                    continue
                trace_map[assistant_message_id] = {
                    "trace_reason": str(item.get("trace_reason", "")),
                    "trace_function_call": str(item.get("trace_function_call", "")),
                    "trace_observation": str(item.get("trace_observation", "")),
                    "trace_elapsed": str(item.get("trace_elapsed", "")),
                    "trace_collapsed": str(item.get("trace_collapsed", "1")),
                    "trace_context_collapsed": str(item.get("trace_context_collapsed", "1")),
                    "trace_intent": str(item.get("trace_intent", "")),
                    "trace_plan": str(item.get("trace_plan", "")),
                    "trace_skills": str(item.get("trace_skills", "")),
                    "trace_mcp": str(item.get("trace_mcp", "")),
                }
        self.chat_agent_traces = trace_map
        rows: list[dict[str, str]] = []
        for msg in session.messages:
            message_id = _session_message_id(msg)
            trace_payload = trace_map.get(message_id, {})
            rows.append(
                {
                    "id": message_id,
                    "role": msg.role,
                    "content": msg.content,
                    "created_at": _iso(msg.created_at),
                    "status": "done",
                    "trace_reason": str(trace_payload.get("trace_reason", trace_payload.get("trace_detail", ""))),
                    "trace_function_call": str(trace_payload.get("trace_function_call", "")),
                    "trace_observation": str(trace_payload.get("trace_observation", "")),
                    "trace_elapsed": str(trace_payload.get("trace_elapsed", "")),
                    "trace_collapsed": str(trace_payload.get("trace_collapsed", "1")),
                    "trace_context_collapsed": str(trace_payload.get("trace_context_collapsed", "1")),
                    "trace_intent": str(trace_payload.get("trace_intent", "")),
                    "trace_plan": str(trace_payload.get("trace_plan", "")),
                    "trace_skills": str(trace_payload.get("trace_skills", "")),
                    "trace_mcp": str(trace_payload.get("trace_mcp", "")),
                }
            )

        self.chat_messages = rows

    async def _refresh_sessions_only(self) -> None:
        sessions = await _session_store().list_all()
        self.sessions = [_session_to_row(row) for row in sessions]

        valid_session_ids = {row["id"] for row in self.sessions}
        self.pinned_session_ids = [sid for sid in self.pinned_session_ids if sid in valid_session_ids]
        valid_session_group_keys = {
            _time_bucket_key_title(_parse_task_updated_at(str(row.get("updated_at", ""))), self.ui_language)[0]
            for row in self.sessions
        }
        self.collapsed_session_group_keys = [
            key for key in self.collapsed_session_group_keys if key in valid_session_group_keys
        ]

        if self.selected_session_id not in valid_session_ids:
            self.selected_session_id = self.sessions[0]["id"] if self.sessions else ""

        if self.session_action_open_id not in valid_session_ids:
            self.session_action_open_id = ""
        if self.session_delete_confirm_id not in valid_session_ids:
            self.session_delete_confirm_id = ""
        if self.session_delete_confirm_group_key not in valid_session_group_keys:
            self.session_delete_confirm_group_key = ""
            self.session_delete_confirm_group_title = ""
            self.session_delete_confirm_group_count = 0
        if self.session_rename_id not in valid_session_ids:
            self.session_rename_id = ""
            self.session_rename_value = ""

        await self._load_selected_session_messages()

    async def _refresh_tasks_only(self) -> None:
        tasks = await _task_store().list_all(limit=200)
        self.tasks = [_task_to_row(row) for row in tasks]

        valid_task_ids = {row["id"] for row in self.tasks}
        self.pinned_task_ids = [tid for tid in self.pinned_task_ids if tid in valid_task_ids]
        valid_task_group_keys = {
            _time_bucket_key_title(_parse_task_updated_at(str(row.get("updated_at", ""))), self.ui_language)[0]
            for row in self.tasks
        }
        self.collapsed_task_group_keys = [key for key in self.collapsed_task_group_keys if key in valid_task_group_keys]

        if self.selected_task_id not in valid_task_ids:
            self.selected_task_id = self.tasks[0]["id"] if self.tasks else ""

        if self.task_action_open_id not in valid_task_ids:
            self.task_action_open_id = ""
        if self.task_delete_confirm_id not in valid_task_ids:
            self.task_delete_confirm_id = ""
        if self.task_delete_confirm_group_key not in valid_task_group_keys:
            self.task_delete_confirm_group_key = ""
            self.task_delete_confirm_group_title = ""
            self.task_delete_confirm_group_count = 0
        if self.task_rename_id not in valid_task_ids:
            self.task_rename_id = ""
            self.task_rename_value = ""

        await self._load_selected_task_detail()

    async def _load_selected_task_detail(self) -> None:
        if not self.selected_task_id:
            self.task_plan = []
            self.task_artifacts = []
            self.task_summary = ""
            self.task_error = ""
            self.task_review_score = ""
            self.task_review_issues = []
            self.task_execution_timeline = []
            self.task_agent_intent = ""
            self.task_agent_plan = ""
            self.task_agent_skills = ""
            self.task_agent_mcp = ""
            return

        try:
            task = await _task_store().get(self.selected_task_id)
        except Exception:
            self.task_plan = []
            self.task_artifacts = []
            self.task_summary = ""
            self.task_error = ""
            self.task_review_score = ""
            self.task_review_issues = []
            self.task_execution_timeline = []
            self.task_agent_intent = ""
            self.task_agent_plan = ""
            self.task_agent_skills = ""
            self.task_agent_mcp = ""
            return

        self.task_plan = [str(step) for step in task.plan]
        self.task_artifacts = [
            {
                "filename": str(artifact.get("filename", "output.py")),
                "content": str(artifact.get("content", "")),
                "language": _guess_language(str(artifact.get("filename", "output.py"))),
            }
            for artifact in task.code_artifacts
        ]
        self.task_summary = task.summary
        self.task_error = task.error

        score = task.review_result.get("score", "") if isinstance(task.review_result, dict) else ""
        self.task_review_score = str(score)
        issues = task.review_result.get("issues", []) if isinstance(task.review_result, dict) else []
        self.task_review_issues = [str(item) for item in issues]
        self.task_execution_timeline = self._build_task_execution_timeline(task)
        task_state = task.task_state if isinstance(task.task_state, dict) else {}
        context_payload = task_state.get("chat_agent_context") if isinstance(task_state.get("chat_agent_context"), dict) else {}
        (
            self.task_agent_intent,
            self.task_agent_plan,
            self.task_agent_skills,
            self.task_agent_mcp,
        ) = _agent_context_trace_fields(context_payload)

    def _build_task_execution_timeline(self, task: TaskRecord) -> list[dict[str, str]]:
        timeline: list[dict[str, str]] = []
        execution_rows = task.execution_results if isinstance(task.execution_results, list) else []

        for idx, row in enumerate(execution_rows, 1):
            if not isinstance(row, dict):
                continue

            step_id = str(row.get("step_id") or f"step-{idx}")
            action_type = str(row.get("action_type") or "code")
            success = bool(row.get("success", False))
            tool_events = row.get("tool_events", [])
            evidence = row.get("evidence", [])

            tools_text = ""
            if isinstance(tool_events, list) and tool_events:
                fragments: list[str] = []
                for event in tool_events[:4]:
                    if not isinstance(event, dict):
                        continue
                    action = str(event.get("action") or "")
                    ok = "ok" if event.get("tool_success") else "failed"
                    meta = event.get("tool_metadata") or {}
                    path = str(meta.get("path") or "").strip()
                    exit_code = meta.get("exit_code")

                    segment = f"- {action}: {ok}"
                    if path:
                        segment += f" | {path}"
                    if exit_code is not None:
                        segment += f" | exit={exit_code}"
                    fragments.append(segment)
                tools_text = "\n".join(fragments)

            evidence_text = ""
            if isinstance(evidence, list) and evidence:
                evidence_text = "\n".join(f"- {str(item)}" for item in evidence[:5])

            answer_preview = str(row.get("answer") or "").strip()
            if len(answer_preview) > 300:
                answer_preview = answer_preview[:300] + "..."

            detail_parts = [
                f"Action Type: {action_type}",
            ]
            if tools_text:
                detail_parts.append("Tools:\n" + tools_text)
            if evidence_text:
                detail_parts.append("Evidence:\n" + evidence_text)
            if answer_preview:
                detail_parts.append("Answer:\n" + answer_preview)

            timeline.append(
                {
                    "id": f"timeline-{step_id}-{idx}",
                    "title": f"Step {idx}: {step_id}",
                    "status": "done" if success else "failed",
                    "detail": "\n\n".join(detail_parts),
                    "collapsed": "1",
                }
            )

        return timeline

    async def select_session(self, session_id: str) -> None:
        self.selected_session_id = session_id
        self.session_action_open_id = ""
        self.session_delete_confirm_id = ""
        self.session_rename_id = ""
        self.session_rename_value = ""
        await self._load_selected_session_messages()

    async def select_task(self, task_id: str) -> None:
        self.selected_task_id = task_id
        self.task_action_open_id = ""
        self.task_delete_confirm_id = ""
        self.task_rename_id = ""
        self.task_rename_value = ""
        await self._load_selected_task_detail()

    async def open_task_detail(self, task_id: str) -> None:
        self.selected_page = "task_center"
        await self.select_task(task_id)

    async def create_session(self) -> rx.event.EventSpec | None:
        try:
            created = await _session_store().create(name="新对话" if self.ui_language == "zh" else "New Chat")
            self.selected_session_id = created.id
            self.selected_session_name = created.name
            self.chat_messages = []
            self.session_action_open_id = ""
            self.session_delete_confirm_id = ""
            self.session_rename_id = ""
            self.session_rename_value = ""
            await self._refresh_sessions_only()
            return self._toast_success("新对话已创建" if self.ui_language == "zh" else "New dialogue created")
        except Exception as exc:
            self.error_message = str(exc)
            return self._toast_error(f"创建对话失败: {exc}")

    async def delete_selected_session(self) -> rx.event.EventSpec | None:
        if not self.selected_session_id:
            return None
        return await self.delete_session_by_id(self.selected_session_id)

    async def delete_session_by_id(self, session_id: str) -> rx.event.EventSpec | None:
        if not session_id:
            return None
        try:
            await _session_store().delete(session_id)
            with contextlib.suppress(Exception):
                _task_memory_store().delete_session_entries(session_id)
            if self.selected_session_id == session_id:
                self.selected_session_id = ""
            self.session_delete_confirm_id = ""
            self.session_delete_confirm_group_key = ""
            self.session_delete_confirm_group_title = ""
            self.session_delete_confirm_group_count = 0
            self.session_action_open_id = ""
            self.session_rename_id = ""
            self.session_rename_value = ""
            await self._refresh_sessions_only()
            return self._toast_success("对话已删除" if self.ui_language == "zh" else "Dialogue deleted")
        except Exception as exc:
            self.error_message = str(exc)
            return self._toast_error(f"删除对话失败: {exc}")

    async def delete_session_group_by_key(self, group_key: str) -> rx.event.EventSpec | None:
        if not group_key:
            return None

        target_ids: list[str] = []
        target_title = self.session_delete_confirm_group_title
        for group in self.chat_session_groups:
            if str(group.get("key", "")) != group_key:
                continue
            target_title = str(group.get("title", ""))
            target_ids = [str(item.get("id", "")) for item in group.get("items", []) if str(item.get("id", ""))]
            break

        if not target_ids:
            self.session_delete_confirm_group_key = ""
            self.session_delete_confirm_group_title = ""
            self.session_delete_confirm_group_count = 0
            return self._toast_error("未找到要删除的对话分组" if self.ui_language == "zh" else "Dialogue group not found")

        deleted = 0
        errors: list[str] = []
        for session_id in target_ids:
            try:
                await _session_store().delete(session_id)
                with contextlib.suppress(Exception):
                    _task_memory_store().delete_session_entries(session_id)
                deleted += 1
            except Exception as exc:
                errors.append(f"{session_id}: {exc}")

        if self.selected_session_id in target_ids:
            self.selected_session_id = ""

        self.session_delete_confirm_id = ""
        self.session_delete_confirm_group_key = ""
        self.session_delete_confirm_group_title = ""
        self.session_delete_confirm_group_count = 0
        self.session_action_open_id = ""
        self.session_rename_id = ""
        self.session_rename_value = ""
        await self._refresh_sessions_only()

        if errors and deleted == 0:
            self.error_message = errors[0]
            return self._toast_error(f"删除对话分组失败: {errors[0]}" if self.ui_language == "zh" else f"Delete dialogue group failed: {errors[0]}")
        if errors:
            return self._toast_success(
                f"已删除 {deleted} 条“{target_title}”对话，{len(errors)} 条失败"
                if self.ui_language == "zh"
                else f"Deleted {deleted} dialogues from {target_title}, {len(errors)} failed"
            )
        return self._toast_success(
            f"已删除 “{target_title}” 分组下的 {deleted} 条对话"
            if self.ui_language == "zh"
            else f"Deleted {deleted} dialogues from {target_title}"
        )

    async def delete_requested_skill(self) -> rx.event.EventSpec | None:
        raw_path = str(self.skill_delete_confirm_path or "").strip()
        if not raw_path:
            return None
        target_path = Path(raw_path)

        discovered = {Path(item.path).resolve(): item for item in SkillRegistry().discover()}
        try:
            resolved_target = target_path.resolve()
        except OSError:
            resolved_target = target_path

        skill = discovered.get(resolved_target)
        if skill is None:
            self.skill_delete_confirm_path = ""
            self.skill_delete_confirm_name = ""
            return self._toast_error("Skill 未找到" if self.ui_language == "zh" else "Skill not found")

        delete_target = resolved_target.parent if resolved_target.name.lower() == "skill.md" else resolved_target
        try:
            if delete_target.is_dir():
                shutil.rmtree(delete_target)
            else:
                delete_target.unlink()
        except Exception as exc:
            self.error_message = str(exc)
            return self._toast_error(f"删除 Skill 失败: {exc}" if self.ui_language == "zh" else f"Delete skill failed: {exc}")

        store = _skill_toggle_store()
        flags = store.load()
        for key in list(flags.keys()):
            try:
                key_path = Path(key).resolve()
            except OSError:
                key_path = Path(key)
            if key_path == resolved_target or delete_target in key_path.parents:
                flags.pop(key, None)
        store.save(flags)

        self.skill_delete_confirm_path = ""
        self.skill_delete_confirm_name = ""
        if self.selected_skill_path == str(resolved_target):
            self.selected_skill_path = ""
        if self.extension_detail_kind == "skill":
            self.extension_detail_kind = ""
        _clear_agent_cache()
        await self.refresh_data()
        return self._toast_success(
            f"Skill 已删除: {skill.name}" if self.ui_language == "zh" else f"Skill deleted: {skill.name}"
        )

    async def save_session_rename(self) -> rx.event.EventSpec | None:
        session_id = self.session_rename_id.strip()
        new_name = self.session_rename_value.strip()
        if not session_id:
            return None
        if not new_name:
            return self._toast_error("请输入对话名称" if self.ui_language == "zh" else "Please enter a dialogue name")

        try:
            session = await _session_store().get(session_id)
            session.name = new_name
            await _session_store().update(session)
            self.session_rename_id = ""
            self.session_rename_value = ""
            await self._refresh_sessions_only()
            return self._toast_success("对话已重命名" if self.ui_language == "zh" else "Dialogue renamed")
        except Exception as exc:
            self.error_message = str(exc)
            return self._toast_error(f"重命名失败: {exc}")

    async def save_task_rename(self) -> rx.event.EventSpec | None:
        task_id = self.task_rename_id.strip()
        new_name = self.task_rename_value.strip()
        if not task_id:
            return None
        if not new_name:
            return self._toast_error("请输入任务名称" if self.ui_language == "zh" else "Please enter a task name")

        try:
            task = await _task_store().get(task_id)
            task.task = new_name
            await _task_store().update(task)
            self.task_rename_id = ""
            self.task_rename_value = ""
            await self._refresh_tasks_only()
            return self._toast_success("任务已重命名" if self.ui_language == "zh" else "Task renamed")
        except Exception as exc:
            self.error_message = str(exc)
            return self._toast_error(f"重命名失败: {exc}")

    async def delete_task_by_id(self, task_id: str) -> rx.event.EventSpec | None:
        if not task_id:
            return None

        try:
            await _task_store().delete(task_id)
            if self.selected_task_id == task_id:
                self.selected_task_id = ""
            self.task_delete_confirm_id = ""
            self.task_delete_confirm_group_key = ""
            self.task_delete_confirm_group_title = ""
            self.task_delete_confirm_group_count = 0
            self.task_action_open_id = ""
            self.task_rename_id = ""
            self.task_rename_value = ""
            await self._refresh_tasks_only()
            return self._toast_success("任务已删除" if self.ui_language == "zh" else "Task deleted")
        except Exception as exc:
            self.error_message = str(exc)
            return self._toast_error(f"删除任务失败: {exc}")

    async def delete_task_group_by_key(self, group_key: str) -> rx.event.EventSpec | None:
        if not group_key:
            return None

        target_ids: list[str] = []
        target_title = self.task_delete_confirm_group_title
        for group in self.task_groups:
            if str(group.get("key", "")) != group_key:
                continue
            target_title = str(group.get("title", ""))
            target_ids = [str(item.get("id", "")) for item in group.get("items", []) if str(item.get("id", ""))]
            break

        if not target_ids:
            self.task_delete_confirm_group_key = ""
            self.task_delete_confirm_group_title = ""
            self.task_delete_confirm_group_count = 0
            return self._toast_error("未找到要删除的任务分组" if self.ui_language == "zh" else "Task group not found")

        deleted = 0
        errors: list[str] = []
        for task_id in target_ids:
            try:
                await _task_store().delete(task_id)
                deleted += 1
            except Exception as exc:
                errors.append(f"{task_id}: {exc}")

        if self.selected_task_id in target_ids:
            self.selected_task_id = ""

        self.task_delete_confirm_id = ""
        self.task_delete_confirm_group_key = ""
        self.task_delete_confirm_group_title = ""
        self.task_delete_confirm_group_count = 0
        self.task_action_open_id = ""
        self.task_rename_id = ""
        self.task_rename_value = ""
        await self._refresh_tasks_only()

        if errors and deleted == 0:
            self.error_message = errors[0]
            return self._toast_error(f"删除任务分组失败: {errors[0]}")
        if errors:
            return self._toast_success(
                (
                    f"已删除 {deleted} 条“{target_title}”任务，{len(errors)} 条失败"
                    if self.ui_language == "zh"
                    else f"Deleted {deleted} tasks from {target_title}, {len(errors)} failed"
                )
            )
        return self._toast_success(
            (
                f"已删除 “{target_title}” 分组下的 {deleted} 条任务"
                if self.ui_language == "zh"
                else f"Deleted {deleted} tasks from {target_title}"
            )
        )

    def _replace_chat_message(
        self,
        message_id: str,
        content: str,
        status: str,
        *,
        trace_reason: str | None = None,
        trace_function_call: str | None = None,
        trace_observation: str | None = None,
        trace_elapsed: str | None = None,
        trace_collapsed: str | None = None,
        trace_context_collapsed: str | None = None,
        trace_intent: str | None = None,
        trace_plan: str | None = None,
        trace_skills: str | None = None,
        trace_mcp: str | None = None,
    ) -> None:
        updated: list[dict[str, str]] = []
        for item in self.chat_messages:
            if item.get("id") == message_id:
                next_item = dict(item)
                next_item["content"] = content
                next_item["status"] = status
                if trace_reason is not None:
                    next_item["trace_reason"] = trace_reason
                if trace_function_call is not None:
                    next_item["trace_function_call"] = trace_function_call
                if trace_observation is not None:
                    next_item["trace_observation"] = trace_observation
                if trace_elapsed is not None:
                    next_item["trace_elapsed"] = trace_elapsed
                if trace_collapsed is not None:
                    next_item["trace_collapsed"] = trace_collapsed
                if trace_context_collapsed is not None:
                    next_item["trace_context_collapsed"] = trace_context_collapsed
                if trace_intent is not None:
                    next_item["trace_intent"] = trace_intent
                if trace_plan is not None:
                    next_item["trace_plan"] = trace_plan
                if trace_skills is not None:
                    next_item["trace_skills"] = trace_skills
                if trace_mcp is not None:
                    next_item["trace_mcp"] = trace_mcp
                updated.append(next_item)
            else:
                updated.append(item)
        self.chat_messages = updated

    def _remember_chat_trace(
        self,
        message_id: str,
        *,
        trace_reason: str,
        trace_function_call: str,
        trace_observation: str,
        trace_elapsed: str,
        trace_collapsed: str = "1",
        trace_context_collapsed: str = "1",
        trace_intent: str = "",
        trace_plan: str = "",
        trace_skills: str = "",
        trace_mcp: str = "",
    ) -> None:
        trace_map = dict(getattr(self, "chat_agent_traces", {}))
        trace_map[message_id] = {
            "trace_reason": trace_reason,
            "trace_function_call": trace_function_call,
            "trace_observation": trace_observation,
            "trace_elapsed": trace_elapsed,
            "trace_collapsed": trace_collapsed,
            "trace_context_collapsed": trace_context_collapsed,
            "trace_intent": trace_intent,
            "trace_plan": trace_plan,
            "trace_skills": trace_skills,
            "trace_mcp": trace_mcp,
        }
        self.chat_agent_traces = trace_map

    def toggle_chat_context(self, message_id: str) -> None:
        trace_map = dict(getattr(self, "chat_agent_traces", {}))
        if message_id in trace_map:
            current = dict(trace_map[message_id])
            current["trace_context_collapsed"] = "0" if current.get("trace_context_collapsed") == "1" else "1"
            trace_map[message_id] = current
            self.chat_agent_traces = trace_map

        updated: list[dict[str, str]] = []
        for item in self.chat_messages:
            if item.get("id") != message_id:
                updated.append(item)
                continue

            next_item = dict(item)
            next_item["trace_context_collapsed"] = "0" if next_item.get("trace_context_collapsed") == "1" else "1"
            updated.append(next_item)
        self.chat_messages = updated

    def toggle_chat_trace(self, message_id: str) -> None:
        trace_map = dict(getattr(self, "chat_agent_traces", {}))
        if message_id in trace_map:
            current = dict(trace_map[message_id])
            current["trace_collapsed"] = "0" if current.get("trace_collapsed") == "1" else "1"
            trace_map[message_id] = current
            self.chat_agent_traces = trace_map

        updated: list[dict[str, str]] = []
        for item in self.chat_messages:
            if item.get("id") != message_id:
                updated.append(item)
                continue

            next_item = dict(item)
            next_item["trace_collapsed"] = "0" if next_item.get("trace_collapsed") == "1" else "1"
            updated.append(next_item)
        self.chat_messages = updated

    async def _generate_assistant_reply(
        self,
        session: Any,
        assistant_message: dict[str, str],
        mode_override: str | None = None,
        plan_only_override: bool | None = None,
    ):
        async with self:
            active_mode = normalize_chat_mode(mode_override or self.chat_mode)
            active_plan_only = bool(plan_only_override) if plan_only_override is not None else (self.chat_plan_only == "enabled")
            stopped_label = "已终止本次回复" if self.ui_language == "zh" else "Response cancelled"
        if active_mode == "agent":
            heartbeat = 0
            started_at = perf_counter()
            reason_lines: list[str] = []
            function_call_lines: list[str] = []
            observation_lines: list[str] = []
            answer_text = ""
            agent_context_payload: dict[str, Any] = {}
            context_intent = ""
            context_plan = ""
            context_skills = ""
            context_mcp = ""
            stream_iter = stream_agent_events(
                _chat_client(),
                session.messages,
                tools=build_default_tools(),
                plan_only=active_plan_only,
            ).__aiter__()

            def _current_trace_sections() -> tuple[str, str, str]:
                reason_text = "\n\n".join([line for line in reason_lines if line.strip()]).strip()
                function_text = "\n".join([line for line in function_call_lines if line.strip()]).strip()
                observation_text = "\n\n".join([line for line in observation_lines if line.strip()]).strip()
                return reason_text, function_text, observation_text

            while True:
                async with self:
                    stop_requested = self.chat_stop_requested
                    heartbeat_enabled = self.heartbeat_enabled == "enabled"

                if stop_requested:
                    break

                try:
                    chunk = await asyncio.wait_for(stream_iter.__anext__(), timeout=0.35)
                except StopAsyncIteration:
                    break
                except (TimeoutError, asyncio.TimeoutError):
                    if not heartbeat_enabled:
                        continue
                    heartbeat = (heartbeat % 3) + 1
                    elapsed_label = _format_elapsed_label(perf_counter() - started_at, self.ui_language)
                    reason_text, function_text, observation_text = _current_trace_sections()
                    current_answer = answer_text.strip()
                    async with self:
                        self._replace_chat_message(
                            assistant_message["id"],
                            current_answer,
                            "streaming" if current_answer else "pending",
                            trace_reason=reason_text,
                            trace_function_call=function_text,
                            trace_observation=observation_text,
                            trace_elapsed=elapsed_label,
                            trace_collapsed="1",
                            trace_context_collapsed="0",
                            trace_intent=context_intent,
                            trace_plan=context_plan,
                            trace_skills=context_skills,
                            trace_mcp=context_mcp,
                        )
                    yield
                    continue

                event_type = ""
                payload: dict[str, Any] = {}
                legacy_chunk = ""

                if isinstance(chunk, dict):
                    event_type = str(chunk.get("type", "")).strip().lower()
                    payload = chunk.get("payload") if isinstance(chunk.get("payload"), dict) else {}
                elif isinstance(chunk, str):
                    legacy_chunk = chunk.strip()
                else:
                    continue

                if legacy_chunk:
                    text = legacy_chunk
                    if "Thought:" in text:
                        content = text.split("Thought:", 1)[1].strip()
                        if content:
                            reason_lines.append(content)
                    elif "Function Call:" in text:
                        content = text.split("Function Call:", 1)[1].strip()
                        if content:
                            function_call_lines.append(content)
                    elif "Observation:" in text:
                        content = text.split("Observation:", 1)[1].strip()
                        if content:
                            observation_lines.append(content)
                    elif not text.startswith("Starting task:"):
                        answer_text = f"{answer_text}\n{text}".strip() if answer_text else text

                    current_answer = answer_text.strip()
                    elapsed_label = _format_elapsed_label(perf_counter() - started_at, self.ui_language)
                    status = "streaming" if current_answer else "pending"
                    display_content = current_answer
                    reason_text, function_text, observation_text = _current_trace_sections()

                    async with self:
                        self._replace_chat_message(
                            assistant_message["id"],
                            display_content,
                            status,
                            trace_reason=reason_text,
                            trace_function_call=function_text,
                            trace_observation=observation_text,
                            trace_elapsed=elapsed_label,
                            trace_collapsed="1",
                            trace_context_collapsed="0",
                            trace_intent=context_intent,
                            trace_plan=context_plan,
                            trace_skills=context_skills,
                            trace_mcp=context_mcp,
                        )
                    yield
                    continue

                if event_type == "agent_context":
                    agent_context_payload = dict(payload)
                    next_intent, next_plan, next_skills, next_mcp = _agent_context_trace_fields(agent_context_payload)
                    for intent_piece in _chunk_text(next_intent, chunk_size=28):
                        context_intent += intent_piece
                        elapsed_label = _format_elapsed_label(perf_counter() - started_at, self.ui_language)
                        reason_text, function_text, observation_text = _current_trace_sections()
                        current_answer = answer_text.strip()
                        async with self:
                            self._replace_chat_message(
                                assistant_message["id"],
                                current_answer,
                                "streaming" if current_answer else "pending",
                                trace_reason=reason_text,
                                trace_function_call=function_text,
                                trace_observation=observation_text,
                                trace_elapsed=elapsed_label,
                                trace_collapsed="1",
                                trace_context_collapsed="0",
                                trace_intent=context_intent,
                                trace_plan=context_plan,
                                trace_skills=context_skills,
                                trace_mcp=context_mcp,
                            )
                        yield
                        await asyncio.sleep(0.01)
                    for plan_piece in _chunk_text(next_plan, chunk_size=28):
                        context_plan += plan_piece
                        elapsed_label = _format_elapsed_label(perf_counter() - started_at, self.ui_language)
                        reason_text, function_text, observation_text = _current_trace_sections()
                        current_answer = answer_text.strip()
                        async with self:
                            self._replace_chat_message(
                                assistant_message["id"],
                                current_answer,
                                "streaming" if current_answer else "pending",
                                trace_reason=reason_text,
                                trace_function_call=function_text,
                                trace_observation=observation_text,
                                trace_elapsed=elapsed_label,
                                trace_collapsed="1",
                                trace_context_collapsed="0",
                                trace_intent=context_intent,
                                trace_plan=context_plan,
                                trace_skills=context_skills,
                                trace_mcp=context_mcp,
                            )
                        yield
                        await asyncio.sleep(0.01)
                    for skills_piece in _chunk_text(next_skills, chunk_size=28):
                        context_skills += skills_piece
                        elapsed_label = _format_elapsed_label(perf_counter() - started_at, self.ui_language)
                        reason_text, function_text, observation_text = _current_trace_sections()
                        current_answer = answer_text.strip()
                        async with self:
                            self._replace_chat_message(
                                assistant_message["id"],
                                current_answer,
                                "streaming" if current_answer else "pending",
                                trace_reason=reason_text,
                                trace_function_call=function_text,
                                trace_observation=observation_text,
                                trace_elapsed=elapsed_label,
                                trace_collapsed="1",
                                trace_context_collapsed="0",
                                trace_intent=context_intent,
                                trace_plan=context_plan,
                                trace_skills=context_skills,
                                trace_mcp=context_mcp,
                            )
                        yield
                        await asyncio.sleep(0.01)
                    for mcp_piece in _chunk_text(next_mcp, chunk_size=28):
                        context_mcp += mcp_piece
                        elapsed_label = _format_elapsed_label(perf_counter() - started_at, self.ui_language)
                        reason_text, function_text, observation_text = _current_trace_sections()
                        current_answer = answer_text.strip()
                        async with self:
                            self._replace_chat_message(
                                assistant_message["id"],
                                current_answer,
                                "streaming" if current_answer else "pending",
                                trace_reason=reason_text,
                                trace_function_call=function_text,
                                trace_observation=observation_text,
                                trace_elapsed=elapsed_label,
                                trace_collapsed="1",
                                trace_context_collapsed="0",
                                trace_intent=context_intent,
                                trace_plan=context_plan,
                                trace_skills=context_skills,
                                trace_mcp=context_mcp,
                            )
                        yield
                        await asyncio.sleep(0.01)
                    context_intent, context_plan, context_skills, context_mcp = (
                        next_intent,
                        next_plan,
                        next_skills,
                        next_mcp,
                    )
                elif event_type == "reason":
                    content = str(payload.get("content", "")).strip()
                    if content:
                        reason_lines.append(content)
                elif event_type == "function_call":
                    line = _format_trace_function_call(payload)
                    if line:
                        function_call_lines.append(line)
                elif event_type == "observation":
                    line = _format_trace_observation(payload)
                    if line:
                        observation_lines.append(line)
                elif event_type == "warning":
                    warning_message = str(payload.get("message", "")).strip()
                    if warning_message:
                        observation_lines.append(f"Warning: {warning_message}")
                elif event_type in {"chunk", "message"}:
                    answer_delta = str(payload.get("content", "")).strip()
                    if answer_delta:
                        answer_text = f"{answer_text}\n{answer_delta}".strip() if answer_text else answer_delta
                elif event_type in {"final_answer", "done"}:
                    answer_value = str(
                        payload.get("answer")
                        or payload.get("message")
                        or payload.get("content")
                        or ""
                    ).strip()
                    if answer_value:
                        if answer_text and answer_value.startswith(answer_text):
                            for piece in _chunk_text(answer_value[len(answer_text) :], chunk_size=20):
                                answer_text += piece
                                elapsed_label = _format_elapsed_label(perf_counter() - started_at, self.ui_language)
                                reason_text, function_text, observation_text = _current_trace_sections()
                                async with self:
                                    self._replace_chat_message(
                                        assistant_message["id"],
                                        answer_text,
                                        "streaming",
                                        trace_reason=reason_text,
                                        trace_function_call=function_text,
                                        trace_observation=observation_text,
                                        trace_elapsed=elapsed_label,
                                        trace_collapsed="1",
                                        trace_context_collapsed="0",
                                        trace_intent=context_intent,
                                        trace_plan=context_plan,
                                        trace_skills=context_skills,
                                        trace_mcp=context_mcp,
                                    )
                                yield
                                await asyncio.sleep(0.012)
                        elif not answer_text:
                            for piece in _chunk_text(answer_value, chunk_size=20):
                                answer_text += piece
                                elapsed_label = _format_elapsed_label(perf_counter() - started_at, self.ui_language)
                                reason_text, function_text, observation_text = _current_trace_sections()
                                async with self:
                                    self._replace_chat_message(
                                        assistant_message["id"],
                                        answer_text,
                                        "streaming",
                                        trace_reason=reason_text,
                                        trace_function_call=function_text,
                                        trace_observation=observation_text,
                                        trace_elapsed=elapsed_label,
                                        trace_collapsed="1",
                                        trace_context_collapsed="0",
                                        trace_intent=context_intent,
                                        trace_plan=context_plan,
                                        trace_skills=context_skills,
                                        trace_mcp=context_mcp,
                                    )
                                yield
                                await asyncio.sleep(0.012)
                        else:
                            answer_text = answer_value

                current_answer = answer_text.strip()
                elapsed_label = _format_elapsed_label(perf_counter() - started_at, self.ui_language)
                status = "streaming" if current_answer else "pending"
                display_content = current_answer
                reason_text, function_text, observation_text = _current_trace_sections()

                async with self:
                    self._replace_chat_message(
                        assistant_message["id"],
                        display_content,
                        status,
                        trace_reason=reason_text,
                        trace_function_call=function_text,
                        trace_observation=observation_text,
                        trace_elapsed=elapsed_label,
                        trace_collapsed="1",
                        trace_context_collapsed="0",
                        trace_intent=context_intent,
                        trace_plan=context_plan,
                        trace_skills=context_skills,
                        trace_mcp=context_mcp,
                    )
                yield

            with contextlib.suppress(Exception):
                await stream_iter.aclose()

            async with self:
                stop_requested = self.chat_stop_requested

            if stop_requested:
                final_answer = answer_text.strip() or stopped_label
            else:
                final_answer = answer_text.strip()
            if not final_answer:
                # Fallback once with non-stream Agent call for providers that may
                # intermittently drop streaming final content.
                with contextlib.suppress(Exception):
                    fallback_result = await complete_agent_response(
                        _chat_client(),
                        session.messages,
                        tools=build_default_tools(),
                        plan_only=active_plan_only,
                    )
                    fallback_answer = str(fallback_result.answer or "").strip()
                    if fallback_answer:
                        final_answer = fallback_answer
                    if not agent_context_payload and isinstance(fallback_result.agent_context, dict):
                        agent_context_payload = dict(fallback_result.agent_context)
                        context_intent, context_plan, context_skills, context_mcp = _agent_context_trace_fields(
                            agent_context_payload
                        )
            if not final_answer:
                has_trace = bool(reason_lines or function_call_lines or observation_lines)
                if has_trace:
                    final_answer = (
                        "Agent 未产出最终答案，请查看中间过程后重试。"
                        if self.ui_language == "zh"
                        else "Agent did not produce a final answer. Please review the trace and retry."
                    )
                else:
                    final_answer = "模型未返回内容。" if self.ui_language == "zh" else "The model returned no content."

            session.messages.append(Message(role="assistant", content=final_answer))
            persisted_message_id = _session_message_id(session.messages[-1])
            user_message = _extract_latest_user_message(session.messages[:-1])

            elapsed_label = _format_elapsed_label(perf_counter() - started_at, self.ui_language)
            final_reason, final_function_call, final_observation = _current_trace_sections()
            _append_chat_agent_run_metadata(
                session,
                assistant_message_id=persisted_message_id,
                user_message=user_message,
                assistant_message=final_answer,
                plan_only=active_plan_only,
                agent_context=agent_context_payload,
                trace_reason=final_reason,
                trace_function_call=final_function_call,
                trace_observation=final_observation,
                trace_elapsed=elapsed_label,
                trace_collapsed="1",
                trace_context_collapsed="1",
                trace_intent=context_intent,
                trace_plan=context_plan,
                trace_skills=context_skills,
                trace_mcp=context_mcp,
            )
            await _session_store().update(session)
            with contextlib.suppress(Exception):
                await _persist_chat_agent_task_snapshot(
                    session_id=session.id,
                    user_message=user_message,
                    assistant_message=final_answer,
                    plan_only=active_plan_only,
                    agent_context=agent_context_payload,
                )
            async with self:
                if final_reason or final_function_call or final_observation:
                    self._remember_chat_trace(
                        persisted_message_id,
                        trace_reason=final_reason,
                        trace_function_call=final_function_call,
                        trace_observation=final_observation,
                        trace_elapsed=elapsed_label,
                        trace_collapsed="1",
                        trace_context_collapsed="1",
                        trace_intent=context_intent,
                        trace_plan=context_plan,
                        trace_skills=context_skills,
                        trace_mcp=context_mcp,
                    )
                self._replace_chat_message(
                    assistant_message["id"],
                    final_answer,
                    "done",
                    trace_reason=final_reason,
                    trace_function_call=final_function_call,
                    trace_observation=final_observation,
                    trace_elapsed=elapsed_label,
                    trace_collapsed="1",
                    trace_context_collapsed="1",
                    trace_intent=context_intent,
                    trace_plan=context_plan,
                    trace_skills=context_skills,
                    trace_mcp=context_mcp,
                )
                await self._refresh_sessions_only()
            yield
            return

        assembled = ""
        stream_failed = False
        stream_error_detail = ""
        fallback_error_detail = ""
        ask_tools = build_default_tools()
        stream_iter = stream_chat_response(_chat_client(), session.messages, ask_tools).__aiter__()

        while True:
            async with self:
                stop_requested = self.chat_stop_requested

            if stop_requested:
                break

            try:
                chunk = await asyncio.wait_for(stream_iter.__anext__(), timeout=0.35)
            except StopAsyncIteration:
                break
            except (TimeoutError, asyncio.TimeoutError):
                async with self:
                    self._replace_chat_message(assistant_message["id"], assembled, "streaming" if assembled else "pending")
                yield
                continue
            except Exception as exc:
                stream_failed = True
                stream_error_detail = _format_exception_detail("ask_stream", exc)
                break

            piece = str(chunk or "")
            if not piece:
                continue

            for delta in _chunk_text(piece, chunk_size=20):
                assembled += delta
                async with self:
                    self._replace_chat_message(assistant_message["id"], assembled, "streaming")
                yield
                await asyncio.sleep(0.01)

        with contextlib.suppress(Exception):
            await stream_iter.aclose()

        fallback_answer = ""
        if not assembled or stream_failed:
            try:
                fallback_answer = str(await complete_chat_response(_chat_client(), session.messages, ask_tools) or "").strip()
            except Exception as exc:
                fallback_error_detail = _format_exception_detail("ask_fallback", exc)

            if fallback_answer and not assembled:
                for piece in _chunk_text(fallback_answer, chunk_size=20):
                    async with self:
                        stop_requested = self.chat_stop_requested
                    if stop_requested:
                        break
                    assembled += piece
                    async with self:
                        self._replace_chat_message(assistant_message["id"], assembled, "streaming")
                    yield
                    await asyncio.sleep(0.012)
            elif fallback_answer and assembled and fallback_answer.startswith(assembled):
                for piece in _chunk_text(fallback_answer[len(assembled) :], chunk_size=20):
                    async with self:
                        stop_requested = self.chat_stop_requested
                    if stop_requested:
                        break
                    assembled += piece
                    async with self:
                        self._replace_chat_message(assistant_message["id"], assembled, "streaming")
                    yield
                    await asyncio.sleep(0.012)

        if not assembled:
            assembled = fallback_answer
        if not assembled:
            if stream_error_detail or fallback_error_detail:
                assembled = (
                    "模型未返回内容，请展开 Agent Context 查看错误详情。"
                    if self.ui_language == "zh"
                    else "The model returned no content. Expand Agent Context for detailed errors."
                )
            else:
                assembled = "模型未返回内容。" if self.ui_language == "zh" else "The model returned no content."

        async with self:
            stop_requested = self.chat_stop_requested

        final_answer = assembled.strip()
        if stop_requested and final_answer:
            final_answer = final_answer.strip()
        if not final_answer:
            final_answer = stopped_label

        session.messages.append(Message(role="assistant", content=final_answer))
        await _session_store().update(session)

        ask_trace_reason = ""
        ask_trace_observation = ""
        ask_context_intent = ""
        ask_context_plan = ""
        ask_context_collapsed = "1"
        if stream_error_detail or fallback_error_detail:
            ask_trace_reason = (
                "Ask 模式流式链路出现异常，已尝试回退补偿。"
                if self.ui_language == "zh"
                else "Ask-mode streaming pipeline failed; fallback recovery was attempted."
            )
            detail_rows = [row for row in [stream_error_detail, fallback_error_detail] if row.strip()]
            ask_trace_observation = "\n\n".join(detail_rows).strip()
            ask_context_intent = "模型调用错误" if self.ui_language == "zh" else "Model invocation error"
            ask_context_plan = ask_trace_observation
            ask_context_collapsed = "0"

        async with self:
            self._replace_chat_message(
                assistant_message["id"],
                final_answer,
                "done",
                trace_reason=ask_trace_reason,
                trace_observation=ask_trace_observation,
                trace_collapsed="1",
                trace_context_collapsed=ask_context_collapsed,
                trace_intent=ask_context_intent,
                trace_plan=ask_context_plan,
            )
            await self._refresh_sessions_only()
        yield

    @rx.event(background=True)
    async def resend_edited_message(self):
        async with self:
            prompt = self.chat_edit_prompt.strip()
            selected_session_id = self.selected_session_id
            chat_edit_message_id = self.chat_edit_message_id
            mode_snapshot = self.chat_mode
            plan_only_snapshot = self.chat_plan_only == "enabled"
            runtime_model_payload = self._collect_model_payload(
                profile_id=self.active_model_profile_id.strip() or "profile-default",
                profile_name=self.model_profile_name,
            )
            if not prompt or self.busy or not selected_session_id or not chat_edit_message_id:
                return

            self.busy = True
            self.chat_stop_requested = False
            self.error_message = ""

        if runtime_model_payload is not None:
            _apply_runtime_model_config(runtime_model_payload)
            _clear_agent_cache()

        try:
            session = await _session_store().get(selected_session_id)
            target_index = next(
                (
                    index
                    for index, message in enumerate(session.messages)
                    if message.role == "user" and _session_message_id(message) == chat_edit_message_id
                ),
                None,
            )
            if target_index is None:
                return

            session.messages = session.messages[: target_index + 1]
            session.messages[target_index].content = prompt
            local_command_result = self._resolve_local_chat_command(prompt)
            if local_command_result is not None:
                local_answer = str(local_command_result.get("content", "")).strip()
                session.messages.append(Message(role="assistant", content=local_answer))
                await _session_store().update(session)

                assistant_message = _chat_message(
                    "assistant",
                    local_answer,
                    status="done",
                    trace_context_collapsed="1",
                    trace_intent=str(local_command_result.get("trace_intent", "")),
                    trace_plan=str(local_command_result.get("trace_plan", "")),
                )
                async with self:
                    self.chat_edit_message_id = ""
                    self.chat_edit_prompt = ""
                    await self._refresh_sessions_only()
                    self.chat_messages = [*self.chat_messages, assistant_message]
                yield
                return

            settings = get_settings()
            await _compress_session_context_if_needed(
                session,
                _chat_client(),
                token_threshold=int(settings.chat_context_compress_threshold),
                keep_recent_messages=int(settings.chat_context_keep_recent_messages),
                language=self.ui_language,
            )
            await _session_store().update(session)

            async with self:
                self.chat_edit_message_id = ""
                self.chat_edit_prompt = ""
                await self._refresh_sessions_only()

            assistant_message = _chat_message(
                "assistant",
                "",
                status="pending",
                trace_context_collapsed="0" if mode_snapshot == "agent" else "1",
                trace_intent=(
                    ("正在构建 Agent Context..." if self.ui_language == "zh" else "Building Agent Context...")
                    if mode_snapshot == "agent"
                    else ""
                ),
            )
            async with self:
                self.chat_messages = [*self.chat_messages, assistant_message]
            yield

            async for _ in self._generate_assistant_reply(
                session,
                assistant_message,
                mode_override=mode_snapshot,
                plan_only_override=plan_only_snapshot,
            ):
                yield
        except Exception as exc:
            async with self:
                self.error_message = f"对话失败: {exc}"
                failure_text = f"对话失败: {exc}"
                detail_text = _format_exception_detail("resend_edited_message", exc, include_traceback=True)
                if self.chat_messages and self.chat_messages[-1].get("role") == "assistant":
                    self._replace_chat_message(
                        self.chat_messages[-1].get("id", ""),
                        failure_text,
                        "error",
                        trace_reason="resend_edited_message failed",
                        trace_observation=detail_text,
                        trace_collapsed="0",
                        trace_context_collapsed="0",
                        trace_intent="运行异常" if self.ui_language == "zh" else "Runtime error",
                        trace_plan=detail_text,
                    )
            yield self._toast_error(f"对话失败: {exc}")
        finally:
            async with self:
                self.busy = False
                self.chat_stop_requested = False

    @rx.event(background=True)
    async def send_chat(self):
        async with self:
            prompt = self.chat_prompt.strip()
            selected_session_id = self.selected_session_id
            ui_language = self.ui_language
            mode_snapshot = self.chat_mode
            plan_only_snapshot = self.chat_plan_only == "enabled"
            runtime_model_payload = self._collect_model_payload(
                profile_id=self.active_model_profile_id.strip() or "profile-default",
                profile_name=self.model_profile_name,
            )
            if not prompt or self.busy:
                return

            self.busy = True
            self.chat_stop_requested = False
            self.error_message = ""

        if runtime_model_payload is not None:
            _apply_runtime_model_config(runtime_model_payload)
            _clear_agent_cache()

        try:
            if not selected_session_id:
                created = await _session_store().create(name="新对话" if ui_language == "zh" else "New Chat")
                selected_session_id = created.id
                async with self:
                    self.selected_session_id = created.id
                    self.selected_session_name = created.name

            session = await _session_store().get(selected_session_id)
            session.messages.append(Message(role="user", content=prompt))
            local_command_result = self._resolve_local_chat_command(prompt)
            if local_command_result is not None:
                local_answer = str(local_command_result.get("content", "")).strip()
                session.messages.append(Message(role="assistant", content=local_answer))
                await _session_store().update(session)

                assistant_message = _chat_message(
                    "assistant",
                    local_answer,
                    status="done",
                    trace_context_collapsed="1",
                    trace_intent=str(local_command_result.get("trace_intent", "")),
                    trace_plan=str(local_command_result.get("trace_plan", "")),
                )
                async with self:
                    self.chat_prompt = ""
                    self.chat_edit_message_id = ""
                    self.chat_edit_prompt = ""
                    await self._refresh_sessions_only()
                    self.chat_messages = [*self.chat_messages, assistant_message]
                yield
                return

            settings = get_settings()
            await _compress_session_context_if_needed(
                session,
                _chat_client(),
                token_threshold=int(settings.chat_context_compress_threshold),
                keep_recent_messages=int(settings.chat_context_keep_recent_messages),
                language=self.ui_language,
            )
            await _session_store().update(session)

            assistant_message = _chat_message(
                "assistant",
                "",
                status="pending",
                trace_context_collapsed="0" if mode_snapshot == "agent" else "1",
                trace_intent=(
                    ("正在构建 Agent Context..." if self.ui_language == "zh" else "Building Agent Context...")
                    if mode_snapshot == "agent"
                    else ""
                ),
            )
            async with self:
                self.chat_prompt = ""
                self.chat_edit_message_id = ""
                self.chat_edit_prompt = ""
                await self._refresh_sessions_only()
                self.chat_messages = [*self.chat_messages, assistant_message]
            yield

            async for _ in self._generate_assistant_reply(
                session,
                assistant_message,
                mode_override=mode_snapshot,
                plan_only_override=plan_only_snapshot,
            ):
                yield
        except Exception as exc:
            async with self:
                self.error_message = f"对话失败: {exc}"
                failure_text = f"对话失败: {exc}"
                detail_text = _format_exception_detail("send_chat", exc, include_traceback=True)
                if self.chat_messages and self.chat_messages[-1].get("role") == "assistant":
                    self._replace_chat_message(
                        self.chat_messages[-1].get("id", ""),
                        failure_text,
                        "error",
                        trace_reason="send_chat failed",
                        trace_observation=detail_text,
                        trace_collapsed="0",
                        trace_context_collapsed="0",
                        trace_intent="运行异常" if self.ui_language == "zh" else "Runtime error",
                        trace_plan=detail_text,
                    )
            yield self._toast_error(f"对话失败: {exc}")
        finally:
            async with self:
                self.busy = False
                self.chat_stop_requested = False

    @rx.event(background=True)
    async def run_task(self):
        target_task_id = ""
        async with self:
            task_text = self.task_prompt.strip()
            ui_language = self.ui_language
            selected_task_id = self.selected_task_id
            if not task_text or self.task_busy:
                return

            self.task_busy = True
            self.task_stop_requested = False
            self.error_message = ""
            self.task_runtime_cursor = "..."
            self.task_runtime_phase = "任务已提交" if ui_language == "zh" else "Task submitted"
            self.task_runtime_steps = [
                {
                    "id": "task-submit",
                    "title": "任务提交" if ui_language == "zh" else "Submission",
                    "status": "done",
                    "detail": task_text,
                    "collapsed": "0",
                },
                {
                    "id": "task-plan",
                    "title": "规划与拆解" if ui_language == "zh" else "Planning",
                    "status": "running",
                    "detail": "模型正在构建执行计划..." if ui_language == "zh" else "Model is building a plan...",
                    "collapsed": "0",
                },
                {
                    "id": "task-exec",
                    "title": "实现与执行" if ui_language == "zh" else "Execution",
                    "status": "pending",
                    "detail": "等待进入实现阶段" if ui_language == "zh" else "Waiting for execution stage",
                    "collapsed": "0",
                },
                {
                    "id": "task-review",
                    "title": "复盘与总结" if ui_language == "zh" else "Review",
                    "status": "pending",
                    "detail": "等待结果汇总" if ui_language == "zh" else "Waiting for summary",
                    "collapsed": "0",
                },
            ]

        try:
            task_record, execution_input = await _prepare_task_record_for_run(
                _task_store(),
                selected_task_id,
                task_text,
                ui_language,
            )
            target_task_id = task_record.id
            await _task_store().set_status(
                task_record.id,
                "running",
                plan=[],
                code_artifacts=[],
                review_result={},
                summary="",
                error="",
            )

            async with self:
                self.selected_task_id = task_record.id
                self.task_prompt = ""
                await self._refresh_tasks_only()
            yield

            task_future = asyncio.create_task(_orchestrator().run(execution_input))
            heartbeat_frames = [".", "..", "...", "...."]
            tick = 0

            while not task_future.done():
                async with self:
                    stop_requested = self.task_stop_requested
                    heartbeat_enabled = self.heartbeat_enabled == "enabled"

                if stop_requested:
                    task_future.cancel()
                    try:
                        await task_future
                    except asyncio.CancelledError:
                        pass

                    await _task_store().set_status(
                        task_record.id,
                        "failed",
                        error="任务已由用户终止" if ui_language == "zh" else "Task terminated by user",
                    )

                    async with self:
                        self.task_runtime_phase = "任务已终止" if ui_language == "zh" else "Task stopped"
                        self.task_runtime_cursor = "..."
                        next_steps: list[dict[str, str]] = []
                        for step in self.task_runtime_steps:
                            row = dict(step)
                            if row["id"] in {"task-plan", "task-exec", "task-review"}:
                                row["status"] = "failed"
                            if row["id"] == "task-review":
                                row["detail"] = "任务已被手动终止" if ui_language == "zh" else "Task stopped manually"
                            next_steps.append(row)
                        self.task_runtime_steps = next_steps
                        await self._refresh_tasks_only()
                    yield self._toast_error("任务已终止" if ui_language == "zh" else "Task stopped")
                    return

                tick += 1
                elapsed = tick * 0.35
                phase_text = "模型处理中" if ui_language == "zh" else "Model is processing"
                active_step = "task-plan"
                if elapsed >= 2.0:
                    active_step = "task-exec"
                    phase_text = "执行中" if ui_language == "zh" else "Executing"
                if elapsed >= 4.0:
                    active_step = "task-review"
                    phase_text = "总结中" if ui_language == "zh" else "Summarizing"

                async with self:
                    self.task_runtime_cursor = heartbeat_frames[tick % len(heartbeat_frames)] if heartbeat_enabled else "·"
                    self.task_runtime_phase = phase_text

                    next_steps: list[dict[str, str]] = []
                    for step in self.task_runtime_steps:
                        row = dict(step)
                        if row["id"] == active_step:
                            row["status"] = "running"
                            suffix = "." * ((tick % 3) + 1) if heartbeat_enabled else ""
                            row["detail"] = (
                                f"{phase_text}{suffix}"
                                if ui_language == "zh"
                                else f"{phase_text}{suffix}"
                            )
                        elif row["id"] in {"task-plan", "task-exec", "task-review"}:
                            if row["status"] != "done":
                                row["status"] = "pending"
                        next_steps.append(row)
                    self.task_runtime_steps = next_steps
                yield
                await asyncio.sleep(0.35)

            result = await task_future
            updated = await _task_store().set_status(
                task_record.id,
                "completed" if result.success else "failed",
                plan=result.plan,
                code_artifacts=result.code_artifacts,
                review_result=result.review_result,
                execution_results=result.execution_results,
                task_state=result.task_state,
                observations=result.observations,
                reflections=result.reflections,
                errors=result.errors,
                summary=result.summary,
                error=result.error,
            )

            with contextlib.suppress(Exception):
                _task_memory_store().record(
                    session_id=str(updated.session_id or "").strip(),
                    task_id=updated.id,
                    source="orchestrator",
                    status="completed" if result.success else "failed",
                    user_request=execution_input[:4000],
                    outcome_summary=(result.summary or result.error or "")[:4000],
                    process_summary=_build_memory_process_summary(
                        plan=result.plan,
                        execution_results=result.execution_results,
                    )[:4000],
                    tags=["task-center"],
                    metadata={
                        "success": bool(result.success),
                        "artifact_count": len(result.code_artifacts),
                    },
                )

            async with self:
                self.selected_task_id = updated.id
                self.task_runtime_phase = "任务执行完成" if result.success else ("任务执行失败" if ui_language == "zh" else "Task failed")
                self.task_runtime_cursor = "✓" if result.success else "!"

                plan_detail = "\n".join([f"{idx + 1}. {step}" for idx, step in enumerate(result.plan)])
                if not plan_detail:
                    plan_detail = "未返回计划" if ui_language == "zh" else "No plan returned"

                exec_detail = (
                    f"生成工件 {len(result.code_artifacts)} 个"
                    if ui_language == "zh"
                    else f"Generated {len(result.code_artifacts)} artifact(s)"
                )
                review_detail = result.summary or ("任务已完成" if result.success else (result.error or "任务失败"))

                self.task_runtime_steps = [
                    {
                        "id": "task-submit",
                        "title": "任务提交" if ui_language == "zh" else "Submission",
                        "status": "done",
                        "detail": task_text,
                        "collapsed": "0",
                    },
                    {
                        "id": "task-plan",
                        "title": "规划与拆解" if ui_language == "zh" else "Planning",
                        "status": "done" if result.success else "failed",
                        "detail": plan_detail,
                        "collapsed": "0",
                    },
                    {
                        "id": "task-exec",
                        "title": "实现与执行" if ui_language == "zh" else "Execution",
                        "status": "done" if result.success else "failed",
                        "detail": exec_detail,
                        "collapsed": "0",
                    },
                    {
                        "id": "task-review",
                        "title": "复盘与总结" if ui_language == "zh" else "Review",
                        "status": "done" if result.success else "failed",
                        "detail": review_detail,
                        "collapsed": "0",
                    },
                ]

                await self._refresh_tasks_only()

            if result.success:
                yield self._toast_success("任务执行完成" if ui_language == "zh" else "Task completed")
            else:
                yield self._toast_error("任务执行失败" if ui_language == "zh" else "Task failed")
        except Exception as exc:
            if target_task_id:
                try:
                    await _task_store().set_status(target_task_id, "failed", error=str(exc))
                except Exception:
                    pass

            async with self:
                self.error_message = f"任务执行失败: {exc}"
                self.task_runtime_phase = "任务执行失败" if self.ui_language == "zh" else "Task failed"
                self.task_runtime_cursor = "!"
                next_steps: list[dict[str, str]] = []
                for step in self.task_runtime_steps:
                    row = dict(step)
                    if row["id"] in {"task-plan", "task-exec", "task-review"} and row["status"] != "done":
                        row["status"] = "failed"
                    if row["id"] == "task-review":
                        row["detail"] = str(exc)
                    next_steps.append(row)
                self.task_runtime_steps = next_steps
                await self._refresh_tasks_only()
            yield self._toast_error(f"任务执行失败: {exc}")
        finally:
            async with self:
                self.task_busy = False
                self.task_stop_requested = False

    async def upsert_mcp(self) -> rx.event.EventSpec | None:
        if not self.mcp_form_valid:
            self.error_message = self.mcp_form_hint
            return self._toast_error(self.mcp_form_hint)

        args = [item.strip() for item in self.mcp_args.split(",") if item.strip()]
        _mcp_registry().upsert(
            MCPServerConfig(
                name=self.mcp_name.strip(),
                transport=self.mcp_transport,
                command=self.mcp_command.strip(),
                args=args,
                description=self.mcp_description.strip(),
                enabled=self.mcp_enabled == "enabled",
            )
        )
        self.mcp_name = ""
        self.mcp_command = ""
        self.mcp_args = ""
        self.mcp_description = ""
        await self.refresh_data()
        return self._toast_success("MCP 配置已保存" if self.ui_language == "zh" else "MCP config saved")

    async def remove_mcp(self, name: str) -> rx.event.EventSpec | None:
        removed = False
        if _mcp_registry().remove(name):
            removed = True
        await self.refresh_data()
        if removed:
            return self._toast_success("MCP 配置已删除" if self.ui_language == "zh" else "MCP config removed")
        return None

    async def toggle_mcp_enabled(self, name: str) -> rx.event.EventSpec | None:
        target_name = str(name or "").strip()
        if not target_name:
            return None

        registry = _mcp_registry()
        target: MCPServerConfig | None = None
        for item in registry.load():
            if item.name == target_name:
                target = item
                break

        if target is None:
            return self._toast_error("MCP 未找到" if self.ui_language == "zh" else "MCP not found")

        target.enabled = not bool(target.enabled)
        registry.upsert(target)
        _clear_agent_cache()
        await self.refresh_data()

        if target.enabled:
            return self._toast_success("MCP 已启用，将暴露给模型" if self.ui_language == "zh" else "MCP enabled for model")
        return self._toast_success("MCP 已禁用，不再暴露给模型" if self.ui_language == "zh" else "MCP disabled for model")

    async def toggle_skill_enabled(self, path: str) -> rx.event.EventSpec | None:
        target_path = str(path or "").strip()
        if not target_path:
            return None

        rows = {str(item.path) for item in SkillRegistry().discover()}
        if target_path not in rows:
            return self._toast_error("Skill 未找到" if self.ui_language == "zh" else "Skill not found")

        store = _skill_toggle_store()
        current = store.is_enabled(target_path, default=True)
        store.set_enabled(target_path, not current)
        _clear_agent_cache()
        await self.refresh_data()

        if not current:
            return self._toast_success("Skill 已启用，将暴露给模型" if self.ui_language == "zh" else "Skill enabled for model")
        return self._toast_success("Skill 已禁用，不再暴露给模型" if self.ui_language == "zh" else "Skill disabled for model")

    async def handle_skill_archive_upload(self, files: list[rx.UploadFile]) -> rx.event.EventSpec | None:
        if not files:
            return self._toast_error("请选择 Skills 压缩包" if self.ui_language == "zh" else "Please select skill archives")

        installed: list[str] = []
        errors: list[str] = []

        settings = get_settings()
        skills_root = settings.data_dir / "skills" / "packages"
        skills_root.mkdir(parents=True, exist_ok=True)

        for upload in files:
            filename = str(getattr(upload, "name", "") or getattr(upload, "filename", "") or "skill.zip")
            try:
                data = await upload.read()
                result = install_skill_archive_bytes(filename, data, skills_dir=skills_root)
                installed.append(str(result.get("package_name", filename)))
            except Exception as exc:
                errors.append(f"{filename}: {exc}")

        await self.refresh_data()

        if installed and not errors:
            return self._toast_success(
                f"已安装 {len(installed)} 个 Skill 包" if self.ui_language == "zh" else f"Installed {len(installed)} skill package(s)"
            )
        if installed and errors:
            return self._toast_success(
                (
                    f"已安装 {len(installed)} 个 Skill 包，{len(errors)} 个文件不符合格式"
                    if self.ui_language == "zh"
                    else f"Installed {len(installed)} skill package(s), {len(errors)} invalid archive(s)"
                )
            )
        detail = errors[0] if errors else ("文件格式不对" if self.ui_language == "zh" else "Invalid archive format")
        return self._toast_error(detail)

    async def create_hook_rule(self) -> rx.event.EventSpec | None:
        if not self.hook_form_valid:
            self.error_message = self.hook_form_hint
            return self._toast_error(self.hook_form_hint)

        _hook_rule_store().upsert(
            HookRule(
                name=self.hook_name.strip(),
                event=HookEvent(self.hook_event),
                handler_type=self.hook_handler_type,
                handler_value=self.hook_handler_value.strip(),
                description=self.hook_description.strip(),
                enabled=self.hook_enabled == "enabled",
            )
        )
        self.hook_name = ""
        self.hook_handler_value = ""
        self.hook_description = ""
        await self.refresh_data()
        return self._toast_success("Hook 规则已创建" if self.ui_language == "zh" else "Hook rule created")

    async def remove_hook_rule(self, rule_id: str) -> rx.event.EventSpec | None:
        removed = False
        if _hook_rule_store().remove(rule_id):
            removed = True
        await self.refresh_data()
        if removed:
            return self._toast_success("Hook 规则已删除" if self.ui_language == "zh" else "Hook rule removed")
        return None

    async def create_policy_rule(self) -> rx.event.EventSpec | None:
        if not self.policy_name.strip():
            self.error_message = "策略名称不能为空"
            return self._toast_error("策略名称不能为空" if self.ui_language == "zh" else "Policy name is required")

        _policy_store().upsert(
            PolicyRule(
                name=self.policy_name.strip(),
                scope=self.policy_scope.strip() or "global",
                target=self.policy_target.strip() or "*",
                decision=self.policy_decision,
                description=self.policy_description.strip(),
                enabled=self.policy_enabled == "enabled",
            )
        )
        self.policy_name = ""
        self.policy_description = ""
        await self.refresh_data()
        return self._toast_success("策略规则已创建" if self.ui_language == "zh" else "Policy rule created")

    async def remove_policy_rule(self, rule_id: str) -> rx.event.EventSpec | None:
        removed = False
        if _policy_store().remove(rule_id):
            removed = True
        await self.refresh_data()
        if removed:
            return self._toast_success("策略规则已删除" if self.ui_language == "zh" else "Policy rule removed")
        return None

    async def refresh_audit(self) -> None:
        await self.refresh_data()

    async def add_custom_source(self) -> rx.event.EventSpec | None:
        if not self.source_form_valid:
            self.error_message = self.source_form_hint
            return self._toast_error(self.source_form_hint)

        tags = [item.strip() for item in self.source_tags.split(",") if item.strip()]
        _upsert_extension_source(
            {
                "name": self.source_name.strip(),
                "kind": self.source_kind,
                "url": self.source_url.strip(),
                "description": self.source_description.strip(),
                "tags": tags,
            }
        )
        self.source_name = ""
        self.source_url = ""
        self.source_description = ""
        self.source_tags = ""
        await self.refresh_data()
        return self._toast_success("扩展来源已保存" if self.ui_language == "zh" else "Source saved")

    async def remove_extension_source(self, url: str) -> rx.event.EventSpec | None:
        removed = False
        if _remove_extension_source(url):
            removed = True
        await self.refresh_data()
        if removed:
            return self._toast_success("扩展来源已删除" if self.ui_language == "zh" else "Source removed")
        return None

    async def add_source_from_market(self, item_id: str) -> rx.event.EventSpec | None:
        item = self._find_market_item(item_id)
        if not item:
            return None
        tags = [token.strip() for token in str(item.get("tags", "")).split(",") if token.strip()]
        _upsert_extension_source(
            {
                "name": item["name"],
                "kind": item["kind"],
                "url": item["url"],
                "description": item["description"],
                "tags": tags,
            }
        )
        await self.refresh_data()
        if self.error_message:
            return self._toast_error(self.error_message)
        return self._toast_success("市场来源已加入" if self.ui_language == "zh" else "Source added")

    async def install_skills_from_market(self, item_id: str) -> rx.event.EventSpec | None:
        item = self._find_market_item(item_id)
        if not item or item.get("kind") != "skill_site":
            return None
        return await self.install_skills_from_source(str(item.get("url", "")))

    async def install_mcp_from_market(self, item_id: str) -> rx.event.EventSpec | None:
        item = self._find_market_item(item_id)
        if not item or item.get("kind") != "mcp_site":
            return None
        return await self.install_mcp_from_source(str(item.get("url", "")))

    async def install_skills_from_source(self, source_url: str) -> rx.event.EventSpec | None:
        url = str(source_url or "").strip()
        if not _looks_like_http_url(url):
            return self._toast_error("Invalid source URL")

        try:
            outcome = await install_skills_from_remote(url)
        except Exception as exc:
            self.error_message = str(exc)
            return self._toast_error(f"Skill install failed: {exc}")

        await self.refresh_data()
        installed = outcome.get("installed", []) if isinstance(outcome, dict) else []
        errors = outcome.get("errors", []) if isinstance(outcome, dict) else []
        installed_count = len(installed) if isinstance(installed, list) else 0
        error_count = len(errors) if isinstance(errors, list) else 0
        if installed_count == 0:
            detail = "; ".join(str(item) for item in (errors[:2] if isinstance(errors, list) else []))
            return self._toast_error(f"No skills installed. {detail}".strip())
        if error_count:
            return self._toast_success(f"Installed {installed_count} skill(s), {error_count} failed")
        return self._toast_success(f"Installed {installed_count} skill(s)")

    async def run_clawhub_auto_install(self, dry_run: bool = True) -> rx.event.EventSpec | None:
        source_url = str(self.clawhub_source_url or "").strip() or "https://clawhub.ai"
        query = str(self.clawhub_query or "").strip()
        direct_slug = _resolve_clawhub_direct_query(query)
        if not _looks_like_http_url(source_url):
            return self._toast_error("ClawHub URL 无效" if self.ui_language == "zh" else "Invalid ClawHub URL")

        self.clawhub_panel_hint = ""

        selected_slug = ""
        selected_name = ""
        selected_summary = ""
        selected_version = ""
        selected_score = 0.0
        candidate_count = 0

        try:
            if query and direct_slug:
                selected_slug = direct_slug
                details = await get_clawhub_skill_details(source_url, slug=selected_slug)
                selected_name = str(details.get("name", ""))
                selected_summary = str(details.get("summary", ""))
                selected_version = str(details.get("version", ""))
            elif query:
                candidates = await search_clawhub_skills(
                    source_url,
                    query=query,
                    limit=8,
                    non_suspicious_only=True,
                    highlighted_only=False,
                )
                candidate_count = len(candidates)
                if not candidates:
                    return self._toast_error(
                        "未找到匹配的 ClawHub Skill" if self.ui_language == "zh" else "No matching ClawHub skill found"
                    )

                selected = _select_best_clawhub_candidate(candidates, query)
                selected_slug = resolve_clawhub_skill_slug(str(selected.get("slug", "")))
                if not selected_slug:
                    return self._toast_error(
                        "无法解析候选 Skill 标识" if self.ui_language == "zh" else "Unable to resolve selected skill slug"
                    )

                details = await get_clawhub_skill_details(source_url, slug=selected_slug)
                selected_name = str(details.get("name", selected.get("name", "")))
                selected_summary = str(details.get("summary", selected.get("summary", "")))
                selected_version = str(details.get("version", selected.get("version", "")))
                selected_score = _coerce_float(selected.get("score"), 0.0)
            else:
                selected_slug = resolve_clawhub_skill_slug(self.clawhub_selected_slug)
                if not selected_slug:
                    return self._toast_error(
                        "请先输入关键词再预览" if self.ui_language == "zh" else "Enter query and run dry preview first"
                    )
                details = await get_clawhub_skill_details(source_url, slug=selected_slug)
                selected_name = str(details.get("name", ""))
                selected_summary = str(details.get("summary", ""))
                selected_version = str(details.get("version", ""))

            self.clawhub_selected_slug = selected_slug
            self.clawhub_preview_query = query
            self.clawhub_preview_candidate_count = str(candidate_count) if candidate_count else ""
            self.clawhub_preview_name = selected_name or selected_slug
            self.clawhub_preview_version = selected_version or "unknown"
            self.clawhub_preview_score = f"{selected_score:.3f}" if selected_score > 0 else ""
            self.clawhub_preview_summary = selected_summary or "N/A"
            preview_lines = [
                f"Source: {source_url}",
                f"Query: {query or '(from selected slug)'}",
                f"Candidate Count: {candidate_count}",
                f"Selected: {selected_name or selected_slug}",
                f"Slug: {selected_slug}",
                f"Version: {selected_version or 'unknown'}",
                f"Score: {selected_score:.3f}",
                f"Summary: {selected_summary or 'N/A'}",
            ]

            if dry_run:
                self.clawhub_preview_package_name = ""
                self.clawhub_preview_install_dir = ""
                self.clawhub_preview_text = "\n".join(preview_lines)
                return self._toast_success(
                    "Dry run 完成，可点击确认安装"
                    if self.ui_language == "zh"
                    else "Dry run completed, ready to install"
                )

            install_result = await install_skill_from_clawhub(source_url, slug=selected_slug)
            install_dir = str(install_result.get("install_dir", ""))
            package_name = str(install_result.get("package_name", ""))
            preview_lines.extend(
                [
                    f"Installed Package: {package_name or 'unknown'}",
                    f"Install Dir: {install_dir or 'unknown'}",
                ]
            )
            self.clawhub_preview_package_name = package_name or "unknown"
            self.clawhub_preview_install_dir = install_dir or "unknown"
            self.clawhub_preview_text = "\n".join(preview_lines)
            await self.refresh_data()
            return self._toast_success(
                f"Skill 安装成功: {package_name or selected_slug}"
                if self.ui_language == "zh"
                else f"Skill installed: {package_name or selected_slug}"
            )
        except Exception as exc:
            self.error_message = str(exc)
            if "429" in self.error_message or "Rate limited by remote source" in self.error_message:
                self.clawhub_panel_hint = (
                    "搜索接口当前被限流。可直接在上方输入 slug:agent-browser 或粘贴 ClawHub 技能详情页链接，然后点击确认安装以绕过搜索。"
                    if self.ui_language == "zh"
                    else "Search is currently rate limited. Enter slug:agent-browser or paste a ClawHub skill detail URL above, then click Install to bypass search."
                )
            return self._toast_error(
                f"ClawHub 安装失败: {exc}" if self.ui_language == "zh" else f"ClawHub install failed: {exc}"
            )

    async def install_mcp_from_source(self, source_url: str) -> rx.event.EventSpec | None:
        url = str(source_url or "").strip()
        if not _looks_like_http_url(url):
            return self._toast_error("Invalid source URL")

        try:
            outcome = await install_mcp_from_remote(url)
        except Exception as exc:
            self.error_message = str(exc)
            return self._toast_error(f"MCP install failed: {exc}")

        await self.refresh_data()
        installed = outcome.get("installed", []) if isinstance(outcome, dict) else []
        errors = outcome.get("errors", []) if isinstance(outcome, dict) else []
        installed_count = len(installed) if isinstance(installed, list) else 0
        error_count = len(errors) if isinstance(errors, list) else 0
        if installed_count == 0:
            detail = "; ".join(str(item) for item in (errors[:2] if isinstance(errors, list) else []))
            return self._toast_error(f"No MCP servers installed. {detail}".strip())
        if error_count:
            return self._toast_success(f"Installed {installed_count} MCP server(s), {error_count} failed")
        return self._toast_success(f"Installed {installed_count} MCP server(s)")
    async def create_mcp_from_market(self, item_id: str) -> rx.event.EventSpec | None:
        item = self._find_market_item(item_id)
        if not item or item.get("kind") != "mcp_site":
            return None

        _mcp_registry().upsert(
            MCPServerConfig(
                name=item["name"],
                transport="http",
                command=item["url"],
                args=[],
                description=item["description"],
                enabled=True,
            )
        )
        await self.refresh_data()
        return self._toast_success("MCP 条目已创建" if self.ui_language == "zh" else "MCP entry created")

    async def add_mcp_template(self, template_id: str) -> rx.event.EventSpec | None:
        use_zh = self.ui_language == "zh"
        for item in MCP_TEMPLATE_CATALOG:
            if item["id"] != template_id:
                continue
            name = item.get("name_zh", item["name"]) if use_zh else item["name"]
            description = item.get("description_zh", item["description"]) if use_zh else item["description"]
            _mcp_registry().upsert(
                MCPServerConfig(
                    name=name,
                    transport=item["transport"],
                    command=item["command"],
                    args=item.get("args", []),
                    description=description,
                    enabled=True,
                )
            )
            await self.refresh_data()
            return self._toast_success("模板已添加到 MCP 注册表" if self.ui_language == "zh" else "Template added to MCP")
            break
        return None

    def _make_unique_profile_name(self, base_name: str) -> str:
        existing = {str(profile.get("name", "")).strip() for profile in self.model_profiles}
        candidate = base_name.strip() or ("新配置" if self.ui_language == "zh" else "New Profile")
        if candidate not in existing:
            return candidate
        index = 2
        while True:
            next_name = f"{candidate} {index}"
            if next_name not in existing:
                return next_name
            index += 1

    def _collect_model_payload(self, profile_id: str, profile_name: str) -> dict[str, Any] | None:
        try:
            temperature = float(self.llm_temperature)
            max_tokens = int(self.llm_max_tokens)
        except ValueError:
            return None

        temperature = max(0.0, min(2.0, temperature))
        max_tokens = max(128, max_tokens)

        provider = self.llm_provider.strip().lower()
        if provider not in MODEL_PROVIDER_OPTIONS:
            provider = "openai"

        return {
            "id": profile_id,
            "name": profile_name.strip() or ("默认配置" if self.ui_language == "zh" else "Default Profile"),
            "llm_provider": provider,
            "llm_model": self.llm_model.strip() or "gpt-4o-mini",
            "llm_base_url": self.llm_base_url.strip(),
            "llm_temperature": temperature,
            "llm_max_tokens": max_tokens,
            "llm_enable_thinking": self.llm_enable_thinking == "enabled",
            "persist_api_key": self.persist_api_key == "enabled",
            "llm_api_key": self.llm_api_key.strip() if self.persist_api_key == "enabled" else "",
        }

    async def switch_model_profile(self, profile_id: str) -> rx.event.EventSpec | None:
        profile = next((item for item in self.model_profiles if item.get("id") == profile_id), None)
        if profile is None:
            return None

        self.active_model_profile_id = str(profile.get("id", ""))
        self.model_profile_name = str(profile.get("name", ""))
        self.llm_provider = str(profile.get("llm_provider", "openai"))
        self.llm_model = str(profile.get("llm_model", "gpt-4o-mini"))
        self.llm_base_url = str(profile.get("llm_base_url", ""))
        self.llm_temperature = str(profile.get("llm_temperature", "0.0"))
        self.llm_max_tokens = str(profile.get("llm_max_tokens", "4096"))
        self.llm_enable_thinking = "enabled" if _coerce_bool(profile.get("llm_enable_thinking", False)) else "disabled"
        self.llm_api_key = str(profile.get("llm_api_key", ""))
        self.persist_api_key = "enabled" if str(profile.get("persist_api_key", "False")).lower() == "true" else "disabled"

        _apply_runtime_model_config(profile)
        _clear_agent_cache()
        _save_model_profiles(self.model_profiles, self.active_model_profile_id)
        return self._toast_success("已切换模型配置" if self.ui_language == "zh" else "Model profile switched")

    async def switch_model_profile_by_name(self, profile_name: str) -> rx.event.EventSpec | None:
        profile = next((item for item in self.model_profiles if item.get("name") == profile_name), None)
        if profile is None:
            return None
        return await self.switch_model_profile(str(profile.get("id", "")))

    async def save_as_new_model_profile(self) -> rx.event.EventSpec | None:
        profile_name = self._make_unique_profile_name(self.model_profile_name)
        profile_id = f"profile-{int(datetime.now().timestamp() * 1000)}"
        payload = self._collect_model_payload(profile_id=profile_id, profile_name=profile_name)
        if payload is None:
            self.error_message = "模型参数格式错误，请检查温度和最大 Tokens"
            return self._toast_error("模型参数格式错误，请检查温度和最大 Tokens")

        self.model_profiles = [*self.model_profiles, {key: str(value) for key, value in payload.items()}]
        self.active_model_profile_id = profile_id
        _save_model_profiles(self.model_profiles, self.active_model_profile_id)
        _apply_runtime_model_config(payload)
        _clear_agent_cache()
        self.model_profile_name = profile_name
        return self._toast_success("已保存为新配置" if self.ui_language == "zh" else "Saved as new profile")

    async def delete_model_profile(self, profile_id: str) -> rx.event.EventSpec | None:
        if len(self.model_profiles) <= 1:
            return self._toast_error("至少保留一个配置" if self.ui_language == "zh" else "At least one profile is required")

        remaining = [profile for profile in self.model_profiles if profile.get("id") != profile_id]
        if len(remaining) == len(self.model_profiles):
            return None

        self.model_profiles = remaining
        if self.active_model_profile_id == profile_id:
            self.active_model_profile_id = str(self.model_profiles[0].get("id", ""))
            await self.switch_model_profile(self.active_model_profile_id)
        else:
            _save_model_profiles(self.model_profiles, self.active_model_profile_id)
        return self._toast_success("配置已删除" if self.ui_language == "zh" else "Profile deleted")

    async def save_model_settings(self) -> rx.event.EventSpec | None:
        current_profile_id = self.active_model_profile_id.strip() or "profile-default"
        payload = self._collect_model_payload(
            profile_id=current_profile_id,
            profile_name=self.model_profile_name,
        )
        if payload is None:
            self.error_message = "模型参数格式错误，请检查温度和最大 Tokens"
            return self._toast_error("模型参数格式错误，请检查温度和最大 Tokens")

        updated = False
        next_profiles: list[dict[str, str]] = []
        for profile in self.model_profiles:
            if profile.get("id") == current_profile_id:
                next_profiles.append({key: str(value) for key, value in payload.items()})
                updated = True
            else:
                next_profiles.append(profile)
        if not updated:
            next_profiles.append({key: str(value) for key, value in payload.items()})

        self.model_profiles = next_profiles
        self.active_model_profile_id = current_profile_id
        _save_model_profiles(self.model_profiles, self.active_model_profile_id)
        _apply_runtime_model_config(payload)
        _clear_agent_cache()
        self._refresh_model_fields()
        return self._toast_success("模型配置已保存并应用" if self.ui_language == "zh" else "Model settings applied")

    async def save_runtime_switches(self) -> rx.event.EventSpec | None:
        _save_ui_runtime_flags(
            {
                "heartbeat_enabled": self.heartbeat_enabled == "enabled",
            }
        )
        return self._toast_success("运行开关已保存" if self.ui_language == "zh" else "Runtime switches saved")

    async def save_platform_bridge_settings(self) -> rx.event.EventSpec | None:
        def _parse_positive_int(raw: str, *, fallback: int) -> int:
            try:
                return max(int(str(raw).strip()), 1)
            except (TypeError, ValueError):
                return fallback

        overrides: dict[str, Any] = {
            "chat_bridge_enabled": self.platform_bridge_enabled == "enabled",
            "chat_bridge_inbound_enabled": self.platform_bridge_inbound_enabled == "enabled",
            "chat_bridge_inbound_port": _parse_positive_int(
                self.platform_bridge_inbound_port,
                fallback=getattr(get_settings(), "chat_bridge_inbound_port", 8000),
            ),
            "chat_bridge_inbound_debug": self.platform_bridge_inbound_debug == "enabled",
            "chat_bridge_verify_token": self.platform_bridge_verify_token.strip(),
            "chat_bridge_default_mode": "agent" if self.platform_bridge_default_mode == "agent" else "ask",
            "chat_bridge_allowed_platforms": self.platform_bridge_allowed_platforms.strip(),
            "chat_bridge_signature_ttl_seconds": _parse_positive_int(
                self.platform_bridge_signature_ttl_seconds,
                fallback=300,
            ),
            "chat_bridge_event_id_ttl_seconds": _parse_positive_int(
                self.platform_bridge_event_id_ttl_seconds,
                fallback=86400,
            ),
            "chat_bridge_feishu_encrypt_key": self.platform_bridge_feishu_encrypt_key.strip(),
            "chat_bridge_wechat_token": self.platform_bridge_wechat_token.strip(),
            "chat_bridge_qq_signing_secret": self.platform_bridge_qq_signing_secret.strip(),
            "chat_bridge_callback_delivery_enabled": self.platform_bridge_callback_delivery_enabled == "enabled",
            "chat_bridge_callback_timeout_seconds": _parse_positive_int(
                self.platform_bridge_callback_timeout_seconds,
                fallback=12,
            ),
            "chat_bridge_feishu_api_base_url": self.platform_bridge_feishu_api_base_url.strip(),
            "chat_bridge_feishu_app_id": self.platform_bridge_feishu_app_id.strip(),
            "chat_bridge_feishu_app_secret": self.platform_bridge_feishu_app_secret.strip(),
            "chat_bridge_wechat_delivery_mode": self.platform_bridge_wechat_delivery_mode,
            "chat_bridge_wechat_work_api_base_url": self.platform_bridge_wechat_work_api_base_url.strip(),
            "chat_bridge_wechat_work_corp_id": self.platform_bridge_wechat_work_corp_id.strip(),
            "chat_bridge_wechat_work_corp_secret": self.platform_bridge_wechat_work_corp_secret.strip(),
            "chat_bridge_wechat_work_agent_id": self.platform_bridge_wechat_work_agent_id.strip(),
            "chat_bridge_wechat_official_api_base_url": self.platform_bridge_wechat_official_api_base_url.strip(),
            "chat_bridge_wechat_official_app_id": self.platform_bridge_wechat_official_app_id.strip(),
            "chat_bridge_wechat_official_app_secret": self.platform_bridge_wechat_official_app_secret.strip(),
            "chat_bridge_qq_api_base_url": self.platform_bridge_qq_api_base_url.strip(),
            "chat_bridge_qq_delivery_mode": self.platform_bridge_qq_delivery_mode,
            "chat_bridge_qq_bot_app_id": self.platform_bridge_qq_bot_app_id.strip(),
            "chat_bridge_qq_bot_token": self.platform_bridge_qq_bot_token.strip(),
            "chat_bridge_qq_napcat_api_base_url": self.platform_bridge_qq_napcat_api_base_url.strip(),
            "chat_bridge_qq_napcat_access_token": self.platform_bridge_qq_napcat_access_token.strip(),
            "chat_bridge_qq_napcat_webhook_token": self.platform_bridge_qq_napcat_webhook_token.strip(),
        }

        settings = get_settings()
        save_chat_bridge_runtime_overrides(overrides, settings=settings)
        apply_chat_bridge_runtime_overrides(settings=settings, overrides=overrides)
        self._refresh_platform_bridge_fields()
        self._refresh_platform_bridge_inbound_logs()
        return self._toast_success("平台桥接配置已保存并生效" if self.ui_language == "zh" else "Platform bridge settings saved")

    async def reload_platform_bridge_settings(self) -> rx.event.EventSpec | None:
        self._refresh_platform_bridge_fields()
        self._refresh_platform_bridge_inbound_logs()
        return self._toast_success("平台桥接配置已刷新" if self.ui_language == "zh" else "Platform bridge settings reloaded")

    async def refresh_platform_bridge_inbound_listener_status(self) -> rx.event.EventSpec | None:
        self._refresh_platform_bridge_inbound_listener_status()
        return self._toast_success("入站监听状态已刷新" if self.ui_language == "zh" else "Inbound listener status refreshed")

    async def start_platform_bridge_inbound_listener(self) -> rx.event.EventSpec | None:
        settings = get_settings()
        result = start_napcat_inbound_listener(settings=settings)
        self._refresh_platform_bridge_inbound_listener_status()
        if bool(result.get("running")):
            return self._toast_success("入站监听已启动" if self.ui_language == "zh" else "Inbound listener started")
        return self._toast_error("入站监听启动失败" if self.ui_language == "zh" else "Failed to start inbound listener")

    async def stop_platform_bridge_inbound_listener(self) -> rx.event.EventSpec | None:
        settings = get_settings()
        result = stop_napcat_inbound_listener(settings=settings)
        self._refresh_platform_bridge_inbound_listener_status()
        if not bool(result.get("running")):
            return self._toast_success("入站监听已停止" if self.ui_language == "zh" else "Inbound listener stopped")
        return self._toast_error("入站监听停止失败" if self.ui_language == "zh" else "Failed to stop inbound listener")

    async def refresh_platform_bridge_inbound_logs(self) -> rx.event.EventSpec | None:
        self._refresh_platform_bridge_inbound_logs()
        return self._toast_success("入站回调日志已刷新" if self.ui_language == "zh" else "Inbound callback logs refreshed")

    async def clear_platform_bridge_inbound_logs(self) -> rx.event.EventSpec | None:
        _platform_inbound_debug_store().clear()
        self._refresh_platform_bridge_inbound_logs()
        return self._toast_success("入站回调日志已清空" if self.ui_language == "zh" else "Inbound callback logs cleared")

    async def validate_model_client(self) -> rx.event.EventSpec | None:
        try:
            _clear_agent_cache()
            _ = _chat_agent()
            self.error_message = ""
            return self._toast_success("模型客户端初始化成功" if self.ui_language == "zh" else "Model client initialized")
        except Exception as exc:
            self.error_message = f"模型客户端初始化失败: {exc}"
            return self._toast_error(f"模型客户端初始化失败: {exc}")

    async def reset_model_settings(self) -> rx.event.EventSpec | None:
        defaults = self.model_defaults or {}
        _delete_model_config()
        _apply_runtime_model_config(defaults)
        _clear_agent_cache()
        await self.refresh_data()
        return self._toast_success("模型配置已恢复默认" if self.ui_language == "zh" else "Model settings reset")

    def _refresh_model_fields(self) -> None:
        payload = _load_model_config()
        profiles, active_profile_id = _model_profiles_from_payload(payload)
        self.model_profiles = [{key: str(value) for key, value in profile.items()} for profile in profiles]

        ids = {str(profile.get("id", "")) for profile in self.model_profiles}
        if active_profile_id not in ids and self.model_profiles:
            active_profile_id = str(self.model_profiles[0].get("id", ""))
        self.active_model_profile_id = active_profile_id

        active_profile = next(
            (profile for profile in self.model_profiles if profile.get("id") == self.active_model_profile_id),
            self.model_profiles[0] if self.model_profiles else {},
        )

        self.model_profile_name = str(active_profile.get("name", "默认配置"))
        self.llm_provider = str(active_profile.get("llm_provider", "openai"))
        self.llm_model = str(active_profile.get("llm_model", "gpt-4o-mini"))
        self.llm_base_url = str(active_profile.get("llm_base_url", ""))
        self.llm_temperature = str(active_profile.get("llm_temperature", "0.0"))
        self.llm_max_tokens = str(active_profile.get("llm_max_tokens", "4096"))
        self.llm_enable_thinking = "enabled" if _coerce_bool(active_profile.get("llm_enable_thinking", False)) else "disabled"
        self.llm_api_key = str(active_profile.get("llm_api_key", ""))
        self.persist_api_key = "enabled" if str(active_profile.get("persist_api_key", "False")).lower() == "true" else "disabled"

    def _refresh_platform_bridge_fields(self) -> None:
        settings = get_settings()
        overrides = load_chat_bridge_runtime_overrides(settings=settings)
        apply_chat_bridge_runtime_overrides(settings=settings, overrides=overrides)

        self.platform_bridge_enabled = "enabled" if bool(getattr(settings, "chat_bridge_enabled", True)) else "disabled"
        self.platform_bridge_inbound_enabled = "enabled" if bool(getattr(settings, "chat_bridge_inbound_enabled", True)) else "disabled"
        self.platform_bridge_inbound_port = str(
            getattr(settings, "chat_bridge_inbound_port", getattr(settings, "api_port", 8000))
        )
        inbound_debug_enabled = bool(getattr(settings, "chat_bridge_inbound_debug", False))
        self.platform_bridge_inbound_debug = "enabled" if inbound_debug_enabled else "disabled"
        self.platform_bridge_verify_token = str(getattr(settings, "chat_bridge_verify_token", ""))
        self.platform_bridge_default_mode = "agent" if str(getattr(settings, "chat_bridge_default_mode", "ask")).lower() == "agent" else "ask"
        self.platform_bridge_allowed_platforms = str(getattr(settings, "chat_bridge_allowed_platforms", "generic,qq,wechat,feishu"))
        self.platform_bridge_signature_ttl_seconds = str(getattr(settings, "chat_bridge_signature_ttl_seconds", 300))
        self.platform_bridge_event_id_ttl_seconds = str(getattr(settings, "chat_bridge_event_id_ttl_seconds", 86400))
        self.platform_bridge_feishu_encrypt_key = str(getattr(settings, "chat_bridge_feishu_encrypt_key", ""))
        self.platform_bridge_wechat_token = str(getattr(settings, "chat_bridge_wechat_token", ""))
        self.platform_bridge_qq_signing_secret = str(getattr(settings, "chat_bridge_qq_signing_secret", ""))
        callback_delivery_enabled = bool(getattr(settings, "chat_bridge_callback_delivery_enabled", False))
        self.platform_bridge_callback_delivery_enabled = "enabled" if callback_delivery_enabled else "disabled"
        self.platform_bridge_callback_timeout_seconds = str(getattr(settings, "chat_bridge_callback_timeout_seconds", 12))
        self.platform_bridge_feishu_api_base_url = str(getattr(settings, "chat_bridge_feishu_api_base_url", "https://open.feishu.cn"))
        self.platform_bridge_feishu_app_id = str(getattr(settings, "chat_bridge_feishu_app_id", ""))
        self.platform_bridge_feishu_app_secret = str(getattr(settings, "chat_bridge_feishu_app_secret", ""))
        wechat_mode = str(getattr(settings, "chat_bridge_wechat_delivery_mode", "auto")).lower()
        self.platform_bridge_wechat_delivery_mode = wechat_mode if wechat_mode in {"auto", "work", "official"} else "auto"
        self.platform_bridge_wechat_work_api_base_url = str(
            getattr(settings, "chat_bridge_wechat_work_api_base_url", "https://qyapi.weixin.qq.com")
        )
        self.platform_bridge_wechat_work_corp_id = str(getattr(settings, "chat_bridge_wechat_work_corp_id", ""))
        self.platform_bridge_wechat_work_corp_secret = str(getattr(settings, "chat_bridge_wechat_work_corp_secret", ""))
        self.platform_bridge_wechat_work_agent_id = str(getattr(settings, "chat_bridge_wechat_work_agent_id", ""))
        self.platform_bridge_wechat_official_api_base_url = str(
            getattr(settings, "chat_bridge_wechat_official_api_base_url", "https://api.weixin.qq.com")
        )
        self.platform_bridge_wechat_official_app_id = str(getattr(settings, "chat_bridge_wechat_official_app_id", ""))
        self.platform_bridge_wechat_official_app_secret = str(getattr(settings, "chat_bridge_wechat_official_app_secret", ""))
        self.platform_bridge_qq_api_base_url = str(getattr(settings, "chat_bridge_qq_api_base_url", "https://api.sgroup.qq.com"))
        qq_mode = str(getattr(settings, "chat_bridge_qq_delivery_mode", "auto")).lower()
        self.platform_bridge_qq_delivery_mode = qq_mode if qq_mode in {"auto", "official", "napcat"} else "auto"
        self.platform_bridge_qq_bot_app_id = str(getattr(settings, "chat_bridge_qq_bot_app_id", ""))
        self.platform_bridge_qq_bot_token = str(getattr(settings, "chat_bridge_qq_bot_token", ""))
        self.platform_bridge_qq_napcat_api_base_url = str(
            getattr(settings, "chat_bridge_qq_napcat_api_base_url", "http://127.0.0.1:3000")
        )
        self.platform_bridge_qq_napcat_access_token = str(getattr(settings, "chat_bridge_qq_napcat_access_token", ""))
        self.platform_bridge_qq_napcat_webhook_token = str(getattr(settings, "chat_bridge_qq_napcat_webhook_token", ""))
        self._refresh_platform_bridge_inbound_listener_status()

    def _refresh_platform_bridge_inbound_logs(self) -> None:
        rows = list(reversed(_platform_inbound_debug_store().list_recent(limit=30)))
        self.platform_bridge_inbound_logs = [
            {
                "timestamp": _iso(item.timestamp),
                "platform": str(item.platform),
                "method": str(item.method),
                "url": str(item.url),
                "path": str(item.path),
                "client": str(item.client),
                "status": str(item.response_status),
                "request_body": str(item.request_body),
                "response_body": str(item.response_body),
                "headers": json.dumps(item.headers, ensure_ascii=False, indent=2),
                "query": json.dumps(item.query, ensure_ascii=False, indent=2),
            }
            for item in rows
        ]

    def _refresh_platform_bridge_inbound_listener_status(self) -> None:
        settings = get_settings()
        status = get_napcat_inbound_listener_status(settings=settings)
        state = str(status.get("status", "unknown"))
        self.platform_bridge_inbound_listener_status = state if state in {"running", "stopped"} else "unknown"
        self.platform_bridge_inbound_listener_pid = str(status.get("pid") or "")
        self.platform_bridge_inbound_listener_message = str(status.get("action") or status.get("status") or "")
        self.platform_bridge_inbound_listener_updated_at = _iso(datetime.now())

    def _find_market_item(self, item_id: str) -> dict[str, str] | None:
        for item in self.filtered_market_items:
            if item.get("id") == item_id:
                return item
        return None

    @rx.var
    def about_markdown(self) -> str:
        body = I18N.get(self.ui_language, I18N["zh"]).get("about.body", "")
        return str(body).format(version=__version__)



