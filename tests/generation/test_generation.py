from __future__ import annotations

import json
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from graphcheck.contracts.check import load_suite
from graphcheck.contracts.profile import BaselineProfile
from graphcheck.errors import GraphCheckError
from graphcheck.generation.config import GenerateConfig, resolve_api_key
from graphcheck.generation.disclosure import GenerateDisclosure
from graphcheck.generation.prompts import (
    build_correction_request,
    build_initial_request,
    build_pack_catalog,
)
from graphcheck.generation.proposals import (
    RawProposal,
    validate_candidate,
)
from graphcheck.generation.transmission import (
    MAX_DOCUMENT_BYTES,
    GenerateRequest,
    build_profile_context,
    read_documents,
)
from graphcheck.generation.writer import GeneratedSuiteWriter
from graphcheck.packs import REGISTRY

FIXTURE = Path(__file__).parents[1] / "contracts" / "fixtures" / "baseline.json"


def baseline() -> BaselineProfile:
    return BaselineProfile.model_validate_json(FIXTURE.read_text(encoding="utf-8"))


def conformance(identifier: str = "customer-id-complete") -> RawProposal:
    return RawProposal(
        kind="conformance",
        spec={
            "id": identifier,
            "check": "completeness",
            "with": {"label": "Customer", "property": "id"},
        },
    )


def test_generate_config_and_secret_resolution() -> None:
    cloud = GenerateConfig(
        provider="anthropic",
        model=" claude ",
        api_key_env="CORP_TOKEN",
    )
    assert cloud.model == "claude"
    assert resolve_api_key(cloud, environ={"CORP_TOKEN": "secret"}) == "secret"

    with pytest.raises(GraphCheckError) as caught:
        resolve_api_key(cloud, environ={})
    assert caught.value.error.code == "generate.api_key_missing"
    assert caught.value.error.fix == "set $CORP_TOKEN"

    local = GenerateConfig(
        provider="ollama",
        model="qwen3:8b",
        base_url="http://localhost:11434/v1",
    )
    assert resolve_api_key(local, environ={}) is None
    with pytest.raises(ValidationError):
        GenerateConfig(provider="ollama", model="qwen3:8b")
    with pytest.raises(ValidationError):
        GenerateConfig(provider="openai", model="gpt", api_key_env=None)


@pytest.mark.parametrize(
    "base_url",
    [
        "https://proxy.example/v1?api_key=DO-NOT-LOG",
        "https://proxy.example/v1#DO-NOT-LOG",
    ],
)
def test_generate_config_rejects_undisclosable_urls(base_url: str) -> None:
    with pytest.raises(ValidationError):
        GenerateConfig(
            provider="openai",
            model="gpt",
            api_key_env="CORP_TOKEN",
            base_url=base_url,
        )


def test_transmission_is_an_explicit_allow_list() -> None:
    context = build_profile_context(baseline())
    payload = context.model_dump(mode="json")

    assert payload["profile_status"] == "complete"
    assert payload["labels"][1]["name"] == "Customer"
    assert payload["constraints"] == [
        {
            "type": "UNIQUENESS",
            "labels_or_types": ["Customer"],
            "properties": ["id"],
        }
    ]
    serialized = json.dumps(payload)
    for forbidden in (
        "database",
        "server_version",
        "edition",
        "fingerprint",
        "generated_at",
        "graphcheck_version",
        "partial_reason",
        "customer_id_unique",
        "customer_name_index",
    ):
        assert forbidden not in serialized


def test_documents_preserve_order_bytes_and_provider_safe_names(tmp_path: Path) -> None:
    first = tmp_path / "a.md"
    second_dir = tmp_path / "nested"
    second_dir.mkdir()
    second = second_dir / "a.md"
    first.write_text("héllo", encoding="utf-8")
    second.write_text("two", encoding="utf-8")

    loaded = read_documents(
        [Path("a.md"), Path("nested/a.md")],
        project_root=tmp_path,
        invocation_dir=tmp_path,
    )

    assert [item.document.ordinal for item in loaded] == [1, 2]
    assert [item.document.name for item in loaded] == ["a.md", "a.md"]
    assert [item.byte_count for item in loaded] == [6, 3]
    assert all(str(tmp_path) not in item.document.model_dump_json() for item in loaded)

    invalid = tmp_path / "invalid.txt"
    invalid.write_bytes(b"\xff")
    with pytest.raises(GraphCheckError) as caught:
        read_documents([invalid], project_root=tmp_path)
    assert caught.value.error.code == "generate.doc_invalid"


def test_document_read_is_bounded_before_size_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    document = tmp_path / "large.txt"
    document.touch()
    read_sizes: list[int] = []

    class RecordingStream(BytesIO):
        def read(self, size: int = -1) -> bytes:
            read_sizes.append(size)
            return super().read(size)

    monkeypatch.setattr(
        Path,
        "open",
        lambda self, *args, **kwargs: RecordingStream(b"x" * (MAX_DOCUMENT_BYTES + 1)),
    )

    with pytest.raises(GraphCheckError) as caught:
        read_documents([document], project_root=tmp_path)

    assert caught.value.error.code == "generate.doc_too_large"
    assert read_sizes == [MAX_DOCUMENT_BYTES + 1]


def test_disclosure_explicitly_warns_that_documents_are_not_redacted(tmp_path: Path) -> None:
    document = tmp_path / "rules.md"
    document.write_text("sensitive", encoding="utf-8")
    loaded = read_documents([document], project_root=tmp_path)
    disclosure = GenerateDisclosure.build(
        config=GenerateConfig(
            provider="ollama",
            model="qwen",
            base_url="http://localhost:11434/v1",
        ),
        baseline=".graphcheck/baselines/latest.json",
        profile_status="complete",
        documents=loaded,
    )

    rendered = disclosure.render_human()
    payload = disclosure.as_json()
    assert "may contain sensitive content" in rendered
    assert "does not inspect or redact" in rendered
    assert payload["documents_may_contain_sensitive_content"] is True
    assert payload["documents_inspected_or_redacted"] is False


@pytest.mark.parametrize(
    "raw",
    [
        conformance(),
        RawProposal(
            kind="competency",
            spec={
                "id": "customer-count",
                "question": "How many customers exist?",
                "query": "MATCH (c:Customer) RETURN count(c) AS count",
                "params": {},
                "expect": {"columns": ["count"], "rows": {"exactly": 1}},
            },
        ),
        RawProposal(
            kind="drift",
            spec={
                "id": "customer-count-stable",
                "metric": "node_count",
                "target": {"label": "Customer"},
                "tolerance": {"max_drop_pct": 10},
            },
        ),
    ],
)
def test_candidates_cross_the_real_spec02_loader(raw: RawProposal) -> None:
    candidate = validate_candidate(
        raw,
        provider="anthropic",
        model="claude",
        candidate_name="proposal[0]",
    )
    assert candidate.payload["generated"] is True
    assert candidate.payload["provenance"] == "graphcheck-generate:anthropic/claude"
    if raw.kind == "conformance":
        assert candidate.payload["with"]["threshold"] == 1.0


def test_candidate_rejects_provider_owned_and_unknown_pack_fields() -> None:
    raw = conformance()
    raw.spec["PRIVATE-DOCUMENT-CANARY"] = True
    with pytest.raises(ValueError, match="extra field is not permitted") as caught:
        validate_candidate(
            raw,
            provider="openai",
            model="gpt",
            candidate_name="proposal[0]",
        )
    assert "PRIVATE-DOCUMENT-CANARY" not in str(caught.value)

    with pytest.raises(ValueError, match="check: unknown check type") as caught:
        validate_candidate(
            RawProposal(
                kind="conformance",
                spec={"id": "bad", "check": "PRIVATE-DOCUMENT-CANARY", "with": {}},
            ),
            provider="openai",
            model="gpt",
            candidate_name="proposal[0]",
        )
    assert "PRIVATE-DOCUMENT-CANARY" not in str(caught.value)


@pytest.mark.parametrize(
    ("raw", "safe_reason"),
    [
        (
            RawProposal(kind="PRIVATE-DOCUMENT-CANARY", spec={"id": "bad"}),
            "candidate: invalid discriminator value",
        ),
        (
            RawProposal(
                kind="conformance",
                spec={
                    "id": "bad",
                    "check": "property_format",
                    "with": {
                        "label": "Customer",
                        "property": "id",
                        "regex": "(?P<PRIVATE-DOCUMENT-CANARY>a)",
                    },
                },
            ),
            "field: invalid value",
        ),
    ],
)
def test_candidate_validation_messages_never_echo_provider_values(
    raw: RawProposal, safe_reason: str
) -> None:
    with pytest.raises(ValueError) as caught:
        validate_candidate(
            raw,
            provider="openai",
            model="gpt",
            candidate_name="proposal[0]",
        )

    assert str(caught.value) == safe_reason
    assert "PRIVATE-DOCUMENT-CANARY" not in str(caught.value)


def test_prompts_publish_real_schemas_and_keep_documents_in_user_data() -> None:
    request = GenerateRequest(
        profile=build_profile_context(baseline()),
        documents=[],
        requested_count=5,
    )
    initial = build_initial_request(request)
    correction = build_correction_request(
        request,
        needed=2,
        validation_summaries=["proposal[1]: invalid pack"],
        retained_ids=["first"],
    )

    assert set(build_pack_catalog()) == set(REGISTRY)
    assert "Never emit severity, generated, provenance" in initial.system_prompt
    assert "read-only" in initial.system_prompt
    assert '"requested_count":5' in initial.user_prompt
    assert '"needed":2' in correction.user_prompt
    assert '"first"' in correction.user_prompt


def test_writer_is_exclusive_loadable_and_inert(tmp_path: Path) -> None:
    candidate = validate_candidate(
        conformance(),
        provider="ollama",
        model="qwen",
        candidate_name="proposal[0]",
    )
    fixed = datetime(2026, 7, 24, 15, 30, 12, 123456, tzinfo=UTC)
    first_name = "generated-20260724T153012.123456Z.yml"
    checks = tmp_path / "checks"
    checks.mkdir()
    (checks / first_name).write_text("suite: hand-written\n", encoding="utf-8")

    written = GeneratedSuiteWriter(checks, clock=lambda: fixed).write([candidate])

    assert written.path.name == "generated-20260724T153012.123457Z.yml"
    assert (checks / first_name).read_text(encoding="utf-8") == "suite: hand-written\n"
    data = yaml.safe_load(written.text)
    assert data["generated"] is True
    assert data["conformance"][0]["generated"] is True
    loaded = load_suite(written.text, source=str(written.path))
    assert loaded.checks[0].generated is True
