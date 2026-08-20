from __future__ import annotations

import json

import pytest

from tests.performance.gates import (
    assert_plan_gate,
    assert_required_gates,
    evaluate_record,
)
from tests.performance.helpers import BenchmarkRecord


def test_timing_failure_is_machine_readable_and_names_the_regressing_family():
    record = BenchmarkRecord.from_samples("conformance-hub-sampling", [121, 125, 130])
    result = evaluate_record(
        record,
        {
            "mode": "required",
            "reference_environment": "customer-10m-concurrency-2",
            "baseline_median_ms": 100,
            "median_regression_pct": 20,
            "observation_runs": 5,
        },
        gate="customer-scale-family",
        details={"family": "conformance-hub-sampling", "concurrency": 2},
    )

    with pytest.raises(AssertionError) as caught:
        assert_required_gates([result])

    payload = json.loads(str(caught.value))
    failure = payload["performance_regressions"][0]
    assert failure["benchmark"] == "conformance-hub-sampling"
    assert failure["observed"] == {
        "samples": 3,
        "median_ms": 125.0,
        "p95_ms": 130.0,
        "maximum_ms": 130.0,
    }
    assert failure["metadata"]["family"] == "conformance-hub-sampling"


def test_report_only_regression_keeps_measurement_without_failing():
    result = evaluate_record(
        BenchmarkRecord.from_samples("unstable", [200]),
        {
            "mode": "report-only",
            "reference_environment": "observation",
            "baseline_median_ms": 100,
            "median_regression_pct": 20,
        },
    )

    assert result["passed"] is False
    assert_required_gates([result])


def test_plan_failure_contains_query_tree_and_version_dimensions():
    with pytest.raises(AssertionError) as caught:
        assert_plan_gate(
            name="typed-relationship",
            query="MATCH ()-[r:`PURCHASED`]->() RETURN r",
            operators=[{"operator": "AllRelationshipsScan", "arguments": {}}],
            server="2026.06.0",
            cypher="25",
            required_any={"DirectedRelationshipTypeScan"},
            forbidden={"AllRelationshipsScan"},
        )

    failure = json.loads(str(caught.value))["performance_regressions"][0]
    assert failure["query"].startswith("MATCH")
    assert failure["operators"][0]["operator"] == "AllRelationshipsScan"
    assert failure["server"] == "2026.06.0"
    assert failure["cypher"] == "25"
