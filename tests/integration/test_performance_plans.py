from __future__ import annotations

import json
import os
import time

import pytest
import yaml
from neo4j import GraphDatabase

from graphcheck.contracts.check import load_suite
from graphcheck.engine.compiler import CypherCompiler
from graphcheck.neo4j_adapter import Neo4jClient
from tests.performance.gates import assert_plan_gate
from tests.performance.helpers import (
    BenchmarkRecord,
    cypher_version_for_server,
    validate_record,
    walk_plan,
    write_records,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("GRAPHCHECK_NEO4J_INTEGRATION") != "1",
    reason="set GRAPHCHECK_NEO4J_INTEGRATION=1 to capture Neo4j plans",
)


@pytest.fixture
def neo4j_performance_profile(neo4j_profile):
    try:
        _seed_indexed_fixture(neo4j_profile)
        yield neo4j_profile
    finally:
        _clean_indexed_fixture(neo4j_profile)


def test_representative_native_token_plans_are_extractable(
    neo4j_performance_profile, neo4j_test_target, tmp_path
):
    neo4j_profile = neo4j_performance_profile
    suite = load_suite(
        yaml.safe_dump(
            {
                "suite": "performance-plan-baseline",
                "drift": [
                    {
                        "id": "label-count",
                        "metric": "node_count",
                        "target": {"label": "Customer"},
                        "baseline": "plan",
                        "tolerance": {"max_delta": 0},
                    },
                    {
                        "id": "relationship-count",
                        "metric": "relationship_count",
                        "target": {"type": "PURCHASED"},
                        "baseline": "plan",
                        "tolerance": {"max_delta": 0},
                    },
                ],
                "conformance": [
                    {
                        "id": "completeness",
                        "check": "completeness",
                        "with": {"label": "Customer", "property": "id"},
                    },
                    {
                        "id": "uniqueness",
                        "check": "uniqueness",
                        "with": {"label": "Customer", "property": "id"},
                    },
                    {
                        "id": "hub-sampling",
                        "check": "hub_outlier",
                        "with": {"label": "Customer", "sample_size": 100},
                    },
                    {
                        "id": "pii-sampling",
                        "check": "pii_value_match",
                        "with": {"label": "Customer", "sample_size": 100},
                    },
                    {
                        "id": "typed-relationship",
                        "check": "rel_direction",
                        "with": {
                            "from_label": "Customer",
                            "rel_type": "PURCHASED",
                            "to_label": "Order",
                        },
                    },
                ],
            },
            sort_keys=False,
        )
    )
    compiler = CypherCompiler()
    client = Neo4jClient(neo4j_profile)
    records = []
    try:
        target, _, _ = client.probe()
        for check in suite.checks:
            compiled = compiler.compile(check, sample_seed=17)
            started = time.perf_counter_ns()
            plan = client.explain_read(compiled.query, compiled.params)
            elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000
            operators = walk_plan(plan)
            assert operators
            assert all(operator["operator"] != "unknown" for operator in operators)
            _assert_builtin_plan(
                check.id,
                compiled.query,
                operators,
                target.server_version,
                neo4j_test_target.cypher,
            )
            records.append(
                BenchmarkRecord.from_samples(
                    f"plan-{check.id}",
                    [elapsed_ms],
                    server=target.server_version,
                    cypher=cypher_version_for_server(
                        target.server_version, configured=neo4j_test_target.cypher
                    ),
                    details={"query": compiled.query, "operators": operators},
                )
            )
        index_query = (
            "MATCH (n:`Customer`) WHERE n.`id` = $value RETURN elementId(n) AS node_element_id"
        )
        started = time.perf_counter_ns()
        index_plan = client.explain_read(index_query, {"value": "customer-500"})
        elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000
        index_operators = walk_plan(index_plan)
        assert_plan_gate(
            name="indexed-customer-lookup",
            query=index_query,
            operators=index_operators,
            server=target.server_version,
            cypher=neo4j_test_target.cypher,
            required_any={
                "NodeIndexSeek",
                "NodeUniqueIndexSeek",
                "NodeIndexSeekByRange",
            },
            forbidden={"AllNodesScan", "NodeByLabelScan", "UnionNodeByLabelsScan"},
        )
        records.append(
            BenchmarkRecord.from_samples(
                "plan-indexed-customer-lookup",
                [elapsed_ms],
                server=target.server_version,
                cypher=cypher_version_for_server(
                    target.server_version, configured=neo4j_test_target.cypher
                ),
                details={"query": index_query, "operators": index_operators},
            )
        )
    finally:
        client.close()

    output = write_records(records, tmp_path / "neo4j-plans.json")
    payload = json.loads(output.read_text(encoding="utf-8"))

    assert {record["benchmark"] for record in payload} == {
        "plan-label-count",
        "plan-relationship-count",
        "plan-completeness",
        "plan-uniqueness",
        "plan-hub-sampling",
        "plan-pii-sampling",
        "plan-typed-relationship",
        "plan-indexed-customer-lookup",
    }
    for record in payload:
        validate_record(record)
        assert record["driver"]
        assert record["server"]
        assert record["cypher"]


def _seed_indexed_fixture(profile):
    with (
        GraphDatabase.driver(profile.uri, auth=(profile.user, profile.password)) as driver,
        driver.session(database=profile.database) as session,
    ):
        session.run(
            "CREATE RANGE INDEX customer_id_performance IF NOT EXISTS FOR (n:Customer) ON (n.id)"
        ).consume()
        session.run(
            "UNWIND range(1, 1000) AS index CREATE (:Customer {"
            "id: 'customer-' + toString(index), _graphcheck_performance_fixture: true})"
        ).consume()
        session.run(
            "MATCH (customer:Customer {id: 'customer-1'}) "
            "CREATE (customer)-[:PURCHASED]->(:Order {"
            "id: 'order-1', _graphcheck_performance_fixture: true})"
        ).consume()
        session.run("CALL db.awaitIndexes(60)").consume()


def _clean_indexed_fixture(profile):
    with (
        GraphDatabase.driver(profile.uri, auth=(profile.user, profile.password)) as driver,
        driver.session(database=profile.database) as session,
    ):
        session.run("DROP INDEX customer_id_performance IF EXISTS").consume()
        session.run("MATCH (n {_graphcheck_performance_fixture: true}) DETACH DELETE n").consume()


def _assert_builtin_plan(check_id, query, operators, server, cypher):
    required_any: set[str] = set()
    forbidden: set[str] = set()
    if check_id == "label-count":
        required_any = {"NodeCountFromCountStore"}
        forbidden = {"AllNodesScan"}
    elif check_id == "relationship-count":
        required_any = {"RelationshipCountFromCountStore"}
        forbidden = {"AllRelationshipsScan", "DirectedAllRelationshipsScan"}
    elif check_id == "typed-relationship":
        required_any = {"DirectedRelationshipTypeScan", "UndirectedRelationshipTypeScan"}
        forbidden = {
            "AllRelationshipsScan",
            "DirectedAllRelationshipsScan",
            "UndirectedAllRelationshipsScan",
        }
    assert_plan_gate(
        name=check_id,
        query=query,
        operators=operators,
        server=server,
        cypher=cypher,
        required_any=required_any,
        forbidden=forbidden,
    )
