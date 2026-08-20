from __future__ import annotations

import logging
import time
from typing import Literal, Protocol, runtime_checkable

import instructor
from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

from graphcheck.errors import GraphCheckError
from graphcheck.generation.config import GenerateConfig
from graphcheck.generation.proposals import (
    MAX_CANDIDATES,
    ProposalRequest,
    ProposedCheck,
    RawProposal,
    RawProposalBatch,
)

MAX_PROVIDER_CALLS = 2
PROVIDER_TIMEOUT_SECONDS = 120
MAX_OUTPUT_TOKENS = 8192
GEMMA_MAX_OUTPUT_TOKENS = 2048
GOOGLE_RETRY_DELAY_SECONDS = 1

# Instructor's own exception logging can contain provider exception strings or response material.
# GraphCheck emits only its safe category mapping.
logging.getLogger("instructor").setLevel(logging.CRITICAL)


class _GoogleProposalBase(BaseModel):
    """Common fields for Google-only, kind-specific function arguments."""

    model_config = ConfigDict(extra="ignore", strict=True)

    id: str = Field(description="Lowercase kebab-case candidate identifier.")
    tags: list[str] = Field(default=None)  # type: ignore[assignment]


class _GoogleConformanceProposal(_GoogleProposalBase):
    check: Literal["completeness", "uniqueness"]
    label: str
    property: str
    threshold: float = Field(default=None, gt=0, le=1)  # type: ignore[assignment]


class _GoogleCompetencyProposal(_GoogleProposalBase):
    question: str
    query: str
    params: dict[str, JsonValue] = Field(default=None)  # type: ignore[assignment]
    columns: list[str] = Field(min_length=1)


class _GoogleDriftProposal(_GoogleProposalBase):
    metric: Literal["node_count"]
    label: str
    baseline: str = Field(default=None)  # type: ignore[assignment]
    max_change_pct: float = Field(ge=0)


class _GoogleRawProposalBatch(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    conformance: list[_GoogleConformanceProposal] = Field(max_length=MAX_CANDIDATES)
    competency: list[_GoogleCompetencyProposal] = Field(max_length=MAX_CANDIDATES)
    drift: list[_GoogleDriftProposal] = Field(max_length=MAX_CANDIDATES)


class _GeminiProposalBatch(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    candidates: list[ProposedCheck] = Field(max_length=MAX_CANDIDATES)


_GOOGLE_TOOL_INSTRUCTION = (
    "Google tool transport: return no more than {requested_count} total items across the "
    "conformance, competency, and drift arrays, using an empty array for unused kinds. Fields are "
    "flat: conformance uses label/property instead of with; competency uses columns instead of "
    "expect; drift uses label/max_change_pct instead of target/tolerance. Do not emit kind or spec."
)


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
        kwargs: dict[str, object] = {"temperature": config.temperature}
        if config.provider == "google":
            kwargs["http_options"] = {
                "timeout": PROVIDER_TIMEOUT_SECONDS * 1000,
                "retry_options": {"attempts": 1},
                **({"base_url": str(config.base_url)} if config.base_url is not None else {}),
            }
        else:
            kwargs["timeout"] = PROVIDER_TIMEOUT_SECONDS
        if config.provider == "openai":
            # Instructor's OpenAI builder consumes this as an SDK-construction option.
            # Its Anthropic and Ollama builders instead retain it as a request default,
            # which would collide with propose(max_retries=0) below.
            kwargs["max_retries"] = 0
            kwargs["max_completion_tokens"] = MAX_OUTPUT_TOKENS
        else:
            kwargs["max_tokens"] = (
                GEMMA_MAX_OUTPUT_TOKENS if config.uses_google_tool_transport else MAX_OUTPUT_TOKENS
            )
        if api_key is not None:
            kwargs["api_key"] = api_key
        if config.base_url is not None and config.provider not in {"anthropic", "google"}:
            kwargs["base_url"] = str(config.base_url)
        if config.provider == "anthropic" or config.uses_google_tool_transport:
            kwargs["mode"] = instructor.Mode.TOOLS
        elif config.provider == "ollama" or config.uses_google_native_structured_output:
            kwargs["mode"] = instructor.Mode.JSON
        elif config.provider != "openai":
            raise GraphCheckError(
                "generate.provider_unsupported",
                f"Generation provider is not supported: {config.provider}",
                "Set `generate.provider` to `anthropic`, `google`, `openai`, or `ollama`.",
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
            response_model = (
                _GoogleRawProposalBatch
                if self._config.uses_google_tool_transport
                else (
                    _GeminiProposalBatch
                    if self._config.uses_google_native_structured_output
                    else RawProposalBatch
                )
            )
            system_prompt = (
                _google_system_prompt(request)
                if self._config.uses_google_tool_transport
                else request.system_prompt
            )
            call = {
                "response_model": response_model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": request.user_prompt},
                ],
                "max_retries": 0,
            }
            for attempt in range(2 if self._config.provider == "google" else 1):
                try:
                    result = self._client.create(**call)
                    break
                except Exception as exc:
                    if attempt == 0 and _retryable_google_server_error(exc):
                        time.sleep(GOOGLE_RETRY_DELAY_SECONDS)
                        continue
                    raise
            if self._config.uses_google_tool_transport:
                batch = (
                    result
                    if isinstance(result, _GoogleRawProposalBatch)
                    else _GoogleRawProposalBatch.model_validate(
                        result.model_dump(mode="json", exclude_unset=True)
                        if isinstance(result, BaseModel)
                        else result
                    )
                )
                return RawProposalBatch(
                    candidates=[
                        _normalize_google_proposal(kind, candidate)
                        for kind, candidates in (
                            ("conformance", batch.conformance),
                            ("competency", batch.competency),
                            ("drift", batch.drift),
                        )
                        for candidate in candidates
                    ]
                )
            if self._config.uses_google_native_structured_output:
                batch = (
                    result
                    if isinstance(result, _GeminiProposalBatch)
                    else _GeminiProposalBatch.model_validate(
                        result.model_dump(mode="json", exclude_unset=True)
                        if isinstance(result, BaseModel)
                        else result
                    )
                )
                return RawProposalBatch(
                    candidates=[_normalize_gemini_proposal(c) for c in batch.candidates]
                )
            return RawProposalBatch.model_validate(result)
        except GraphCheckError:
            raise
        except Exception as exc:
            raise _map_provider_exception(exc, during_output=True) from None


def _google_system_prompt(request: ProposalRequest) -> str:
    policy = request.system_prompt.partition("\n\nPROPOSAL_SCHEMA=")[0]
    instruction = _GOOGLE_TOOL_INSTRUCTION.format(requested_count=request.requested_count)
    return f"{policy}\n\n{instruction}"


def _normalize_google_proposal(
    kind: Literal["conformance", "competency", "drift"],
    candidate: _GoogleConformanceProposal | _GoogleCompetencyProposal | _GoogleDriftProposal,
) -> RawProposal:
    spec: dict[str, JsonValue] = {"id": candidate.id}
    if candidate.tags is not None:
        spec["tags"] = candidate.tags
    if kind == "conformance" and isinstance(candidate, _GoogleConformanceProposal):
        spec.update(
            check=candidate.check,
            **{
                "with": {
                    "label": candidate.label,
                    "property": candidate.property,
                    **(
                        {"threshold": candidate.threshold}
                        if candidate.check == "completeness" and candidate.threshold is not None
                        else {}
                    ),
                }
            },
        )
    elif kind == "competency" and isinstance(candidate, _GoogleCompetencyProposal):
        spec.update(
            question=candidate.question,
            query=candidate.query,
            expect={"columns": candidate.columns},
        )
        if candidate.params is not None:
            spec["params"] = candidate.params
    elif kind == "drift" and isinstance(candidate, _GoogleDriftProposal):
        spec.update(
            metric=candidate.metric,
            target={"label": candidate.label},
            tolerance={"max_change_pct": candidate.max_change_pct},
        )
        if candidate.baseline is not None:
            spec["baseline"] = candidate.baseline
    return RawProposal(kind=kind, spec=spec)


def _normalize_gemini_proposal(candidate: ProposedCheck) -> RawProposal:
    spec = candidate.model_dump(mode="json", by_alias=True, exclude_none=True, exclude_unset=True)
    return RawProposal(kind=spec.pop("kind"), spec=spec)


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


def _provider_exception(exc: Exception) -> Exception:
    while "instructorretry" in type(exc).__name__.casefold() and isinstance(
        exc.__cause__, Exception
    ):
        exc = exc.__cause__
    return exc


def _retryable_google_server_error(exc: Exception) -> bool:
    exc = _provider_exception(exc)
    return getattr(exc, "status_code", getattr(exc, "code", None)) in {500, 502, 503}


def _map_provider_exception(exc: Exception, *, during_output: bool) -> GraphCheckError:
    """Map by safe exception category; never include provider response text."""

    exc = _provider_exception(exc)
    name = type(exc).__name__.casefold()
    module = type(exc).__module__.casefold()
    status_code = getattr(exc, "status_code", getattr(exc, "code", None))
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
    if status_code in {401, 403} or "authentication" in name or "permissiondenied" in name:
        return GraphCheckError(
            "generate.provider_auth_failed",
            "The provider rejected authentication.",
            "Verify the configured environment variable and provider account.",
        )
    if status_code == 429 or "ratelimit" in name:
        return GraphCheckError(
            "generate.provider_rate_limited",
            "The provider rate limit was reached.",
            "Retry later or reduce `--count`.",
        )
    if status_code in {408, 504} or "timeout" in name or isinstance(exc, TimeoutError):
        return GraphCheckError(
            "generate.provider_timeout",
            "The provider request exceeded the 120 second timeout.",
            "Retry, reduce docs/count, or verify the local service.",
        )
    if (isinstance(status_code, int) and 500 <= status_code < 600) or "servererror" in name:
        return GraphCheckError(
            "generate.provider_unavailable",
            "The provider remained unavailable after a bounded retry.",
            "Retry later, reduce document size, or check the provider status page.",
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
