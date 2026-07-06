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

    spec = ConformanceCheck(id="x", check="completeness", with_={"label": "C", "property": "p"})
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
