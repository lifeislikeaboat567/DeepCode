"""Shared chat runtime helpers for conversational LLM turns."""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Literal

from deepcode.agents.base import AgentResponse, BaseAgent
from deepcode.agents.prompt_layers import get_agent_chat_system_prompt, get_chat_system_prompt
from deepcode.llm.base import BaseLLMClient, LLMMessage
from deepcode.memory import TaskMemoryStore
from deepcode.storage import Message
from deepcode.tools.base import BaseTool

ChatMode = Literal["ask", "agent"]


@dataclass
class IntentRoute:
    """Deterministic intent routing snapshot for one user turn."""

    intent: str
    rationale: str
    preferred_tools: list[str]
    subtasks: list[str]
    success_criteria: list[str]


@dataclass
class AgentRuntimeContext:
    """Prepared runtime context used by Agent mode prompt assembly and streaming."""

    capability_context: str
    intent_route: IntentRoute
    decomposed_task: dict[str, Any]
    relevant_skills: list[dict[str, Any]]
    relevant_mcp_servers: list[dict[str, Any]]
    relevant_memories: list[dict[str, Any]]
    task_prompt: str


def runtime_context_to_dict(runtime_context: AgentRuntimeContext) -> dict[str, Any]:
    """Convert runtime context to a JSON-safe dictionary."""
    return {
        "intent_route": {
            "intent": runtime_context.intent_route.intent,
            "rationale": runtime_context.intent_route.rationale,
            "preferred_tools": list(runtime_context.intent_route.preferred_tools),
            "subtasks": list(runtime_context.intent_route.subtasks),
            "success_criteria": list(runtime_context.intent_route.success_criteria),
        },
        "decomposed_task": runtime_context.decomposed_task,
        "relevant_skills": runtime_context.relevant_skills,
        "relevant_mcp_servers": runtime_context.relevant_mcp_servers,
        "relevant_memories": runtime_context.relevant_memories,
        "capability_context": runtime_context.capability_context,
    }


@lru_cache(maxsize=1)
def _task_memory_store() -> TaskMemoryStore:
    return TaskMemoryStore()


def normalize_chat_mode(mode: str | None) -> ChatMode:
    """Normalize incoming mode and fall back to ask mode."""
    normalized = str(mode or "ask").strip().lower()
    if normalized == "agent":
        return "agent"
    return "ask"


def build_chat_messages(
    history: Iterable[Message],
    *,
    system_prompt: str | None = None,
    extra_system_context: str | None = None,
) -> list[LLMMessage]:
    """Build a conversational message list from persisted session history."""
    messages = [LLMMessage.system(system_prompt or get_chat_system_prompt())]
    if str(extra_system_context or "").strip():
        messages.append(LLMMessage.system(str(extra_system_context).strip()))

    for item in history:
        role = str(item.role).strip().lower()
        if role not in {"system", "user", "assistant"}:
            role = "user"

        content = str(item.content or "").strip()
        if not content:
            continue

        messages.append(LLMMessage(role=role, content=content))

    return messages


def _parse_json_rows(raw: str) -> list[dict[str, Any]]:
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return []

    if not isinstance(parsed, list):
        return []
    return [item for item in parsed if isinstance(item, dict)]


def _parse_json_object(raw: str) -> dict[str, Any] | None:
    text = str(raw or "").strip()
    if not text:
        return None

    fenced_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidate = fenced_match.group(1) if fenced_match else text
    if not fenced_match:
        brace_match = re.search(r"\{.*\}", text, re.DOTALL)
        if brace_match:
            candidate = brace_match.group(0)

    try:
        parsed = json.loads(candidate)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


def _extract_history_snapshot(history: Iterable[Message], max_context_messages: int = 12) -> tuple[list[str], str]:
    rows = [item for item in history if str(item.content or "").strip()]
    if not rows:
        return [], ""

    latest_user = ""
    context_lines: list[str] = []
    for item in rows:
        role = str(item.role or "user").strip().lower()
        if role not in {"system", "user", "assistant"}:
            role = "user"
        content = str(item.content or "").strip()
        if not content:
            continue
        if role == "user":
            latest_user = content
        context_lines.append(f"{role}: {content}")

    if not latest_user:
        latest_user = str(rows[-1].content or "").strip()
    return context_lines[-max_context_messages:], latest_user


def _tokenize_query(text: str, limit: int = 8) -> list[str]:
    normalized = str(text or "").lower()
    tokens = re.findall(r"[a-z0-9_\-\u4e00-\u9fff]{2,}", normalized)
    stop_words = {
        "please",
        "help",
        "with",
        "this",
        "that",
        "for",
        "the",
        "and",
        "agent",
        "task",
    }
    deduped: list[str] = []
    for token in tokens:
        if token in stop_words or token in deduped:
            continue
        deduped.append(token)
        if len(deduped) >= limit:
            break
    return deduped


def _normalize_match_text(text: str) -> str:
    lowered = str(text or "").strip().lower()
    lowered = re.sub(r"[\s_\-]+", " ", lowered)
    return lowered


def _contains_skill_intent(text: str) -> bool:
    query = _normalize_match_text(text)
    if not query:
        return False
    keywords = {
        "skill",
        "skills",
        "skill.md",
        "skill.md",
        "技能",
        "说明文档",
        "说明",
        "文档",
        "用途",
        "作用",
        "怎么用",
        "如何用",
        "如何使用",
        "使用方法",
        "介绍",
        "guide",
        "how to use",
        "usage",
        "what is",
        "explain",
    }
    return any(token in query for token in keywords)


def _skill_name_matches_query(name: str, query: str) -> bool:
    normalized_name = _normalize_match_text(name)
    normalized_query = _normalize_match_text(query)
    if not normalized_name or not normalized_query:
        return False
    if normalized_name in normalized_query:
        return True
    # Also support hyphen/underscore variations, e.g. python_debug vs python-debug.
    compact_name = normalized_name.replace(" ", "")
    compact_query = normalized_query.replace(" ", "")
    return bool(compact_name and compact_name in compact_query)


def _skill_usage_scenario(item: dict[str, Any]) -> str:
    description = str(item.get("description", "")).strip()
    tags = item.get("tags") if isinstance(item.get("tags"), list) else []
    tag_text = ", ".join(str(tag).strip() for tag in tags if str(tag).strip())
    if description:
        return description
    if tag_text:
        return f"Useful for: {tag_text}"
    return "General reusable workflow"


def _skill_match_score(item: dict[str, Any], latest_user: str, terms: list[str]) -> int:
    name = str(item.get("name", "")).strip()
    description = str(item.get("description", "")).strip()
    tags = item.get("tags") if isinstance(item.get("tags"), list) else []
    corpus = " ".join([name, description, " ".join(str(tag) for tag in tags)]).lower()
    score = sum(1 for term in terms if term and term in corpus)
    if _skill_name_matches_query(name, latest_user):
        score += 4
    if _contains_skill_intent(latest_user):
        score += 1
    return score


def _infer_route_hints(latest_user: str, route: IntentRoute) -> list[str]:
    hints = [
        f"Intent route: {route.intent}",
        f"Rationale: {route.rationale}",
        "Preferred tools: " + (", ".join(route.preferred_tools) if route.preferred_tools else "none"),
    ]
    for idx, step in enumerate(route.subtasks[:4], 1):
        hints.append(f"Suggested subtask {idx}: {step}")
    return hints


def _infer_intent_route(latest_user: str) -> IntentRoute:
    query = latest_user.strip().lower()
    network_keywords = {"ping", "dns", "latency", "connect", "network", "baidu"}
    automation_keywords = {"script", "batch", "automation", "powershell", "bash", "sh", "ps1"}
    retrieval_keywords = {"search", "docs", "mcp", "integration", "api reference", "knowledge"}
    coding_keywords = {"build", "implement", "feature", "api", "refactor", "fix", "test"}

    if any(keyword in query for keyword in network_keywords):
        return IntentRoute(
            intent="network_diagnosis",
            rationale="Detected connectivity and network diagnostics intent.",
            preferred_tools=["shell", "mcp_service"],
            subtasks=[
                "Collect concrete network evidence with shell commands.",
                "Summarize root cause candidates from command output.",
                "Propose and verify remediation steps.",
            ],
            success_criteria=[
                "At least one real command output is captured.",
                "Diagnosis includes evidence, not assumptions.",
            ],
        )

    if any(keyword in query for keyword in automation_keywords):
        return IntentRoute(
            intent="automation_script",
            rationale="Detected script-driven automation intent.",
            preferred_tools=["script_runner", "file_manager", "code_executor"],
            subtasks=[
                "Define script inputs and expected outputs.",
                "Generate a minimal script artifact.",
                "Execute script and collect stdout/stderr/exit_code.",
                "Patch and retry if verification fails.",
            ],
            success_criteria=[
                "Script artifact is generated.",
                "Execution result is verified with exit code/output.",
            ],
        )

    if any(keyword in query for keyword in retrieval_keywords):
        return IntentRoute(
            intent="context_enrichment",
            rationale="Detected requirement for external or reusable knowledge.",
            preferred_tools=["skill_registry", "mcp_service", "file_manager"],
            subtasks=[
                "Discover relevant local skills and read concise guidance.",
                "Inspect available MCP servers and choose applicable ones.",
                "Use retrieved context to drive implementation decisions.",
            ],
            success_criteria=[
                "Relevant skills or MCP services are explicitly considered.",
                "Final action plan references retrieved context.",
            ],
        )

    if any(keyword in query for keyword in coding_keywords):
        return IntentRoute(
            intent="feature_delivery",
            rationale="Detected coding task requiring implementation and verification.",
            preferred_tools=["file_manager", "script_runner", "code_executor", "skill_registry"],
            subtasks=[
                "Clarify deliverables and constraints.",
                "Implement incrementally in small verifiable steps.",
                "Run tests or executable checks.",
                "Summarize changed artifacts and evidence.",
            ],
            success_criteria=[
                "Requested change is implemented.",
                "At least one verification step is executed.",
            ],
        )

    return IntentRoute(
        intent="general_engineering",
        rationale="No hard keyword match; defaulting to execution-first engineering route.",
        preferred_tools=["file_manager", "skill_registry", "mcp_service"],
        subtasks=[
            "Observe context before making assumptions.",
            "Choose the most direct next tool action.",
            "Iterate with validation evidence.",
        ],
        success_criteria=[
            "Answer is grounded in observed evidence.",
        ],
    )


async def _decompose_request_with_llm(
    llm: BaseLLMClient,
    latest_user: str,
    route: IntentRoute,
    context_lines: list[str],
) -> dict[str, Any]:
    context_block = "\n".join(context_lines[-8:]) or "(empty)"
    prompt = (
        "Convert the user request into an executable task breakdown JSON.\n"
        "Return JSON only with keys:\n"
        "{goal, constraints, success_criteria, deliverables, subtasks}\n\n"
        f"Intent route: {route.intent}\n"
        f"Preferred tools: {route.preferred_tools}\n"
        f"Conversation context:\n{context_block}\n\n"
        f"Latest user request:\n{latest_user}"
    )
    response = await llm.complete(
        [
            LLMMessage.system("You are a task normalizer. Return JSON only."),
            LLMMessage.user(prompt),
        ]
    )
    parsed = _parse_json_object(response.content)
    if not parsed:
        return {}
    return parsed


def _fallback_decomposition(latest_user: str, route: IntentRoute) -> dict[str, Any]:
    return {
        "goal": latest_user,
        "constraints": [],
        "success_criteria": route.success_criteria,
        "deliverables": ["Implementation changes", "Execution evidence summary"],
        "subtasks": route.subtasks,
    }


def _normalize_task_decomposition(
    latest_user: str,
    route: IntentRoute,
    llm_result: dict[str, Any] | None,
) -> dict[str, Any]:
    base = _fallback_decomposition(latest_user, route)
    parsed = llm_result or {}
    if not parsed:
        return base

    goal = str(parsed.get("goal") or base["goal"]).strip() or base["goal"]
    constraints = parsed.get("constraints")
    success_criteria = parsed.get("success_criteria")
    deliverables = parsed.get("deliverables")
    subtasks = parsed.get("subtasks")

    def _ensure_list(value: Any, fallback: list[str]) -> list[str]:
        if isinstance(value, list):
            rows = [str(item).strip() for item in value if str(item).strip()]
            return rows or fallback
        if isinstance(value, str) and value.strip():
            return [value.strip()]
        return fallback

    return {
        "goal": goal,
        "constraints": _ensure_list(constraints, base["constraints"]),
        "success_criteria": _ensure_list(success_criteria, base["success_criteria"]),
        "deliverables": _ensure_list(deliverables, base["deliverables"]),
        "subtasks": _ensure_list(subtasks, base["subtasks"]),
    }


async def _collect_capability_context(tools: list[BaseTool]) -> str:
    lines: list[str] = []
    if tools:
        local_tools = ", ".join(sorted(tool.name for tool in tools))
        lines.append(f"Local tools: {local_tools}")

    skill_tool = next((tool for tool in tools if tool.name == "skill_registry"), None)
    if skill_tool is not None:
        try:
            skill_result = await skill_tool.run(action="list", limit=12)
            if skill_result.success:
                skills = _parse_json_rows(skill_result.output)
                names = [str(item.get("name", "")).strip() for item in skills if str(item.get("name", "")).strip()]
                if names:
                    lines.append("Discovered skills: " + ", ".join(names[:12]))
        except Exception:
            pass

    mcp_tool = next((tool for tool in tools if tool.name == "mcp_service"), None)
    if mcp_tool is not None:
        try:
            mcp_result = await mcp_tool.run(action="list_servers", enabled_only=True)
            if mcp_result.success:
                servers = _parse_json_rows(mcp_result.output)
                names = [str(item.get("name", "")).strip() for item in servers if str(item.get("name", "")).strip()]
                lines.append(
                    "Enabled MCP servers: " + (", ".join(names[:12]) if names else "none")
                )
        except Exception:
            pass

    return "\n".join(lines).strip()


async def _collect_relevant_skills(tools: list[BaseTool], latest_user: str) -> list[dict[str, Any]]:
    skill_tool = next((tool for tool in tools if tool.name == "skill_registry"), None)
    if skill_tool is None:
        return []

    skill_intent = _contains_skill_intent(latest_user)
    query_terms = _tokenize_query(latest_user, limit=8)
    query = " ".join(query_terms[:4])
    try:
        listed = await skill_tool.run(action="list", query=query, limit=20)
    except Exception:
        return []
    if not listed.success and skill_intent:
        try:
            listed = await skill_tool.run(action="list", limit=20)
        except Exception:
            return []
    if not listed.success:
        return []

    rows = _parse_json_rows(listed.output)
    if not rows:
        return []

    ranked = sorted(
        rows,
        key=lambda item: _skill_match_score(item, latest_user, query_terms),
        reverse=True,
    )

    matched_names = {
        str(item.get("name", "")).strip()
        for item in ranked
        if _skill_name_matches_query(str(item.get("name", "")).strip(), latest_user)
    }

    relevant: list[dict[str, Any]] = []
    for item in ranked[:5]:
        name = str(item.get("name", "")).strip()
        if not name:
            continue
        relevant.append(
            {
                "name": name,
                "description": str(item.get("description", "")).strip(),
                "usage_scenario": _skill_usage_scenario(item),
                "path": str(item.get("path", "")).strip(),
                "tags": item.get("tags") if isinstance(item.get("tags"), list) else [],
                "detail_excerpt": "",
                "detail_loaded": False,
            }
        )

    if not skill_intent:
        return relevant

    detail_targets = [
        item
        for item in relevant
        if str(item.get("name", "")).strip() in matched_names
    ]
    if not detail_targets:
        detail_targets = relevant[:1]

    for item in detail_targets[:2]:
        name = str(item.get("name", "")).strip()
        if not name:
            continue
        try:
            read_result = await skill_tool.run(action="read", name=name)
        except Exception:
            continue
        if not read_result.success:
            continue
        excerpt = str(read_result.output or "").strip()
        if excerpt:
            item["detail_excerpt"] = excerpt[:1200]
            item["detail_loaded"] = True
    return relevant


async def _collect_relevant_mcp_servers(tools: list[BaseTool], latest_user: str) -> list[dict[str, Any]]:
    mcp_tool = next((tool for tool in tools if tool.name == "mcp_service"), None)
    if mcp_tool is None:
        return []

    try:
        listed = await mcp_tool.run(action="list_servers", enabled_only=True)
    except Exception:
        return []
    if not listed.success:
        return []

    rows = _parse_json_rows(listed.output)
    if not rows:
        return []

    terms = _tokenize_query(latest_user, limit=6)
    if not terms:
        return rows[:3]

    scored: list[tuple[int, dict[str, Any]]] = []
    for item in rows:
        corpus = " ".join(
            [
                str(item.get("name", "")).lower(),
                str(item.get("description", "")).lower(),
                str(item.get("transport", "")).lower(),
            ]
        )
        score = sum(1 for term in terms if term in corpus)
        scored.append((score, item))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    selected = [item for score, item in scored if score > 0]
    if not selected:
        selected = [item for _, item in scored]
    return selected[:3]


def _collect_relevant_memories(latest_user: str, limit: int = 4) -> list[dict[str, Any]]:
    query = str(latest_user or "").strip()
    if not query:
        return []
    try:
        hits = _task_memory_store().search(query, limit=max(limit, 1))
    except Exception:
        return []

    memories: list[dict[str, Any]] = []
    for item in hits[:limit]:
        if not isinstance(item, dict):
            continue
        memories.append(
            {
                "id": str(item.get("id", "")).strip(),
                "task_id": str(item.get("task_id", "")).strip(),
                "source": str(item.get("source", "")).strip(),
                "status": str(item.get("status", "")).strip(),
                "user_request": str(item.get("user_request", "")).strip(),
                "outcome_summary": str(item.get("outcome_summary", "")).strip(),
                "process_summary": str(item.get("process_summary", "")).strip(),
                "score": float(item.get("score", 0.0) or 0.0),
            }
        )
    return memories


def _format_relevant_memories_block(relevant_memories: list[dict[str, Any]]) -> str:
    if not relevant_memories:
        return "No strongly relevant task memory detected."

    chunks: list[str] = []
    for item in relevant_memories:
        request = str(item.get("user_request", "")).strip()
        outcome = str(item.get("outcome_summary", "")).strip()
        process = str(item.get("process_summary", "")).strip()
        source = str(item.get("source", "")).strip()
        status = str(item.get("status", "")).strip()
        score = float(item.get("score", 0.0) or 0.0)
        chunk = (
            f"Request: {request or '(none)'}\n"
            f"Outcome: {outcome or '(none)'}\n"
            f"Process: {process or '(none)'}\n"
            f"Source: {source or '(unknown)'} | Status: {status or '(unknown)'} | Score: {score:.2f}"
        )
        chunks.append(chunk)
    return "\n\n".join(chunks)


def _format_ask_memory_block(relevant_memories: list[dict[str, Any]]) -> str:
    if not relevant_memories:
        return ""
    lines = [
        "Task memory context (retrieved):",
        "- Use these as prior evidence/patterns when relevant.",
        "- Do not copy blindly; adapt to current constraints.",
    ]
    for item in relevant_memories[:4]:
        request = str(item.get("user_request", "")).strip()
        outcome = str(item.get("outcome_summary", "")).strip()
        lines.append(f"- Request: {request or '(none)'}")
        lines.append(f"  Outcome: {outcome or '(none)'}")
    return "\n".join(lines).strip()


def _format_tool_descriptions(tools: Iterable[BaseTool]) -> str:
    lines = [f"- **{tool.name}**: {tool.description}" for tool in tools]
    return "\n".join(lines) if lines else "No tools available."


def _format_decomposition_block(decomposed_task: dict[str, Any]) -> str:
    goal = str(decomposed_task.get("goal", "")).strip()
    constraints = decomposed_task.get("constraints", [])
    success_criteria = decomposed_task.get("success_criteria", [])
    deliverables = decomposed_task.get("deliverables", [])
    subtasks = decomposed_task.get("subtasks", [])

    def _as_lines(label: str, value: Any) -> str:
        if isinstance(value, list):
            rows = [str(item).strip() for item in value if str(item).strip()]
            if rows:
                return label + "\n" + "\n".join(f"- {row}" for row in rows)
            return label + "\n- (none)"
        text = str(value or "").strip()
        return label + f"\n- {text or '(none)'}"

    return "\n\n".join(
        [
            f"Goal:\n- {goal or '(none)'}",
            _as_lines("Constraints:", constraints),
            _as_lines("Success criteria:", success_criteria),
            _as_lines("Deliverables:", deliverables),
            _as_lines("Subtasks:", subtasks),
        ]
    )


def _format_relevant_skills_block(relevant_skills: list[dict[str, Any]]) -> str:
    if not relevant_skills:
        return "No strongly relevant skills detected."
    chunks: list[str] = []
    for item in relevant_skills:
        name = str(item.get("name", "")).strip()
        description = str(item.get("description", "")).strip()
        usage_scenario = str(item.get("usage_scenario", "")).strip()
        path = str(item.get("path", "")).strip()
        detail_loaded = bool(item.get("detail_loaded"))
        detail_excerpt = str(item.get("detail_excerpt", "")).strip()
        chunk = (
            f"Skill: {name}\n"
            f"Usage scenario: {usage_scenario or description or '(none)'}\n"
            f"Path: {path or '(unknown)'}"
        )
        if detail_loaded and detail_excerpt:
            chunk += f"\nOn-demand details:\n{detail_excerpt}"
        chunks.append(chunk)
    return "\n\n".join(chunks)


def _format_skill_catalog_block(relevant_skills: list[dict[str, Any]]) -> str:
    if not relevant_skills:
        return "- (no skill discovered)"
    lines: list[str] = []
    for item in relevant_skills[:6]:
        name = str(item.get("name", "")).strip()
        if not name:
            continue
        usage_scenario = str(item.get("usage_scenario", "")).strip()
        description = str(item.get("description", "")).strip()
        lines.append(f"- {name}: {usage_scenario or description or '(no scenario)'}")
    return "\n".join(lines) if lines else "- (no skill discovered)"


def _format_skill_detail_block(relevant_skills: list[dict[str, Any]]) -> str:
    rows: list[str] = []
    for item in relevant_skills:
        if not bool(item.get("detail_loaded")):
            continue
        name = str(item.get("name", "")).strip()
        detail_excerpt = str(item.get("detail_excerpt", "")).strip()
        if not name or not detail_excerpt:
            continue
        rows.append(f"Skill `{name}` detail excerpt:\n{detail_excerpt}")
    return "\n\n".join(rows)


async def _build_ask_mode_context(tools: list[BaseTool], history: Iterable[Message]) -> str:
    history_rows = list(history)
    _, latest_user = _extract_history_snapshot(history_rows)
    if not latest_user:
        return ""

    relevant_memories = _collect_relevant_memories(latest_user, limit=4)
    memory_block = _format_ask_memory_block(relevant_memories)

    relevant_skills = await _collect_relevant_skills(tools, latest_user)
    skill_block = ""
    if relevant_skills:
        catalog = _format_skill_catalog_block(relevant_skills)
        detail = _format_skill_detail_block(relevant_skills)
        lines = [
            "Skill context injection policy:",
            "- Treat this skill catalog as compact baseline context.",
            "- Use detail excerpts only when the user explicitly asks about a skill or wants to use that skill.",
            "",
            "Skill catalog (name + usage scenario):",
            catalog,
        ]
        if detail:
            lines.extend(["", "On-demand skill details:", detail])
        skill_block = "\n".join(lines).strip()

    parts = [block for block in [memory_block, skill_block] if block]
    return "\n\n".join(parts).strip()


def _format_relevant_mcp_block(relevant_mcp_servers: list[dict[str, Any]]) -> str:
    if not relevant_mcp_servers:
        return "No strongly relevant MCP server detected."
    rows: list[str] = []
    for item in relevant_mcp_servers:
        rows.append(
            "- "
            + ", ".join(
                [
                    f"name={str(item.get('name', '')).strip() or '(unknown)'}",
                    f"transport={str(item.get('transport', '')).strip() or '(unknown)'}",
                    f"description={str(item.get('description', '')).strip() or '(none)'}",
                ]
            )
        )
    return "\n".join(rows)


def build_agent_task_prompt(
    history: Iterable[Message],
    *,
    max_context_messages: int = 12,
    capability_context: str = "",
    intent_route: IntentRoute | None = None,
    decomposed_task: dict[str, Any] | None = None,
    relevant_skills: list[dict[str, Any]] | None = None,
    relevant_mcp_servers: list[dict[str, Any]] | None = None,
    relevant_memories: list[dict[str, Any]] | None = None,
) -> str:
    """Build an execution-oriented task prompt for Agent mode chat turns."""
    context_lines, latest_user = _extract_history_snapshot(history, max_context_messages=max_context_messages)
    if not context_lines and not latest_user:
        return "User request: Please help with the current task."

    route = intent_route or _infer_intent_route(latest_user)
    route_hints = _infer_route_hints(latest_user, route)
    route_block = "\n".join(f"- {hint}" for hint in route_hints)
    context_block = "\n".join(context_lines) or "(empty)"
    capability_block = capability_context or "No dynamic capability snapshot available."

    decomposition = decomposed_task or _fallback_decomposition(latest_user, route)
    decomposition_block = _format_decomposition_block(decomposition)
    skills_block = _format_relevant_skills_block(relevant_skills or [])
    mcp_block = _format_relevant_mcp_block(relevant_mcp_servers or [])
    memory_block = _format_relevant_memories_block(relevant_memories or [])

    return (
        "You are in an ongoing engineering conversation. Continue from the context below and solve "
        "the latest user request with a strict ReAct loop.\n\n"
        f"Conversation context:\n{context_block}\n\n"
        f"Capability snapshot:\n{capability_block}\n\n"
        f"Intent routing hints:\n{route_block}\n\n"
        f"Task decomposition:\n{decomposition_block}\n\n"
        f"Relevant skills to expose/use:\n{skills_block}\n\n"
        f"Relevant MCP servers to expose/use:\n{mcp_block}\n\n"
        f"Relevant task memories (RAG hits):\n{memory_block}\n\n"
        f"Latest user request:\n{latest_user}\n\n"
        "Execution policy:\n"
        "- Treat the decomposition as an executable draft; refine it if better evidence appears.\n"
        "- For fuzzy user asks, proactively translate into concrete subtasks before execution.\n"
        "- If relevant skills are listed, consult `skill_registry` before large implementation.\n"
        "- If relevant MCP servers are listed, use `mcp_service` when external context reduces uncertainty.\n"
        "- If relevant task memories are available, reuse proven approach patterns and adapt to current constraints.\n"
        "- If prerequisites are missing but can be created, create them via tools and continue.\n"
        "- Never claim inability before at least one relevant tool attempt.\n"
        "- After each tool call, use observation evidence to decide next action.\n\n"
        "Function-calling contract:\n"
        "- For tool usage, output JSON with either {action, action_input} or {function_call: {name, arguments}}.\n"
        "- Keep each turn to a single action until final_answer.\n"
        "- End with action=final_answer and include concrete evidence summary.\n"
        "Output only valid ReAct JSON blocks until final_answer."
    )


async def _prepare_agent_runtime_context(
    llm: BaseLLMClient,
    history: Iterable[Message],
    tools: list[BaseTool],
    *,
    max_context_messages: int = 12,
) -> AgentRuntimeContext:
    history_rows = list(history)
    context_lines, latest_user = _extract_history_snapshot(history_rows, max_context_messages=max_context_messages)
    route = _infer_intent_route(latest_user)

    llm_decomposition: dict[str, Any] = {}
    llm_class_name = llm.__class__.__name__.lower()
    supports_decomposition = "mock" not in llm_class_name
    if latest_user and supports_decomposition:
        try:
            llm_decomposition = await _decompose_request_with_llm(llm, latest_user, route, context_lines)
        except Exception:
            llm_decomposition = {}

    decomposition = _normalize_task_decomposition(latest_user, route, llm_decomposition)
    capability_context = await _collect_capability_context(tools)
    relevant_skills = await _collect_relevant_skills(tools, latest_user)
    relevant_mcp_servers = await _collect_relevant_mcp_servers(tools, latest_user)
    relevant_memories = _collect_relevant_memories(latest_user, limit=4)

    task_prompt = build_agent_task_prompt(
        history_rows,
        max_context_messages=max_context_messages,
        capability_context=capability_context,
        intent_route=route,
        decomposed_task=decomposition,
        relevant_skills=relevant_skills,
        relevant_mcp_servers=relevant_mcp_servers,
        relevant_memories=relevant_memories,
    )
    return AgentRuntimeContext(
        capability_context=capability_context,
        intent_route=route,
        decomposed_task=decomposition,
        relevant_skills=relevant_skills,
        relevant_mcp_servers=relevant_mcp_servers,
        relevant_memories=relevant_memories,
        task_prompt=task_prompt,
    )


def _build_prelude_reason_events(runtime_context: AgentRuntimeContext) -> list[dict[str, Any]]:
    subtasks = runtime_context.decomposed_task.get("subtasks", [])
    if isinstance(subtasks, list):
        subtask_rows = [str(item).strip() for item in subtasks if str(item).strip()]
    else:
        subtask_rows = []

    return [
        {
            "type": "reason",
            "payload": {
                "step": 0,
                "content": (
                    f"Intent route selected: {runtime_context.intent_route.intent}. "
                    f"Preferred tools: {', '.join(runtime_context.intent_route.preferred_tools)}"
                ),
            },
        },
        {
            "type": "reason",
            "payload": {
                "step": 0,
                "content": "Decomposed subtasks: " + (" | ".join(subtask_rows[:5]) if subtask_rows else "(none)"),
            },
        },
        {
            "type": "reason",
            "payload": {
                "step": 0,
                "content": (
                    "Exposed capabilities: "
                    f"{len(runtime_context.relevant_skills)} relevant skill(s), "
                    f"{len(runtime_context.relevant_mcp_servers)} relevant MCP server(s), "
                    f"{len(runtime_context.relevant_memories)} memory hit(s)."
                ),
            },
        },
    ]


def _build_plan_only_answer(runtime_context: AgentRuntimeContext) -> str:
    task = runtime_context.decomposed_task
    goal = str(task.get("goal", "")).strip() or "(none)"
    subtasks = task.get("subtasks", [])
    if not isinstance(subtasks, list):
        subtasks = []
    rows = [str(item).strip() for item in subtasks if str(item).strip()]
    if not rows:
        rows = list(runtime_context.intent_route.subtasks)

    lines = [
        "Plan-Only mode: execution paused pending confirmation.",
        "",
        f"Goal: {goal}",
        f"Intent: {runtime_context.intent_route.intent}",
        "",
        "Proposed steps:",
    ]
    lines.extend(f"{idx}. {row}" for idx, row in enumerate(rows[:8], 1))
    if runtime_context.relevant_skills:
        lines.append("")
        lines.append("Relevant skills:")
        lines.extend(
            f"- {str(item.get('name', '')).strip()}: {str(item.get('description', '')).strip()}"
            for item in runtime_context.relevant_skills
        )
    if runtime_context.relevant_mcp_servers:
        lines.append("")
        lines.append("Relevant MCP servers:")
        lines.extend(
            f"- {str(item.get('name', '')).strip()} ({str(item.get('transport', '')).strip()})"
            for item in runtime_context.relevant_mcp_servers
        )
    if runtime_context.relevant_memories:
        lines.append("")
        lines.append("Relevant task memories:")
        for item in runtime_context.relevant_memories[:4]:
            request = str(item.get("user_request", "")).strip()
            outcome = str(item.get("outcome_summary", "")).strip()
            lines.append(f"- {request or '(none)'} -> {outcome or '(none)'}")
    lines.append("")
    lines.append("Reply with confirmation to execute this plan.")
    return "\n".join(lines)


async def complete_chat_response(
    llm: BaseLLMClient,
    history: Iterable[Message],
    tools: list[BaseTool] | None = None,
) -> str:
    """Return a full assistant response for the provided chat history."""
    history_rows = list(history)
    ask_context = await _build_ask_mode_context(tools or [], history_rows)
    response = await llm.complete(
        build_chat_messages(history_rows, extra_system_context=ask_context),
    )
    return str(response.content or "")


async def complete_agent_response(
    llm: BaseLLMClient,
    history: Iterable[Message],
    tools: list[BaseTool],
    *,
    max_iterations: int = 10,
    plan_only: bool = False,
) -> AgentResponse:
    """Return an Agent-mode response using ReAct + tools."""
    runtime = await _prepare_agent_runtime_context(llm, history, tools)
    runtime_dict = runtime_context_to_dict(runtime)
    if plan_only:
        return AgentResponse(
            answer=_build_plan_only_answer(runtime),
            steps=[],
            success=True,
            code_artifacts=[],
            agent_context=runtime_dict,
        )

    agent = BaseAgent(
        llm=llm,
        tools=tools,
        max_iterations=max_iterations,
        system_prompt=get_agent_chat_system_prompt(_format_tool_descriptions(tools)),
    )
    result = await agent.run(runtime.task_prompt)
    result.agent_context = runtime_dict
    answer = str(result.answer or "").strip()
    failed_placeholder = (
        (not result.success)
        and (
            "could not complete the task within the maximum number of iterations" in answer.lower()
            or "max iterations reached" in str(result.error or "").lower()
        )
    )
    if answer and not failed_placeholder:
        return result

    # Fallback: return direct chat response to avoid empty Agent-mode replies.
    fallback = await complete_chat_response(llm, history, tools)
    result.answer = str(fallback or "").strip() or "The model returned no content."
    result.success = bool(result.answer)
    if not result.success and not result.error:
        result.error = "Empty agent response and fallback response."
    return result


async def stream_chat_response(
    llm: BaseLLMClient,
    history: Iterable[Message],
    tools: list[BaseTool] | None = None,
) -> AsyncIterator[str]:
    """Yield assistant response chunks for the provided chat history."""
    history_rows = list(history)
    ask_context = await _build_ask_mode_context(tools or [], history_rows)
    async for chunk in llm.stream_complete(
        build_chat_messages(history_rows, extra_system_context=ask_context)
    ):
        if chunk:
            yield str(chunk)


async def stream_agent_response(
    llm: BaseLLMClient,
    history: Iterable[Message],
    tools: list[BaseTool],
    *,
    max_iterations: int = 10,
    plan_only: bool = False,
) -> AsyncIterator[str]:
    """Yield compatibility Agent-mode text chunks from the ReAct execution stream."""
    async for event in stream_agent_events(
        llm,
        history,
        tools,
        max_iterations=max_iterations,
        plan_only=plan_only,
    ):
        event_type = str(event.get("type", ""))
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        if event_type == "reason":
            content = str(payload.get("content", ""))
            if content:
                yield f"Thought: {content}\n"
        elif event_type == "function_call":
            name = str(payload.get("name", ""))
            arguments = payload.get("arguments")
            if isinstance(arguments, dict) and arguments:
                yield f"Function Call: `{name}` with `{json.dumps(arguments, ensure_ascii=False)}`\n"
            else:
                yield f"Function Call: `{name}`\n"
        elif event_type == "observation":
            observation = str(payload.get("content", ""))
            if observation:
                yield f"Observation: {observation}\n"
        elif event_type == "final_answer":
            answer = str(payload.get("answer", ""))
            if answer:
                yield answer


async def stream_agent_events(
    llm: BaseLLMClient,
    history: Iterable[Message],
    tools: list[BaseTool],
    *,
    max_iterations: int = 10,
    plan_only: bool = False,
) -> AsyncIterator[dict[str, Any]]:
    """Yield structured Agent-mode stream events with a `type` and `payload`."""
    emitted_final = False
    try:
        runtime = await _prepare_agent_runtime_context(llm, history, tools)
        runtime_dict = runtime_context_to_dict(runtime)
        yield {"type": "agent_context", "payload": runtime_dict}
        for prelude in _build_prelude_reason_events(runtime):
            yield prelude

        if plan_only:
            yield {
                "type": "final_answer",
                "payload": {"step": 0, "answer": _build_plan_only_answer(runtime)},
            }
            return

        agent = BaseAgent(
            llm=llm,
            tools=tools,
            max_iterations=max_iterations,
            system_prompt=get_agent_chat_system_prompt(_format_tool_descriptions(tools)),
        )
        async for event in agent.stream_run_events(runtime.task_prompt):
            if isinstance(event, dict) and event.get("type"):
                payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
                event_type = str(event.get("type"))
                if event_type == "final_answer":
                    answer = str(payload.get("answer", "")).strip()
                    if answer:
                        emitted_final = True
                yield {
                    "type": event_type,
                    "payload": payload,
                }
    except Exception as exc:
        yield {
            "type": "warning",
            "payload": {"message": f"Agent pipeline degraded: {exc}"},
        }

    if emitted_final:
        return

    # Final safety fallback for providers that occasionally emit empty content.
    fallback = await complete_chat_response(llm, history, tools)
    answer = str(fallback or "").strip() or "The model returned no content."
    yield {
        "type": "warning",
        "payload": {"message": "Agent fallback used due to missing final_answer."},
    }
    yield {
        "type": "final_answer",
        "payload": {"step": max_iterations + 1, "answer": answer},
    }
