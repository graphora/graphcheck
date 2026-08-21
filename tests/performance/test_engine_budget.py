from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest
import yaml

from graphcheck.connection_profiles import ConnectionProfile
from graphcheck.contracts.check import load_suite
from graphcheck.contracts.results import RunStatus, Verdict
from graphcheck.engine.compiler import CypherCompiler
from graphcheck.engine.runner import Engine, EngineConfig
from graphcheck.neo4j_adapter import Neo4jClient
from graphcheck.telemetry.collector import TelemetryCollector
from graphcheck.telemetry.events import QueryFinished
from tests.performance.gates import (
    assert_required_gates,
    evaluate_record,
    load_reference_budgets,
    write_gate_results,
)
from tests.performance.helpers import (
    BenchmarkRecord,
    cypher_version_for_server,
    percentile,
    validate_record,
    walk_plan,
    write_records,
)

URI = os.environ.get("GRAPHCHECK_PERFORMANCE_URI")
PASSWORD = os.environ.get("GRAPHCHECK_PERFORMANCE_PASSWORD")

pytestmark = [
    pytest.mark.performance,
    pytest.mark.skipif(
        URI is None or PASSWORD is None,
        reason=(
            "set GRAPHCHECK_PERFORMANCE_URI and GRAPHCHECK_PERFORMANCE_PASSWORD "
            "for the preloaded 10M-node budget target"
        ),
    ),
]


@pytest.mark.parametrize("concurrency", [1, 2, 4])
def test_thirty_check_run_on_ten_million_nodes_records_measurement_baseline(tmp_path, concurrency):
    profile = ConnectionProfile(
        uri=URI,
        user=os.environ.get("GRAPHCHECK_PERFORMANCE_USER", "neo4j"),
        password=PASSWORD,
        database=os.environ.get("GRAPHCHECK_PERFORMANCE_DATABASE", "neo4j"),
    )
    client = Neo4jClient(profile, max_concurrency=concurrency)
    collector = TelemetryCollector()
    config = EngineConfig(max_concurrency=concurrency)
    try:
        target, visibility, counts = client.probe()
        assert visibility.can_read is True
        assert counts.nodes is not None
        assert counts.relationships is not None
        node_count = counts.nodes
        relationship_count = counts.relationships
        assert node_count >= 10_000_000, "performance target must contain at least 10M nodes"
        label_rows = client.run_read(
            "MATCH (n) UNWIND labels(n) AS label "
            "RETURN label, count(*) AS count ORDER BY count DESC LIMIT 1"
        )
        assert label_rows, "performance target must contain at least one labeled node"
        busiest_label = label_rows[0]["label"]
        competency_queries = (
            ("node-count", "MATCH (n) RETURN count(n) AS total"),
            ("relationship-count", "MATCH ()-[r]->() RETURN count(r) AS total"),
        )
        property_queries = (
            ("sum", "MATCH (n) RETURN sum(size(keys(n))) AS property_slots"),
            ("max", "MATCH (n) RETURN max(size(keys(n))) AS property_slots"),
            ("min", "MATCH (n) RETURN min(size(keys(n))) AS property_slots"),
            ("average", "MATCH (n) RETURN avg(size(keys(n))) AS property_slots"),
        )
        suite = {
            "suite": "ten-million-budget",
            "competency": [
                {
                    "id": f"count-store-{kind}-{index:02d}",
                    "question": f"Can {kind} count-store query {index:02d} answer?",
                    "query": query,
                    "expect": {
                        "rows": {"exactly": 1},
                        "columns": ["total"],
                        "unique": True,
                    },
                }
                for kind, query in competency_queries
                for index in range(3)
            ]
            + [
                {
                    "id": f"property-scan-{kind}",
                    "question": f"Can full property {kind} scan answer?",
                    "query": query,
                    "expect": {
                        "rows": {"exactly": 1},
                        "columns": ["property_slots"],
                        "unique": True,
                    },
                }
                for kind, query in property_queries
            ],
            "drift": [
                {
                    "id": f"node-count-{index:02d}",
                    "metric": "node_count",
                    "target": {},
                    "baseline": "performance",
                    "tolerance": {"max_delta": 0},
                }
                for index in range(4)
            ]
            + [
                {
                    "id": f"relationship-count-{index:02d}",
                    "metric": "relationship_count",
                    "target": {},
                    "baseline": "performance",
                    "tolerance": {"max_delta": 0},
                }
                for index in range(4)
            ],
            "conformance": [
                {
                    "id": f"hub-outlier-{index:02d}",
                    "check": "hub_outlier",
                    "with": {"label": busiest_label, "sample_size": 1000},
                }
                for index in range(3)
            ]
            + [
                {
                    "id": f"pii-name-{index:02d}",
                    "check": "pii_name_match",
                    "with": {"sample_size": 1000},
                }
                for index in range(3)
            ]
            + [
                {
                    "id": f"pii-value-{index:02d}",
                    "check": "pii_value_match",
                    "with": {"sample_size": 1000},
                }
                for index in range(3)
            ]
            + [
                {
                    "id": f"orphan-{index:02d}",
                    "check": "no_orphans",
                    "with": {"label": busiest_label},
                }
                for index in range(3)
            ],
        }

        started = time.monotonic()
        suite_yaml = yaml.safe_dump(suite)
        results = Engine(
            client,
            config=config,
            baselines={
                "performance": {
                    "node_count": node_count,
                    "relationship_count": relationship_count,
                }
            },
            event_sink=collector,
        ).run_yaml(suite_yaml)
        elapsed_ms = (time.monotonic() - started) * 1000
        cypher_version = cypher_version_for_server(
            target.server_version,
            configured=os.environ.get("GRAPHCHECK_PERFORMANCE_CYPHER"),
        )
        representative_plans = _representative_plans(
            client,
            suite_yaml,
            server=target.server_version,
            cypher=cypher_version,
        )
    finally:
        client.close()

    assert results.totals.checks == 30
    assert results.totals.errored == 0
    assert results.totals.skipped == 0
    assert results.run.run_status is RunStatus.COMPLETE
    assert results.run.partial_reason is None
    assert all(check.measured is not None for check in results.checks)
    assert all(
        check.verdict is Verdict.PASS
        for check in results.checks
        if check.id.startswith(
            ("count-store-", "property-scan-", "node-count-", "relationship-count-")
        )
    )
    family_timings: dict[str, list[int]] = {}
    for check in results.checks:
        family_timings.setdefault(_family(check.id), []).append(check.duration_ms or 0)
    query_timings = [
        {
            "check_sequence": event.check_sequence,
            "role": event.query_role.value,
            "client_wall_ms": event.duration_ms,
            "server_available_after_ms": event.server_available_after_ms,
            "server_consumed_after_ms": event.server_consumed_after_ms,
        }
        for event in collector.events
        if isinstance(event, QueryFinished)
    ]
    family_records = [
        BenchmarkRecord.from_samples(
            f"customer-10m-concurrency-{concurrency}-{family}",
            durations,
            server=target.server_version,
            cypher=cypher_version,
            details={
                "family": family,
                "concurrency": concurrency,
                "representative_plan": representative_plans[family],
            },
        )
        for family, durations in sorted(family_timings.items())
    ]
    record = BenchmarkRecord.from_samples(
        f"engine-30-check-10m-concurrency-{concurrency}",
        [elapsed_ms],
        server=target.server_version,
        cypher=cypher_version,
        details={
            "concurrency": config.max_concurrency,
            "per_check_family_ms": {
                family: {
                    "checks": len(durations),
                    "total_ms": sum(durations),
                    "median_ms": percentile(durations, 0.5),
                    "p95_ms": percentile(durations, 0.95),
                    "maximum_ms": max(durations),
                    "representative_plan": representative_plans[family],
                }
                for family, durations in sorted(family_timings.items())
            },
            "queries": query_timings,
        },
    )
    configured_output = os.environ.get("GRAPHCHECK_PERFORMANCE_OUTPUT")
    output = (
        Path(configured_output).with_stem(
            f"{Path(configured_output).stem}-concurrency-{concurrency}"
        )
        if configured_output
        else tmp_path / f"engine-10m-concurrency-{concurrency}.json"
    )
    write_records([record, *family_records], output)
    payload = json.loads(output.read_text(encoding="utf-8"))
    for item in payload:
        validate_record(item)
    assert payload[0]["details"]["concurrency"] == config.max_concurrency
    assert payload[0]["details"]["queries"]
    assert all("client_wall_ms" in timing for timing in payload[0]["details"]["queries"])
    _enforce_family_budgets(family_records, output, concurrency)


def _family(check_id: str) -> str:
    return next(
        (
            family
            for prefix, family in (
                ("count-store-node-count-", "competency-node-count-store"),
                ("count-store-relationship-count-", "competency-relationship-count-store"),
                ("property-scan-", "competency-property-scan"),
                ("node-count-", "drift-node-count"),
                ("relationship-count-", "drift-relationship-count"),
                ("hub-outlier-", "conformance-hub-sampling"),
                ("pii-name-", "conformance-pii-name-sampling"),
                ("pii-value-", "conformance-pii-value-sampling"),
                ("orphan-", "conformance-orphan"),
            )
            if check_id.startswith(prefix)
        ),
        "unknown",
    )


def _representative_plans(client, suite_yaml, *, server, cypher):
    plans = {}
    compiler = CypherCompiler()
    for check in load_suite(suite_yaml).checks:
        family = _family(check.id)
        if family in plans:
            continue
        compiled = compiler.compile(check, sample_seed=0)
        plans[family] = {
            "query": compiled.query,
            "operators": walk_plan(client.explain_read(compiled.query, compiled.params)),
            "server": server,
            "cypher": cypher,
        }
    return plans


def _enforce_family_budgets(records, output, concurrency):
    budget_path = os.environ.get("GRAPHCHECK_PERFORMANCE_BUDGETS")
    if budget_path is None:
        return
    reference = os.environ.get("GRAPHCHECK_PERFORMANCE_GATE")
    if reference is None:
        raise ValueError(
            "GRAPHCHECK_PERFORMANCE_GATE must name the customer-scale reference environment"
        )
    budgets = load_reference_budgets(Path(budget_path), reference)
    results = [
        evaluate_record(
            record,
            budgets[record.benchmark],
            gate="customer-scale-family",
            details={"family": record.details["family"], "concurrency": concurrency},
        )
        for record in records
    ]
    write_gate_results(
        results,
        output.with_name(f"{output.stem}-family-gates{output.suffix}"),
    )
    assert_required_gates(results)
