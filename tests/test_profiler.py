from __future__ import annotations

from typing import Any, cast

import pytest

import graphcheck.profiler as profiler_module
from graphcheck.contracts.profile import (
    BaselineProfile,
    ConstraintProfile,
    DegreeDistribution,
    IndexProfile,
    LabelProfile,
    ProfileProperty,
    ProfileStatus,
    PropertyCoverage,
    RelationshipTypeProfile,
    profile_fingerprint,
)
from graphcheck.contracts.results import Capabilities, RunTarget
from graphcheck.errors import GraphCheckError
from graphcheck.neo4j_adapter import Counts, Neo4jClient
from graphcheck.profiler import (
    _collect_degree_distribution,
    collect_constraints,
    collect_indexes,
    collect_labels,
    collect_property_coverage,
    collect_relationship_property_coverage,
    collect_relationship_types,
    profile,
)


class FakeNeo4jClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object] | None]] = []
        self.probe_timeout_s: float | None = None
        self.query_timeouts: list[float | None] = []

    def probe(self, *, timeout_s: float | None = None) -> tuple[RunTarget, object, Counts]:
        self.probe_timeout_s = timeout_s
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

    def run_read(
        self,
        query: str,
        params: dict[str, object] | None = None,
        *,
        timeout_s: float | None = None,
    ) -> list[dict[str, Any]]:
        self.calls.append((query, params))
        self.query_timeouts.append(timeout_s)
        if query == "CALL db.labels() YIELD label RETURN label":
            return [{"label": "Customer"}, {"label": "Account"}]
        if query == ("CALL db.relationshipTypes() YIELD relationshipType RETURN relationshipType"):
            return [{"relationshipType": "OWNS"}, {"relationshipType": "HAS_ACCOUNT"}]
        if query == "MATCH ()-[r:`HAS_ACCOUNT`]->() RETURN count(r) AS count":
            return [{"count": 2}]
        if query == "MATCH ()-[r:`OWNS`]->() RETURN count(r) AS count":
            return [{"count": 5}]
        if query.startswith("MATCH ()-[r:`HAS_ACCOUNT`]->() UNWIND keys(r)"):
            return []
        if query.startswith("MATCH ()-[r:`OWNS`]->() UNWIND keys(r)"):
            return [{"property": "since"}, {"property": "role"}]
        if query == (
            "MATCH ()-[r:`OWNS`]->() WHERE r[$property] IS NOT NULL RETURN count(r) AS count"
        ):
            return [{"count": 5 if params == {"property": "since"} else 3}]
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
            "WITH n "
            "ORDER BY id(n) "
            "RETURN n[$property] AS value LIMIT 1"
        ):
            return [{"value": "account-1"}]
        if query == (
            "MATCH (n:`Customer`) WHERE n[$property] IS NOT NULL "
            "WITH n "
            "ORDER BY id(n) "
            "RETURN n[$property] AS value LIMIT 1"
        ):
            return [{"value": 1 if params == {"property": "id"} else "Ada"}]
        raise AssertionError(f"unexpected query: {query}")


class UnknownTypeClient:
    def run_read(
        self,
        query: str,
        params: dict[str, object] | None = None,
        *,
        timeout_s: float | None = None,
    ) -> list[dict[str, Any]]:
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
            "WITH n "
            "ORDER BY id(n) "
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


def test_collect_labels_propagates_type_probe_failure() -> None:
    with pytest.raises(GraphCheckError, match="query failed"):
        collect_labels(cast(Neo4jClient, UnknownTypeClient()))


class EmptyTypeClient(UnknownTypeClient):
    def run_read(
        self,
        query: str,
        params: dict[str, object] | None = None,
        *,
        timeout_s: float | None = None,
    ) -> list[dict[str, Any]]:
        if query == (
            "MATCH (n:`Customer`) WHERE n[$property] IS NOT NULL "
            "WITH n "
            "ORDER BY id(n) "
            "RETURN n[$property] AS value LIMIT 1"
        ):
            return []
        return super().run_read(query, params, timeout_s=timeout_s)


def test_collect_labels_uses_unknown_when_type_probe_returns_no_value() -> None:
    labels = collect_labels(cast(Neo4jClient, EmptyTypeClient()))

    assert labels[0].properties == [ProfileProperty(name="id", type="unknown")]


class FailedPropertyTypeClient(FakeNeo4jClient):
    def run_read(
        self,
        query: str,
        params: dict[str, object] | None = None,
        *,
        timeout_s: float | None = None,
    ) -> list[dict[str, Any]]:
        if "RETURN n[$property] AS value LIMIT 1" in query:
            raise GraphCheckError("neo4j.query_failed", "type query failed", "try again")
        return super().run_read(query, params, timeout_s=timeout_s)


def test_profile_is_partial_when_property_type_measurement_fails() -> None:
    baseline = profile(cast(Neo4jClient, FailedPropertyTypeClient()))

    assert baseline.status is ProfileStatus.PARTIAL
    assert baseline.partial_reason
    assert "Failed collecting labels" in baseline.partial_reason
    assert "type query failed" in baseline.partial_reason


class DegreeClient:
    def __init__(self, degrees: list[int]) -> None:
        self.degrees = degrees

    def run_read(
        self,
        query: str,
        params: dict[str, object] | None = None,
        *,
        timeout_s: float | None = None,
    ) -> list[dict[str, Any]]:
        assert query == "MATCH (n:`Test`) RETURN COUNT { (n)--() } AS degree"
        assert params is None
        return [{"degree": degree} for degree in self.degrees]


class RelationshipCoverageClient:
    def __init__(self, properties: dict[str, dict[str, int]]) -> None:
        self.properties = properties

    def run_read(
        self,
        query: str,
        params: dict[str, object] | None = None,
        *,
        timeout_s: float | None = None,
    ) -> list[dict[str, Any]]:
        if query == "CALL db.relationshipTypes() YIELD relationshipType RETURN relationshipType":
            return [{"relationshipType": name} for name in reversed(self.properties)]
        for relationship_type, property_counts in self.properties.items():
            relationship_type_ref = f"`{relationship_type}`"
            if query == (f"MATCH ()-[r:{relationship_type_ref}]->() RETURN count(r) AS count"):
                return [{"count": max(property_counts.values(), default=0)}]
            if query.startswith(f"MATCH ()-[r:{relationship_type_ref}]->() UNWIND keys(r)"):
                return [{"property": name} for name in reversed(property_counts)]
            if query == (
                f"MATCH ()-[r:{relationship_type_ref}]->() "
                "WHERE r[$property] IS NOT NULL RETURN count(r) AS count"
            ):
                assert params is not None
                return [{"count": property_counts[str(params["property"])]}]
        raise AssertionError(f"unexpected query: {query}")


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
        PropertyCoverage(
            owner="relationship",
            owner_name="OWNS",
            property="role",
            coverage=60.0,
        ),
        PropertyCoverage(
            owner="relationship",
            owner_name="OWNS",
            property="since",
            coverage=100.0,
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


def test_profile_passes_remaining_budget_to_probe_and_queries() -> None:
    client = FakeNeo4jClient()

    baseline = profile(cast(Neo4jClient, client))

    assert baseline.status is ProfileStatus.COMPLETE
    assert client.probe_timeout_s is not None
    assert 0 < client.probe_timeout_s <= 60
    assert client.query_timeouts
    assert all(timeout is not None and timeout > 0 for timeout in client.query_timeouts)
    assert all(timeout <= client.probe_timeout_s for timeout in client.query_timeouts if timeout)


def test_collectors_shrink_timeout_for_each_database_operation(monkeypatch) -> None:
    clock_values = iter(float(value) for value in range(20))
    monkeypatch.setattr("graphcheck.profiler.time.monotonic", lambda: next(clock_values))
    client = FakeNeo4jClient()

    collect_labels(cast(Neo4jClient, client), timeout_s=20)

    timeouts = [timeout for timeout in client.query_timeouts if timeout is not None]
    assert timeouts
    assert timeouts == sorted(timeouts, reverse=True)
    assert len(set(timeouts)) == len(timeouts)


def test_profile_returns_valid_partial_baseline_when_wall_clock_budget_is_exceeded(
    monkeypatch,
) -> None:
    clock = [0.0]
    original = profiler_module.collect_labels

    def collect_then_expire(client: Neo4jClient, *, timeout_s: float | None = None):
        labels = original(client, timeout_s=timeout_s)
        clock[0] = 61.0
        return labels

    monkeypatch.setattr("graphcheck.profiler.time.monotonic", lambda: clock[0])
    monkeypatch.setattr("graphcheck.profiler.collect_labels", collect_then_expire)

    baseline = profile(cast(Neo4jClient, FakeNeo4jClient()))

    assert baseline.status is ProfileStatus.PARTIAL
    assert baseline.status is not ProfileStatus.COMPLETE
    assert baseline.partial_reason
    assert "exceeded the 60 second budget" in baseline.partial_reason
    assert baseline.statistics.node_count == 5
    assert baseline.statistics.relationship_count == 7
    assert [label.name for label in baseline.graph_schema.labels] == ["Account", "Customer"]
    assert baseline.graph_schema.relationship_types == []
    assert baseline.graph_schema.constraints == []
    assert baseline.graph_schema.indexes == []
    assert baseline.statistics.property_coverage == []
    assert baseline.fingerprint == profile_fingerprint(
        baseline.graph_schema,
        baseline.statistics,
    )


def test_profile_returns_partial_baseline_when_budget_is_exceeded_after_probe(
    monkeypatch,
) -> None:
    clock = [0.0]

    class ExpiringProbeClient(FakeNeo4jClient):
        def probe(self, *, timeout_s: float | None = None) -> tuple[RunTarget, object, Counts]:
            result = super().probe(timeout_s=timeout_s)
            clock[0] = 61.0
            return result

    monkeypatch.setattr("graphcheck.profiler.time.monotonic", lambda: clock[0])

    baseline = profile(cast(Neo4jClient, ExpiringProbeClient()))

    assert baseline.status is ProfileStatus.PARTIAL
    assert baseline.partial_reason
    assert "exceeded the 60 second budget after probe" in baseline.partial_reason
    assert baseline.statistics.node_count == 5
    assert baseline.statistics.relationship_count == 7
    _assert_collected_profile_sections(baseline, 0)
    assert baseline.fingerprint == profile_fingerprint(
        baseline.graph_schema,
        baseline.statistics,
    )


@pytest.mark.parametrize(
    ("collector_name", "reason_fragment", "completed_sections"),
    [
        ("collect_labels", "Failed collecting labels", 0),
        ("collect_relationship_types", "Failed collecting relationship types", 1),
        ("collect_constraints", "Failed collecting constraints", 2),
        ("collect_indexes", "Failed collecting indexes", 3),
        ("collect_property_coverage", "Failed collecting property coverage", 4),
    ],
)
def test_profile_returns_partial_baseline_when_collector_fails(
    monkeypatch,
    collector_name: str,
    reason_fragment: str,
    completed_sections: int,
) -> None:
    def fail_collection(client: Neo4jClient, *, timeout_s: float | None = None) -> None:
        raise GraphCheckError("neo4j.query_failed", "simulated collector failure", "retry")

    monkeypatch.setattr(f"graphcheck.profiler.{collector_name}", fail_collection)

    baseline = profile(cast(Neo4jClient, FakeNeo4jClient()))

    assert baseline.status is ProfileStatus.PARTIAL
    assert baseline.partial_reason
    assert reason_fragment in baseline.partial_reason
    assert "simulated collector failure" in baseline.partial_reason
    assert baseline.statistics.node_count == 5
    assert baseline.statistics.relationship_count == 7
    _assert_collected_profile_sections(baseline, completed_sections)
    assert baseline.fingerprint == profile_fingerprint(
        baseline.graph_schema,
        baseline.statistics,
    )


@pytest.mark.parametrize(
    ("stage", "collector_name", "completed_sections"),
    [
        ("labels", "collect_labels", 1),
        ("relationship types", "collect_relationship_types", 2),
        ("constraints", "collect_constraints", 3),
        ("indexes", "collect_indexes", 4),
        ("property coverage", "collect_property_coverage", 5),
    ],
)
def test_profile_returns_partial_baseline_when_budget_is_exceeded_after_stage(
    monkeypatch,
    stage: str,
    collector_name: str,
    completed_sections: int,
) -> None:
    clock = [0.0]
    original = getattr(profiler_module, collector_name)

    def collect_then_expire(client: Neo4jClient, *, timeout_s: float | None = None):
        result = original(client, timeout_s=timeout_s)
        clock[0] = 61.0
        return result

    monkeypatch.setattr("graphcheck.profiler.time.monotonic", lambda: clock[0])
    monkeypatch.setattr(f"graphcheck.profiler.{collector_name}", collect_then_expire)

    baseline = profile(cast(Neo4jClient, FakeNeo4jClient()))

    assert baseline.status is ProfileStatus.PARTIAL
    assert baseline.partial_reason
    assert f"exceeded the 60 second budget after collecting {stage}" in baseline.partial_reason
    assert baseline.statistics.node_count == 5
    assert baseline.statistics.relationship_count == 7
    _assert_collected_profile_sections(baseline, completed_sections)
    assert baseline.fingerprint == profile_fingerprint(
        baseline.graph_schema,
        baseline.statistics,
    )


def _assert_collected_profile_sections(
    baseline: BaselineProfile,
    completed_sections: int,
) -> None:
    sections = (
        baseline.graph_schema.labels,
        baseline.graph_schema.relationship_types,
        baseline.graph_schema.constraints,
        baseline.graph_schema.indexes,
        baseline.statistics.property_coverage,
    )
    assert [bool(section) for section in sections] == [
        index < completed_sections for index in range(len(sections))
    ]


def test_relationship_property_coverage_handles_multiple_types_and_properties() -> None:
    client = RelationshipCoverageClient(
        {
            "OWNS": {"since": 5, "role": 3},
            "HAS_ACCOUNT": {"source": 2},
        }
    )

    coverage = collect_relationship_property_coverage(cast(Neo4jClient, client))

    assert coverage == [
        PropertyCoverage(
            owner="relationship",
            owner_name="HAS_ACCOUNT",
            property="source",
            coverage=100.0,
        ),
        PropertyCoverage(
            owner="relationship",
            owner_name="OWNS",
            property="role",
            coverage=60.0,
        ),
        PropertyCoverage(
            owner="relationship",
            owner_name="OWNS",
            property="since",
            coverage=100.0,
        ),
    ]


def test_no_relationship_properties_returns_no_coverage_entries() -> None:
    client = RelationshipCoverageClient({"OWNS": {}})

    assert collect_relationship_property_coverage(cast(Neo4jClient, client)) == []


def test_merged_property_coverage_is_canonically_sorted() -> None:
    coverage = collect_property_coverage(cast(Neo4jClient, FakeNeo4jClient()))

    identities = [(item.owner, item.owner_name, item.property) for item in coverage]
    assert identities == sorted(identities)
