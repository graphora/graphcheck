import json
from pathlib import Path

import jsonschema
import pytest
from pydantic import ValidationError

from graphcheck.contracts.check import (
    DuplicateKeyError,
    UnknownCheckError,
    load_suite,
    load_suite_yaml,
)
from graphcheck.contracts.results import Pattern, Severity
from graphcheck.contracts.schemas import (
    SPECS_DIR,
    check_combined_schema,
    check_envelope_schema,
)
from graphcheck.packs import PACK_VERSION, REGISTRY

FIX = Path(__file__).parent / "fixtures"


def _load(name):
    return load_suite((FIX / name).read_text())


# --- pack registry ---


def test_completeness_registered():
    assert "completeness" in REGISTRY
    ok = REGISTRY["completeness"].model_validate(
        {"label": "Customer", "property": "tax_id", "threshold": 1.0}
    )
    assert ok.threshold == 1.0


def test_with_forbids_unknown_keys():
    with pytest.raises(ValidationError):
        REGISTRY["completeness"].model_validate({"label": "C", "property": "p", "bogus": 1})


def test_pack_version_is_a_string():
    assert isinstance(PACK_VERSION, str)


# --- duplicate-key loader ---


def test_duplicate_keys_raise():
    with pytest.raises(DuplicateKeyError):
        load_suite_yaml("suite: s\nsuite: t\n")


def test_normal_yaml_loads():
    assert load_suite_yaml("suite: s\n") == {"suite": "s"}


# --- envelope, defaults, generated precedence, with validation ---


def test_valid_suite_loads_and_resolves_defaults():
    suite = _load("suite.valid.yml")
    by_id = {c.id: c for c in suite.checks}
    assert by_id["cust-tax-id-present"].severity is Severity.ERROR  # from defaults
    assert set(by_id["cust-tax-id-present"].tags) == {"production", "pii", "kyc"}  # union
    assert by_id["cq-001"].pattern is Pattern.COMPETENCY_SHAPE
    assert by_id["cq-001-regression"].pattern is Pattern.COMPETENCY_REGRESSION


def test_unknown_key_errors():
    with pytest.raises(ValidationError):
        _load("suite.invalid-unknown-key.yml")


def test_unknown_check_errors():
    with pytest.raises(UnknownCheckError):
        _load("suite.invalid-unknown-check.yml")


def test_duplicate_key_errors():
    with pytest.raises(DuplicateKeyError):
        _load("suite.invalid-duplicate-key.yml")


def test_unknown_expect_key_errors():
    with pytest.raises(ValidationError):
        _load("suite.invalid-bad-expect.yml")


def test_generated_file_marker_dominates_children():
    # File-level generated:true forces every check generated; a child cannot override it.
    # (The engine, not the loader, later turns generated into a skipped CheckResult.)
    text = (
        "suite: s\ngenerated: true\nconformance:\n  - id: x\n    check: completeness\n"
        "    with: {label: C, property: p}\n    generated: false\n"
    )
    suite = load_suite(text)
    assert suite.checks[0].generated is True


def test_suite_name_defaults_to_source_stem():
    suite = load_suite("conformance: []\n", source="checks/customer-360.yml")
    assert suite.suite == "customer-360"


def test_suite_name_required_without_key_or_source():
    with pytest.raises(ValueError):
        load_suite("conformance: []\n")


def test_loaded_check_forbids_unknown_keys():
    from graphcheck.contracts.check import ConformanceCheck, LoadedCheck

    spec = ConformanceCheck(
        id="x", check="completeness", **{"with": {"label": "C", "property": "p"}}
    )
    with pytest.raises(ValidationError):
        LoadedCheck(
            id="x",
            pattern=Pattern.CONFORMANCE,
            severity=Severity.ERROR,
            tags=[],
            generated=False,
            spec=spec,
            bogus=1,
        )


def test_conformance_requires_with():
    with pytest.raises(ValidationError):
        load_suite("suite: s\nconformance:\n  - id: x\n    check: completeness\n")


def test_with_underscore_key_rejected():
    # The frozen key is `with`; `with_` (the internal field name) must not be accepted.
    with pytest.raises(ValidationError):
        load_suite(
            "suite: s\nconformance:\n  - id: x\n    check: completeness\n"
            "    with_: {label: C, property: p}\n"
        )


def test_conformance_with_defaults_are_normalized_onto_spec():
    suite = load_suite(
        "suite: s\nconformance:\n  - id: x\n    check: completeness\n"
        "    with: {label: C, property: p}\n"
    )
    assert suite.checks[0].spec.with_["threshold"] == 1.0  # pack default filled, not lost


def test_duplicate_check_id_in_suite_rejected():
    text = (
        "suite: s\ncompetency:\n"
        "  - id: dup\n    question: q\n    query: RETURN 1\n    expect: {unique: true}\n"
        "  - id: dup\n    question: q\n    query: RETURN 1\n    expect: {unique: true}\n"
    )
    with pytest.raises(ValueError):
        load_suite(text)


def _competency_suite(expect: str, *, question: str = "q", query: str = "RETURN 1 AS n") -> str:
    return (
        "suite: s\ncompetency:\n"
        f"  - id: x\n    question: {question!r}\n    query: {query!r}\n"
        f"    expect: {expect}\n"
    )


@pytest.mark.parametrize(
    "rows",
    [
        "{}",
        "{min: -1}",
        "{max: -1}",
        "{exactly: -1}",
        "{min: 2, max: 1}",
        "{min: 2, exactly: 1}",
        "{max: 1, exactly: 2}",
    ],
)
def test_row_bounds_reject_empty_negative_or_inconsistent_values(rows):
    with pytest.raises(ValidationError):
        load_suite(_competency_suite(f"{{rows: {rows}}}"))


def test_row_bounds_allow_consistent_exactly_overlay():
    suite = load_suite(_competency_suite("{rows: {min: 1, max: 3, exactly: 2}}"))

    bounds = suite.checks[0].spec.expect.rows
    assert bounds is not None
    assert (bounds.min, bounds.max, bounds.exactly) == (1, 3, 2)


@pytest.mark.parametrize("expect", ["{}", "{contains: []}"])
def test_expect_rejects_assertion_free_or_vacuous_payloads(expect):
    with pytest.raises(ValidationError):
        load_suite(_competency_suite(expect))


@pytest.mark.parametrize(
    "expect",
    [
        "{empty: true, rows: {min: 1}}",
        "{empty: false, rows: {max: 0}}",
        "{empty: true, contains: [1]}",
        "{empty: false, equals: []}",
        "{rows: {min: 2}, equals: [1]}",
        "{rows: {max: 1}, equals: [1, 2]}",
        "{contains: [2], equals: [1]}",
        "{contains: [1], rows: {max: 0}}",
    ],
)
def test_expect_rejects_obvious_cross_assertion_contradictions(expect):
    with pytest.raises(ValidationError):
        load_suite(_competency_suite(expect))


def test_regression_overlay_allows_shape_contains_and_equals_together():
    suite = load_suite(
        _competency_suite(
            "{rows: {min: 1, max: 2}, columns: [n], unique: true, contains: [1], equals: [1]}"
        )
    )

    loaded = suite.checks[0]
    assert loaded.pattern is Pattern.COMPETENCY_REGRESSION
    assert loaded.spec.expect.contains == [1]
    assert loaded.spec.expect.equals == [1]


@pytest.mark.parametrize(
    ("question", "query"),
    [("''", "'RETURN 1'"), ("'   '", "'RETURN 1'"), ("'q'", "''"), ("'q'", "'  '")],
)
def test_competency_rejects_blank_question_or_query(question, query):
    text = (
        "suite: s\ncompetency:\n"
        f"  - id: x\n    question: {question}\n    query: {query}\n"
        "    expect: {empty: false}\n"
    )

    with pytest.raises(ValidationError):
        load_suite(text)


def test_competency_params_require_string_keys():
    text = (
        "suite: s\ncompetency:\n"
        "  - id: x\n    question: q\n    query: RETURN $p AS n\n"
        "    params: {1: value}\n    expect: {empty: false}\n"
    )

    with pytest.raises(ValidationError):
        load_suite(text)


@pytest.mark.parametrize(
    "fragment",
    [
        "metric: ''\n    target: {label: Customer}\n    tolerance: {max_drop_pct: 10}",
        "metric: '   '\n    target: {label: Customer}\n    tolerance: {max_drop_pct: 10}",
        "metric: node_count\n    baseline: ''\n    target: {label: Customer}\n"
        "    tolerance: {max_drop_pct: 10}",
        "metric: node_count\n    baseline: '  '\n    target: {label: Customer}\n"
        "    tolerance: {max_drop_pct: 10}",
        "metric: node_count\n    target: {label: Customer}\n    tolerance: {}",
    ],
)
def test_drift_rejects_blank_names_or_empty_tolerance(fragment):
    text = f"suite: s\ndrift:\n  - id: x\n    {fragment}\n"

    with pytest.raises(ValidationError):
        load_suite(text)


def test_drift_keeps_target_and_tolerance_vocabularies_open():
    text = (
        "suite: s\ndrift:\n  - id: x\n    metric: custom_metric\n"
        "    baseline: custom-baseline\n    target: {custom_target: value}\n"
        "    tolerance: {custom_tolerance: 3}\n"
    )

    suite = load_suite(text)

    drift = suite.checks[0].spec
    assert drift.target == {"custom_target": "value"}
    assert drift.tolerance == {"custom_tolerance": 3}


def test_drift_allows_empty_target_for_graph_wide_metrics():
    suite = load_suite(
        "suite: s\ndrift:\n  - id: x\n    metric: node_count\n"
        "    target: {}\n    tolerance: {max_drop_pct: 10}\n"
    )

    assert suite.checks[0].spec.target == {}


@pytest.mark.parametrize(
    "fragment",
    [
        "target: {1: Customer}\n    tolerance: {max_drop_pct: 10}",
        "target: {label: Customer}\n    tolerance: {1: 10}",
    ],
)
def test_drift_mapping_keys_must_be_strings(fragment):
    text = f"suite: s\ndrift:\n  - id: x\n    metric: node_count\n    {fragment}\n"

    with pytest.raises(ValidationError):
        load_suite(text)


# --- schema generation ---


def test_envelope_schema_exposes_with_not_with_():
    props = check_envelope_schema()["$defs"]["ConformanceCheck"]["properties"]
    assert "with" in props and "with_" not in props


def test_envelope_schema_requires_with():
    conf = check_envelope_schema()["$defs"]["ConformanceCheck"]
    assert "with" in conf["required"]


def test_combined_schema_is_pack_versioned():
    assert check_combined_schema()["x-pack-version"] == PACK_VERSION


def test_combined_schema_validates_good_and_rejects_bad_with():
    schema = check_combined_schema()
    good = {
        "suite": "s",
        "conformance": [
            {"id": "x", "check": "completeness", "with": {"label": "C", "property": "p"}}
        ],
    }
    jsonschema.validate(good, schema)  # must not raise
    bad = {
        "suite": "s",
        "conformance": [
            {
                "id": "x",
                "check": "completeness",
                "with": {"label": "C", "property": "p", "bogus": 1},
            }
        ],
    }
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, schema)


def test_combined_schema_requires_with():
    schema = check_combined_schema()
    missing = {"suite": "s", "conformance": [{"id": "x", "check": "completeness"}]}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(missing, schema)


def test_valid_suite_fixture_validates_against_combined_schema():
    raw = load_suite_yaml((FIX / "suite.valid.yml").read_text())
    jsonschema.validate(raw, check_combined_schema())  # must not raise


def test_committed_check_schemas_are_current():
    envelope = json.loads((SPECS_DIR / "check.envelope.schema.json").read_text())
    combined = json.loads((SPECS_DIR / "check.schema.json").read_text())
    assert envelope == check_envelope_schema()
    assert combined == check_combined_schema()  # regenerate + recommit if this fails


def test_pack_with_models_are_ref_free():
    for name, model in REGISTRY.items():
        assert "$defs" not in model.model_json_schema(), f"{name} pack model must be flat for v0"
