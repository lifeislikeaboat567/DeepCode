"""Configuration management for DeepCode Agent."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """DeepCode Agent application settings.

    All settings can be overridden via environment variables with the
    ``DEEPCODE_`` prefix (e.g. ``DEEPCODE_LLM_MODEL=gpt-4o``).
    """

    model_config = SettingsConfigDict(
        env_prefix="DEEPCODE_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ─── LLM configuration ────────────────────────────────────────────────────
    llm_provider: Literal["openai", "anthropic", "ollama", "gemini", "github_copilot", "mock"] = Field(
        default="openai",
        description="LLM provider to use",
    )
    llm_model: str = Field(
        default="gpt-4o-mini",
        description="Model name to use",
    )
    llm_api_key: str = Field(
        default="",
        description="API key for the LLM provider",
    )
    llm_base_url: str = Field(
        default="",
        description="Optional base URL for the LLM provider (useful for proxies or Ollama)",
    )
    llm_temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    llm_max_tokens: int = Field(default=4096, gt=0)
    llm_enable_thinking: bool = Field(
        default=False,
        description="Whether to enable model thinking mode when supported by the provider",
    )

    # ─── API server configuration ──────────────────────────────────────────────
    api_host: str = Field(default="0.0.0.0")
    api_port: int = Field(default=8000, gt=0, lt=65536)
    debug: bool = Field(default=False)

    # ─── Storage configuration ─────────────────────────────────────────────────
    data_dir: Path = Field(
        default=Path("~/.deepcode"),
        description="Directory for persistent data",
    )
    db_url: str = Field(
        default="",
        description="Database URL; defaults to SQLite in data_dir",
    )

    # ─── Search tool ───────────────────────────────────────────────────────────
    search_api_key: str = Field(default="", description="Tavily API key for web search")

    # ─── Security / execution ──────────────────────────────────────────────────
    max_execution_time: int = Field(
        default=30,
        description="Maximum seconds allowed for code execution",
        gt=0,
    )
    allowed_shells: str = Field(
        default="ls,cat,grep,find,python3,pip,echo,pwd,head,tail,wc,ping,nslookup,tracert,curl",
        description="Comma-separated list of allowed shell commands",
    )

    # ─── Memory configuration ──────────────────────────────────────────────────
    max_history_messages: int = Field(
        default=50,
        description="Maximum number of conversation messages to retain",
        gt=0,
    )
    vector_collection: str = Field(
        default="deepcode",
        description="ChromaDB collection name for long-term memory",
    )
    chat_context_compress_threshold: int = Field(
        default=3200,
        gt=200,
        description="Approx token threshold that triggers automatic context compression",
    )
    chat_context_keep_recent_messages: int = Field(
        default=12,
        gt=2,
        description="How many latest messages to keep when compressing chat context",
    )
    ui_heartbeat_enabled: bool = Field(
        default=True,
        description="Whether heartbeat animations/status pulses are enabled in Reflex UI",
    )

    # ─── Chat bridge configuration ────────────────────────────────────────────
    chat_bridge_enabled: bool = Field(
        default=True,
        description="Enable webhook bridge for external chat platforms",
    )
    chat_bridge_inbound_enabled: bool = Field(
        default=True,
        description="Enable inbound webhook callback endpoints for external chat platforms",
    )
    chat_bridge_inbound_port: int = Field(
        default=8000,
        gt=0,
        lt=65536,
        description="Preferred API port used for inbound platform callback endpoints",
    )
    chat_bridge_inbound_debug: bool = Field(
        default=False,
        description="Enable inbound webhook debug logging for platform callback requests",
    )
    chat_bridge_verify_token: str = Field(
        default="",
        description="Optional shared secret token expected in X-DeepCode-Bridge-Token",
    )
    chat_bridge_default_mode: Literal["ask", "agent"] = Field(
        default="ask",
        description="Default chat mode when message has no command prefix",
    )
    chat_bridge_allowed_platforms: str = Field(
        default="generic,qq,wechat,feishu",
        description="Comma-separated list of allowed bridge platform identifiers",
    )
    chat_bridge_signature_ttl_seconds: int = Field(
        default=300,
        gt=0,
        description="Allowed clock skew/age for signed webhook timestamps",
    )
    chat_bridge_event_id_ttl_seconds: int = Field(
        default=86400,
        gt=0,
        description="Deduplication window for platform event_id idempotency",
    )
    chat_bridge_feishu_encrypt_key: str = Field(
        default="",
        description="Optional Feishu encrypt key for callback signature validation",
    )
    chat_bridge_wechat_token: str = Field(
        default="",
        description="Optional WeChat callback token used to verify signature",
    )
    chat_bridge_qq_signing_secret: str = Field(
        default="",
        description="Optional QQ webhook HMAC secret for signature verification",
    )
    chat_bridge_callback_delivery_enabled: bool = Field(
        default=False,
        description="Enable outbound callback delivery to official platform message APIs",
    )
    chat_bridge_callback_timeout_seconds: int = Field(
        default=12,
        gt=0,
        description="Timeout in seconds for outbound platform callback requests",
    )
    chat_bridge_feishu_api_base_url: str = Field(
        default="https://open.feishu.cn",
        description="Feishu OpenAPI base URL",
    )
    chat_bridge_feishu_app_id: str = Field(
        default="",
        description="Feishu app_id used to fetch tenant_access_token",
    )
    chat_bridge_feishu_app_secret: str = Field(
        default="",
        description="Feishu app_secret used to fetch tenant_access_token",
    )
    chat_bridge_wechat_delivery_mode: Literal["auto", "work", "official"] = Field(
        default="auto",
        description="WeChat outbound delivery mode: auto, work, or official",
    )
    chat_bridge_wechat_work_api_base_url: str = Field(
        default="https://qyapi.weixin.qq.com",
        description="WeChat Work API base URL",
    )
    chat_bridge_wechat_work_corp_id: str = Field(
        default="",
        description="WeChat Work corpid used to fetch access_token",
    )
    chat_bridge_wechat_work_corp_secret: str = Field(
        default="",
        description="WeChat Work corpsecret used to fetch access_token",
    )
    chat_bridge_wechat_work_agent_id: str = Field(
        default="",
        description="WeChat Work agentid used in message send payload",
    )
    chat_bridge_wechat_official_api_base_url: str = Field(
        default="https://api.weixin.qq.com",
        description="WeChat Official Account API base URL",
    )
    chat_bridge_wechat_official_app_id: str = Field(
        default="",
        description="WeChat Official Account appid used to fetch access_token",
    )
    chat_bridge_wechat_official_app_secret: str = Field(
        default="",
        description="WeChat Official Account secret used to fetch access_token",
    )
    chat_bridge_qq_api_base_url: str = Field(
        default="https://api.sgroup.qq.com",
        description="QQ bot OpenAPI base URL",
    )
    chat_bridge_qq_delivery_mode: Literal["auto", "official", "napcat"] = Field(
        default="auto",
        description="QQ outbound delivery mode: auto, official, or napcat",
    )
    chat_bridge_qq_bot_app_id: str = Field(
        default="",
        description="QQ bot app id used to fetch official access_token",
    )
    chat_bridge_qq_bot_app_secret: str = Field(
        default="",
        description="QQ bot app secret used to fetch official access_token",
    )
    chat_bridge_qq_bot_token: str = Field(
        default="",
        description="[Legacy] QQ bot token used in Authorization header",
    )
    chat_bridge_qq_napcat_api_base_url: str = Field(
        default="http://127.0.0.1:3000",
        description="NapCat / OneBot HTTP API base URL",
    )
    chat_bridge_qq_napcat_access_token: str = Field(
        default="",
        description="Optional NapCat HTTP API access token used in Authorization header",
    )
    chat_bridge_qq_napcat_webhook_token: str = Field(
        default="",
        description="Optional NapCat webhook token used to validate incoming QQ callbacks",
    )

    @field_validator("data_dir", mode="before")
    @classmethod
    def expand_data_dir(cls, v: str | Path) -> Path:
        """Expand ``~`` in the data directory path."""
        return Path(v).expanduser()

    @property
    def resolved_db_url(self) -> str:
        """Return the effective database URL."""
        if self.db_url:
            return self._normalize_db_url(self.db_url)
        return f"sqlite+aiosqlite:///{self.data_dir / 'deepcode.db'}"

    @staticmethod
    def _normalize_db_url(raw_url: str) -> str:
        """Normalize DB URL for local SQLite paths.

        Expands leading ``~`` for ``sqlite:///`` and ``sqlite+aiosqlite:///``
        URLs so data files are written to the user home directory instead of a
        literal ``~/`` folder inside the workspace.
        """
        prefixes = ("sqlite+aiosqlite:///", "sqlite:///")
        for prefix in prefixes:
            if not raw_url.startswith(prefix):
                continue

            db_path = raw_url[len(prefix) :]
            if db_path.startswith("~/") or db_path.startswith("~\\"):
                expanded = Path(db_path).expanduser()
                return f"{prefix}{expanded}"
            return raw_url

        return raw_url

    @property
    def allowed_shell_commands(self) -> list[str]:
        """Return the list of allowed shell commands."""
        return [cmd.strip() for cmd in self.allowed_shells.split(",") if cmd.strip()]

    @property
    def allowed_chat_bridge_platforms(self) -> list[str]:
        """Return normalized platform IDs accepted by the webhook bridge."""
        rows = [item.strip().lower() for item in self.chat_bridge_allowed_platforms.split(",")]
        return [item for item in rows if item]

    def ensure_data_dir(self) -> None:
        """Create the data directory if it does not exist."""
        self.data_dir.mkdir(parents=True, exist_ok=True)


def get_settings() -> Settings:
    """Return a cached singleton :class:`Settings` instance."""
    return _settings


# Module-level singleton – created once on import.
_settings = Settings()


_CHAT_BRIDGE_RUNTIME_OVERRIDES_FILE = "chat_bridge_runtime_overrides.json"
_CHAT_BRIDGE_RUNTIME_OVERRIDE_KEYS = [
    "chat_bridge_enabled",
    "chat_bridge_inbound_enabled",
    "chat_bridge_inbound_port",
    "chat_bridge_inbound_debug",
    "chat_bridge_verify_token",
    "chat_bridge_default_mode",
    "chat_bridge_allowed_platforms",
    "chat_bridge_signature_ttl_seconds",
    "chat_bridge_event_id_ttl_seconds",
    "chat_bridge_feishu_encrypt_key",
    "chat_bridge_wechat_token",
    "chat_bridge_qq_signing_secret",
    "chat_bridge_callback_delivery_enabled",
    "chat_bridge_callback_timeout_seconds",
    "chat_bridge_feishu_api_base_url",
    "chat_bridge_feishu_app_id",
    "chat_bridge_feishu_app_secret",
    "chat_bridge_wechat_delivery_mode",
    "chat_bridge_wechat_work_api_base_url",
    "chat_bridge_wechat_work_corp_id",
    "chat_bridge_wechat_work_corp_secret",
    "chat_bridge_wechat_work_agent_id",
    "chat_bridge_wechat_official_api_base_url",
    "chat_bridge_wechat_official_app_id",
    "chat_bridge_wechat_official_app_secret",
    "chat_bridge_qq_api_base_url",
    "chat_bridge_qq_delivery_mode",
    "chat_bridge_qq_bot_app_id",
    "chat_bridge_qq_bot_app_secret",
    "chat_bridge_qq_bot_token",
    "chat_bridge_qq_napcat_api_base_url",
    "chat_bridge_qq_napcat_access_token",
    "chat_bridge_qq_napcat_webhook_token",
    "llm_provider",
    "llm_model",
    "llm_base_url",
    "llm_temperature",
    "llm_max_tokens",
    "llm_enable_thinking",
    "ui_heartbeat_enabled",
]


def _coerce_runtime_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    lowered = str(value or "").strip().lower()
    return lowered in {"1", "true", "yes", "enabled", "on"}


def chat_bridge_runtime_overrides_path(settings: Settings | None = None) -> Path:
    """Return the runtime override file path for chat bridge settings."""
    active = settings or get_settings()
    active.ensure_data_dir()
    return active.data_dir / _CHAT_BRIDGE_RUNTIME_OVERRIDES_FILE


def load_chat_bridge_runtime_overrides(settings: Settings | None = None) -> dict[str, Any]:
    """Load runtime bridge overrides from local persisted JSON file."""
    path = chat_bridge_runtime_overrides_path(settings)
    if not path.exists():
        return {}

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}

    if not isinstance(payload, dict):
        return {}

    result: dict[str, Any] = {}
    for key in _CHAT_BRIDGE_RUNTIME_OVERRIDE_KEYS:
        if key in payload:
            result[key] = payload[key]
    return result


def save_chat_bridge_runtime_overrides(
    overrides: dict[str, Any],
    settings: Settings | None = None,
) -> None:
    """Persist runtime bridge overrides to local JSON file."""
    path = chat_bridge_runtime_overrides_path(settings)
    payload = {
        key: overrides[key]
        for key in _CHAT_BRIDGE_RUNTIME_OVERRIDE_KEYS
        if key in overrides
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def apply_chat_bridge_runtime_overrides(
    settings: Settings | None = None,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Apply runtime bridge override values onto the active settings object."""
    active = settings or get_settings()
    rows = overrides if overrides is not None else load_chat_bridge_runtime_overrides(active)

    for key, value in rows.items():
        if key not in _CHAT_BRIDGE_RUNTIME_OVERRIDE_KEYS:
            continue

        if key in {
            "chat_bridge_enabled",
            "chat_bridge_inbound_enabled",
            "chat_bridge_inbound_debug",
            "chat_bridge_callback_delivery_enabled",
            "llm_enable_thinking",
            "ui_heartbeat_enabled",
        }:
            setattr(active, key, _coerce_runtime_bool(value))
            continue

        if key in {
            "chat_bridge_inbound_port",
            "chat_bridge_signature_ttl_seconds",
            "chat_bridge_event_id_ttl_seconds",
            "chat_bridge_callback_timeout_seconds",
            "llm_max_tokens",
        }:
            try:
                setattr(active, key, max(int(value), 1))
            except (TypeError, ValueError):
                continue
            continue

        if key == "llm_temperature":
            try:
                setattr(active, key, max(float(value), 0.0))
            except (TypeError, ValueError):
                continue
            continue

        if key == "llm_provider":
            normalized_provider = str(value or "").strip().lower()
            if normalized_provider not in {"openai", "anthropic", "ollama", "gemini", "github_copilot", "mock"}:
                continue
            setattr(active, key, normalized_provider)
            continue

        if key == "chat_bridge_default_mode":
            normalized_mode = str(value or "").strip().lower()
            setattr(active, key, "agent" if normalized_mode == "agent" else "ask")
            continue

        if key == "chat_bridge_wechat_delivery_mode":
            normalized = str(value or "").strip().lower()
            if normalized not in {"auto", "work", "official"}:
                normalized = "auto"
            setattr(active, key, normalized)
            continue

        if key == "chat_bridge_qq_delivery_mode":
            normalized = str(value or "").strip().lower()
            if normalized not in {"auto", "official", "napcat"}:
                normalized = "auto"
            setattr(active, key, normalized)
            continue

        setattr(active, key, str(value or "").strip())

    return rows
