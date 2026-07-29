from __future__ import annotations

import json
import os
import time

import pytest
import yaml

from graphcheck.contracts.check import load_suite
from graphcheck.engine.compiler import CypherCompiler
from graphcheck.neo4j_adapter import Neo4jClient
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


def test_representative_native_token_plans_are_extractable(neo4j_profile, tmp_path):
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
            operator_names = {operator["operator"] for operator in operators}
            if check.id == "label-count":
                assert "AllNodesScan" not in operator_names
                assert "NodeCountFromCountStore" in operator_names
            if check.id == "relationship-count":
                assert "RelationshipCountFromCountStore" in operator_names
            records.append(
                BenchmarkRecord.from_samples(
                    f"plan-{check.id}",
                    [elapsed_ms],
                    server=target.server_version,
                    cypher=cypher_version_for_server(target.server_version),
                    details={"operators": operators},
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
    }
    for record in payload:
        validate_record(record)
        assert record["driver"]
        assert record["server"]
        assert record["cypher"]
