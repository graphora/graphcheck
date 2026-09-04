from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from graphcheck.errors import GraphCheckError
from graphcheck.generation.client import InstructorStructuredOutputClient, _map_provider_exception
from graphcheck.generation.config import GenerateConfig
from graphcheck.generation.proposals import ProposalRequest, RawProposalBatch


class FakeInstructor:
    def __init__(self) -> None:
        self.client = SimpleNamespace(max_retries=9, api_key="library-default")
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs["response_model"].__name__ == "_GoogleRawProposalBatch":
            return kwargs["response_model"](conformance=[], competency=[], drift=[])
        return kwargs["response_model"](candidates=[])


@pytest.mark.parametrize(
    ("provider", "model", "base_url", "api_key", "expected_mode"),
    [
        ("anthropic", "model", None, "key", "TOOLS"),
        ("google", "gemma-4-31b-it", "https://proxy.example/v1", "key", "TOOLS"),
        ("google", "gemini-2.5-flash", "https://proxy.example/v1", "key", "JSON"),
        ("openai", "model", None, "key", None),
        ("ollama", "model", "http://localhost:11434/v1", None, "JSON"),
    ],
)
def test_adapter_constructs_each_provider_without_network(
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
    model: str,
    base_url: str | None,
    api_key: str | None,
    expected_mode: str | None,
) -> None:
    created: dict[str, object] = {}
    fake = FakeInstructor()

    def from_provider(model: str, **kwargs):
        created.update(model=model, **kwargs)
        return fake

    monkeypatch.setattr("graphcheck.generation.client.instructor.from_provider", from_provider)
    config = GenerateConfig(
        provider=provider,
        model=model,
        api_key_env="KEY" if provider != "ollama" else None,
        base_url=base_url,
    )
    client = InstructorStructuredOutputClient(config, api_key)
    batch = client.propose(
        ProposalRequest(
            system_prompt="system",
            user_prompt="user",
            requested_count=1,
            attempt=1,
        )
    )

    assert created["model"] == f"{provider}/{model}"
    if provider == "openai":
        assert created["max_retries"] == 0
    else:
        assert "max_retries" not in created
    if provider == "google":
        assert created["http_options"] == {
            "timeout": 120_000,
            "retry_options": {"attempts": 1},
            "base_url": "https://proxy.example/v1",
        }
        assert created["max_tokens"] == (8192 if model.startswith("gemini-") else 2048)
        assert "timeout" not in created
    else:
        assert created["timeout"] == 120
    assert fake.client.max_retries == 0
    assert batch.candidates == []
    assert fake.calls[0]["max_retries"] == 0
    if provider == "google" and model.startswith("gemma-"):
        assert fake.calls[0]["response_model"].__name__ == "_GoogleRawProposalBatch"
        assert "Google tool transport" in fake.calls[0]["messages"][0]["content"]
    elif provider == "google":
        assert fake.calls[0]["response_model"].__name__ == "_GeminiProposalBatch"
        assert "Google tool transport" not in fake.calls[0]["messages"][0]["content"]
    else:
        assert fake.calls[0]["response_model"] is RawProposalBatch
        assert "Google tool transport" not in fake.calls[0]["messages"][0]["content"]
    if api_key is None:
        assert "api_key" not in created
    else:
        assert created["api_key"] == api_key
    if expected_mode is None:
        assert "mode" not in created
    else:
        assert created["mode"].name == expected_mode


def test_adapter_maps_errors_without_leaking_exception_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "DO-NOT-LEAK"

    def fail(*args, **kwargs):
        raise TimeoutError(secret)

    monkeypatch.setattr("graphcheck.generation.client.instructor.from_provider", fail)
    with pytest.raises(GraphCheckError) as caught:
        InstructorStructuredOutputClient(
            GenerateConfig(
                provider="openai",
                model="model",
                api_key_env="KEY",
            ),
            secret,
        )
    assert caught.value.error.code == "generate.provider_timeout"
    assert secret not in caught.value.error.message


@pytest.mark.parametrize(
    ("status_code", "expected"),
    [
        (401, "generate.provider_auth_failed"),
        (429, "generate.provider_rate_limited"),
        (500, "generate.provider_unavailable"),
    ],
)
def test_google_status_errors_are_mapped_without_leaking_response(
    status_code: int,
    expected: str,
) -> None:
    GoogleClientError = type("GoogleClientError", (Exception,), {"code": status_code})
    mapped = _map_provider_exception(GoogleClientError("DO-NOT-LEAK"), during_output=True)

    assert mapped.error.code == expected
    assert "DO-NOT-LEAK" not in mapped.error.message


def test_google_retries_server_error_once_without_retrying_timeouts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeInstructor()
    GoogleServerError = type("GoogleServerError", (Exception,), {"code": 500})
    outcomes = [GoogleServerError(), {"conformance": [], "competency": [], "drift": []}]
    fake.create = lambda **kwargs: (
        (_ for _ in ()).throw(outcomes.pop(0)) if len(outcomes) == 2 else outcomes.pop(0)
    )
    sleeps: list[int] = []
    monkeypatch.setattr(
        "graphcheck.generation.client.instructor.from_provider", lambda *args, **kwargs: fake
    )
    monkeypatch.setattr("graphcheck.generation.client.time.sleep", sleeps.append)
    client = InstructorStructuredOutputClient(
        GenerateConfig(provider="google", model="gemma", api_key_env="KEY"), "key"
    )

    assert (
        client.propose(
            ProposalRequest(
                system_prompt="system", user_prompt="user", requested_count=1, attempt=1
            )
        ).candidates
        == []
    )
    assert sleeps == [1]

    calls = 0

    def timeout(**kwargs):
        nonlocal calls
        calls += 1
        raise TimeoutError

    fake.create = timeout
    with pytest.raises(GraphCheckError, match="120 second timeout"):
        client.propose(
            ProposalRequest(
                system_prompt="system", user_prompt="user", requested_count=1, attempt=1
            )
        )
    assert calls == 1


def test_ollama_optional_key_is_restored_directly_to_sdk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeInstructor()
    monkeypatch.setattr(
        "graphcheck.generation.client.instructor.from_provider",
        lambda model, **kwargs: fake,
    )

    InstructorStructuredOutputClient(
        GenerateConfig(
            provider="ollama",
            model="model",
            api_key_env="CORP_LLM_TOKEN",
            base_url="http://localhost:11434/v1",
        ),
        "custom-key",
    )

    assert fake.client.api_key == "custom-key"


@pytest.mark.parametrize(
    ("provider", "base_url", "api_key"),
    [
        ("anthropic", None, "key"),
        ("google", None, "key"),
        ("openai", None, "key"),
        ("ollama", "http://localhost:11434/v1", None),
    ],
)
def test_pinned_instructor_defaults_do_not_collide_with_validation_retries(
    provider: str,
    base_url: str | None,
    api_key: str | None,
) -> None:
    config = GenerateConfig(
        provider=provider,
        model="model",
        api_key_env="KEY" if provider != "ollama" else None,
        base_url=base_url,
    )
    client = InstructorStructuredOutputClient(config, api_key)
    captured: dict[str, object] = {}

    def create(**kwargs):
        captured.update(kwargs)
        response_model = kwargs["response_model"]
        return (
            response_model(conformance=[], competency=[], drift=[])
            if response_model.__name__ == "_GoogleRawProposalBatch"
            else response_model(candidates=[])
        )

    client._client.create_fn = create

    batch = client.propose(
        ProposalRequest(
            system_prompt="system",
            user_prompt="user",
            requested_count=1,
            attempt=1,
        )
    )

    assert batch.candidates == []
    assert captured["max_retries"] == 0


def test_pinned_gemma_adapter_builds_sdk_tool_request(monkeypatch: pytest.MonkeyPatch) -> None:
    client = InstructorStructuredOutputClient(
        GenerateConfig(
            provider="google",
            model="gemma-4-26b-a4b-it",
            api_key_env="GEMINI_API_KEY",
        ),
        "key",
    )
    captured: dict[str, object] = {}

    def generate_content(**kwargs):
        captured.update(kwargs)
        raise TimeoutError

    monkeypatch.setattr(client._client.client.models, "generate_content", generate_content)
    try:
        with pytest.raises(GraphCheckError) as caught:
            client.propose(
                ProposalRequest(
                    system_prompt="policy\n\nPROPOSAL_SCHEMA=large\nPACK_CATALOG=large",
                    user_prompt="user",
                    requested_count=1,
                    attempt=1,
                )
            )
        config = captured["config"]
        assert caught.value.error.code == "generate.provider_timeout"
        assert captured["model"] == "gemma-4-26b-a4b-it"
        assert config.max_output_tokens == 2048
        assert config.temperature == 0
        assert config.tools
        declaration = config.tools[0].function_declarations[0]
        parameters = declaration.parameters
        assert set(parameters.required) == {"conformance", "competency", "drift"}
        conformance = parameters.properties["conformance"].items
        competency = parameters.properties["competency"].items
        drift = parameters.properties["drift"].items
        assert set(conformance.required) == {"id", "check", "label", "property"}
        assert set(competency.required) == {"id", "question", "query", "columns"}
        assert set(drift.required) == {"id", "metric", "label", "max_change_pct"}
        assert "kind" not in conformance.properties
        assert "spec" not in conformance.properties
        assert "with" not in conformance.properties
        assert "expect" not in competency.properties
        assert "target" not in drift.properties
        assert "tolerance" not in drift.properties
        assert "Google tool transport" in config.system_instruction
        assert "no more than 1 total items" in config.system_instruction
        assert "PROPOSAL_SCHEMA" not in config.system_instruction
        assert "PACK_CATALOG" not in config.system_instruction

        from google.genai import types

        response = types.GenerateContentResponse(
            candidates=[
                types.Candidate(
                    content=types.Content(
                        role="model",
                        parts=[
                            types.Part(
                                function_call=types.FunctionCall(
                                    name="_GoogleRawProposalBatch",
                                    args={
                                        "conformance": [
                                            {
                                                "id": "customer-id-complete",
                                                "check": "completeness",
                                                "label": "Customer",
                                                "property": "id",
                                            }
                                        ],
                                        "competency": [],
                                        "drift": [],
                                    },
                                )
                            )
                        ],
                    )
                )
            ]
        )
        monkeypatch.setattr(
            client._client.client.models, "generate_content", lambda **kwargs: response
        )
        batch = client.propose(
            ProposalRequest(
                system_prompt="system", user_prompt="user", requested_count=1, attempt=1
            )
        )
        assert batch.candidates[0].spec == {
            "id": "customer-id-complete",
            "check": "completeness",
            "with": {"label": "Customer", "property": "id"},
        }
    finally:
        client._client.client.close()


def test_pinned_gemini_adapter_builds_native_structured_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = InstructorStructuredOutputClient(
        GenerateConfig(
            provider="google",
            model="gemini-2.5-flash",
            api_key_env="GEMINI_API_KEY",
        ),
        "key",
    )
    captured: dict[str, object] = {}

    def generate_content(**kwargs):
        captured.update(kwargs)
        raise TimeoutError

    monkeypatch.setattr(client._client.client.models, "generate_content", generate_content)
    request = ProposalRequest(
        system_prompt="policy\n\nPROPOSAL_SCHEMA=full\nPACK_CATALOG=full",
        user_prompt="user",
        requested_count=3,
        attempt=1,
    )
    try:
        with pytest.raises(GraphCheckError) as caught:
            client.propose(request)
        config = captured["config"]
        assert caught.value.error.code == "generate.provider_timeout"
        assert captured["model"] == "gemini-2.5-flash"
        assert config.max_output_tokens == 8192
        assert config.response_mime_type == "application/json"
        assert config.response_schema.__name__ == "_GeminiProposalBatch"
        assert not config.tools
        assert "PROPOSAL_SCHEMA=full" in config.system_instruction
        assert "PACK_CATALOG=full" in config.system_instruction
        assert "Google tool transport" not in config.system_instruction

        from google.genai import types

        payload = {
            "candidates": [
                {
                    "kind": "conformance",
                    "id": "account-balance-type",
                    "check": "property_type",
                    "with": {
                        "label": "Account",
                        "property": "balance",
                        "type": "integer",
                    },
                },
                {
                    "kind": "competency",
                    "id": "customer-count",
                    "question": "How many customers exist?",
                    "query": "MATCH (c:Customer) RETURN count(c) AS count",
                    "expect": {"columns": ["count"], "rows": {"exactly": 1}},
                },
                {
                    "kind": "drift",
                    "id": "owns-count-stable",
                    "metric": "relationship_count",
                    "target": {"type": "OWNS"},
                    "tolerance": {"max_drop_pct": 10},
                },
            ]
        }
        response = types.GenerateContentResponse(
            candidates=[
                types.Candidate(
                    content=types.Content(
                        role="model",
                        parts=[types.Part(text=json.dumps(payload))],
                    )
                )
            ]
        )
        monkeypatch.setattr(
            client._client.client.models, "generate_content", lambda **kwargs: response
        )
        batch = client.propose(request)
        assert [candidate.kind for candidate in batch.candidates] == [
            "conformance",
            "competency",
            "drift",
        ]
        assert batch.candidates[0].spec["check"] == "property_type"
        assert batch.candidates[1].spec["expect"] == {
            "rows": {"exactly": 1},
            "columns": ["count"],
        }
        assert batch.candidates[2].spec == {
            "id": "owns-count-stable",
            "metric": "relationship_count",
            "target": {"type": "OWNS"},
            "tolerance": {"max_drop_pct": 10},
        }
    finally:
        client._client.client.close()


def test_gemma_tool_result_normalizes_to_shared_raw_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeInstructor()
    fake.create = lambda **kwargs: {
        "conformance": [
            {
                "id": "customer-id-complete",
                "check": "completeness",
                "label": "Customer",
                "property": "id",
            }
        ],
        "competency": [
            {
                "id": "customer-count",
                "question": "How many customers exist?",
                "query": "MATCH (c:Customer) RETURN count(c) AS count",
                "columns": ["count"],
                "expect": {"bogus": True},
            }
        ],
        "drift": [
            {
                "id": "customer-count-stable",
                "metric": "node_count",
                "label": "Customer",
                "max_change_pct": 10,
                "tolerance": {},
            }
        ],
    }
    monkeypatch.setattr(
        "graphcheck.generation.client.instructor.from_provider", lambda model, **kwargs: fake
    )

    client = InstructorStructuredOutputClient(
        GenerateConfig(provider="google", model="gemma", api_key_env="KEY"), "key"
    )
    batch = client.propose(
        ProposalRequest(system_prompt="system", user_prompt="user", requested_count=1, attempt=1)
    )

    assert batch == RawProposalBatch(
        candidates=[
            {
                "kind": "conformance",
                "spec": {
                    "id": "customer-id-complete",
                    "check": "completeness",
                    "with": {"label": "Customer", "property": "id"},
                },
            },
            {
                "kind": "competency",
                "spec": {
                    "id": "customer-count",
                    "question": "How many customers exist?",
                    "query": "MATCH (c:Customer) RETURN count(c) AS count",
                    "expect": {"columns": ["count"]},
                },
            },
            {
                "kind": "drift",
                "spec": {
                    "id": "customer-count-stable",
                    "metric": "node_count",
                    "target": {"label": "Customer"},
                    "tolerance": {"max_change_pct": 10.0},
                },
            },
        ]
    )


def test_anthropic_base_url_is_applied_to_sdk_not_request_defaults() -> None:
    client = InstructorStructuredOutputClient(
        GenerateConfig(
            provider="anthropic",
            model="model",
            api_key_env="KEY",
            base_url="https://proxy.example/v1",
        ),
        "key",
    )

    assert str(client._client.client.base_url) == "https://proxy.example/v1/"
    assert "base_url" not in client._client.kwargs
