from __future__ import annotations

from types import SimpleNamespace

import pytest

from graphcheck.errors import GraphCheckError
from graphcheck.generation.client import InstructorStructuredOutputClient
from graphcheck.generation.config import GenerateConfig
from graphcheck.generation.proposals import ProposalRequest, RawProposalBatch


class FakeInstructor:
    def __init__(self) -> None:
        self.client = SimpleNamespace(max_retries=9, api_key="library-default")
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return RawProposalBatch(candidates=[])


@pytest.mark.parametrize(
    ("provider", "base_url", "api_key", "expected_adapter", "expected_mode"),
    [
        ("anthropic", None, "key", "anthropic", "TOOLS"),
        ("gemini", None, "key", "openai", "JSON_SCHEMA"),
        ("openai", None, "key", "openai", None),
        ("openrouter", None, "key", "openai", "JSON_SCHEMA"),
        ("ollama", "http://localhost:11434/v1", None, "ollama", "JSON"),
    ],
)
def test_adapter_constructs_each_provider_without_network(
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
    base_url: str | None,
    api_key: str | None,
    expected_adapter: str,
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
        model="model",
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

    assert created["model"] == f"{expected_adapter}/model"
    if provider == "openai":
        assert created["max_retries"] == 0
    else:
        assert "max_retries" not in created
    assert created["timeout"] == 120
    assert fake.client.max_retries == 0
    assert batch.candidates == []
    assert fake.calls[0]["max_retries"] == 0
    if api_key is None:
        assert "api_key" not in created
    else:
        assert created["api_key"] == api_key
    if expected_mode is None:
        assert "mode" not in created
    else:
        assert created["mode"].name == expected_mode
    expected_base_url = {
        "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "openrouter": "https://openrouter.ai/api/v1",
    }.get(provider, base_url)
    if expected_base_url is None:
        assert "base_url" not in created
    else:
        assert created["base_url"] == expected_base_url


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
        ("gemini", None, "key"),
        ("openai", None, "key"),
        ("openrouter", None, "key"),
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
        return RawProposalBatch(candidates=[])

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
