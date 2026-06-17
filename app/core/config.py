from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Agent Playground"
    database_url: str = "sqlite+aiosqlite:///./agent_playground.db"
    sandbox_dir: Path = Path("sandbox/notes")
    max_agent_loops: int = 4
    model_provider: Literal["fake", "claude", "openai"] = "fake"
    llm_timeout_seconds: float = Field(default=60.0, gt=0)
    llm_max_retries: int = Field(default=2, ge=0)
    claude_model: str = "claude-opus-4-8"
    anthropic_api_key: str | None = None
    claude_max_tokens: int = Field(default=16000, gt=0)
    claude_effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"
    claude_thinking: Literal["disabled", "adaptive"] = "adaptive"
    openai_api_key: str | None = None
    openai_model: str = "gpt-4.1"
    openai_base_url: str | None = None
    openai_max_tokens: int = Field(default=16000, gt=0)
    openai_token_parameter: Literal["max_completion_tokens", "max_tokens"] = "max_completion_tokens"
    openai_tool_calling: bool = True
    openai_protocol_mode: Literal["auto", "on", "off"] = "auto"
    openai_compatibility_mode: Literal["auto", "on", "off"] | None = None
    openai_user_agent: str | None = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
    )
    summary_enabled: bool = True
    summary_trigger_message_count: int = Field(default=20, ge=0)
    summary_recent_message_keep: int = Field(default=6, ge=1)
    summary_max_chars: int = Field(default=2000, gt=0)

    model_config = SettingsConfigDict(env_file=".env", env_prefix="AGENT_PLAYGROUND_")

    @property
    def effective_openai_protocol_mode(self) -> Literal["auto", "on", "off"]:
        return self.openai_compatibility_mode or self.openai_protocol_mode

    @property
    def openai_protocol_mode_uses_legacy_env(self) -> bool:
        return self.openai_compatibility_mode is not None


@lru_cache
def get_settings() -> Settings:
    return Settings()
