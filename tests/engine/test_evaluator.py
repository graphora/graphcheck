import copy
from dataclasses import replace

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from graphcheck.contracts.check import (
    CompetencyCheck,
    ConformanceCheck,
    DriftCheck,
    Expect,
    LoadedCheck,
)
from graphcheck.contracts.results import Pattern, Severity
from graphcheck.engine.baseline import BaselineValue
from graphcheck.engine.compiler import CypherCompiler
from graphcheck.engine.evaluator import _pointers_from_row, evaluate_check
from graphcheck.errors import GraphCheckError


def _loaded(spec, pattern: Pattern, *, severity: Severity = Severity.ERROR) -> LoadedCheck:
    return LoadedCheck(
        id=spec.id,
        pattern=pattern,
        severity=severity,
        tags=[],
        provenance=None,
        generated=False,
        spec=spec,
    )


def _completeness(
    *, threshold: float = 1.0, severity: Severity = Severity.ERROR, evidence_cap: int = 3
):
    spec = ConformanceCheck.model_validate(
        {
            "id": "complete",
            "check": "completeness",
            "with": {"label": "Customer", "property": "tax_id", "threshold": threshold},
        }
    )
    check = _loaded(spec, Pattern.CONFORMANCE, severity=severity)
    return CypherCompiler(evidence_cap=evidence_cap).compile(check)


def _competency(
    expect: dict[str, object],
    *,
    severity: Severity = Severity.ERROR,
    evidence_cap: int = 5,
    params: dict[str, object] | None = None,
):
    expectation = Expect.model_validate(expect)
    spec = CompetencyCheck(
        id="competency",
        question="Which records are returned?",
        query="RETURN $node_element_id AS node_element_id",
        params=params or {"node_element_id": "parameter-pointer"},
        expect=expectation,
    )
    pattern = (
        Pattern.COMPETENCY_REGRESSION
        if expectation.contains is not None or expectation.equals is not None
        else Pattern.COMPETENCY_SHAPE
    )
    return CypherCompiler(evidence_cap=evidence_cap).compile(
        _loaded(spec, pattern, severity=severity)
    )


def _drift(
    tolerance: dict[str, object],
    *,
    severity: Severity = Severity.ERROR,
    evidence_cap: int = 4,
):
    spec = DriftCheck(
        id="drift",
        metric="node_count",
        target={"label": "Customer"},
        baseline="release-42",
        tolerance=tolerance,
    )
    return CypherCompiler(evidence_cap=evidence_cap).compile(
        _loaded(spec, Pattern.DRIFT, severity=severity)
    )


def _completeness_row(**updates):
    row = {
        "schema_ok": True,
        "missing_labels": [],
        "missing_relationship_types": [],
        "coverage": 1.0,
        "population": 10,
        "conforming_count": 10,
        "violation_count": 0,
        "evidence": [],
    }
    row.update(updates)
    return row


def test_completeness_passes_at_threshold_and_returns_exact_measurements():
    compiled = _completeness(threshold=0.8)

    evaluation = evaluate_check(
        compiled,
        [
            _completeness_row(
                coverage=0.8,
                population=10,
                conforming_count=8,
                violation_count=2,
            )
        ],
    )

    assert evaluation.passed is True
    assert evaluation.measured == {
        "coverage": 0.8,
        "population": 10,
        "conforming": 8,
        "violations": 2,
    }
    assert evaluation.evidence is None
    assert evaluation.estimate is False


def test_completeness_failure_deduplicates_and_caps_node_and_relationship_pointers():
    compiled = _completeness(threshold=1.0, evidence_cap=2)
    pointers = [
        {"kind": "node", "id": "4:graph:1", "labels": ["Customer"]},
        {"kind": "node", "id": "4:graph:1", "labels": ["Customer"]},
        {"kind": "rel", "id": "5:graph:2", "type": "CONTROLS"},
        {"kind": "node", "id": "4:graph:3", "labels": ["Customer"]},
    ]

    evaluation = evaluate_check(
        compiled,
        [
            _completeness_row(
                coverage=0.5,
                population=10,
                conforming_count=5,
                violation_count=5,
                evidence=pointers,
            )
        ],
    )

    assert evaluation.passed is False
    assert evaluation.evidence is not None
    assert [(item.kind, item.id) for item in evaluation.evidence.elements] == [
        ("node", "4:graph:1"),
        ("rel", "5:graph:2"),
    ]
    assert evaluation.evidence.cap == 2
    assert evaluation.evidence.total_count == 5
    assert evaluation.evidence.truncated is True


def test_raw_neo4j_entities_are_preserved_as_evidence_pointers():
    from neo4j.graph import Graph, Node

    graph = Graph()
    node = Node(graph, "4:graph:1", 1, ["Customer"], {})
    relationship_type = graph.relationship_type("CONTROLS")
    relationship = relationship_type(graph, "5:graph:2", 2, {})

    evaluation = evaluate_check(
        _completeness(threshold=1.0),
        [
            _completeness_row(
                coverage=0.8,
                population=10,
                conforming_count=8,
                violation_count=2,
                evidence=[node, relationship],
            )
        ],
    )

    assert evaluation.evidence is not None
    assert evaluation.evidence.elements[0].model_dump() == {
        "kind": "node",
        "id": "4:graph:1",
        "labels": ["Customer"],
        "type": None,
    }
    assert evaluation.evidence.elements[1].model_dump() == {
        "kind": "rel",
        "id": "5:graph:2",
        "labels": None,
        "type": "CONTROLS",
    }


def test_nested_result_maps_and_path_like_values_preserve_graph_pointers():
    from neo4j.graph import Graph, Node

    node = Node(Graph(), "4:graph:nested", 7, ["Customer"], {})

    class PathLike:
        nodes = (node,)
        relationships = ()

    pointers = _pointers_from_row({"payload": {"path": PathLike(), "nodes": [node]}})

    assert [(pointer.kind, pointer.id) for pointer in pointers] == [
        ("node", "4:graph:nested"),
        ("node", "4:graph:nested"),
    ]


def test_failing_conformance_without_a_pointer_is_an_error_not_a_finding():
    compiled = _completeness(threshold=1.0)

    with pytest.raises(GraphCheckError) as caught:
        evaluate_check(
            compiled,
            [
                _completeness_row(
                    coverage=0.9,
                    population=10,
                    conforming_count=9,
                    violation_count=1,
                    evidence=[],
                )
            ],
        )

    assert caught.value.error.code == "engine.evidence_missing"


def test_domain_property_ids_are_not_fabricated_into_element_pointers():
    compiled = replace(_competency({"contains": ["A-2"]}), params={})

    with pytest.raises(GraphCheckError) as caught:
        evaluate_check(compiled, [{"account_id": "A-1"}], columns=["account_id"])

    assert caught.value.error.code == "engine.evidence_missing"


def test_missing_schema_reference_is_an_error_never_an_empty_pass():
    compiled = _completeness()

    with pytest.raises(GraphCheckError) as caught:
        evaluate_check(
            compiled,
            [
                {
                    "schema_ok": False,
                    "missing_labels": ["CustomerTypo"],
                    "missing_relationship_types": [],
                }
            ],
        )

    assert caught.value.error.code == "engine.schema_reference_missing"
    assert "CustomerTypo" in caught.value.error.message


def test_compiled_summary_must_explicitly_report_schema_status():
    row = _completeness_row()
    row.pop("schema_ok")

    with pytest.raises(GraphCheckError) as caught:
        evaluate_check(_completeness(), [row])

    assert caught.value.error.code == "engine.invalid_query_result"


def test_internally_inconsistent_completeness_summary_is_an_error():
    with pytest.raises(GraphCheckError) as caught:
        evaluate_check(
            _completeness(threshold=1.0),
            [
                _completeness_row(
                    coverage=1.0,
                    population=10,
                    conforming_count=10,
                    violation_count=5,
                )
            ],
        )

    assert caught.value.error.code == "engine.invalid_query_result"


@pytest.mark.parametrize(
    "rows",
    [
        [],
        [_completeness_row(), _completeness_row()],
        [
            {
                "schema_ok": True,
                "population": 1,
                "conforming_count": 1,
                "violation_count": 0,
                "evidence": [],
            }
        ],
        [_completeness_row(coverage=float("nan"))],
    ],
)
def test_broken_conformance_summary_is_an_error(rows):
    with pytest.raises(GraphCheckError) as caught:
        evaluate_check(_completeness(), rows)

    assert caught.value.error.code == "engine.invalid_query_result"


def test_competency_regression_overlay_applies_every_assertion_together():
    compiled = _competency(
        {
            "rows": {"min": 1, "max": 3, "exactly": 2},
            "columns": ["account_id"],
            "unique": True,
            "contains": ["A-1"],
            "equals": ["A-1", "A-2"],
            "empty": False,
        }
    )

    evaluation = evaluate_check(
        compiled,
        [{"account_id": "A-1"}, {"account_id": "A-2"}],
        columns=["account_id"],
    )

    assert evaluation.passed is True
    assert evaluation.measured == {
        "rows": 2,
        "columns": ["account_id"],
        "unique": True,
        "empty": False,
        "contains": True,
        "equals": True,
    }
    assert evaluation.evidence is None


@pytest.mark.parametrize(
    ("expect", "rows", "columns", "message"),
    [
        ({"rows": {"min": 2}}, [{"node_element_id": "n-1"}], ["node_element_id"], "below min"),
        (
            {"rows": {"max": 1}},
            [{"node_element_id": "n-1"}, {"node_element_id": "n-2"}],
            ["node_element_id"],
            "exceeds max",
        ),
        (
            {"rows": {"exactly": 2}},
            [{"node_element_id": "n-1"}],
            ["node_element_id"],
            "does not equal",
        ),
        (
            {"columns": ["wanted"]},
            [{"node_element_id": "n-1"}],
            ["node_element_id"],
            "do not equal expected",
        ),
        (
            {"unique": True},
            [{"node_element_id": "n-1"}, {"node_element_id": "n-1"}],
            ["node_element_id"],
            "not unique",
        ),
        ({"empty": True}, [{"node_element_id": "n-1"}], ["node_element_id"], "empty is not True"),
        ({"empty": False}, [], ["node_element_id"], "empty is not False"),
        (
            {"contains": ["wanted"]},
            [{"node_element_id": "n-1", "value": "other"}],
            ["value"],
            "does not contain",
        ),
        (
            {"equals": ["wanted"]},
            [{"node_element_id": "n-1", "value": "other"}],
            ["value"],
            "does not equal the pinned values",
        ),
    ],
)
def test_each_competency_assertion_fails_with_pointer_evidence(expect, rows, columns, message):
    evaluation = evaluate_check(_competency(expect), rows, columns=columns)

    assert evaluation.passed is False
    assert evaluation.evidence is not None
    assert evaluation.evidence.elements
    assert message in evaluation.evidence.message


def test_zero_row_column_validation_uses_result_metadata_not_first_row_inference():
    compiled = _competency(
        {
            "rows": {"exactly": 0},
            "columns": ["account_id"],
            "unique": True,
            "empty": True,
        }
    )

    evaluation = evaluate_check(compiled, [], columns=["account_id"])

    assert evaluation.passed is True
    assert evaluation.measured["columns"] == ["account_id"]


def test_uniqueness_treats_mapping_insertion_order_as_irrelevant():
    compiled = _competency({"unique": True})
    first = {"node_element_id": "n-1", "value": 7}
    reordered = {"value": 7, "node_element_id": "n-1"}

    evaluation = evaluate_check(
        compiled,
        [first, reordered],
        columns=["node_element_id", "value"],
    )

    assert evaluation.passed is False
    assert evaluation.measured["unique"] is False
    assert evaluation.evidence is not None
    assert evaluation.evidence.elements[0].id == "n-1"


def test_unique_false_requires_at_least_one_duplicate_row():
    compiled = _competency({"unique": False})

    passing = evaluate_check(
        compiled,
        [{"node_element_id": "n-1"}, {"node_element_id": "n-1"}],
        columns=["node_element_id"],
    )
    failing = evaluate_check(
        compiled,
        [{"node_element_id": "n-1"}, {"node_element_id": "n-2"}],
        columns=["node_element_id"],
    )

    assert passing.passed is True
    assert failing.passed is False
    assert "rows are unique" in failing.evidence.message


def test_multi_column_regression_compares_pinned_row_mappings():
    pinned = {"customer": "C-1", "account": "A-1"}
    compiled = _competency(
        {
            "contains": [pinned],
            "equals": [pinned],
            "columns": ["customer", "account"],
        }
    )

    evaluation = evaluate_check(
        compiled,
        [{"customer": "C-1", "account": "A-1"}],
        columns=["customer", "account"],
    )

    assert evaluation.passed is True
    assert evaluation.measured["contains"] is True
    assert evaluation.measured["equals"] is True


def test_regression_equals_is_order_independent_but_preserves_duplicates():
    compiled = _competency({"equals": ["A-1", "A-2"]})

    reordered = evaluate_check(
        compiled,
        [{"node_element_id": "A-2"}, {"node_element_id": "A-1"}],
        columns=["node_element_id"],
    )
    wrong_multiplicity = evaluate_check(
        compiled,
        [{"node_element_id": "A-1"}, {"node_element_id": "A-1"}],
        columns=["node_element_id"],
    )

    assert reordered.passed is True
    assert wrong_multiplicity.passed is False


@pytest.mark.parametrize(
    ("tolerance", "current", "baseline", "passed"),
    [
        ({"max_drop_pct": 10}, 90, 100, True),
        ({"max_drop_pct": 10}, 89, 100, False),
        ({"max_increase_pct": 10}, 110, 100, True),
        ({"max_increase_pct": 10}, 111, 100, False),
        ({"max_change_pct": 10}, 89, 100, False),
        ({"max_delta": 5}, 106, 100, False),
        ({"absolute": 5}, 94, 100, False),
        ({"min": 90}, 89, 100, False),
        ({"max": 110}, 111, 100, False),
    ],
)
def test_every_supported_drift_tolerance(tolerance, current, baseline, passed):
    compiled = _drift(tolerance)
    row = {
        "schema_ok": True,
        "missing_labels": [],
        "missing_relationship_types": [],
        "current": current,
        "population": current,
        "evidence": [{"kind": "node", "id": "4:graph:1", "labels": ["Customer"]}],
    }

    evaluation = evaluate_check(compiled, [row], baseline=BaselineValue(baseline))

    assert evaluation.passed is passed
    assert evaluation.measured["current"] == float(current)
    assert evaluation.measured["baseline"] == baseline
    assert (evaluation.evidence is None) is passed


def test_drift_percentage_from_zero_baseline_fails_with_pointer_evidence():
    evaluation = evaluate_check(
        _drift({"max_increase_pct": 1}),
        [
            {
                "schema_ok": True,
                "missing_labels": [],
                "missing_relationship_types": [],
                "current": 1,
                "population": 1,
                "evidence": [{"kind": "node", "id": "4:graph:1"}],
            }
        ],
        baseline=BaselineValue(0),
    )

    assert evaluation.passed is False
    assert evaluation.evidence is not None
    assert "inf%" in evaluation.evidence.message


def test_drift_requires_a_baseline_and_a_well_formed_summary():
    compiled = _drift({"max_delta": 1})
    good_row = {
        "schema_ok": True,
        "missing_labels": [],
        "missing_relationship_types": [],
        "current": 100,
        "population": 100,
        "evidence": [],
    }
    with pytest.raises(GraphCheckError) as no_baseline:
        evaluate_check(compiled, [good_row])
    assert no_baseline.value.error.code == "engine.baseline_missing"

    with pytest.raises(GraphCheckError) as bad_summary:
        evaluate_check(
            compiled,
            [{"schema_ok": True, "population": 1, "evidence": []}],
            baseline=BaselineValue(1),
        )
    assert bad_summary.value.error.code == "engine.invalid_query_result"


@pytest.mark.parametrize(("current", "previous"), [(101.0, 90.0), (90.0, 101.0), (-1.0, 90.0)])
def test_property_coverage_drift_requires_c4_percent_units(current, previous):
    spec = DriftCheck(
        id="coverage",
        metric="property_coverage",
        target={"label": "Customer", "property": "tax_id"},
        baseline="release-42",
        tolerance={"max_delta": 5},
    )
    compiled = CypherCompiler().compile(_loaded(spec, Pattern.DRIFT))
    row = {
        "schema_ok": True,
        "missing_labels": [],
        "missing_relationship_types": [],
        "current": current,
        "population": 10,
        "evidence": [{"kind": "node", "id": "4:graph:1"}],
    }

    with pytest.raises(GraphCheckError) as caught:
        evaluate_check(compiled, [row], baseline=BaselineValue(previous))

    assert caught.value.error.code == "engine.invalid_query_result"
    assert "percent units" in caught.value.error.message


def test_evaluation_is_independent_of_check_severity():
    rows = [{"node_element_id": "4:graph:1"}, {"node_element_id": "4:graph:1"}]
    hard = evaluate_check(
        _competency({"unique": True}, severity=Severity.ERROR),
        rows,
        columns=["node_element_id"],
    )
    soft = evaluate_check(
        _competency({"unique": True}, severity=Severity.WARN),
        rows,
        columns=["node_element_id"],
    )

    assert hard == soft
    assert hard.passed is False


@settings(max_examples=50, deadline=None)
@given(st.lists(st.integers(min_value=-20, max_value=20), max_size=15))
def test_evaluation_is_deterministic_and_does_not_mutate_rows(values):
    compiled = _competency({"unique": True}, evidence_cap=100)
    rows = [{"value": value, "node_element_id": f"4:graph:{value}"} for value in values]
    original = copy.deepcopy(rows)

    first = evaluate_check(compiled, rows, columns=["value", "node_element_id"])
    repeated = evaluate_check(compiled, rows, columns=["value", "node_element_id"])

    assert first == repeated
    assert rows == original
