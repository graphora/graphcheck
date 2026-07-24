from __future__ import annotations

import time
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from statistics import median
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


class _LabelCollectionError(GraphCheckError):
    def __init__(self, cause: GraphCheckError, labels: list[LabelProfile]) -> None:
        super().__init__(cause.error.code, cause.error.message, cause.error.fix)
        self.labels = labels
        self.partial_reason_code = (
            "degree_distribution_incomplete"
            if isinstance(cause, _DegreeDistributionError)
            else "schema_incomplete"
        )


class _DegreeDistributionError(GraphCheckError):
    def __init__(self, cause: GraphCheckError, label: LabelProfile) -> None:
        super().__init__(cause.error.code, cause.error.message, cause.error.fix)
        self.label = label


def profile(
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
    # time.sleep(3)  # Wait for the database to stabilize before collecting labels
    labels = sorted(
        str(row["label"])
        for row in _run_read(client, "CALL db.labels() YIELD label RETURN label", deadline=deadline)
    )
    collected: list[LabelProfile] = []
    failure: GraphCheckError | None = None
    for label in labels:
        try:
            collected.append(
                _collect_label(
                    client,
                    label,
                    deadline,
                    telemetry_observer=_telemetry_observer,
                )
            )
        except _DegreeDistributionError as exc:
            collected.append(exc.label)
            failure = failure or exc
        except GraphCheckError as exc:
            failure = failure or exc
    if failure is not None:
        raise _LabelCollectionError(failure, collected) from failure
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
    return [
        _collect_relationship_type(client, relationship_type, deadline)
        for relationship_type in relationship_types
    ]


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


def _collect_relationship_type(
    client: Neo4jClient, relationship_type: str, deadline: float | None
) -> RelationshipTypeProfile:
    relationship_type_ref = _cypher_identifier(relationship_type)
    return RelationshipTypeProfile(
        name=relationship_type,
        count=_relationship_type_count(client, relationship_type_ref, deadline),
    )


def _relationship_type_count(
    client: Neo4jClient, relationship_type_ref: str, deadline: float | None
) -> int:
    rows = _run_read(
        client,
        f"MATCH ()-[r:{relationship_type_ref}]->() RETURN count(r) AS count",
        deadline=deadline,
    )
    return int(rows[0]["count"]) if rows else 0


def _collect_label(
    client: Neo4jClient,
    label: str,
    deadline: float | None,
    *,
    telemetry_observer: ProfileTelemetryObserver | None = None,
) -> LabelProfile:
    label_ref = _cypher_identifier(label)
    count = _label_count(client, label_ref, deadline)
    properties = [
        _collect_property(client, label_ref, property_name, deadline)
        for property_name in _label_properties(client, label_ref, deadline)
    ]
    try:
        degree_distribution = _collect_degree_distribution(
            client,
            label_ref,
            _deadline=deadline,
            _telemetry_observer=telemetry_observer,
        )
    except GraphCheckError as exc:
        raise _DegreeDistributionError(
            exc,
            LabelProfile(
                name=label,
                count=count,
                properties=properties,
                degree_distribution=None,
            ),
        ) from exc
    return LabelProfile(
        name=label,
        count=count,
        properties=properties,
        degree_distribution=degree_distribution,
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
            f"MATCH (n:{label_ref}) RETURN COUNT {{ (n)--() }} AS degree",
            deadline=deadline,
        ),
    )
    degrees = sorted(int(row["degree"]) for row in rows)
    if not degrees:
        return DegreeDistribution(median=0, p95=0, p99=0, maximum=0)
    return DegreeDistribution(
        median=median(degrees),
        p95=_percentile(degrees, 0.95),
        p99=_percentile(degrees, 0.99),
        maximum=degrees[-1],
    )


def _percentile(sorted_values: list[int], percentile: float) -> float:
    rank = (len(sorted_values) - 1) * percentile
    lower_index = int(rank)
    upper_index = min(lower_index + 1, len(sorted_values) - 1)
    fraction = rank - lower_index
    lower = sorted_values[lower_index]
    upper = sorted_values[upper_index]
    return round(lower + (upper - lower) * fraction, 2)


def _label_count(client: Neo4jClient, label_ref: str, deadline: float | None) -> int:
    rows = _run_read(client, f"MATCH (n:{label_ref}) RETURN count(n) AS count", deadline=deadline)
    return int(rows[0]["count"]) if rows else 0


def _label_properties(client: Neo4jClient, label_ref: str, deadline: float | None) -> list[str]:
    rows = _run_read(
        client,
        f"MATCH (n:{label_ref}) UNWIND keys(n) AS property "
        "RETURN DISTINCT property ORDER BY property",
        deadline=deadline,
    )
    return sorted(str(row["property"]) for row in rows)


def _collect_property(
    client: Neo4jClient, label_ref: str, property_name: str, deadline: float | None
) -> ProfileProperty:
    return ProfileProperty(
        name=property_name,
        type=_property_type(client, label_ref, property_name, deadline),
    )


def _property_count(
    client: Neo4jClient, label_ref: str, property_name: str, deadline: float | None
) -> int:
    rows = _run_read(
        client,
        f"MATCH (n:{label_ref}) WHERE n[$property] IS NOT NULL RETURN count(n) AS count",
        {"property": property_name},
        deadline=deadline,
    )
    return int(rows[0]["count"]) if rows else 0


def _relationship_properties(
    client: Neo4jClient, relationship_type_ref: str, deadline: float | None
) -> list[str]:
    rows = _run_read(
        client,
        f"MATCH ()-[r:{relationship_type_ref}]->() UNWIND keys(r) AS property "
        "RETURN DISTINCT property ORDER BY property",
        deadline=deadline,
    )
    return sorted(str(row["property"]) for row in rows)


def _relationship_property_count(
    client: Neo4jClient,
    relationship_type_ref: str,
    property_name: str,
    deadline: float | None,
) -> int:
    rows = _run_read(
        client,
        f"MATCH ()-[r:{relationship_type_ref}]->() "
        "WHERE r[$property] IS NOT NULL RETURN count(r) AS count",
        {"property": property_name},
        deadline=deadline,
    )
    return int(rows[0]["count"]) if rows else 0


def _property_type(
    client: Neo4jClient,
    label_ref: str,
    property_name: str,
    deadline: float | None,
) -> str:
    rows = _run_read(
        client,
        f"MATCH (n:{label_ref}) WHERE n[$property] IS NOT NULL "
        "WITH n "
        "ORDER BY id(n) "
        "RETURN n[$property] AS value LIMIT 1",
        {"property": property_name},
        deadline=deadline,
    )
    if not rows:
        return "unknown"
    return _python_value_type(rows[0].get("value"))


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
    # time.sleep(3)  # Wait for the database to stabilize before collecting property coverage
    coverage = [
        *collect_node_property_coverage(
            client,
            _deadline=deadline,
        ),
        *collect_relationship_property_coverage(
            client,
            _deadline=deadline,
        ),
    ]
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
        label_ref = _cypher_identifier(label)
        label_count = _label_count(client, label_ref, deadline)

        for property_name in _label_properties(client, label_ref, deadline):
            populated_count = _property_count(
                client,
                label_ref,
                property_name,
                deadline,
            )

            coverage.append(
                PropertyCoverage(
                    owner="node",
                    owner_name=label,
                    property=property_name,
                    coverage=_coverage(
                        populated_count,
                        label_count,
                    ),
                )
            )

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
        relationship_type_ref = _cypher_identifier(relationship_type)
        relationship_count = _relationship_type_count(client, relationship_type_ref, deadline)
        for property_name in _relationship_properties(client, relationship_type_ref, deadline):
            populated_count = _relationship_property_count(
                client,
                relationship_type_ref,
                property_name,
                deadline,
            )
            coverage.append(
                PropertyCoverage(
                    owner="relationship",
                    owner_name=relationship_type,
                    property=property_name,
                    coverage=_coverage(populated_count, relationship_count),
                )
            )

    return coverage
