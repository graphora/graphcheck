from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

import instructor
from pydantic import ValidationError

from graphcheck.errors import GraphCheckError
from graphcheck.generation.config import GenerateConfig
from graphcheck.generation.proposals import ProposalRequest, RawProposalBatch

MAX_PROVIDER_CALLS = 2
PROVIDER_TIMEOUT_SECONDS = 120
MAX_OUTPUT_TOKENS = 8192

# Instructor's own exception logging can contain provider exception strings or response material.
# GraphCheck emits only its safe category mapping.
logging.getLogger("instructor").setLevel(logging.CRITICAL)


@runtime_checkable
class StructuredOutputClient(Protocol):
    def propose(self, request: ProposalRequest) -> RawProposalBatch:
        """Return one provider response parsed into the bounded raw envelope."""


class InstructorStructuredOutputClient:
    """Small provider-neutral boundary around Instructor's unified client."""

    def __init__(
        self,
        config: GenerateConfig,
        api_key: str | None,
    ) -> None:
        self._config = config
        provider_model = f"{config.provider}/{config.model}"
        kwargs: dict[str, object] = {
            "timeout": PROVIDER_TIMEOUT_SECONDS,
            "temperature": config.temperature,
        }
        if config.provider == "openai":
            # Instructor's OpenAI builder consumes this as an SDK-construction option.
            # Its Anthropic and Ollama builders instead retain it as a request default,
            # which would collide with propose(max_retries=0) below.
            kwargs["max_retries"] = 0
            kwargs["max_completion_tokens"] = MAX_OUTPUT_TOKENS
        else:
            kwargs["max_tokens"] = MAX_OUTPUT_TOKENS
        if api_key is not None:
            kwargs["api_key"] = api_key
        if config.base_url is not None and config.provider != "anthropic":
            kwargs["base_url"] = str(config.base_url)
        if config.provider == "anthropic":
            kwargs["mode"] = instructor.Mode.TOOLS
        elif config.provider == "ollama":
            kwargs["mode"] = instructor.Mode.JSON
        elif config.provider != "openai":
            raise GraphCheckError(
                "generate.provider_unsupported",
                f"Generation provider is not supported: {config.provider}",
                "Set `generate.provider` to `anthropic`, `openai`, or `ollama`.",
            )
        try:
            self._client = instructor.from_provider(provider_model, **kwargs)
            _configure_sdk_client(
                self._client,
                provider=config.provider,
                api_key=api_key,
                base_url=config.normalized_base_url,
            )
        except GraphCheckError:
            raise
        except Exception as exc:
            raise _map_provider_exception(exc, during_output=False) from None

    def propose(self, request: ProposalRequest) -> RawProposalBatch:
        try:
            result = self._client.create(
                response_model=RawProposalBatch,
                messages=[
                    {"role": "system", "content": request.system_prompt},
                    {"role": "user", "content": request.user_prompt},
                ],
                max_retries=0,
            )
            return RawProposalBatch.model_validate(result)
        except GraphCheckError:
            raise
        except Exception as exc:
            raise _map_provider_exception(exc, during_output=True) from None


def create_structured_output_client(
    config: GenerateConfig,
    api_key: str | None,
) -> StructuredOutputClient:
    return InstructorStructuredOutputClient(config, api_key)


def _configure_sdk_client(
    client: object,
    *,
    provider: str,
    api_key: str | None,
    base_url: str | None,
) -> None:
    """Apply transport settings that Instructor does not preserve for every provider."""

    sdk_client = getattr(client, "client", None)
    if sdk_client is None:
        return
    if hasattr(sdk_client, "max_retries"):
        sdk_client.max_retries = 0
    # Instructor 1.15's Anthropic provider treats unknown construction kwargs as
    # messages.create defaults. Configure the SDK itself so disclosure and transport agree.
    if provider == "anthropic" and base_url is not None:
        sdk_client.base_url = base_url
    # Instructor's Ollama builder uses the OpenAI SDK and currently replaces the key while
    # constructing it. Restore an explicitly configured key directly without copying it into
    # any conventional provider environment variable.
    if provider == "ollama" and api_key is not None:
        sdk_client.api_key = api_key


def _map_provider_exception(exc: Exception, *, during_output: bool) -> GraphCheckError:
    """Map by safe exception category; never include provider response text."""

    name = type(exc).__name__.casefold()
    module = type(exc).__module__.casefold()
    if isinstance(exc, ValidationError) or any(
        marker in name
        for marker in (
            "instructorretry",
            "validation",
            "parsing",
            "jsondecode",
        )
    ):
        return GraphCheckError(
            "generate.output_invalid",
            "The provider did not return a valid structured candidate batch.",
            "Choose a model with structured-output support or reduce docs/count.",
        )
    if "authentication" in name or "permissiondenied" in name:
        return GraphCheckError(
            "generate.provider_auth_failed",
            "The provider rejected authentication.",
            "Verify the configured environment variable and provider account.",
        )
    if "ratelimit" in name:
        return GraphCheckError(
            "generate.provider_rate_limited",
            "The provider rate limit was reached.",
            "Retry later or reduce `--count`.",
        )
    if "timeout" in name or isinstance(exc, TimeoutError):
        return GraphCheckError(
            "generate.provider_timeout",
            "The provider request exceeded the 120 second timeout.",
            "Retry, reduce docs/count, or verify the local service.",
        )
    if (
        "connection" in name
        or "connect" in name
        or isinstance(exc, ConnectionError)
        or module.startswith("httpx")
        and "network" in name
    ):
        return GraphCheckError(
            "generate.provider_unreachable",
            "The configured provider destination could not be reached.",
            "Start the local service or verify `generate.base_url` and network access.",
        )
    if during_output and ("response" in name or "tool" in name):
        return GraphCheckError(
            "generate.output_invalid",
            "The provider did not return a valid structured candidate batch.",
            "Choose a model with structured-output support or reduce docs/count.",
        )
    return GraphCheckError(
        "generate.provider_failed",
        f"The provider request failed ({type(exc).__name__}).",
        "Verify provider/model/base URL and retry; use local Ollama if egress is unavailable.",
    )
