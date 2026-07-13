import re

import pytest

from graphcheck.contracts.check import (
    CompetencyCheck,
    ConformanceCheck,
    DriftCheck,
    Expect,
    LoadedCheck,
)
from graphcheck.contracts.results import Pattern, Severity
from graphcheck.engine.compiler import CypherCompiler
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


def _completeness(config: dict[str, object]) -> LoadedCheck:
    spec = ConformanceCheck.model_validate(
        {"id": "complete", "check": "completeness", "with": config}
    )
    return _loaded(spec, Pattern.CONFORMANCE)


def _competency(
    query: str,
    *,
    params: dict[str, object] | None = None,
    expect: dict[str, object] | None = None,
) -> LoadedCheck:
    expectation = Expect.model_validate(expect or {"rows": {"min": 1}})
    spec = CompetencyCheck(
        id="competency",
        question="Which records are returned?",
        query=query,
        params=params or {},
        expect=expectation,
    )
    pattern = (
        Pattern.COMPETENCY_REGRESSION
        if expectation.contains is not None or expectation.equals is not None
        else Pattern.COMPETENCY_SHAPE
    )
    return _loaded(spec, pattern)


def _drift(metric: str, target: dict[str, object]) -> LoadedCheck:
    spec = DriftCheck(
        id=f"drift-{metric}",
        metric=metric,
        target=target,
        baseline="release-42",
        tolerance={"max_change_pct": 5},
    )
    return _loaded(spec, Pattern.DRIFT)


def test_completeness_compiler_parameterizes_dynamic_schema_tokens_and_evidence_cap():
    label = "Customer`) DELETE n //"
    property_name = "tax_id}]"

    compiled = CypherCompiler(evidence_cap=7).compile(
        _completeness({"label": label, "property": property_name, "threshold": 0.875})
    )

    assert label not in compiled.query
    assert property_name not in compiled.query
    assert "$label" in compiled.query
    assert "n[$property]" in compiled.query
    assert "$evidence_cap" in compiled.query
    assert compiled.params == {
        "label": label,
        "property": property_name,
        "threshold": 0.875,
        "evidence_cap": 7,
        "required_labels": [label],
        "required_relationship_types": [],
    }
    assert compiled.expected == {"threshold": 0.875}
    assert "schema_ok" in compiled.query
    assert "violation_count" in compiled.query
    assert "coverage" in compiled.query
    assert "evidence" in compiled.query
    assert not re.search(r"\b(CREATE|MERGE|DELETE|SET|REMOVE|DROP)\b", compiled.query)


@pytest.mark.parametrize("threshold", [-0.01, 1.01])
def test_completeness_compiler_rejects_out_of_range_threshold(threshold):
    check = _completeness({"label": "Customer", "property": "tax_id", "threshold": threshold})

    with pytest.raises(GraphCheckError) as caught:
        CypherCompiler().compile(check)

    assert caught.value.error.code == "engine.invalid_check"


@pytest.mark.parametrize(
    "config",
    [
        {"label": "", "property": "tax_id", "threshold": 1.0},
        {"label": "Customer", "property": "   ", "threshold": 1.0},
    ],
)
def test_completeness_compiler_rejects_blank_identifiers(config):
    with pytest.raises(GraphCheckError) as caught:
        CypherCompiler().compile(_completeness(config))

    assert caught.value.error.code == "engine.invalid_check"


def test_competency_compiler_preserves_query_and_ignores_fake_parameters_in_lexical_regions():
    query = """
    MATCH (c:Customer {id: $customer_id})
    WITH c, '$quoted' AS literal, "$double_quoted" AS another
    // $line_comment
    /* $block_comment */
    RETURN c.id AS `$backtick_identifier`
    """
    value = "CUST-1042-sensitive-value"

    compiled = CypherCompiler().compile(_competency(query, params={"customer_id": value}))

    assert compiled.query == query.strip()
    assert compiled.params == {"customer_id": value}
    assert value not in compiled.query
    assert compiled.name == "Which records are returned"


def test_competency_compiler_reports_every_missing_parameter_in_stable_order():
    check = _competency(
        "RETURN $zeta AS z, $alpha AS a, '$ignored' AS literal",
        params={},
    )

    with pytest.raises(GraphCheckError) as caught:
        CypherCompiler().compile(check)

    assert caught.value.error.code == "engine.parameter_missing"
    assert "$alpha, $zeta" in caught.value.error.message


@pytest.mark.parametrize(
    ("metric", "target", "bindings", "placeholders"),
    [
        (
            "node_count",
            {"label": "Customer"},
            {
                "label": "Customer",
                "required_labels": ["Customer"],
                "required_relationship_types": [],
            },
            ("$label",),
        ),
        (
            "relationship_count",
            {"type": "CONTROLS"},
            {
                "relationship_type": "CONTROLS",
                "required_labels": [],
                "required_relationship_types": ["CONTROLS"],
            },
            ("$relationship_type",),
        ),
        (
            "property_coverage",
            {"label": "Customer", "property": "tax_id"},
            {
                "label": "Customer",
                "property": "tax_id",
                "required_labels": ["Customer"],
                "required_relationship_types": [],
            },
            ("$label", "$property"),
        ),
        (
            "property_coverage",
            {"type": "CONTROLS", "property": "since"},
            {
                "relationship_type": "CONTROLS",
                "property": "since",
                "required_labels": [],
                "required_relationship_types": ["CONTROLS"],
            },
            ("$relationship_type", "$property"),
        ),
    ],
)
def test_drift_compilers_emit_parameterized_schema_checked_queries(
    metric, target, bindings, placeholders
):
    compiled = CypherCompiler(evidence_cap=9).compile(_drift(metric, target))

    for value in target.values():
        assert value not in compiled.query
    for placeholder in placeholders:
        assert placeholder in compiled.query
    for key, value in bindings.items():
        assert compiled.params[key] == value
    assert compiled.params["evidence_cap"] == 9
    assert compiled.expected == {
        "baseline": "release-42",
        "tolerance": {"max_change_pct": 5},
    }
    assert "schema_ok" in compiled.query
    assert "missing_labels" in compiled.query
    assert "missing_relationship_types" in compiled.query
    assert "current" in compiled.query
    assert "evidence" in compiled.query
    assert not re.search(r"\b(CREATE|MERGE|DELETE|SET|REMOVE|DROP)\b", compiled.query)


def test_drift_compiler_rejects_unknown_target_keys_and_metrics():
    with pytest.raises(GraphCheckError) as bad_target:
        CypherCompiler().compile(_drift("node_count", {"label": "Customer", "bogus": 1}))
    assert bad_target.value.error.code == "engine.invalid_target"

    with pytest.raises(GraphCheckError) as unsupported:
        CypherCompiler().compile(_drift("degree_quantile", {"label": "Customer"}))
    assert unsupported.value.error.code == "engine.metric_unsupported"


@pytest.mark.parametrize(
    "check",
    [
        _completeness({"label": "Customer", "property": "tax_id", "threshold": 0.9}),
        _competency("RETURN $customer_id AS id", params={"customer_id": "C-1"}),
        _drift("node_count", {"label": "Customer"}),
    ],
)
def test_compilation_is_deterministic(check):
    compiler = CypherCompiler(evidence_cap=11)

    first = compiler.compile(check, sample_seed=1234)
    repeated = compiler.compile(check, sample_seed=1234)

    assert first.query == repeated.query
    assert first.params == repeated.params
    assert first.expected == repeated.expected
    assert first.name == repeated.name
    assert first.sampled == repeated.sampled


@pytest.mark.parametrize("invalid", [0, -1, True, 1.5, "10"])
def test_compiler_rejects_non_positive_integer_evidence_caps(invalid):
    with pytest.raises(ValueError, match="positive integer"):
        CypherCompiler(evidence_cap=invalid)
