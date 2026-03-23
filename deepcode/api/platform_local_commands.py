"""Local platform command execution without LLM dependency."""

from __future__ import annotations

import json
import shlex
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from deepcode import __version__
from deepcode.api.napcat_inbound_listener import (
    get_napcat_inbound_listener_status,
    start_napcat_inbound_listener,
    stop_napcat_inbound_listener,
)
from deepcode.config import (
    Settings,
    _CHAT_BRIDGE_RUNTIME_OVERRIDE_KEYS,
    apply_chat_bridge_runtime_overrides,
    load_chat_bridge_runtime_overrides,
    save_chat_bridge_runtime_overrides,
)
from deepcode.extensions import (
    SkillRegistry,
    SkillToggleStore,
    install_skill_from_clawhub,
    install_skills_from_remote,
    resolve_clawhub_skill_slug,
)

_SECRET_TOKENS = ("token", "secret", "password", "api_key")
_BRIDGE_BOOL_KEYS = {
    "chat_bridge_enabled",
    "chat_bridge_inbound_enabled",
    "chat_bridge_inbound_debug",
    "chat_bridge_callback_delivery_enabled",
}
_BRIDGE_INT_KEYS = {
    "chat_bridge_inbound_port",
    "chat_bridge_signature_ttl_seconds",
    "chat_bridge_event_id_ttl_seconds",
    "chat_bridge_callback_timeout_seconds",
}
_RUNTIME_CONFIG_KEYS = {
    "llm_provider",
    "llm_model",
    "llm_base_url",
    "llm_temperature",
    "llm_max_tokens",
    "llm_enable_thinking",
    "ui_heartbeat_enabled",
}
_RUNTIME_BOOL_KEYS = {"llm_enable_thinking", "ui_heartbeat_enabled"}
_RUNTIME_INT_KEYS = {"llm_max_tokens"}
_RUNTIME_FLOAT_KEYS = {"llm_temperature"}
_EDITABLE_CONFIG_KEYS = sorted(set(_CHAT_BRIDGE_RUNTIME_OVERRIDE_KEYS) | _RUNTIME_CONFIG_KEYS)
_MODEL_PROFILE_FILE = "ui_model_config.json"


@dataclass
class PlatformLocalCommandResult:
    handled: bool
    reply_text: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


def _mask_value(key: str, value: Any) -> str:
    lowered = key.lower()
    text = str(value or "")
    if any(token in lowered for token in _SECRET_TOKENS):
        if not text:
            return ""
        if len(text) <= 6:
            return "***"
        return f"{text[:2]}***{text[-2:]}"
    return text


def _parse_bool(value: str) -> bool:
    lowered = str(value or "").strip().lower()
    return lowered in {"1", "true", "yes", "on", "enabled", "enable"}


def _parse_tokens(text: str) -> list[str]:
    try:
        return shlex.split(text)
    except ValueError:
        return str(text or "").strip().split()


def _config_help() -> str:
    return "\n".join(
        [
            "配置指令：",
            "- /config keys",
            "- /config show",
            "- /config show <key>",
            "- /config set <key> <value>",
            "- /config reset <key>",
            "- /config mode [ask|agent]",
            "- /config profile list",
            "- /config profile use <id|name>",
            "",
            "说明：",
            "- 支持修改平台桥接配置和模型运行时配置（均持久化）。",
            "- 支持切换当前模型配置档（来自 WebUI 保存的模型配置）。",
            "- 配置会写入本地运行时覆盖文件，重启后仍生效。",
        ]
    )


def _skill_help() -> str:
    return "\n".join(
        [
            "技能指令：",
            "- /skill list [all|enabled|disabled] [query]",
            "- /skill show <name>",
            "- /skill enable <name>",
            "- /skill disable <name>",
            "- /skill uninstall <name>",
            "- /skill install <source>",
            "",
            "source 示例：",
            "- slug:agent-browser",
            "- https://clawhub.ai/TheSethRose/agent-browser",
            "- https://example.com/skills.zip",
        ]
    )


def _help_text() -> str:
    return "\n".join(
        [
            "可用本地指令（不调用大模型）：",
            "- /help",
            "- /ping",
            "- /version",
            "- /new 或 /newchat",
            "- /mode [ask|agent]",
            "- /config ...",
            "- /skill ...",
            "- /inbound ...",
            "",
            _config_help(),
            "",
            _skill_help(),
            "",
            "入站监听指令：",
            "- /inbound status",
            "- /inbound start",
            "- /inbound stop",
            "",
            "提示：",
            "- /ask、/agent、/plan 仍按原有聊天模式执行。",
            "- /mode 可以切换默认聊天模式（ask/agent）。",
            "- /new 用于开启新对话（重置当前平台会话绑定）。",
            "- 上述本地指令命中后会直接本地执行并返回结果。",
        ]
    )


def _bridge_settings_snapshot(settings: Settings) -> dict[str, Any]:
    snapshot = {
        key: getattr(settings, key)
        for key in _CHAT_BRIDGE_RUNTIME_OVERRIDE_KEYS
    }
    for key in _RUNTIME_CONFIG_KEYS:
        snapshot[key] = getattr(settings, key)

    active_profile = _active_model_profile(settings)
    if active_profile:
        snapshot["llm_profile_active"] = active_profile
    return snapshot


def _model_profiles_path(settings: Settings) -> Path:
    settings.ensure_data_dir()
    return settings.data_dir / _MODEL_PROFILE_FILE


def _load_model_profiles(settings: Settings) -> tuple[list[dict[str, Any]], str]:
    path = _model_profiles_path(settings)
    if not path.exists():
        return [], ""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return [], ""

    if not isinstance(payload, dict):
        return [], ""

    rows = payload.get("profiles") if isinstance(payload.get("profiles"), list) else []
    profiles = [row for row in rows if isinstance(row, dict)]
    active_profile_id = str(payload.get("active_profile_id", "")).strip()
    return profiles, active_profile_id


def _save_model_profiles(settings: Settings, profiles: list[dict[str, Any]], active_profile_id: str) -> None:
    path = _model_profiles_path(settings)
    payload = {
        "active_profile_id": active_profile_id,
        "profiles": profiles,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _active_model_profile(settings: Settings) -> str:
    profiles, active_profile_id = _load_model_profiles(settings)
    if not profiles:
        return ""

    for row in profiles:
        row_id = str(row.get("id", "")).strip()
        if active_profile_id and row_id == active_profile_id:
            return str(row.get("name") or row_id)
    return str(profiles[0].get("name") or profiles[0].get("id") or "")


def _apply_model_profile(settings: Settings, profile: dict[str, Any]) -> None:
    if "llm_provider" in profile:
        settings.llm_provider = _normalize_runtime_value("llm_provider", str(profile.get("llm_provider", "")))
    if "llm_model" in profile:
        settings.llm_model = str(profile.get("llm_model", "")).strip() or settings.llm_model
    if "llm_base_url" in profile:
        settings.llm_base_url = str(profile.get("llm_base_url", "")).strip()
    if "llm_temperature" in profile:
        settings.llm_temperature = _normalize_runtime_value("llm_temperature", str(profile.get("llm_temperature", "0")))
    if "llm_max_tokens" in profile:
        settings.llm_max_tokens = _normalize_runtime_value("llm_max_tokens", str(profile.get("llm_max_tokens", "0")))
    if "llm_enable_thinking" in profile:
        settings.llm_enable_thinking = _normalize_runtime_value("llm_enable_thinking", str(profile.get("llm_enable_thinking", "false")))

    persist_api_key = _parse_bool(str(profile.get("persist_api_key", False)))
    if persist_api_key:
        api_key = str(profile.get("llm_api_key", "")).strip()
        if api_key:
            settings.llm_api_key = api_key


def _persist_runtime_settings(settings: Settings) -> None:
    overrides = load_chat_bridge_runtime_overrides(settings=settings)
    for key in _RUNTIME_CONFIG_KEYS:
        overrides[key] = getattr(settings, key)
    save_chat_bridge_runtime_overrides(overrides, settings=settings)
    apply_chat_bridge_runtime_overrides(settings=settings, overrides=overrides)


def _format_model_profiles(settings: Settings) -> str:
    profiles, active_profile_id = _load_model_profiles(settings)
    if not profiles:
        return "未找到模型配置档。请先在 WebUI 模型配置页面保存配置。"

    rows = ["模型配置档："]
    for row in profiles:
        profile_id = str(row.get("id", "")).strip() or "-"
        profile_name = str(row.get("name", "")).strip() or profile_id
        marker = "*" if active_profile_id and profile_id == active_profile_id else " "
        provider = str(row.get("llm_provider", "")).strip()
        model = str(row.get("llm_model", "")).strip()
        rows.append(f"{marker} {profile_name} ({profile_id}) :: {provider}/{model}")
    rows.append("提示：使用 /config profile use <id|name> 切换")
    return "\n".join(rows)


def _use_model_profile(settings: Settings, selector: str) -> str:
    profiles, active_profile_id = _load_model_profiles(settings)
    if not profiles:
        return "未找到模型配置档。请先在 WebUI 模型配置页面保存配置。"

    target = str(selector or "").strip().lower()
    if not target:
        return "用法: /config profile use <id|name>"

    matched: dict[str, Any] | None = None
    for row in profiles:
        row_id = str(row.get("id", "")).strip().lower()
        row_name = str(row.get("name", "")).strip().lower()
        if target in {row_id, row_name}:
            matched = row
            break
    if matched is None:
        for row in profiles:
            row_id = str(row.get("id", "")).strip().lower()
            row_name = str(row.get("name", "")).strip().lower()
            if target in row_id or target in row_name:
                matched = row
                break

    if matched is None:
        return f"未找到模型配置档: {selector}"

    _apply_model_profile(settings, matched)
    _persist_runtime_settings(settings)
    matched_id = str(matched.get("id", "")).strip()
    if not matched_id:
        matched_id = active_profile_id
    _save_model_profiles(settings, profiles, matched_id)

    profile_name = str(matched.get("name", "")).strip() or matched_id
    return (
        f"已切换模型配置档: {profile_name}\n"
        f"llm_provider = {settings.llm_provider}\n"
        f"llm_model = {settings.llm_model}"
    )


def _set_default_mode(settings: Settings, mode: str) -> str:
    normalized = "agent" if str(mode or "").strip().lower() == "agent" else "ask"
    _apply_bridge_override_key(settings, "chat_bridge_default_mode", normalized)
    return f"默认对话模式已更新: chat_bridge_default_mode = {settings.chat_bridge_default_mode}"


def _format_json(payload: dict[str, Any], *, masked: bool = True) -> str:
    if masked:
        rendered = {
            key: _mask_value(key, value)
            for key, value in payload.items()
        }
    else:
        rendered = payload
    return json.dumps(rendered, ensure_ascii=False, indent=2)


def _normalize_bridge_value(key: str, value: str) -> Any:
    raw = str(value or "").strip()
    if key in _BRIDGE_BOOL_KEYS:
        return _parse_bool(raw)
    if key in _BRIDGE_INT_KEYS:
        return max(int(raw), 1)
    if key == "chat_bridge_default_mode":
        return "agent" if raw.lower() == "agent" else "ask"
    if key == "chat_bridge_wechat_delivery_mode":
        normalized = raw.lower()
        return normalized if normalized in {"auto", "work", "official"} else "auto"
    if key == "chat_bridge_qq_delivery_mode":
        normalized = raw.lower()
        return normalized if normalized in {"auto", "official", "napcat"} else "auto"
    return raw


def _normalize_runtime_value(key: str, value: str) -> Any:
    raw = str(value or "").strip()
    if key in _RUNTIME_BOOL_KEYS:
        return _parse_bool(raw)
    if key in _RUNTIME_INT_KEYS:
        return max(int(raw), 1)
    if key in _RUNTIME_FLOAT_KEYS:
        return max(float(raw), 0.0)
    if key == "llm_provider":
        normalized = raw.lower()
        allowed = {"openai", "anthropic", "ollama", "gemini", "github_copilot", "mock"}
        if normalized not in allowed:
            raise ValueError("invalid llm_provider")
        return normalized
    return raw


def _apply_bridge_override_key(settings: Settings, key: str, value: Any) -> None:
    overrides = load_chat_bridge_runtime_overrides(settings=settings)
    overrides[key] = value
    save_chat_bridge_runtime_overrides(overrides, settings=settings)
    apply_chat_bridge_runtime_overrides(settings=settings, overrides=overrides)


def _reset_bridge_override_key(settings: Settings, key: str) -> None:
    overrides = load_chat_bridge_runtime_overrides(settings=settings)
    overrides.pop(key, None)
    save_chat_bridge_runtime_overrides(overrides, settings=settings)

    defaults = Settings()
    setattr(settings, key, getattr(defaults, key))
    apply_chat_bridge_runtime_overrides(settings=settings, overrides=overrides)


def _find_skill_by_name(name: str) -> tuple[object, bool] | None:
    target = str(name or "").strip().lower()
    if not target:
        return None
    registry = SkillRegistry()
    status_store = SkillToggleStore()

    rows = []
    for skill in registry.discover():
        enabled = status_store.is_enabled(str(skill.path), default=True)
        rows.append((skill, enabled))

    for skill, enabled in rows:
        if str(skill.name).lower() == target:
            return skill, enabled
    for skill, enabled in rows:
        if target in str(skill.name).lower():
            return skill, enabled
    return None


def _format_skill_list(*, mode: str, query: str) -> str:
    registry = SkillRegistry()
    status_store = SkillToggleStore()

    query_text = str(query or "").strip().lower()
    rows: list[str] = []
    count = 0
    for skill in sorted(registry.discover(), key=lambda item: str(item.name).lower()):
        enabled = status_store.is_enabled(str(skill.path), default=True)
        if mode == "enabled" and not enabled:
            continue
        if mode == "disabled" and enabled:
            continue

        searchable = " ".join(
            [
                str(skill.name),
                str(skill.description),
                " ".join(str(tag) for tag in skill.tags),
            ]
        ).lower()
        if query_text and query_text not in searchable:
            continue

        state_text = "enabled" if enabled else "disabled"
        rows.append(f"- {skill.name} [{state_text}] :: {skill.description}")
        count += 1

    if count == 0:
        return "未找到匹配技能。"
    return "\n".join([f"技能列表（{count}）:", *rows])


def _toggle_skill(name: str, enabled: bool) -> str:
    matched = _find_skill_by_name(name)
    if matched is None:
        return f"技能不存在: {name}"

    skill, _old_enabled = matched
    store = SkillToggleStore()
    store.set_enabled(str(skill.path), enabled)
    state_text = "enabled" if enabled else "disabled"
    return f"技能已{('启用' if enabled else '禁用')}: {skill.name} ({state_text})"


def _uninstall_skill(name: str) -> str:
    matched = _find_skill_by_name(name)
    if matched is None:
        return f"技能不存在: {name}"

    skill, _enabled = matched
    skill_path = Path(str(skill.path))
    skills_root = skill_path
    try:
        settings = Settings()
        skills_root = settings.data_dir / "skills"
    except Exception:
        pass

    removed_target = skill_path
    if skill_path.name.lower() == "skill.md" and skill_path.parent != skills_root:
        removed_target = skill_path.parent
    removed_is_dir = removed_target.is_dir()

    if removed_is_dir:
        shutil.rmtree(removed_target, ignore_errors=True)
    else:
        try:
            removed_target.unlink(missing_ok=True)
        except TypeError:
            if removed_target.exists():
                removed_target.unlink()

    toggle_store = SkillToggleStore()
    current = toggle_store.load()
    if removed_is_dir:
        removed_prefix = str(removed_target).lower()
        remaining = {
            key: value
            for key, value in current.items()
            if not str(key).lower().startswith(removed_prefix)
        }
    else:
        remaining = {key: value for key, value in current.items() if str(key) != str(skill_path)}
    toggle_store.save(remaining)

    return f"技能已卸载: {skill.name} ({removed_target})"


async def _install_skill(source: str) -> str:
    value = str(source or "").strip()
    if not value:
        return "缺少安装源。请使用 /skill install <source>。"

    slug = ""
    if value.lower().startswith("slug:"):
        slug = resolve_clawhub_skill_slug(value.split(":", 1)[1].strip())
    else:
        slug = resolve_clawhub_skill_slug(value)

    if slug:
        result = await install_skill_from_clawhub("https://clawhub.ai", slug=slug)
        package_name = str(result.get("package_name", slug))
        install_dir = str(result.get("install_dir", ""))
        return f"技能安装成功: {package_name}\n目录: {install_dir}"

    result = await install_skills_from_remote(value)
    installed = result.get("installed", []) if isinstance(result, dict) else []
    rows = [str(item) for item in installed if str(item).strip()]
    if not rows:
        return "安装完成，但未返回具体技能文件。"
    return "技能安装成功:\n" + "\n".join(f"- {item}" for item in rows)


def _handle_config_command(tokens: list[str], *, settings: Settings) -> PlatformLocalCommandResult:
    if len(tokens) < 2:
        return PlatformLocalCommandResult(handled=True, reply_text=_config_help())

    subcommand = tokens[1].lower()
    if subcommand in {"help", "-h", "--help"}:
        return PlatformLocalCommandResult(handled=True, reply_text=_config_help())

    if subcommand in {"keys", "list"}:
        return PlatformLocalCommandResult(
            handled=True,
            reply_text="支持修改的配置键:\n" + "\n".join(f"- {key}" for key in _EDITABLE_CONFIG_KEYS),
        )

    if subcommand in {"show", "get"}:
        snapshot = _bridge_settings_snapshot(settings)
        if len(tokens) >= 3:
            key = tokens[2].strip()
            if key not in _EDITABLE_CONFIG_KEYS:
                return PlatformLocalCommandResult(handled=True, reply_text=f"不支持的配置键: {key}")
            value = snapshot.get(key)
            return PlatformLocalCommandResult(
                handled=True,
                reply_text=f"{key} = {_mask_value(key, value)}",
            )
        return PlatformLocalCommandResult(
            handled=True,
            reply_text="当前配置（平台桥接 + 模型运行时）:\n" + _format_json(snapshot, masked=True),
        )

    if subcommand == "profile":
        action = tokens[2].lower() if len(tokens) >= 3 else "list"
        if action in {"list", "ls"}:
            return PlatformLocalCommandResult(handled=True, reply_text=_format_model_profiles(settings))
        if action in {"use", "switch"}:
            selector = " ".join(tokens[3:]).strip() if len(tokens) >= 4 else ""
            return PlatformLocalCommandResult(handled=True, reply_text=_use_model_profile(settings, selector))
        return PlatformLocalCommandResult(
            handled=True,
            reply_text="用法: /config profile list | /config profile use <id|name>",
        )

    if subcommand == "mode":
        if len(tokens) < 3:
            return PlatformLocalCommandResult(
                handled=True,
                reply_text=f"当前默认模式: {settings.chat_bridge_default_mode}\n用法: /config mode <ask|agent>",
            )
        return PlatformLocalCommandResult(
            handled=True,
            reply_text=_set_default_mode(settings, tokens[2]),
            metadata={"key": "chat_bridge_default_mode", "value": settings.chat_bridge_default_mode, "scope": "persisted"},
        )

    if subcommand == "set":
        if len(tokens) < 4:
            return PlatformLocalCommandResult(
                handled=True,
                reply_text="用法: /config set <key> <value>",
            )
        key = tokens[2].strip()
        if key not in _EDITABLE_CONFIG_KEYS:
            return PlatformLocalCommandResult(handled=True, reply_text=f"不支持的配置键: {key}")
        try:
            if key in _RUNTIME_CONFIG_KEYS:
                normalized_value = _normalize_runtime_value(key, " ".join(tokens[3:]))
            else:
                normalized_value = _normalize_bridge_value(key, " ".join(tokens[3:]))
        except ValueError:
            return PlatformLocalCommandResult(handled=True, reply_text=f"配置值格式错误: {key}")

        if key in _RUNTIME_CONFIG_KEYS:
            setattr(settings, key, normalized_value)
            _persist_runtime_settings(settings)
        else:
            _apply_bridge_override_key(settings, key, normalized_value)
        rendered = _mask_value(key, getattr(settings, key))
        persist_scope = "persisted"
        return PlatformLocalCommandResult(
            handled=True,
            reply_text=f"配置已更新({persist_scope}): {key} = {rendered}",
            metadata={"key": key, "value": getattr(settings, key), "scope": persist_scope},
        )

    if subcommand == "reset":
        if len(tokens) < 3:
            return PlatformLocalCommandResult(handled=True, reply_text="用法: /config reset <key>")
        key = tokens[2].strip()
        if key not in _EDITABLE_CONFIG_KEYS:
            return PlatformLocalCommandResult(handled=True, reply_text=f"不支持的配置键: {key}")
        if key in _CHAT_BRIDGE_RUNTIME_OVERRIDE_KEYS:
            _reset_bridge_override_key(settings, key)
        else:
            defaults = Settings()
            setattr(settings, key, getattr(defaults, key))
        rendered = _mask_value(key, getattr(settings, key))
        return PlatformLocalCommandResult(
            handled=True,
            reply_text=f"配置已重置为默认值: {key} = {rendered}",
            metadata={"key": key, "value": getattr(settings, key)},
        )

    return PlatformLocalCommandResult(handled=True, reply_text=_config_help())


async def _handle_skill_command(tokens: list[str]) -> PlatformLocalCommandResult:
    if len(tokens) < 2:
        return PlatformLocalCommandResult(handled=True, reply_text=_skill_help())

    subcommand = tokens[1].lower()
    if subcommand in {"help", "-h", "--help"}:
        return PlatformLocalCommandResult(handled=True, reply_text=_skill_help())

    if subcommand == "list":
        mode = "all"
        query_start = 2
        if len(tokens) >= 3 and tokens[2].lower() in {"all", "enabled", "disabled"}:
            mode = tokens[2].lower()
            query_start = 3
        query = " ".join(tokens[query_start:]).strip() if len(tokens) > query_start else ""
        return PlatformLocalCommandResult(
            handled=True,
            reply_text=_format_skill_list(mode=mode, query=query),
        )

    if subcommand == "show":
        if len(tokens) < 3:
            return PlatformLocalCommandResult(handled=True, reply_text="用法: /skill show <name>")
        matched = _find_skill_by_name(" ".join(tokens[2:]))
        if matched is None:
            return PlatformLocalCommandResult(handled=True, reply_text="技能不存在。")
        skill, enabled = matched
        skill_path = Path(str(skill.path))
        try:
            markdown_text = skill_path.read_text(encoding="utf-8")
        except OSError as exc:
            return PlatformLocalCommandResult(handled=True, reply_text=f"读取技能文件失败: {exc}")

        snippet = markdown_text[:2000]
        if len(markdown_text) > 2000:
            snippet += "\n...<truncated>"
        return PlatformLocalCommandResult(
            handled=True,
            reply_text=(
                f"技能: {skill.name}\n"
                f"状态: {'enabled' if enabled else 'disabled'}\n"
                f"路径: {skill.path}\n"
                f"描述: {skill.description}\n"
                f"标签: {', '.join(skill.tags)}\n\n"
                f"内容预览:\n{snippet}"
            ),
        )

    if subcommand == "enable":
        if len(tokens) < 3:
            return PlatformLocalCommandResult(handled=True, reply_text="用法: /skill enable <name>")
        return PlatformLocalCommandResult(handled=True, reply_text=_toggle_skill(" ".join(tokens[2:]), True))

    if subcommand == "disable":
        if len(tokens) < 3:
            return PlatformLocalCommandResult(handled=True, reply_text="用法: /skill disable <name>")
        return PlatformLocalCommandResult(handled=True, reply_text=_toggle_skill(" ".join(tokens[2:]), False))

    if subcommand == "uninstall":
        if len(tokens) < 3:
            return PlatformLocalCommandResult(handled=True, reply_text="用法: /skill uninstall <name>")
        return PlatformLocalCommandResult(handled=True, reply_text=_uninstall_skill(" ".join(tokens[2:])))

    if subcommand == "install":
        if len(tokens) < 3:
            return PlatformLocalCommandResult(handled=True, reply_text="用法: /skill install <source>")
        return PlatformLocalCommandResult(handled=True, reply_text=await _install_skill(" ".join(tokens[2:])))

    return PlatformLocalCommandResult(handled=True, reply_text=_skill_help())


async def try_execute_platform_local_command(
    text: str,
    *,
    settings: Settings,
) -> PlatformLocalCommandResult:
    """Execute a local platform command and bypass LLM when matched."""
    raw = str(text or "").strip()
    if not raw.startswith("/"):
        return PlatformLocalCommandResult(handled=False)

    tokens = _parse_tokens(raw)
    if not tokens:
        return PlatformLocalCommandResult(handled=False)

    root = tokens[0].lower()
    if root in {"/help", "/h", "/?"}:
        return PlatformLocalCommandResult(handled=True, reply_text=_help_text())

    if root in {"/new", "/newchat"}:
        return PlatformLocalCommandResult(handled=False)

    if root == "/mode":
        if len(tokens) < 2:
            return PlatformLocalCommandResult(
                handled=True,
                reply_text=f"当前默认模式: {settings.chat_bridge_default_mode}\n用法: /mode <ask|agent>",
            )
        return PlatformLocalCommandResult(
            handled=True,
            reply_text=_set_default_mode(settings, tokens[1]),
            metadata={"key": "chat_bridge_default_mode", "value": settings.chat_bridge_default_mode, "scope": "persisted"},
        )

    if root == "/ping":
        return PlatformLocalCommandResult(handled=True, reply_text="pong")

    if root == "/version":
        return PlatformLocalCommandResult(
            handled=True,
            reply_text=(
                f"DeepCode {__version__}\n"
                f"llm_provider={settings.llm_provider}\n"
                f"llm_model={settings.llm_model}"
            ),
        )

    if root in {"/config", "/cfg"}:
        return _handle_config_command(tokens, settings=settings)

    if root in {"/skill", "/skills"}:
        return await _handle_skill_command(tokens)

    if root in {"/inbound", "/napcat-inbound"}:
        if len(tokens) < 2 or tokens[1].lower() in {"help", "-h", "--help"}:
            return PlatformLocalCommandResult(
                handled=True,
                reply_text="可用命令: /inbound status | /inbound start | /inbound stop",
            )

        action = tokens[1].lower()
        if action == "status":
            result = get_napcat_inbound_listener_status(settings=settings)
            return PlatformLocalCommandResult(
                handled=True,
                reply_text="入站监听状态:\n" + _format_json(result, masked=False),
                metadata=result,
            )

        if action == "start":
            result = start_napcat_inbound_listener(settings=settings)
            return PlatformLocalCommandResult(
                handled=True,
                reply_text="入站监听启动结果:\n" + _format_json(result, masked=False),
                metadata=result,
            )

        if action == "stop":
            result = stop_napcat_inbound_listener(settings=settings)
            return PlatformLocalCommandResult(
                handled=True,
                reply_text="入站监听停止结果:\n" + _format_json(result, masked=False),
                metadata=result,
            )

        return PlatformLocalCommandResult(
            handled=True,
            reply_text="未知入站监听动作。可用命令: /inbound status | /inbound start | /inbound stop",
        )

    return PlatformLocalCommandResult(handled=False)
