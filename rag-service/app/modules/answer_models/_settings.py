"""Private answer-model settings and effective endpoint rules."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


_DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "llm.json"
_DEFAULT_URLS = {"openai": "https://api.openai.com/v1", "deepseek": "https://api.deepseek.com/v1"}


class _Update(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, hide_input_in_errors=True)

    provider: Literal["openai", "deepseek"]
    model: str = Field(min_length=1, max_length=200, strict=True)
    base_url: AnyHttpUrl = Field(max_length=2048)
    api_key: SecretStr | None = Field(default=None, max_length=4096)

    @field_validator("base_url")
    @classmethod
    def address_has_no_credentials(cls, value: AnyHttpUrl) -> AnyHttpUrl:
        if value.username or value.password or value.query or value.fragment:
            raise ValueError("use the separate API key field")
        return value


class _Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="LLM_", env_file=".env", env_file_encoding="utf-8",
        extra="ignore", hide_input_in_errors=True, frozen=True,
    )

    provider: Literal["openai", "deepseek"] = "openai"
    model: str = Field(min_length=1)
    base_url: AnyHttpUrl | None = None
    api_key: SecretStr = Field(min_length=1)
    timeout_seconds: float = Field(default=60.0, gt=0)


def _resolve_base_url(settings: _Settings) -> str:
    """Match provider SDK precedence before displaying or using a destination."""
    address = settings.base_url
    if address is None:
        if settings.provider == "deepseek":
            address = os.getenv("DEEPSEEK_API_BASE", _DEFAULT_URLS["deepseek"])
        else:
            address = os.getenv("OPENAI_API_BASE") or None
            if address is None:
                gateway = os.getenv("LANGSMITH_GATEWAY", "")
                if gateway.lower() not in ("", "false", "0", "no"):
                    base = (
                        "https://gateway.smith.langchain.com"
                        if gateway.lower() in ("true", "1", "yes") else gateway.rstrip("/")
                    )
                    address = f"{base}/openai/v1"
            if address is None:
                address = os.getenv("OPENAI_BASE_URL", _DEFAULT_URLS["openai"])
    validated = _Update.address_has_no_credentials(AnyHttpUrl(address))
    return str(validated).rstrip("/")
