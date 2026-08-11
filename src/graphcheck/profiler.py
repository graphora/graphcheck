from __future__ import annotations

import time
from collections.abc import Callable
from contextlib import suppress
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from graphcheck import __version__
from graphcheck.contracts.profile import (
    BaselineProfile,
    ConstraintProfile,
    DegreeDistribution,
    GraphSchema,
    IndexProfile,
    LabelProfile,
    ProfileMetadata,
    ProfileProperty,
    ProfileStatistics,
    ProfileStatus,
    PropertyCoverage,
    RelationshipTypeProfile,
    profile_fingerprint,
)
from graphcheck.errors import GraphCheckError, GraphCheckTimeoutError
from graphcheck.neo4j_adapter import Neo4jClient

# test addition
# DEFAULT_PROFILE_BUDGET_SECONDS = 3
DEFAULT_PROFILE_BUDGET_SECONDS = 60
ProfileTelemetryObserver = Callable[[str, str, int, object | None], None]
ProfileResultTelemetryObserver = Callable[[str, str | None, bool], None]


@dataclass
class _CoverageInventory:
    nodes: list[PropertyCoverage] = field(default_factory=list)
    relationships: list[PropertyCoverage] = field(default_factory=list)
    nodes_complete: bool = False
    relationships_complete: bool = False


_ACTIVE_INVENTORY: ContextVar[_CoverageInventory | None] = ContextVar(
    "graphcheck_profile_inventory", default=None
)


class _LabelCollectionError(GraphCheckError):
    def __init__(self, cause: GraphCheckError, labels: list[LabelProfile]) -> None:
        super().__init__(cause.error.code, cause.error.message, cause.error.fix)
        self.labels = labels
        self.partial_reason_code = "schema_incomplete"


def profile(
    client: Neo4jClient,
    *,
    telemetry_observer: ProfileTelemetryObserver | None = None,
    telemetry_result_observer: ProfileResultTelemetryObserver | None = None,
) -> BaselineProfile:
    token = _ACTIVE_INVENTORY.set(_CoverageInventory())
    try:
        return _profile(
            client,
            telemetry_observer=telemetry_observer,
            telemetry_result_observer=telemetry_result_observer,
        )
    finally:
        _ACTIVE_INVENTORY.reset(token)


def _profile(
    client: Neo4jClient,
    *,
    telemetry_observer: ProfileTelemetryObserver | None = None,
    telemetry_result_observer: ProfileResultTelemetryObserver | None = None,
) -> BaselineProfile:
    deadline = time.monotonic() + DEFAULT_PROFILE_BUDGET_SECONDS
    probe_started = time.monotonic() if telemetry_observer is not None else None
    try:
        target, _, counts = client.probe(timeout_s=_remaining_budget(deadline))
    except Exception as exc:
        _observe_profile_stage(
            telemetry_observer,
            "probe",
            "timeout" if isinstance(exc, GraphCheckTimeoutError) else "error",
            probe_started,
        )
        raise
    _observe_profile_stage(
        telemetry_observer,
        "probe",
        "success",
        probe_started,
        target=target,
    )
    if counts.nodes is None or counts.relationships is None:
        raise GraphCheckError(
            "profile.counts_unavailable",
            "Core node and relationship counts are unavailable.",
            "Grant the Neo4j user graph read access, then run `graphcheck profile` again.",
        )
    labels: list[LabelProfile] = []
    relationship_types: list[RelationshipTypeProfile] = []
    constraints: list[ConstraintProfile] = []
    indexes: list[IndexProfile] = []
    property_coverage: list[PropertyCoverage] = []

    if _budget_exceeded(deadline):
        return _partial_profile(
            target,
            counts,
            labels,
            relationship_types,
            constraints,
            indexes,
            property_coverage,
            f"Profiling exceeded the {DEFAULT_PROFILE_BUDGET_SECONDS} second budget after probe.",
            partial_reason_code="probe_incomplete",
            deadline=deadline,
            telemetry_result_observer=telemetry_result_observer,
        )

    try:
        labels = _observed_profile_call(
            telemetry_observer,
            "labels",
            lambda: (
                collect_labels(client, _deadline=deadline)
                if telemetry_observer is None
                else collect_labels(
                    client,
                    _deadline=deadline,
                    _telemetry_observer=telemetry_observer,
                )
            ),
        )
    except _LabelCollectionError as exc:
        labels = exc.labels
        return _partial_profile(
            target,
            counts,
            labels,
            relationship_types,
            constraints,
            indexes,
            property_coverage,
            f"Failed collecting labels: {exc}",
            partial_reason_code=exc.partial_reason_code,
            deadline=deadline,
            telemetry_result_observer=telemetry_result_observer,
        )
    except GraphCheckError as exc:
        return _partial_profile(
            target,
            counts,
            labels,
            relationship_types,
            constraints,
            indexes,
            property_coverage,
            f"Failed collecting labels: {exc}",
            partial_reason_code="schema_incomplete",
            deadline=deadline,
            telemetry_result_observer=telemetry_result_observer,
        )
    if _budget_exceeded(deadline):
        return _partial_profile(
            target,
            counts,
            labels,
            relationship_types,
            constraints,
            indexes,
            property_coverage,
            f"Profiling exceeded the {DEFAULT_PROFILE_BUDGET_SECONDS} second budget "
            "after collecting labels.",
            partial_reason_code="schema_incomplete",
            deadline=deadline,
            telemetry_result_observer=telemetry_result_observer,
        )

    try:
        relationship_types = _observed_profile_call(
            telemetry_observer,
            "relationship_types",
            lambda: collect_relationship_types(client, _deadline=deadline),
        )
    except GraphCheckError as exc:
        return _partial_profile(
            target,
            counts,
            labels,
            relationship_types,
            constraints,
            indexes,
            property_coverage,
            f"Failed collecting relationship types: {exc}",
            partial_reason_code="schema_incomplete",
            deadline=deadline,
            telemetry_result_observer=telemetry_result_observer,
        )
    if _budget_exceeded(deadline):
        return _partial_profile(
            target,
            counts,
            labels,
            relationship_types,
            constraints,
            indexes,
            property_coverage,
            f"Profiling exceeded the {DEFAULT_PROFILE_BUDGET_SECONDS} second budget "
            "after collecting relationship types.",
            partial_reason_code="schema_incomplete",
            deadline=deadline,
            telemetry_result_observer=telemetry_result_observer,
        )

    try:
        constraints = _observed_profile_call(
            telemetry_observer,
            "constraints",
            lambda: collect_constraints(client, _deadline=deadline),
        )
    except GraphCheckError as exc:
        return _partial_profile(
            target,
            counts,
            labels,
            relationship_types,
            constraints,
            indexes,
            property_coverage,
            f"Failed collecting constraints: {exc}",
            partial_reason_code="schema_incomplete",
            deadline=deadline,
            telemetry_result_observer=telemetry_result_observer,
        )
    if _budget_exceeded(deadline):
        return _partial_profile(
            target,
            counts,
            labels,
            relationship_types,
            constraints,
            indexes,
            property_coverage,
            f"Profiling exceeded the {DEFAULT_PROFILE_BUDGET_SECONDS} second budget "
            "after collecting constraints.",
            partial_reason_code="schema_incomplete",
            deadline=deadline,
            telemetry_result_observer=telemetry_result_observer,
        )

    try:
        indexes = _observed_profile_call(
            telemetry_observer,
            "indexes",
            lambda: collect_indexes(client, _deadline=deadline),
        )
    except GraphCheckError as exc:
        return _partial_profile(
            target,
            counts,
            labels,
            relationship_types,
            constraints,
            indexes,
            property_coverage,
            f"Failed collecting indexes: {exc}",
            partial_reason_code="schema_incomplete",
            deadline=deadline,
            telemetry_result_observer=telemetry_result_observer,
        )
    if _budget_exceeded(deadline):
        return _partial_profile(
            target,
            counts,
            labels,
            relationship_types,
            constraints,
            indexes,
            property_coverage,
            f"Profiling exceeded the {DEFAULT_PROFILE_BUDGET_SECONDS} second budget "
            "after collecting indexes.",
            partial_reason_code="schema_incomplete",
            deadline=deadline,
            telemetry_result_observer=telemetry_result_observer,
        )

    try:
        property_coverage = _observed_profile_call(
            telemetry_observer,
            "property_coverage",
            lambda: collect_property_coverage(client, _deadline=deadline),
        )
    except GraphCheckError as exc:
        return _partial_profile(
            target,
            counts,
            labels,
            relationship_types,
            constraints,
            indexes,
            property_coverage,
            f"Failed collecting property coverage: {exc}",
            partial_reason_code="property_coverage_incomplete",
            deadline=deadline,
            telemetry_result_observer=telemetry_result_observer,
        )
    if _budget_exceeded(deadline):
        return _partial_profile(
            target,
            counts,
            labels,
            relationship_types,
            constraints,
            indexes,
            property_coverage,
            f"Profiling exceeded the {DEFAULT_PROFILE_BUDGET_SECONDS} second budget "
            "after collecting property coverage.",
            partial_reason_code="property_coverage_incomplete",
            deadline=deadline,
            telemetry_result_observer=telemetry_result_observer,
        )

    graph_schema = GraphSchema(
        labels=labels,
        relationship_types=relationship_types,
        constraints=constraints,
        indexes=indexes,
    )
    statistics = ProfileStatistics(
        node_count=counts.nodes,
        relationship_count=counts.relationships,
        property_coverage=property_coverage,
    )
    baseline = BaselineProfile(
        schema_version="1.0",
        status=ProfileStatus.COMPLETE,
        partial_reason=None,
        target=target,
        metadata=ProfileMetadata(
            generated_at=datetime.now(UTC).isoformat(),
            graphcheck_version=__version__,
        ),
        schema=graph_schema,
        statistics=statistics,
        fingerprint=profile_fingerprint(graph_schema, statistics),
    )
    _observe_profile_result(
        telemetry_result_observer,
        "complete",
        partial_reason_code=None,
        deadline_exhausted=False,
    )
    return baseline


def _partial_profile(
    target,
    counts,
    labels: list[LabelProfile],
    relationship_types: list[RelationshipTypeProfile],
    constraints: list[ConstraintProfile],
    indexes: list[IndexProfile],
    property_coverage: list[PropertyCoverage],
    reason: str,
    *,
    partial_reason_code: str,
    deadline: float,
    telemetry_result_observer: ProfileResultTelemetryObserver | None,
) -> BaselineProfile:
    graph_schema = GraphSchema(
        labels=labels,
        relationship_types=relationship_types,
        constraints=constraints,
        indexes=indexes,
    )

    statistics = ProfileStatistics(
        node_count=counts.nodes,
        relationship_count=counts.relationships,
        property_coverage=property_coverage,
    )

    baseline = BaselineProfile(
        schema_version="1.0",
        status=ProfileStatus.PARTIAL,
        partial_reason=reason,
        target=target,
        metadata=ProfileMetadata(
            generated_at=datetime.now(UTC).isoformat(),
            graphcheck_version=__version__,
        ),
        schema=graph_schema,
        statistics=statistics,
        fingerprint=profile_fingerprint(graph_schema, statistics),
    )
    deadline_exhausted = _budget_exceeded(deadline)
    _observe_profile_result(
        telemetry_result_observer,
        "partial",
        partial_reason_code=("deadline_exhausted" if deadline_exhausted else partial_reason_code),
        deadline_exhausted=deadline_exhausted,
    )
    return baseline


def print_profile(client: Neo4jClient) -> None:
    print(profile(client).model_dump_json(indent=2, by_alias=True))


def _observed_profile_call[T](
    observer: ProfileTelemetryObserver | None,
    stage: str,
    operation: Callable[[], T],
) -> T:
    started = time.monotonic() if observer is not None else None
    try:
        result = operation()
    except Exception as exc:
        _observe_profile_stage(
            observer,
            stage,
            "timeout" if isinstance(exc, GraphCheckTimeoutError) else "error",
            started,
        )
        raise
    _observe_profile_stage(observer, stage, "success", started)
    return result


def _observe_profile_stage(
    observer: ProfileTelemetryObserver | None,
    stage: str,
    outcome: str,
    started: float | None,
    *,
    target: object | None = None,
) -> None:
    if observer is None or started is None:
        return
    duration_ms = max(0, round((time.monotonic() - started) * 1000))
    # Profiling telemetry is best-effort and must never change the profile result.
    with suppress(Exception):
        observer(stage, outcome, duration_ms, target)


def _observe_profile_result(
    observer: ProfileResultTelemetryObserver | None,
    outcome: str,
    *,
    partial_reason_code: str | None,
    deadline_exhausted: bool,
) -> None:
    if observer is None:
        return
    with suppress(Exception):
        observer(outcome, partial_reason_code, deadline_exhausted)


def collect_labels(
    client: Neo4jClient,
    *,
    timeout_s: float | None = None,
    _deadline: float | None = None,
    _telemetry_observer: ProfileTelemetryObserver | None = None,
) -> list[LabelProfile]:
    deadline = _deadline if _deadline is not None else _timeout_deadline(timeout_s)
    labels = sorted(
        str(row["label"])
        for row in _run_read(client, "CALL db.labels() YIELD label RETURN label", deadline=deadline)
    )
    collected: list[LabelProfile] = []
    failure: GraphCheckError | None = None
    coverage: list[PropertyCoverage] = []
    for label in labels:
        try:
            profile, label_coverage = _collect_label_inventory(client, label, deadline)
            collected.append(profile)
            coverage.extend(label_coverage)
        except GraphCheckError as exc:
            failure = failure or exc
    if failure is not None:
        raise _LabelCollectionError(failure, collected) from failure
    inventory = _ACTIVE_INVENTORY.get()
    if inventory is not None:
        inventory.nodes = coverage
        inventory.nodes_complete = True
    return collected


def collect_relationship_types(
    client: Neo4jClient,
    *,
    timeout_s: float | None = None,
    _deadline: float | None = None,
) -> list[RelationshipTypeProfile]:
    deadline = _deadline if _deadline is not None else _timeout_deadline(timeout_s)
    relationship_types = sorted(
        str(row["relationshipType"])
        for row in _run_read(
            client,
            "CALL db.relationshipTypes() YIELD relationshipType RETURN relationshipType",
            deadline=deadline,
        )
    )
    collected: list[RelationshipTypeProfile] = []
    coverage: list[PropertyCoverage] = []
    for relationship_type in relationship_types:
        profile, relationship_coverage = _collect_relationship_inventory(
            client, relationship_type, deadline
        )
        collected.append(profile)
        coverage.extend(relationship_coverage)
    inventory = _ACTIVE_INVENTORY.get()
    if inventory is not None:
        inventory.relationships = coverage
        inventory.relationships_complete = True
    return collected


def collect_constraints(
    client: Neo4jClient,
    *,
    timeout_s: float | None = None,
    _deadline: float | None = None,
) -> list[ConstraintProfile]:
    deadline = _deadline if _deadline is not None else _timeout_deadline(timeout_s)
    constraints = [
        _collect_constraint(row) for row in _run_read(client, "SHOW CONSTRAINTS", deadline=deadline)
    ]
    return sorted(constraints, key=lambda constraint: constraint.name)


def _collect_constraint(row: dict[str, Any]) -> ConstraintProfile:
    return ConstraintProfile(
        name=str(row["name"]),
        type=str(row["type"]),
        labels_or_types=sorted(str(value) for value in row["labelsOrTypes"]),
        properties=sorted(str(value) for value in row["properties"]),
    )


def collect_indexes(
    client: Neo4jClient,
    *,
    timeout_s: float | None = None,
    _deadline: float | None = None,
) -> list[IndexProfile]:
    deadline = _deadline if _deadline is not None else _timeout_deadline(timeout_s)
    indexes = [_collect_index(row) for row in _run_read(client, "SHOW INDEXES", deadline=deadline)]
    return sorted(indexes, key=lambda index: index.name)


def _collect_index(row: dict[str, Any]) -> IndexProfile:
    return IndexProfile(
        name=str(row["name"]),
        type=str(row["type"]),
        labels_or_types=sorted(str(value) for value in row["labelsOrTypes"] or []),
        properties=sorted(str(value) for value in row["properties"] or []),
    )


def _collect_relationship_inventory(
    client: Neo4jClient, relationship_type: str, deadline: float | None
) -> tuple[RelationshipTypeProfile, list[PropertyCoverage]]:
    relationship_type_ref = _cypher_identifier(relationship_type)
    rows = _run_read(
        client,
        "CALL {\n"
        f"  MATCH ()-[r:{relationship_type_ref}]->()\n"
        "  RETURN count(r) AS count\n"
        "}\n"
        "CALL {\n"
        f"  MATCH ()-[r:{relationship_type_ref}]->()\n"
        "  UNWIND keys(r) AS property\n"
        "  WITH property, count(r) AS populated_count\n"
        "  ORDER BY property\n"
        "  RETURN collect({name: property, populated_count: populated_count}) AS properties\n"
        "}\n"
        "RETURN count, properties",
        deadline=deadline,
    )
    row = rows[0] if rows else {}
    count = int(row.get("count", 0))
    coverage = [
        PropertyCoverage(
            owner="relationship",
            owner_name=relationship_type,
            property=str(prop["name"]),
            coverage=_coverage(int(prop["populated_count"]), count),
        )
        for prop in row.get("properties", [])
    ]
    return RelationshipTypeProfile(name=relationship_type, count=count), coverage


def _collect_label_inventory(
    client: Neo4jClient, label: str, deadline: float | None
) -> tuple[LabelProfile, list[PropertyCoverage]]:
    label_ref = _cypher_identifier(label)
    rows = _run_read(
        client,
        "CALL {\n"
        f"  MATCH (n:{label_ref})\n"
        "  WITH n, COUNT { (n)--() } AS degree\n"
        "  RETURN count(n) AS count,\n"
        "         coalesce(percentileCont(degree, 0.5), 0) AS median,\n"
        "         coalesce(percentileCont(degree, 0.95), 0) AS p95,\n"
        "         coalesce(percentileCont(degree, 0.99), 0) AS p99,\n"
        "         coalesce(max(degree), 0) AS maximum\n"
        "}\n"
        "CALL {\n"
        f"  MATCH (n:{label_ref})\n"
        "  UNWIND keys(n) AS property\n"
        "  WITH property, count(n) AS populated_count, min(elementId(n)) AS sample_id\n"
        "  MATCH (sample) WHERE elementId(sample) = sample_id\n"
        "  ORDER BY property\n"
        "  RETURN collect({name: property, populated_count: populated_count, "
        "sample: sample[property]}) "
        "AS properties\n"
        "}\n"
        "RETURN count, median, p95, p99, maximum, properties",
        deadline=deadline,
    )
    row = rows[0] if rows else {}
    count = int(row.get("count", 0))
    raw_properties = row.get("properties", [])
    properties = [
        ProfileProperty(name=str(prop["name"]), type=_python_value_type(prop.get("sample")))
        for prop in raw_properties
    ]
    coverage = [
        PropertyCoverage(
            owner="node",
            owner_name=label,
            property=str(prop["name"]),
            coverage=_coverage(int(prop["populated_count"]), count),
        )
        for prop in raw_properties
    ]
    return (
        LabelProfile(
            name=label,
            count=count,
            properties=properties,
            degree_distribution=DegreeDistribution(
                median=round(float(row.get("median", 0)), 2),
                p95=round(float(row.get("p95", 0)), 2),
                p99=round(float(row.get("p99", 0)), 2),
                maximum=int(row.get("maximum", 0)),
            ),
        ),
        coverage,
    )


def _collect_degree_distribution(
    client: Neo4jClient,
    label_ref: str,
    *,
    timeout_s: float | None = None,
    _deadline: float | None = None,
    _telemetry_observer: ProfileTelemetryObserver | None = None,
) -> DegreeDistribution:
    deadline = _deadline if _deadline is not None else _timeout_deadline(timeout_s)
    rows = _observed_profile_call(
        _telemetry_observer,
        "degree_distribution",
        lambda: _run_read(
            client,
            f"MATCH (n:{label_ref}) WITH COUNT {{ (n)--() }} AS degree "
            "RETURN coalesce(percentileCont(degree, 0.5), 0) AS median, "
            "coalesce(percentileCont(degree, 0.95), 0) AS p95, "
            "coalesce(percentileCont(degree, 0.99), 0) AS p99, "
            "coalesce(max(degree), 0) AS maximum",
            deadline=deadline,
        ),
    )
    if not rows:
        return DegreeDistribution(median=0, p95=0, p99=0, maximum=0)
    row = rows[0]
    return DegreeDistribution(
        median=round(float(row["median"]), 2),
        p95=round(float(row["p95"]), 2),
        p99=round(float(row["p99"]), 2),
        maximum=int(row["maximum"]),
    )


def _python_value_type(value: Any) -> str:
    if value is None:
        return "unknown"
    if isinstance(value, bool):
        return "BOOLEAN"
    if isinstance(value, int):
        return "INTEGER"
    if isinstance(value, float):
        return "FLOAT"
    if isinstance(value, str):
        return "STRING"
    if isinstance(value, list):
        return "LIST"
    if isinstance(value, dict):
        return "MAP"
    return value.__class__.__name__


def _timeout_deadline(timeout_s: float | None) -> float | None:
    return None if timeout_s is None else time.monotonic() + timeout_s


def _remaining_budget(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise GraphCheckError(
            "profile.budget_exceeded",
            f"Profiling exceeded the {DEFAULT_PROFILE_BUDGET_SECONDS} second budget.",
            "Retry profiling after reducing graph load.",
        )
    return remaining


def _run_read(
    client: Neo4jClient,
    query: str,
    params: dict[str, object] | None = None,
    *,
    deadline: float | None,
) -> list[dict[str, Any]]:
    if deadline is None:
        return client.run_read(query, params)
    remaining = _remaining_budget(deadline)
    # print(f"Remaining timeout: {remaining:.2f}s")
    return client.run_read(query, params, timeout_s=remaining)


def _budget_exceeded(deadline: float) -> bool:
    return time.monotonic() >= deadline


def _coverage(populated_count: int, total_count: int) -> float:
    if total_count == 0:
        return 0.0
    return round((populated_count / total_count) * 100, 2)


def _cypher_identifier(value: str) -> str:
    return f"`{value.replace('`', '``')}`"


def collect_property_coverage(
    client: Neo4jClient,
    *,
    timeout_s: float | None = None,
    _deadline: float | None = None,
) -> list[PropertyCoverage]:
    deadline = _deadline if _deadline is not None else _timeout_deadline(timeout_s)
    inventory = _ACTIVE_INVENTORY.get()
    coverage = (
        [*inventory.nodes, *inventory.relationships]
        if inventory is not None and inventory.nodes_complete and inventory.relationships_complete
        else [
            *collect_node_property_coverage(client, _deadline=deadline),
            *collect_relationship_property_coverage(client, _deadline=deadline),
        ]
    )
    return sorted(
        coverage,
        key=lambda item: (item.owner, item.owner_name, item.property),
    )


def collect_node_property_coverage(
    client: Neo4jClient,
    *,
    timeout_s: float | None = None,
    _deadline: float | None = None,
) -> list[PropertyCoverage]:
    deadline = _deadline if _deadline is not None else _timeout_deadline(timeout_s)
    coverage: list[PropertyCoverage] = []

    labels = sorted(
        str(row["label"])
        for row in _run_read(client, "CALL db.labels() YIELD label RETURN label", deadline=deadline)
    )

    for label in labels:
        _, label_coverage = _collect_label_inventory(client, label, deadline)
        coverage.extend(label_coverage)

    return coverage


def collect_relationship_property_coverage(
    client: Neo4jClient,
    *,
    timeout_s: float | None = None,
    _deadline: float | None = None,
) -> list[PropertyCoverage]:
    deadline = _deadline if _deadline is not None else _timeout_deadline(timeout_s)
    coverage: list[PropertyCoverage] = []
    relationship_types = sorted(
        str(row["relationshipType"])
        for row in _run_read(
            client,
            "CALL db.relationshipTypes() YIELD relationshipType RETURN relationshipType",
            deadline=deadline,
        )
    )

    for relationship_type in relationship_types:
        _, relationship_coverage = _collect_relationship_inventory(
            client, relationship_type, deadline
        )
        coverage.extend(relationship_coverage)

    return coverage
