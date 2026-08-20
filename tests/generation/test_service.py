from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

import pytest
import yaml

from graphcheck.contracts.check import load_suite
from graphcheck.errors import GraphCheckError
from graphcheck.generation.proposals import ProposalRequest, RawProposal, RawProposalBatch
from graphcheck.generation.service import GenerationService
from graphcheck.project import write_default_project

FIXTURE = Path(__file__).parents[1] / "contracts" / "fixtures" / "baseline.json"


def proposal(identifier: str, *, valid: bool = True) -> RawProposal:
    return RawProposal(
        kind="conformance",
        spec={
            "id": identifier,
            "check": "completeness" if valid else "invented",
            "with": {"label": "Customer", "property": "id"},
        },
    )


class FakeClient:
    def __init__(self, responses: Sequence[RawProposalBatch | GraphCheckError]) -> None:
        self.responses = list(responses)
        self.requests: list[ProposalRequest] = []

    def propose(self, request: ProposalRequest) -> RawProposalBatch:
        self.requests.append(request)
        response = self.responses.pop(0)
        if isinstance(response, GraphCheckError):
            raise response
        return response


def project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    provider: str = "openai",
    model: str = "gpt-test",
) -> Path:
    write_default_project(tmp_path)
    config = yaml.safe_load((tmp_path / "graphcheck.yml").read_text(encoding="utf-8"))
    config["generate"] = {
        "provider": provider,
        "model": model,
        "api_key_env": "TEST_LLM_KEY",
        "temperature": 0,
    }
    (tmp_path / "graphcheck.yml").write_text(
        yaml.safe_dump(config, sort_keys=False),
        encoding="utf-8",
    )
    baseline_dir = tmp_path / ".graphcheck" / "baselines"
    baseline_dir.mkdir(parents=True)
    (baseline_dir / "20260724T120000.000000.json").write_bytes(FIXTURE.read_bytes())
    monkeypatch.setenv("TEST_LLM_KEY", "super-secret")
    return tmp_path


def test_gemma_accepts_partial_first_batch_without_slow_correction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = project(tmp_path, monkeypatch, "google", "gemma-4-31b-it")
    fake = FakeClient([RawProposalBatch(candidates=[proposal("one")])])

    result = GenerationService(client_factory=lambda config, key: fake).generate(
        project_root=root,
        baseline_from=None,
        document_paths=None,
        requested_count=3,
        disclosure_sink=lambda event: None,
    )

    assert len(fake.requests) == 1
    assert result.written == 1
    assert result.dropped == 2


def test_gemini_uses_normal_correction_after_partial_first_batch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = project(tmp_path, monkeypatch, "google", "gemini-2.5-flash")
    fake = FakeClient(
        [
            RawProposalBatch(candidates=[proposal("one")]),
            RawProposalBatch(candidates=[proposal("two")]),
        ]
    )

    result = GenerationService(client_factory=lambda config, key: fake).generate(
        project_root=root,
        baseline_from=None,
        document_paths=None,
        requested_count=2,
        disclosure_sink=lambda event: None,
    )

    assert len(fake.requests) == 2
    assert fake.requests[1].requested_count == 1
    assert result.written == 2
    assert result.dropped == 0


def test_google_rejects_profile_references_corrupted_by_tool_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = project(tmp_path, monkeypatch, "google", "gemma-4-31b-it")
    malformed = RawProposalBatch(
        candidates=[
            RawProposal(
                kind="conformance",
                spec={
                    "id": "bad",
                    "check": "uniqueness",
                    "with": {"label": "Customer**,property:", "property": "id"},
                },
            )
        ]
    )
    fake = FakeClient([malformed, RawProposalBatch(candidates=[proposal("good")])])

    result = GenerationService(client_factory=lambda config, key: fake).generate(
        project_root=root,
        baseline_from=None,
        document_paths=None,
        requested_count=1,
        disclosure_sink=lambda event: None,
    )

    assert len(fake.requests) == 2
    assert "not present in baseline profile" in fake.requests[1].user_prompt
    assert result.checks[0].id == "good"


def test_service_reasks_once_and_writes_partial_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = project(tmp_path, monkeypatch)
    fake = FakeClient(
        [
            RawProposalBatch(candidates=[proposal("one"), proposal("bad", valid=False)]),
            RawProposalBatch(candidates=[proposal("two"), proposal("still-bad", valid=False)]),
        ]
    )
    events: list[str] = []
    service = GenerationService(
        client_factory=lambda config, key: events.append(f"factory:{config.provider}:{key}") or fake
    )

    result = service.generate(
        project_root=root,
        baseline_from=None,
        document_paths=None,
        requested_count=3,
        disclosure_sink=lambda event: events.append(f"disclosure:{event.provider}"),
        warning_sink=lambda item: events.append(f"warning:{item.attempt}"),
        invocation_dir=root,
    )

    assert len(fake.requests) == 2
    assert fake.requests[1].requested_count == 2
    assert events[:2] == ["factory:openai:super-secret", "disclosure:openai"]
    assert result.written == 2
    assert result.dropped == 1
    assert result.non_deterministic is True
    assert "super-secret" not in json.dumps(result.model_dump(mode="json"))
    generated = root / result.path
    assert generated.is_file()
    assert all(check.generated for check in load_suite(generated.read_text()).checks)


def test_service_never_discloses_provider_values_in_rejections(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = project(tmp_path, monkeypatch)
    canary = "PRIVATE-DOCUMENT-CANARY"
    fake = FakeClient(
        [
            RawProposalBatch(
                candidates=[
                    proposal("one"),
                    RawProposal(
                        kind="conformance",
                        spec={"id": "bad-one", "check": canary, "with": {}},
                    ),
                ]
            ),
            RawProposalBatch(
                candidates=[
                    RawProposal(
                        kind="conformance",
                        spec={"id": "bad-two", "check": canary, "with": {}},
                    )
                ]
            ),
        ]
    )
    warnings = []

    result = GenerationService(client_factory=lambda config, key: fake).generate(
        project_root=root,
        baseline_from=None,
        document_paths=None,
        requested_count=2,
        disclosure_sink=lambda event: None,
        warning_sink=warnings.append,
    )

    assert canary not in fake.requests[1].user_prompt
    assert canary not in result.model_dump_json()
    assert canary not in json.dumps([warning.model_dump(mode="json") for warning in warnings])
    assert result.dropped_candidates[0].reason == "check: unknown check type"


def test_service_never_writes_when_no_candidate_survives(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = project(tmp_path, monkeypatch)
    fake = FakeClient(
        [
            RawProposalBatch(candidates=[proposal("bad-1", valid=False)]),
            RawProposalBatch(candidates=[proposal("bad-2", valid=False)]),
        ]
    )
    service = GenerationService(client_factory=lambda config, key: fake)

    with pytest.raises(GraphCheckError) as caught:
        service.generate(
            project_root=root,
            baseline_from=None,
            document_paths=None,
            requested_count=1,
            disclosure_sink=lambda event: None,
        )

    assert caught.value.error.code == "generate.no_valid_candidates"
    assert not list((root / "checks").glob("generated-*.yml"))


def test_provider_failure_is_not_retried(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = project(tmp_path, monkeypatch)
    failure = GraphCheckError(
        "generate.provider_timeout",
        "timed out",
        "retry",
    )
    fake = FakeClient([failure])
    service = GenerationService(client_factory=lambda config, key: fake)

    with pytest.raises(GraphCheckError) as caught:
        service.generate(
            project_root=root,
            baseline_from=None,
            document_paths=None,
            requested_count=1,
            disclosure_sink=lambda event: None,
        )

    assert caught.value.error.code == "generate.provider_timeout"
    assert len(fake.requests) == 1


def test_invalid_correction_envelope_preserves_valid_first_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = project(tmp_path, monkeypatch)
    output_failure = GraphCheckError(
        "generate.output_invalid",
        "unsafe provider detail",
        "choose another model",
    )
    fake = FakeClient(
        [
            RawProposalBatch(candidates=[proposal("one"), proposal("bad", valid=False)]),
            output_failure,
        ]
    )
    warnings = []
    service = GenerationService(client_factory=lambda config, key: fake)

    result = service.generate(
        project_root=root,
        baseline_from=None,
        document_paths=None,
        requested_count=2,
        disclosure_sink=lambda event: None,
        warning_sink=warnings.append,
    )

    assert len(fake.requests) == 2
    assert result.written == 1
    assert result.dropped == 1
    assert result.dropped_candidates == warnings
    assert result.dropped_candidates[0].attempt == 2
    assert result.dropped_candidates[0].candidate == "response envelope"
    assert result.dropped_candidates[0].reason == "invalid structured candidate batch"
    assert "unsafe provider detail" not in result.model_dump_json()
    generated = root / result.path
    assert [check.id for check in load_suite(generated.read_text()).checks] == ["one"]


def test_invalid_correction_envelope_is_fatal_without_retained_candidates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = project(tmp_path, monkeypatch)
    output_failure = GraphCheckError(
        "generate.output_invalid",
        "invalid structured output",
        "choose another model",
    )
    fake = FakeClient(
        [
            RawProposalBatch(candidates=[proposal("bad", valid=False)]),
            output_failure,
        ]
    )
    service = GenerationService(client_factory=lambda config, key: fake)

    with pytest.raises(GraphCheckError) as caught:
        service.generate(
            project_root=root,
            baseline_from=None,
            document_paths=None,
            requested_count=1,
            disclosure_sink=lambda event: None,
        )

    assert caught.value.error.code == "generate.output_invalid"
    assert len(fake.requests) == 2
    assert not list((root / "checks").glob("generated-*.yml"))


def test_correction_transport_failure_remains_fatal_after_valid_first_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = project(tmp_path, monkeypatch)
    provider_failure = GraphCheckError(
        "generate.provider_timeout",
        "timed out",
        "retry",
    )
    fake = FakeClient(
        [
            RawProposalBatch(candidates=[proposal("one")]),
            provider_failure,
        ]
    )
    service = GenerationService(client_factory=lambda config, key: fake)

    with pytest.raises(GraphCheckError) as caught:
        service.generate(
            project_root=root,
            baseline_from=None,
            document_paths=None,
            requested_count=2,
            disclosure_sink=lambda event: None,
        )

    assert caught.value.error.code == "generate.provider_timeout"
    assert len(fake.requests) == 2
    assert not list((root / "checks").glob("generated-*.yml"))
