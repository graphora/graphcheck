from __future__ import annotations

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


def profile(client: Neo4jClient) -> BaselineProfile:
    target, _, counts = client.probe()
    labels = collect_labels(client)
    graph_schema = GraphSchema(
        labels=labels,
        relationship_types=collect_relationship_types(client),
        constraints=collect_constraints(client),
        indexes=collect_indexes(client),
    )
    statistics = ProfileStatistics(
        node_count=counts.nodes,
        relationship_count=counts.relationships,
        property_coverage=collect_property_coverage(client),
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


def print_profile(client: Neo4jClient) -> None:
    print(profile(client).model_dump_json(indent=2, by_alias=True))


def collect_labels(client: Neo4jClient) -> list[LabelProfile]:
    labels = sorted(
        str(row["label"]) for row in client.run_read("CALL db.labels() YIELD label RETURN label")
    )
    return [_collect_label(client, label) for label in labels]


def collect_relationship_types(client: Neo4jClient) -> list[RelationshipTypeProfile]:
    relationship_types = sorted(
        str(row["relationshipType"])
        for row in client.run_read(
            "CALL db.relationshipTypes() YIELD relationshipType RETURN relationshipType"
        )
    )
    return [
        _collect_relationship_type(client, relationship_type)
        for relationship_type in relationship_types
    ]


def collect_constraints(client: Neo4jClient) -> list[ConstraintProfile]:
    constraints = [_collect_constraint(row) for row in client.run_read("SHOW CONSTRAINTS")]
    return sorted(constraints, key=lambda constraint: constraint.name)


def _collect_constraint(row: dict[str, Any]) -> ConstraintProfile:
    return ConstraintProfile(
        name=str(row["name"]),
        type=str(row["type"]),
        labels_or_types=sorted(str(value) for value in row["labelsOrTypes"]),
        properties=sorted(str(value) for value in row["properties"]),
    )


def collect_indexes(client: Neo4jClient) -> list[IndexProfile]:
    indexes = [_collect_index(row) for row in client.run_read("SHOW INDEXES")]
    return sorted(indexes, key=lambda index: index.name)


def _collect_index(row: dict[str, Any]) -> IndexProfile:
    return IndexProfile(
        name=str(row["name"]),
        type=str(row["type"]),
        labels_or_types=sorted(str(value) for value in row["labelsOrTypes"] or []),
        properties=sorted(str(value) for value in row["properties"] or []),
    )


def _collect_relationship_type(
    client: Neo4jClient, relationship_type: str
) -> RelationshipTypeProfile:
    relationship_type_ref = _cypher_identifier(relationship_type)
    return RelationshipTypeProfile(
        name=relationship_type,
        count=_relationship_type_count(client, relationship_type_ref),
    )


def _relationship_type_count(client: Neo4jClient, relationship_type_ref: str) -> int:
    rows = client.run_read(f"MATCH ()-[r:{relationship_type_ref}]->() RETURN count(r) AS count")
    return int(rows[0]["count"]) if rows else 0


def _collect_label(client: Neo4jClient, label: str) -> LabelProfile:
    label_ref = _cypher_identifier(label)
    count = _label_count(client, label_ref)
    properties = [
        _collect_property(client, label_ref, property_name)
        for property_name in _label_properties(client, label_ref)
    ]
    return LabelProfile(
        name=label,
        count=count,
        properties=properties,
        degree_distribution=_collect_degree_distribution(client, label_ref),
    )


def _collect_degree_distribution(client: Neo4jClient, label_ref: str) -> DegreeDistribution:
    rows = client.run_read(f"MATCH (n:{label_ref}) RETURN COUNT {{ (n)--() }} AS degree")
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


def _label_count(client: Neo4jClient, label_ref: str) -> int:
    rows = client.run_read(f"MATCH (n:{label_ref}) RETURN count(n) AS count")
    return int(rows[0]["count"]) if rows else 0


def _label_properties(client: Neo4jClient, label_ref: str) -> list[str]:
    rows = client.run_read(
        f"MATCH (n:{label_ref}) UNWIND keys(n) AS property "
        "RETURN DISTINCT property ORDER BY property"
    )
    return sorted(str(row["property"]) for row in rows)


def _collect_property(client: Neo4jClient, label_ref: str, property_name: str) -> ProfileProperty:
    return ProfileProperty(
        name=property_name,
        type=_property_type(client, label_ref, property_name),
    )


def _property_count(client: Neo4jClient, label_ref: str, property_name: str) -> int:
    rows = client.run_read(
        f"MATCH (n:{label_ref}) WHERE n[$property] IS NOT NULL RETURN count(n) AS count",
        {"property": property_name},
    )
    return int(rows[0]["count"]) if rows else 0


def _property_type(client: Neo4jClient, label_ref: str, property_name: str) -> str:
    try:
        rows = client.run_read(
            f"MATCH (n:{label_ref}) WHERE n[$property] IS NOT NULL "
            "RETURN n[$property] AS value LIMIT 1",
            {"property": property_name},
        )
    except GraphCheckError:
        return "unknown"
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


def _coverage(populated_count: int, total_count: int) -> float:
    if total_count == 0:
        return 0.0
    return round((populated_count / total_count) * 100, 2)


def _cypher_identifier(value: str) -> str:
    return f"`{value.replace('`', '``')}`"


def collect_property_coverage(client: Neo4jClient) -> list[PropertyCoverage]:
    coverage = []

    labels = sorted(
        str(row["label"]) for row in client.run_read("CALL db.labels() YIELD label RETURN label")
    )

    for label in labels:
        label_ref = _cypher_identifier(label)
        label_count = _label_count(client, label_ref)

        for property_name in _label_properties(client, label_ref):
            populated_count = _property_count(
                client,
                label_ref,
                property_name,
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

    return sorted(
        coverage,
        key=lambda c: (
            c.owner,
            c.owner_name,
            c.property,
        ),
    )
