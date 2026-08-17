from __future__ import annotations

from collections.abc import Iterable

import pytest

from graphcheck.contracts.results import Capabilities, ResultsTarget, Verdict
from graphcheck.engine.runner import Engine, EngineConfig
from graphcheck.errors import GraphCheckError
from graphcheck.neo4j_adapter import QueryResult
from tests.performance.helpers import measure_allocations

pytestmark = pytest.mark.performance
TARGET = ResultsTarget(
    database="neo4j",
    server_version="5.26.28",
    edition="community",
    fingerprint="sha256:performance-memory-gate",
    capabilities=Capabilities(apoc=False, count_store=True),
    labels=[],
    relationship_types=[],
)


class LazyGateClient:
    def __init__(self, rows: Iterable[dict[str, object]]) -> None:
        self.rows = iter(rows)
        self.yielded = 0
        self.retained_peak = 0

    def run_read(self, query, params):
        raise AssertionError("the bounded competency path must be used")

    def run_read_result(self, query, params, *, timeout_s=None):
        raise AssertionError("the bounded competency path must be used")

    def run_read_result_bounded(self, query, params, *, policy, timeout_s=None, stop_when=None):
        retained = []
        for row in self.rows:
            self.yielded += 1
            if policy.max_rows is not None and len(retained) >= policy.max_rows:
                raise GraphCheckError(
                    "engine.result_limit_exceeded", "result limit exceeded", "narrow the query"
                )
            retained.append(row)
            self.retained_peak = max(self.retained_peak, len(retained))
            if stop_when is not None and stop_when(row):
                return QueryResult(
                    retained,
                    ("node_element_id",),
                    (),
                    complete=False,
                    observed_rows=self.yielded,
                    limit=policy.max_rows,
                )
        return QueryResult(
            retained,
            ("node_element_id",),
            (),
            complete=True,
            observed_rows=self.yielded,
            limit=policy.max_rows,
        )


def _rows(count: int):
    return ({"node_element_id": f"n-{index}"} for index in range(count))


def _run(client: LazyGateClient, expectation: str, *, limit: int = 1000):
    suite = f"""\
suite: memory-regression-gate
competency:
  - id: bounded
    question: Is lazy consumption bounded?
    query: MATCH (n) RETURN elementId(n) AS node_element_id
    expect: {{{expectation}}}
"""
    return Engine(
        client,
        config=EngineConfig(evidence_cap=2, result_row_limit=limit),
    ).run_yaml(suite, target=TARGET)


def test_decisive_assertion_has_bounded_retention_and_allocation():
    client = LazyGateClient(_rows(1_000_000))

    results, allocation = measure_allocations(lambda: _run(client, "rows: {min: 3}"))

    assert results.checks[0].verdict is Verdict.PASS
    assert client.yielded == 3
    assert client.retained_peak == 3
    assert allocation.peak_bytes <= 4_000_000


def test_failure_evidence_never_exceeds_cap():
    client = LazyGateClient(_rows(1_000_000))

    check = _run(client, "rows: {max: 2}").checks[0]

    assert check.verdict is Verdict.FAIL
    assert client.yielded == 3
    assert client.retained_peak == 3
    assert check.evidence is not None
    assert len(check.evidence.elements) == 2
    assert check.evidence.cap == 2
    assert check.evidence.truncated is True


def test_full_result_assertion_stops_at_safety_ceiling():
    client = LazyGateClient(_rows(1_000_000))

    check = _run(client, "equals: [n-0]", limit=2).checks[0]

    assert check.verdict is Verdict.ERRORED
    assert check.error is not None
    assert check.error.code == "engine.result_limit_exceeded"
    assert client.yielded == 3
    assert client.retained_peak == 2
