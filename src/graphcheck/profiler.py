from __future__ import annotations

import time
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
from graphcheck.errors import GraphCheckError
from graphcheck.neo4j_adapter import Neo4jClient

# test addition
#DEFAULT_PROFILE_BUDGET_SECONDS = 3
DEFAULT_PROFILE_BUDGET_SECONDS = 60


def profile(client: Neo4jClient) -> BaselineProfile:
    deadline = time.monotonic() + DEFAULT_PROFILE_BUDGET_SECONDS
    target, _, counts = client.probe(timeout_s=_remaining_budget(deadline))
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
        )

    try:
        labels = collect_labels(client, _deadline=deadline)
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
        )

    try:
        relationship_types = collect_relationship_types(client, _deadline=deadline)
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
        )

    try:
        constraints = collect_constraints(client, _deadline=deadline)
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
        )

    try:
        indexes = collect_indexes(client, _deadline=deadline)
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
        )

    try:
        property_coverage = collect_property_coverage(client, _deadline=deadline)
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
    return BaselineProfile(
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


def _partial_profile(
    target,
    counts,
    labels: list[LabelProfile],
    relationship_types: list[RelationshipTypeProfile],
    constraints: list[ConstraintProfile],
    indexes: list[IndexProfile],
    property_coverage: list[PropertyCoverage],
    reason: str,
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

    return BaselineProfile(
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


def print_profile(client: Neo4jClient) -> None:
    print(profile(client).model_dump_json(indent=2, by_alias=True))


def collect_labels(
    client: Neo4jClient,
    *,
    timeout_s: float | None = None,
    _deadline: float | None = None,
) -> list[LabelProfile]:
    deadline = _deadline if _deadline is not None else _timeout_deadline(timeout_s)
    # time.sleep(3)  # Wait for the database to stabilize before collecting labels
    labels = sorted(
        str(row["label"])
        for row in _run_read(client, "CALL db.labels() YIELD label RETURN label", deadline=deadline)
    )
    return [_collect_label(client, label, deadline) for label in labels]


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


def _collect_label(client: Neo4jClient, label: str, deadline: float | None) -> LabelProfile:
    label_ref = _cypher_identifier(label)
    count = _label_count(client, label_ref, deadline)
    properties = [
        _collect_property(client, label_ref, property_name, deadline)
        for property_name in _label_properties(client, label_ref, deadline)
    ]
    return LabelProfile(
        name=label,
        count=count,
        properties=properties,
        degree_distribution=_collect_degree_distribution(
            client,
            label_ref,
            _deadline=deadline,
        ),
    )


def _collect_degree_distribution(
    client: Neo4jClient,
    label_ref: str,
    *,
    timeout_s: float | None = None,
    _deadline: float | None = None,
) -> DegreeDistribution:
    deadline = _deadline if _deadline is not None else _timeout_deadline(timeout_s)
    rows = _run_read(
        client,
        f"MATCH (n:{label_ref}) RETURN COUNT {{ (n)--() }} AS degree",
        deadline=deadline,
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
    return lower + (upper - lower) * fraction


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
    #time.sleep(3)  # Wait for the database to stabilize before collecting property coverage
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
