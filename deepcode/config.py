"""Configuration management for DeepCode Agent."""

from __future__ import annotations

from pathlib import Path
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
    llm_provider: Literal["openai", "anthropic", "ollama", "mock"] = Field(
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
        default="ls,cat,grep,find,python3,pip,echo,pwd,head,tail,wc",
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

    @field_validator("data_dir", mode="before")
    @classmethod
    def expand_data_dir(cls, v: str | Path) -> Path:
        """Expand ``~`` in the data directory path."""
        return Path(v).expanduser()

    @property
    def resolved_db_url(self) -> str:
        """Return the effective database URL."""
        if self.db_url:
            return self.db_url
        return f"sqlite+aiosqlite:///{self.data_dir / 'deepcode.db'}"

    @property
    def allowed_shell_commands(self) -> list[str]:
        """Return the list of allowed shell commands."""
        return [cmd.strip() for cmd in self.allowed_shells.split(",") if cmd.strip()]

    def ensure_data_dir(self) -> None:
        """Create the data directory if it does not exist."""
        self.data_dir.mkdir(parents=True, exist_ok=True)


def get_settings() -> Settings:
    """Return a cached singleton :class:`Settings` instance."""
    return _settings


# Module-level singleton – created once on import.
_settings = Settings()
