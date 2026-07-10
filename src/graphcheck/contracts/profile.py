from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from graphcheck.contracts.results import RunTarget

SCHEMA_VERSION = "1.0"
FINGERPRINT_ALGORITHM = "sha256"


class ProfileStatus(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProfileMetadata(_Strict):
    generated_at: str
    graphcheck_version: str


class ProfileProperty(_Strict):
    name: str
    type: str
    coverage: float = Field(ge=0, le=100)


class LabelProfile(_Strict):
    name: str
    count: int = Field(ge=0)
    properties: list[ProfileProperty]


class RelationshipTypeProfile(_Strict):
    name: str
    count: int = Field(ge=0)


class ConstraintProfile(_Strict):
    name: str
    type: str
    labels_or_types: list[str]
    properties: list[str]


class IndexProfile(_Strict):
    name: str
    type: str
    labels_or_types: list[str]
    properties: list[str]


class GraphSchema(_Strict):
    labels: list[LabelProfile]
    relationship_types: list[RelationshipTypeProfile]
    constraints: list[ConstraintProfile]
    indexes: list[IndexProfile]


class DegreeDistribution(_Strict):
    median: float = Field(ge=0)
    p95: float = Field(ge=0)
    p99: float = Field(ge=0)
    maximum: int = Field(ge=0)

    @model_validator(mode="after")
    def _percentiles_are_ordered(self) -> DegreeDistribution:
        if not self.median <= self.p95 <= self.p99 <= self.maximum:
            raise ValueError("degree distribution must satisfy median <= p95 <= p99 <= maximum")
        return self


class PropertyCoverage(_Strict):
    owner: Literal["node", "relationship"]
    owner_name: str
    property: str
    coverage: float = Field(ge=0, le=100)


class ProfileStatistics(_Strict):
    node_count: int = Field(ge=0)
    relationship_count: int = Field(ge=0)
    property_coverage: list[PropertyCoverage]
    degree_distribution: DegreeDistribution | None


class BaselineProfile(_Strict):
    schema_version: Literal["1.0"]
    status: ProfileStatus
    partial_reason: str | None
    target: RunTarget
    metadata: ProfileMetadata
    graph_schema: GraphSchema = Field(alias="schema")
    statistics: ProfileStatistics
    fingerprint: str

    @model_validator(mode="after")
    def _semantic_invariants(self) -> BaselineProfile:
        if self.status is ProfileStatus.COMPLETE:
            if self.partial_reason is not None:
                raise ValueError("complete baseline must have partial_reason=null")
            if self.statistics.degree_distribution is None:
                raise ValueError("complete baseline must carry degree_distribution")
        elif not self.partial_reason:
            raise ValueError("partial baseline must carry a non-empty partial_reason")

        expected_fingerprint = profile_fingerprint(self.graph_schema, self.statistics)
        if self.fingerprint != expected_fingerprint:
            raise ValueError(
                f"fingerprint must be {expected_fingerprint!r} for the v0 profile fingerprint input"
            )

        _require_sorted("schema.labels", [label.name for label in self.graph_schema.labels])
        for label in self.graph_schema.labels:
            _require_sorted(
                f"schema.labels[{label.name!r}].properties",
                [prop.name for prop in label.properties],
            )
        _require_sorted(
            "schema.relationship_types",
            [rel.name for rel in self.graph_schema.relationship_types],
        )
        _require_sorted(
            "schema.constraints",
            [constraint.name for constraint in self.graph_schema.constraints],
        )
        for constraint in self.graph_schema.constraints:
            _require_sorted(
                f"schema.constraints[{constraint.name!r}].labels_or_types",
                constraint.labels_or_types,
            )
            _require_sorted(
                f"schema.constraints[{constraint.name!r}].properties",
                constraint.properties,
            )
        _require_sorted("schema.indexes", [index.name for index in self.graph_schema.indexes])
        for index in self.graph_schema.indexes:
            _require_sorted(
                f"schema.indexes[{index.name!r}].labels_or_types",
                index.labels_or_types,
            )
            _require_sorted(
                f"schema.indexes[{index.name!r}].properties",
                index.properties,
            )
        _require_sorted(
            "statistics.property_coverage",
            [
                (coverage.owner, coverage.owner_name, coverage.property)
                for coverage in self.statistics.property_coverage
            ],
        )
        return self


def _require_sorted(field: str, values: list[object]) -> None:
    if values != sorted(values):
        raise ValueError(f"{field} must be canonically sorted")


def fingerprint_input(
    graph_schema: GraphSchema, statistics: ProfileStatistics
) -> dict[str, object]:
    return {
        "labels": [label.model_dump() for label in graph_schema.labels],
        "relationship_types": [
            relationship_type.model_dump() for relationship_type in graph_schema.relationship_types
        ],
        "node_count": statistics.node_count,
        "relationship_count": statistics.relationship_count,
    }


def profile_fingerprint(graph_schema: GraphSchema, statistics: ProfileStatistics) -> str:
    raw = json.dumps(
        fingerprint_input(graph_schema, statistics),
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return f"{FINGERPRINT_ALGORITHM}:{hashlib.sha256(raw).hexdigest()}"
