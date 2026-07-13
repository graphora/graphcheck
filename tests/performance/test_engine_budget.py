from __future__ import annotations

import os
import time

import pytest
import yaml

from graphcheck.connection_profiles import ConnectionProfile
from graphcheck.engine.runner import Engine, EngineConfig
from graphcheck.neo4j_adapter import Neo4jClient

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


def test_thirty_check_run_on_ten_million_nodes_finishes_inside_five_minutes():
    profile = ConnectionProfile(
        uri=URI,
        user=os.environ.get("GRAPHCHECK_PERFORMANCE_USER", "neo4j"),
        password=PASSWORD,
        database=os.environ.get("GRAPHCHECK_PERFORMANCE_DATABASE", "neo4j"),
    )
    client = Neo4jClient(profile)
    try:
        node_count = client.run_read("MATCH (n) RETURN count(n) AS count")[0]["count"]
        relationship_count = client.run_read("MATCH ()-[r]->() RETURN count(r) AS count")[0][
            "count"
        ]
        assert node_count >= 10_000_000, "performance target must contain at least 10M nodes"
        suite = {
            "suite": "ten-million-budget",
            "competency": [
                {
                    "id": f"count-store-{index:02d}",
                    "question": f"Can count-store query {index:02d} answer?",
                    "query": "MATCH (n) RETURN count(n) AS total",
                    "expect": {
                        "rows": {"exactly": 1},
                        "columns": ["total"],
                        "unique": True,
                    },
                }
                for index in range(10)
            ]
            + [
                {
                    "id": f"property-scan-{index:02d}",
                    "question": f"Can full property scan {index:02d} answer?",
                    "query": "MATCH (n) RETURN sum(size(keys(n))) AS property_slots",
                    "expect": {
                        "rows": {"exactly": 1},
                        "columns": ["property_slots"],
                        "unique": True,
                    },
                }
                for index in range(10)
            ],
            "drift": [
                {
                    "id": f"node-count-{index:02d}",
                    "metric": "node_count",
                    "target": {},
                    "baseline": "performance",
                    "tolerance": {"max_delta": 0},
                }
                for index in range(5)
            ]
            + [
                {
                    "id": f"relationship-count-{index:02d}",
                    "metric": "relationship_count",
                    "target": {},
                    "baseline": "performance",
                    "tolerance": {"max_delta": 0},
                }
                for index in range(5)
            ],
        }

        started = time.monotonic()
        results = Engine(
            client,
            config=EngineConfig(),
            baselines={
                "performance": {
                    "node_count": node_count,
                    "relationship_count": relationship_count,
                }
            },
        ).run_yaml(yaml.safe_dump(suite))
        elapsed = time.monotonic() - started
    finally:
        client.close()

    assert elapsed < 300
    assert results.totals.checks == 30
    assert results.totals.passed == 30
    assert results.run.partial_reason is None
