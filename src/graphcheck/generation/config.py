from __future__ import annotations

import os
from typing import Literal

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, field_validator, model_validator

from graphcheck.errors import GraphCheckError

OPENAI_COMPATIBLE_BASE_URLS = {
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/",
    "openrouter": "https://openrouter.ai/api/v1",
}


class GenerateConfig(BaseModel):
    """Strict provider configuration for ``graphcheck generate``."""

    model_config = ConfigDict(extra="forbid", strict=True)

    provider: Literal["anthropic", "gemini", "openai", "openrouter", "ollama"]
    model: str
    api_key_env: str | None = None
    base_url: AnyHttpUrl | None = None
    temperature: float = Field(default=0, ge=0, le=2)

    @field_validator("model")
    @classmethod
    def model_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("model must not be blank")
        return value

    @field_validator("api_key_env")
    @classmethod
    def api_key_env_must_not_be_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("api_key_env must not be blank")
        return value

    @model_validator(mode="after")
    def provider_requirements(self) -> GenerateConfig:
        if self.provider != "ollama" and self.api_key_env is None:
            raise ValueError(f"{self.provider} requires api_key_env")
        if self.provider == "ollama" and self.base_url is None:
            raise ValueError(
                "ollama requires an explicit base_url (recommended: http://localhost:11434/v1)"
            )
        if self.base_url is not None and any(
            part is not None
            for part in (
                self.base_url.username,
                self.base_url.password,
                self.base_url.query,
                self.base_url.fragment,
            )
        ):
            raise ValueError(
                "base_url must not contain embedded credentials, query parameters, or fragments"
            )
        return self

    @property
    def normalized_base_url(self) -> str | None:
        return (
            OPENAI_COMPATIBLE_BASE_URLS.get(self.provider)
            if self.base_url is None
            else str(self.base_url)
        )

    @property
    def destination(self) -> str:
        if self.normalized_base_url is not None:
            return self.normalized_base_url
        return f"{self.provider.capitalize()} provider default"


def resolve_api_key(
    config: GenerateConfig,
    *,
    environ: os._Environ[str] | dict[str, str] | None = None,
) -> str | None:
    """Resolve the configured secret without changing or copying the environment."""

    if config.api_key_env is None:
        return None
    source = os.environ if environ is None else environ
    value = source.get(config.api_key_env)
    if value is None or not value.strip():
        raise GraphCheckError(
            "generate.api_key_missing",
            f"Environment variable {config.api_key_env} is not set.",
            f"set ${config.api_key_env}",
        )
    return value
