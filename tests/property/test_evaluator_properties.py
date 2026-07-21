from __future__ import annotations

from dataclasses import replace

import yaml
from hypothesis import given
from hypothesis import strategies as st

from graphcheck.contracts.check import load_suite
from graphcheck.contracts.results import EvidenceElement
from graphcheck.engine.baseline import BaselineValue
from graphcheck.engine.compiler import compile_check
from graphcheck.engine.evaluator import evaluate_check
from graphcheck.errors import GraphCheckError


@st.composite
def shape_cases(draw):
    minimum = draw(st.integers(min_value=0, max_value=12))
    maximum = draw(st.integers(min_value=minimum, max_value=20))
    row_count = draw(st.integers(min_value=0, max_value=24))
    data = {
        "suite": "property-shape",
        "competency": [
            {
                "id": "shape",
                "question": "Are the result rows within bounds?",
                "query": "MATCH (n) RETURN id(n) AS node_element_id",
                "expect": {
                    "rows": {"min": minimum, "max": maximum},
                    "columns": ["node_element_id"],
                    "unique": True,
                },
            }
        ],
    }
    rows = [{"node_element_id": f"node-{index}"} for index in range(row_count)]
    return yaml.safe_dump(data), rows, minimum <= row_count <= maximum


@given(shape_cases())
def test_shape_evaluation_matches_all_declared_predicates(case):
    suite_yaml, rows, expected_pass = case
    check = load_suite(suite_yaml).checks[0]
    compiled = compile_check(check)

    if not rows and not expected_pass:
        try:
            evaluate_check(compiled, rows, columns=("node_element_id",))
        except GraphCheckError as exc:
            assert exc.error.code == "engine.evidence_missing"
            return
        raise AssertionError("a pointerless failure must be errored")

    result = evaluate_check(compiled, rows, columns=("node_element_id",))

    assert result.passed is expected_pass
    assert (result.evidence is None) is expected_pass
    if not expected_pass:
        assert result.evidence.elements


@given(shape_cases())
def test_same_yaml_and_graph_rows_produce_the_same_evaluation(case):
    suite_yaml, rows, _ = case
    first = compile_check(load_suite(suite_yaml).checks[0])
    second = compile_check(load_suite(suite_yaml).checks[0])

    def outcome(compiled):
        try:
            return evaluate_check(compiled, rows, columns=("node_element_id",))
        except GraphCheckError as exc:
            return exc.error

    first_result = outcome(first)
    second_result = outcome(second)

    assert first_result == second_result


@given(
    actual=st.lists(st.integers(min_value=0, max_value=20), max_size=12, unique=True),
    expected=st.lists(st.integers(min_value=0, max_value=20), max_size=12, unique=True),
)
def test_regression_equals_is_exact_and_deterministic(actual, expected):
    data = {
        "suite": "property-regression",
        "competency": [
            {
                "id": "regression",
                "question": "Do pinned ids still match?",
                "query": "MATCH (n) RETURN id(n) AS node_element_id",
                "expect": {"equals": expected},
            }
        ],
    }
    compiled = compile_check(load_suite(yaml.safe_dump(data)).checks[0])
    rows = [{"node_element_id": value} for value in actual]

    equals = sorted(actual) == sorted(expected)
    if actual or equals:
        result = evaluate_check(compiled, rows, columns=("node_element_id",))
        assert result.passed is equals
        if not result.passed:
            assert result.evidence is not None
            assert result.evidence.elements
    else:
        # Empty actual vs non-empty pinned values cannot identify a graph element. The accuracy
        # contract makes this an error instead of emitting a pointerless finding.
        try:
            evaluate_check(compiled, rows, columns=("node_element_id",))
        except GraphCheckError as exc:
            assert exc.error.code == "engine.evidence_missing"
        else:  # pragma: no cover - the assertion documents the total accuracy invariant
            raise AssertionError("a pointerless failure must be errored")


@given(actual=st.booleans())
def test_regression_never_treats_boolean_as_the_corresponding_integer(actual):
    expected = int(actual)
    data = {
        "suite": "property-regression-types",
        "competency": [
            {
                "id": "typed-regression",
                "question": "Does the pinned typed value match?",
                "query": "RETURN $value AS value, $node_element_id AS node_element_id",
                "params": {"value": actual, "node_element_id": "n-1"},
                "expect": {
                    "columns": ["value", "node_element_id"],
                    "equals": [{"value": expected, "node_element_id": "n-1"}],
                },
            }
        ],
    }
    compiled = compile_check(load_suite(yaml.safe_dump(data)).checks[0])

    result = evaluate_check(
        compiled,
        [{"value": actual, "node_element_id": "n-1"}],
        columns=("value", "node_element_id"),
    )

    assert result.passed is False
    assert result.evidence is not None


@given(
    values=st.lists(st.text(min_size=1, max_size=12), min_size=1, max_size=12, unique=True),
    index=st.integers(min_value=0, max_value=30),
)
def test_contains_passes_exactly_when_the_pinned_value_is_present(values, index):
    pinned = values[index % len(values)] if index < len(values) else f"missing-{index}"
    data = {
        "suite": "property-contains",
        "competency": [
            {
                "id": "contains",
                "question": "Is the pinned id present?",
                "query": "MATCH (n) RETURN id(n) AS node_element_id",
                "expect": {"contains": [pinned]},
            }
        ],
    }
    compiled = compile_check(load_suite(yaml.safe_dump(data)).checks[0])
    rows = [{"node_element_id": value} for value in values]

    result = evaluate_check(compiled, rows, columns=("node_element_id",))

    assert result.passed is (pinned in values)
    if not result.passed:
        assert result.evidence is not None
        assert result.evidence.elements


@st.composite
def completeness_cases(draw):
    population = draw(st.integers(min_value=0, max_value=100))
    conforming = draw(st.integers(min_value=0, max_value=population))
    threshold = draw(st.integers(min_value=1, max_value=100)) / 100
    violations = population - conforming
    coverage = 1.0 if population == 0 else conforming / population
    suite_yaml = yaml.safe_dump(
        {
            "suite": "property-conformance",
            "conformance": [
                {
                    "id": "complete",
                    "check": "completeness",
                    "with": {
                        "label": "Customer",
                        "property": "tax_id",
                        "threshold": threshold,
                    },
                }
            ],
        }
    )
    row = {
        "schema_ok": True,
        "missing_labels": [],
        "missing_relationship_types": [],
        "population": population,
        "conforming_count": conforming,
        "violation_count": violations,
        "coverage": coverage,
        "evidence": (
            [{"kind": "node", "id": "4:graph:violation", "labels": ["Customer"]}]
            if violations
            else []
        ),
    }
    return suite_yaml, row, coverage >= threshold


@given(completeness_cases())
def test_random_conformance_summaries_are_exact_and_deterministic(case):
    suite_yaml, row, expected_pass = case
    compiled = compile_check(load_suite(suite_yaml).checks[0])

    first = evaluate_check(compiled, [row])
    repeated = evaluate_check(compiled, [row])

    assert first == repeated
    assert first.passed is expected_pass
    assert (first.evidence is None) is expected_pass


@given(
    current=st.integers(min_value=0, max_value=10_000),
    previous=st.integers(min_value=0, max_value=10_000),
    tolerance=st.integers(min_value=0, max_value=1_000),
)
def test_random_drift_summaries_are_exact_and_deterministic(current, previous, tolerance):
    suite_yaml = yaml.safe_dump(
        {
            "suite": "property-drift",
            "drift": [
                {
                    "id": "node-count",
                    "metric": "node_count",
                    "target": {},
                    "baseline": "pinned",
                    "tolerance": {"max_delta": tolerance},
                }
            ],
        }
    )
    compiled = compile_check(load_suite(suite_yaml).checks[0])
    row = {
        "schema_ok": True,
        "missing_labels": [],
        "missing_relationship_types": [],
        "current": current,
        "population": current,
        "evidence": [{"kind": "node", "id": "4:graph:current"}] if current else [],
    }
    baseline = BaselineValue(
        value=previous,
        evidence=(EvidenceElement(kind="node", id="4:graph:baseline"),),
    )

    first = evaluate_check(compiled, [row], baseline=baseline)
    repeated = evaluate_check(compiled, [row], baseline=baseline)

    assert first == repeated
    assert first.passed is (abs(current - previous) <= tolerance)
    assert (first.evidence is None) is first.passed


@given(matches=st.lists(st.booleans(), max_size=30))
def test_random_pii_value_summaries_are_redacted_exact_and_deterministic(matches):
    suite_yaml = yaml.safe_dump(
        {
            "suite": "property-pii",
            "conformance": [
                {
                    "id": "cards",
                    "check": "pii_value_match",
                    "with": {"patterns": ["credit_card"], "properties": ["notes"]},
                }
            ],
        }
    )
    compiled = replace(
        compile_check(load_suite(suite_yaml).checks[0], sample_seed=73),
        sample_population=len(matches),
    )
    candidates = [
        {
            "evidence": {
                "kind": "node",
                "id": f"node-{index}",
                "labels": ["Payment"],
            },
            "property": "notes",
            "value": "4111 1111 1111 1111" if matched else "4111 1111 1111 1112",
        }
        for index, matched in enumerate(matches)
    ]
    row = {
        "schema_ok": True,
        "missing_labels": [],
        "missing_relationship_types": [],
        "missing_properties": [],
        "population": len(matches),
        "sample_size": len(matches),
        "candidates": candidates,
    }

    first = evaluate_check(compiled, [row])
    repeated = evaluate_check(compiled, [row])

    assert first == repeated
    assert first.passed is (not any(matches))
    assert first.measured["matches"] == sum(matches)
    assert first.estimate is False
    assert "4111 1111 1111 1111" not in repr(first)
    assert (first.evidence is None) is first.passed
