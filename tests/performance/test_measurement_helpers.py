from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

from graphcheck.neo4j_adapter import QueryResult
from tests.performance.helpers import (
    BenchmarkRecord,
    LazyHighCardinalityResult,
    measure_allocations,
    measure_query,
    validate_record,
    walk_plan,
    write_records,
)


@dataclass
class ObjectPlan:
    operator_type: str
    arguments: dict[str, object]
    children: list[object]


def test_benchmark_record_round_trips_as_valid_json(tmp_path):
    record = BenchmarkRecord.from_samples(
        "unit-baseline",
        [3, 1, 2, 8, 5, 4, 6, 7, 10, 9],
        details={"warmups": 1, "unit": "milliseconds"},
    )

    path = write_records([record], tmp_path / "records.json")
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert len(payload) == 1
    validate_record(payload[0])
    assert payload[0]["samples"] == 10
    assert payload[0]["median_ms"] == 5.5
    assert payload[0]["p95_ms"] == 10
    assert payload[0]["maximum_ms"] == 10


def test_record_validation_rejects_missing_fields_and_invalid_units():
    with pytest.raises(ValueError, match="missing fields"):
        validate_record({"benchmark": "incomplete"})
    record = BenchmarkRecord.from_samples("bad-units", [1]).as_dict()
    record["median_ms"] = -1
    with pytest.raises(ValueError, match="finite non-negative"):
        validate_record(record)


def test_plan_walker_accepts_mixed_mapping_and_object_layouts():
    plan = {
        "operatorType": "ProduceResults",
        "arguments": {"EstimatedRows": 1, "Ignored": "private"},
        "children": [
            ObjectPlan(
                "NodeCountFromCountStore",
                {"Details": "count( (:Customer) )", "Rows": 1},
                [],
            )
        ],
    }

    operators = walk_plan(plan)

    assert [item["operator"] for item in operators] == [
        "ProduceResults",
        "NodeCountFromCountStore",
    ]
    assert operators[0]["arguments"] == {"EstimatedRows": 1}
    assert operators[1]["arguments"] == {
        "Details": "count( (:Customer) )",
        "Rows": 1,
    }


def test_lazy_high_cardinality_result_and_allocation_helper_do_not_prebuild_rows():
    result, allocation = measure_allocations(
        lambda: LazyHighCardinalityResult(1_000_000, payload_bytes=32)
    )

    assert result.yielded == 0
    assert [next(result.rows)["index"] for _ in range(3)] == [0, 1, 2]
    assert result.yielded == 3
    assert allocation.retained_bytes >= 0
    assert allocation.peak_bytes >= allocation.retained_bytes


def test_query_timing_keeps_client_wall_and_server_timings_separate():
    result, timing = measure_query(
        lambda: QueryResult(
            [{"value": 1}],
            ("value",),
            (),
            server_available_after_ms=4,
            server_consumed_after_ms=7,
        )
    )

    assert result.rows == [{"value": 1}]
    assert timing.client_wall_ms >= 0
    assert timing.server_available_after_ms == 4
    assert timing.server_consumed_after_ms == 7
