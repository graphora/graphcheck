from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Annotated, Literal

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    TypeAdapter,
    ValidationError,
)

from graphcheck.contracts.check import (
    CompetencyCheck,
    ConformanceCheck,
    DriftCheck,
    Expect,
    SuiteValidationError,
    UnknownCheckError,
    load_suite,
)
from graphcheck.yaml_loader import DuplicateKeyError

MAX_CANDIDATES = 20
_DIAGNOSTIC_FIELDS = frozenset(
    {
        "baseline",
        "candidate",
        "check",
        "competency",
        "conformance",
        "drift",
        "expect",
        "generated",
        "id",
        "kind",
        "metric",
        "params",
        "provenance",
        "query",
        "question",
        "rows",
        "spec",
        "suite",
        "tags",
        "target",
        "tolerance",
        "with",
    }
)
_PYDANTIC_DIAGNOSTICS = {
    "extra_forbidden": "extra field is not permitted",
    "missing": "field is required",
    "union_tag_invalid": "invalid discriminator value",
    "union_tag_not_found": "discriminator field is required",
    "value_error": "invalid value",
}


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ProposalRequest(_Strict):
    system_prompt: str
    user_prompt: str
    requested_count: int
    attempt: Literal[1, 2]


class RawProposal(_Strict):
    kind: str
    spec: dict[str, JsonValue]


class RawProposalBatch(_Strict):
    candidates: list[RawProposal] = Field(max_length=MAX_CANDIDATES)


class ProposedEnvelope(_Strict):
    id: str
    tags: list[str] = Field(default_factory=list)


class ProposedConformance(ProposedEnvelope):
    kind: Literal["conformance"]
    check: str
    with_: dict[str, JsonValue] = Field(alias="with")


class ProposedCompetency(ProposedEnvelope):
    kind: Literal["competency"]
    question: str
    query: str
    params: dict[str, JsonValue] = Field(default_factory=dict)
    expect: Expect


class ProposedDrift(ProposedEnvelope):
    kind: Literal["drift"]
    metric: str
    target: dict[str, JsonValue]
    baseline: str = "latest"
    tolerance: dict[str, JsonValue]


type ProposedCheck = Annotated[
    ProposedConformance | ProposedCompetency | ProposedDrift,
    Field(discriminator="kind"),
]
PROPOSED_CHECK_ADAPTER = TypeAdapter(ProposedCheck)


@dataclass(frozen=True)
class ValidatedCandidate:
    id: str
    kind: Literal["conformance", "competency", "drift"]
    payload: dict[str, object]


@dataclass(frozen=True)
class CandidateRejection:
    candidate: str
    reason: str


def validate_candidate(
    raw: RawProposal,
    *,
    provider: str,
    model: str,
    candidate_name: str,
) -> ValidatedCandidate:
    """Validate one provider item through the proposal DTO and real suite loader."""

    try:
        if "kind" in raw.spec:
            raise ValueError("spec must not contain the reserved field 'kind'")
        proposed = PROPOSED_CHECK_ADAPTER.validate_python({"kind": raw.kind, **raw.spec})
        payload = _marked_payload(
            proposed,
            provenance=f"graphcheck-generate:{provider}/{model}",
        )
        one_check = {
            "suite": "candidate",
            "generated": True,
            proposed.kind: [payload],
        }
        text = dump_suite_yaml(one_check)
        loaded = load_suite(text, source="candidate.yml")
        if len(loaded.checks) != 1:
            raise ValueError("candidate suite did not contain exactly one check")
        normalized = _payload_from_loaded(loaded.checks[0].spec)
        return ValidatedCandidate(
            id=loaded.checks[0].id,
            kind=proposed.kind,
            payload=normalized,
        )
    except (ValidationError, ValueError, TypeError) as exc:
        raise ValueError(safe_validation_reason(exc)) from None


def _marked_payload(
    proposed: ProposedConformance | ProposedCompetency | ProposedDrift,
    *,
    provenance: str,
) -> dict[str, object]:
    payload: dict[str, object] = {"id": proposed.id}
    if isinstance(proposed, ProposedConformance):
        payload["check"] = proposed.check
        payload["with"] = proposed.with_
    elif isinstance(proposed, ProposedCompetency):
        payload["question"] = proposed.question
        payload["query"] = proposed.query
        payload["params"] = proposed.params
        payload["expect"] = proposed.expect.model_dump(exclude_none=True)
    else:
        payload["metric"] = proposed.metric
        payload["target"] = proposed.target
        payload["baseline"] = proposed.baseline
        payload["tolerance"] = proposed.tolerance
    if proposed.tags:
        payload["tags"] = proposed.tags
    payload["provenance"] = provenance
    payload["generated"] = True
    return payload


def _payload_from_loaded(
    spec: ConformanceCheck | CompetencyCheck | DriftCheck,
) -> dict[str, object]:
    payload: dict[str, object] = {"id": spec.id}
    if isinstance(spec, ConformanceCheck):
        payload["check"] = spec.check
        payload["with"] = spec.with_
    elif isinstance(spec, CompetencyCheck):
        payload["question"] = spec.question
        payload["query"] = spec.query
        payload["params"] = spec.params
        payload["expect"] = spec.expect.model_dump(exclude_none=True)
    else:
        payload["metric"] = spec.metric
        payload["target"] = spec.target
        payload["baseline"] = spec.baseline
        payload["tolerance"] = spec.tolerance
    if spec.tags:
        payload["tags"] = spec.tags
    assert spec.provenance is not None
    payload["provenance"] = spec.provenance
    payload["generated"] = True
    return payload


def assemble_suite_data(
    suite_id: str,
    candidates: list[ValidatedCandidate],
) -> dict[str, object]:
    data: dict[str, object] = {"suite": suite_id, "generated": True}
    for kind in ("conformance", "competency", "drift"):
        members = [candidate.payload for candidate in candidates if candidate.kind == kind]
        if members:
            data[kind] = members
    return data


def serialize_validated_suite(
    suite_id: str,
    candidates: list[ValidatedCandidate],
) -> str:
    text = dump_suite_yaml(assemble_suite_data(suite_id, candidates))
    load_suite(text, source=f"{suite_id}.yml")
    return text


def dump_suite_yaml(data: dict[str, object]) -> str:
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


def safe_validation_reason(exc: Exception) -> str:
    """Return bounded structural diagnostics without Pydantic input values."""

    if isinstance(exc, ValidationError):
        summaries: list[str] = []
        for error in exc.errors(include_url=False, include_context=False, include_input=False)[:5]:
            location = (
                ".".join(
                    str(part) if isinstance(part, int) or part in _DIAGNOSTIC_FIELDS else "field"
                    for part in error["loc"]
                )
                or "candidate"
            )
            summaries.append(
                f"{location}: {_PYDANTIC_DIAGNOSTICS.get(error['type'], 'invalid field value')}"
            )
        reason = "; ".join(summaries)
    elif isinstance(exc, UnknownCheckError):
        reason = "check: unknown check type"
    elif isinstance(exc, DuplicateKeyError):
        reason = "candidate: duplicate field"
    elif isinstance(exc, SuiteValidationError):
        reason = "candidate: invalid suite structure"
    elif isinstance(exc, TypeError):
        reason = "candidate: invalid type"
    else:
        reason = "candidate: invalid value"
    reason = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]+", " ", reason)
    return reason[:500] or type(exc).__name__
