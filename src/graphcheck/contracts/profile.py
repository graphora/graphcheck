from __future__ import annotations

import hashlib
import json
from collections.abc import Hashable
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
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
    )


class ProfileMetadata(_Strict):
    generated_at: str
    graphcheck_version: str


class ProfileProperty(_Strict):
    name: str
    type: str


class LabelProfile(_Strict):
    name: str
    count: int = Field(ge=0)
    properties: list[ProfileProperty]
    degree_distribution: DegreeDistribution | None


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

            for label_profile in self.graph_schema.labels:
                if label_profile.degree_distribution is None:
                    raise ValueError(
                        f"label {label_profile.name!r} must carry "
                        "degree_distribution in a complete profile"
                    )

            for relationship_type in self.graph_schema.relationship_types:
                if relationship_type.count > self.statistics.relationship_count:
                    raise ValueError(
                        f"schema.relationship_types[{relationship_type.name!r}].count "
                        f"({relationship_type.count}) exceeds "
                        f"statistics.relationship_count "
                        f"({self.statistics.relationship_count})"
                    )

        elif not self.partial_reason:
            raise ValueError("partial baseline must carry a non-empty partial_reason")

        for label_profile in self.graph_schema.labels:
            if label_profile.count > self.statistics.node_count:
                raise ValueError(
                    f"schema.labels[{label_profile.name!r}].count "
                    f"({label_profile.count}) exceeds "
                    f"statistics.node_count "
                    f"({self.statistics.node_count})"
                )

        expected_fingerprint = profile_fingerprint(self.graph_schema, self.statistics)
        if self.fingerprint != expected_fingerprint:
            raise ValueError(
                f"fingerprint must be {expected_fingerprint!r} for the v0 profile fingerprint input"
            )

        label_names = [label_profile.name for label_profile in self.graph_schema.labels]

        _require_sorted("schema.labels", label_names)
        _require_unique("schema.labels", label_names)

        for label_profile in self.graph_schema.labels:
            property_names = [prop.name for prop in label_profile.properties]

            _require_sorted(
                f"schema.labels[{label_profile.name!r}].properties",
                property_names,
            )
            _require_unique(
                f"schema.labels[{label_profile.name!r}].properties",
                property_names,
            )

        relationship_names = [rel.name for rel in self.graph_schema.relationship_types]

        _require_sorted(
            "schema.relationship_types",
            relationship_names,
        )
        _require_unique(
            "schema.relationship_types",
            relationship_names,
        )

        constraint_names = [constraint.name for constraint in self.graph_schema.constraints]

        _require_sorted(
            "schema.constraints",
            constraint_names,
        )
        _require_unique(
            "schema.constraints",
            constraint_names,
        )

        for constraint in self.graph_schema.constraints:
            _require_sorted(
                f"schema.constraints[{constraint.name!r}].labels_or_types",
                constraint.labels_or_types,
            )

            _require_unique(
                f"schema.constraints[{constraint.name!r}].labels_or_types",
                constraint.labels_or_types,
            )
            _require_sorted(
                f"schema.constraints[{constraint.name!r}].properties",
                constraint.properties,
            )

            _require_unique(
                f"schema.constraints[{constraint.name!r}].properties",
                constraint.properties,
            )

        index_names = [index.name for index in self.graph_schema.indexes]

        _require_sorted(
            "schema.indexes",
            index_names,
        )
        _require_unique(
            "schema.indexes",
            index_names,
        )

        for index in self.graph_schema.indexes:
            _require_sorted(
                f"schema.indexes[{index.name!r}].labels_or_types",
                index.labels_or_types,
            )

            _require_unique(
                f"schema.indexes[{index.name!r}].labels_or_types",
                index.labels_or_types,
            )
            _require_sorted(
                f"schema.indexes[{index.name!r}].properties",
                index.properties,
            )
            _require_unique(
                f"schema.indexes[{index.name!r}].properties",
                index.properties,
            )

        coverage_identities = [
            (
                coverage.owner,
                coverage.owner_name,
                coverage.property,
            )
            for coverage in self.statistics.property_coverage
        ]

        _require_sorted(
            "statistics.property_coverage",
            coverage_identities,
        )

        _require_unique(
            "statistics.property_coverage",
            coverage_identities,
        )

        return self


def _require_sorted(field: str, values: list[object]) -> None:
    """Ensure collections are canonically ordered."""
    if values != sorted(values):
        raise ValueError(f"{field} must be canonically sorted")


def _require_unique(field: str, values: list[Hashable]) -> None:
    """Ensure collections do not contain duplicate identities."""
    if len(values) != len(set(values)):
        raise ValueError(f"{field} must contain unique values")


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
