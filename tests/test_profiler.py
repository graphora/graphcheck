from __future__ import annotations

from typing import Any, cast

import pytest

from graphcheck.contracts.profile import (
    ConstraintProfile,
    DegreeDistribution,
    IndexProfile,
    LabelProfile,
    ProfileProperty,
    PropertyCoverage,
    RelationshipTypeProfile,
)
from graphcheck.contracts.results import Capabilities, RunTarget
from graphcheck.errors import GraphCheckError
from graphcheck.neo4j_adapter import Counts, Neo4jClient
from graphcheck.profiler import (
    _collect_degree_distribution,
    collect_constraints,
    collect_indexes,
    collect_labels,
    collect_relationship_types,
    profile,
)


class FakeNeo4jClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object] | None]] = []

    def probe(self) -> tuple[RunTarget, object, Counts]:
        return (
            RunTarget(
                database="neo4j",
                server_version="5.18.0",
                edition="community",
                fingerprint="abc123",
                capabilities=Capabilities(apoc=False, count_store=True),
            ),
            object(),
            Counts(nodes=5, relationships=7),
        )

    def run_read(self, query: str, params: dict[str, object] | None = None) -> list[dict[str, Any]]:
        self.calls.append((query, params))
        if query == "CALL db.labels() YIELD label RETURN label":
            return [{"label": "Customer"}, {"label": "Account"}]
        if query == ("CALL db.relationshipTypes() YIELD relationshipType RETURN relationshipType"):
            return [{"relationshipType": "OWNS"}, {"relationshipType": "HAS_ACCOUNT"}]
        if query == "MATCH ()-[r:`HAS_ACCOUNT`]->() RETURN count(r) AS count":
            return [{"count": 2}]
        if query == "MATCH ()-[r:`OWNS`]->() RETURN count(r) AS count":
            return [{"count": 5}]
        if query == "SHOW CONSTRAINTS":
            return [
                {
                    "name": "customer_identity",
                    "type": "NODE_KEY",
                    "labelsOrTypes": ["Person", "Customer"],
                    "properties": ["tenant_id", "id"],
                },
                {
                    "name": "account_id_unique",
                    "type": "UNIQUENESS",
                    "labelsOrTypes": ["Account"],
                    "properties": ["id"],
                },
            ]
        if query == "SHOW INDEXES":
            return [
                {
                    "name": "customer_search",
                    "type": "FULLTEXT",
                    "labelsOrTypes": ["Person", "Customer"],
                    "properties": ["name", "email"],
                },
                {
                    "name": "account_lookup",
                    "type": "LOOKUP",
                    "labelsOrTypes": None,
                    "properties": None,
                },
            ]
        if query == "MATCH (n:`Account`) RETURN count(n) AS count":
            return [{"count": 2}]
        if query == "MATCH (n:`Customer`) RETURN count(n) AS count":
            return [{"count": 3}]
        if query == "MATCH (n:`Account`) RETURN COUNT { (n)--() } AS degree":
            return [{"degree": 3}, {"degree": 1}]
        if query == "MATCH (n:`Customer`) RETURN COUNT { (n)--() } AS degree":
            return [{"degree": 4}, {"degree": 0}, {"degree": 2}]
        if query.startswith("MATCH (n:`Account`) UNWIND keys(n)"):
            return [{"property": "id"}]
        if query.startswith("MATCH (n:`Customer`) UNWIND keys(n)"):
            return [{"property": "name"}, {"property": "id"}]
        if query == ("MATCH (n:`Account`) WHERE n[$property] IS NOT NULL RETURN count(n) AS count"):
            return [{"count": 2}]
        if query == (
            "MATCH (n:`Customer`) WHERE n[$property] IS NOT NULL RETURN count(n) AS count"
        ):
            return [{"count": 3 if params == {"property": "id"} else 2}]
        if query == (
            "MATCH (n:`Account`) WHERE n[$property] IS NOT NULL "
            "RETURN n[$property] AS value LIMIT 1"
        ):
            return [{"value": "account-1"}]
        if query == (
            "MATCH (n:`Customer`) WHERE n[$property] IS NOT NULL "
            "RETURN n[$property] AS value LIMIT 1"
        ):
            return [{"value": 1 if params == {"property": "id"} else "Ada"}]
        raise AssertionError(f"unexpected query: {query}")


class UnknownTypeClient:
    def run_read(self, query: str, params: dict[str, object] | None = None) -> list[dict[str, Any]]:
        if query == "CALL db.labels() YIELD label RETURN label":
            return [{"label": "Customer"}]
        if query == "MATCH (n:`Customer`) RETURN count(n) AS count":
            return [{"count": 1}]
        if query == "MATCH (n:`Customer`) RETURN COUNT { (n)--() } AS degree":
            return [{"degree": 5}]
        if query.startswith("MATCH (n:`Customer`) UNWIND keys(n)"):
            return [{"property": "id"}]
        if query == (
            "MATCH (n:`Customer`) WHERE n[$property] IS NOT NULL RETURN count(n) AS count"
        ):
            return [{"count": 1}]
        if query == (
            "MATCH (n:`Customer`) WHERE n[$property] IS NOT NULL "
            "RETURN n[$property] AS value LIMIT 1"
        ):
            raise GraphCheckError("neo4j.query_failed", "query failed", "try again")
        raise AssertionError(f"unexpected query: {query}")


def test_collect_labels_returns_sorted_contract_models() -> None:
    labels = collect_labels(cast(Neo4jClient, FakeNeo4jClient()))

    assert labels == [
        LabelProfile(
            name="Account",
            count=2,
            properties=[ProfileProperty(name="id", type="STRING")],
            degree_distribution=DegreeDistribution(
                median=2,
                p95=2.9,
                p99=2.98,
                maximum=3,
            ),
        ),
        LabelProfile(
            name="Customer",
            count=3,
            properties=[
                ProfileProperty(name="id", type="INTEGER"),
                ProfileProperty(name="name", type="STRING"),
            ],
            degree_distribution=DegreeDistribution(
                median=2,
                p95=3.8,
                p99=3.96,
                maximum=4,
            ),
        ),
    ]


def test_collect_labels_falls_back_to_unknown_when_type_probe_fails() -> None:
    labels = collect_labels(cast(Neo4jClient, UnknownTypeClient()))

    assert labels[0].properties == [ProfileProperty(name="id", type="unknown")]


class DegreeClient:
    def __init__(self, degrees: list[int]) -> None:
        self.degrees = degrees

    def run_read(self, query: str, params: dict[str, object] | None = None) -> list[dict[str, Any]]:
        assert query == "MATCH (n:`Test`) RETURN COUNT { (n)--() } AS degree"
        assert params is None
        return [{"degree": degree} for degree in self.degrees]


def test_collect_degree_distribution_handles_empty_label() -> None:
    distribution = _collect_degree_distribution(cast(Neo4jClient, DegreeClient([])), "`Test`")

    assert distribution == DegreeDistribution(median=0, p95=0, p99=0, maximum=0)


def test_collect_degree_distribution_handles_one_node() -> None:
    distribution = _collect_degree_distribution(cast(Neo4jClient, DegreeClient([7])), "`Test`")

    assert distribution == DegreeDistribution(median=7, p95=7, p99=7, maximum=7)


def test_collect_degree_distribution_is_deterministic_for_multiple_nodes() -> None:
    client = cast(Neo4jClient, DegreeClient([10, 0, 4, 2]))

    first = _collect_degree_distribution(client, "`Test`")
    second = _collect_degree_distribution(client, "`Test`")

    assert first == second
    assert first.median == 3
    assert first.p95 == pytest.approx(9.1)
    assert first.p99 == pytest.approx(9.82)
    assert first.maximum == 10
    assert first.median <= first.p95 <= first.p99 <= first.maximum


def test_collect_relationship_types_returns_sorted_contract_models() -> None:
    relationship_types = collect_relationship_types(cast(Neo4jClient, FakeNeo4jClient()))

    assert relationship_types == [
        RelationshipTypeProfile(name="HAS_ACCOUNT", count=2),
        RelationshipTypeProfile(name="OWNS", count=5),
    ]


def test_collect_constraints_returns_canonically_sorted_contract_models() -> None:
    constraints = collect_constraints(cast(Neo4jClient, FakeNeo4jClient()))

    assert constraints == [
        ConstraintProfile(
            name="account_id_unique",
            type="UNIQUENESS",
            labels_or_types=["Account"],
            properties=["id"],
        ),
        ConstraintProfile(
            name="customer_identity",
            type="NODE_KEY",
            labels_or_types=["Customer", "Person"],
            properties=["id", "tenant_id"],
        ),
    ]


def test_collect_indexes_returns_canonically_sorted_contract_models() -> None:
    indexes = collect_indexes(cast(Neo4jClient, FakeNeo4jClient()))

    assert indexes == [
        IndexProfile(
            name="account_lookup",
            type="LOOKUP",
            labels_or_types=[],
            properties=[],
        ),
        IndexProfile(
            name="customer_search",
            type="FULLTEXT",
            labels_or_types=["Customer", "Person"],
            properties=["email", "name"],
        ),
    ]


def test_profile_returns_valid_baseline_from_probe_and_labels() -> None:
    baseline = profile(cast(Neo4jClient, FakeNeo4jClient()))

    assert baseline.status == "complete"
    assert baseline.target.database == "neo4j"
    assert baseline.statistics.node_count == 5
    assert baseline.statistics.relationship_count == 7
    assert baseline.statistics.property_coverage == [
        PropertyCoverage(
            owner="node",
            owner_name="Account",
            property="id",
            coverage=100.0,
        ),
        PropertyCoverage(
            owner="node",
            owner_name="Customer",
            property="id",
            coverage=100.0,
        ),
        PropertyCoverage(
            owner="node",
            owner_name="Customer",
            property="name",
            coverage=66.67,
        ),
    ]
    assert all(label.degree_distribution is not None for label in baseline.graph_schema.labels)
    assert all(
        label.degree_distribution.maximum in {3, 4}
        for label in baseline.graph_schema.labels
        if label.degree_distribution is not None
    )
    assert [label.name for label in baseline.graph_schema.labels] == ["Account", "Customer"]
    assert baseline.graph_schema.relationship_types == [
        RelationshipTypeProfile(name="HAS_ACCOUNT", count=2),
        RelationshipTypeProfile(name="OWNS", count=5),
    ]
    assert baseline.graph_schema.constraints == [
        ConstraintProfile(
            name="account_id_unique",
            type="UNIQUENESS",
            labels_or_types=["Account"],
            properties=["id"],
        ),
        ConstraintProfile(
            name="customer_identity",
            type="NODE_KEY",
            labels_or_types=["Customer", "Person"],
            properties=["id", "tenant_id"],
        ),
    ]
    assert baseline.graph_schema.indexes == [
        IndexProfile(
            name="account_lookup",
            type="LOOKUP",
            labels_or_types=[],
            properties=[],
        ),
        IndexProfile(
            name="customer_search",
            type="FULLTEXT",
            labels_or_types=["Customer", "Person"],
            properties=["email", "name"],
        ),
    ]
    assert baseline.fingerprint.startswith("sha256:")
