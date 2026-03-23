"""Extension registries for MCP servers, hooks, and skills."""

from deepcode.extensions.hook_rule_store import HookRule, HookRuleStore
from deepcode.extensions.hooks import HookContext, HookEvent, HookManager
from deepcode.extensions.mcp_registry import MCPRegistry, MCPServerConfig
from deepcode.extensions.remote_install import (
    get_clawhub_skill_details,
    install_mcp_from_remote,
    install_skill_from_clawhub,
    install_skills_from_remote,
    resolve_clawhub_skill_slug,
    search_clawhub_skills,
)
from deepcode.extensions.skill_archive_installer import install_skill_archive_bytes
from deepcode.extensions.skill_registry import SkillDefinition, SkillRegistry
from deepcode.extensions.skill_toggle_store import SkillToggleStore

__all__ = [
    "HookRule",
    "HookRuleStore",
    "HookContext",
    "HookEvent",
    "HookManager",
    "MCPRegistry",
    "MCPServerConfig",
    "get_clawhub_skill_details",
    "install_mcp_from_remote",
    "install_skill_from_clawhub",
    "install_skills_from_remote",
    "install_skill_archive_bytes",
    "resolve_clawhub_skill_slug",
    "search_clawhub_skills",
    "SkillDefinition",
    "SkillRegistry",
    "SkillToggleStore",
]
