import json
from pathlib import Path

import jsonschema
import pytest
import yaml
from pydantic import ValidationError

from graphcheck.contracts.check import load_suite
from graphcheck.contracts.results import Pattern
from graphcheck.contracts.schemas import (
    SPECS_DIR,
    check_combined_schema,
    pack_metadata_schema,
    validate_pack_metadata_schema,
)
from graphcheck.packs import PACK_REQUIREMENTS, REGISTRY
from graphcheck.packs.metadata import (
    CORE_CHECK_NAMES,
    CorePackMetadata,
    PiiPackMetadata,
    load_pack_metadata_yaml,
)
from graphcheck.yaml_loader import DuplicateKeyError, load_yaml_mapping

PACKS = Path(__file__).resolve().parents[1] / "src" / "graphcheck" / "packs"

CORE_CHECKS = {
    "completeness": {"label": "Customer", "property": "tax_id"},
    "cardinality": {"from_label": "Customer", "rel_type": "OWNS", "to_label": "Account"},
    "no_orphans": {"label": "Account", "rel_type": "OWNS"},
    "dangling_rels": {"rel_type": "OWNS"},
    "property_type": {"label": "Customer", "property": "age", "type": "integer"},
    "property_format": {"label": "Customer", "property": "tax_id", "regex": "^\\d{9}$"},
    "value_in_set": {"label": "Customer", "property": "status", "values": ["active"]},
    "uniqueness": {"label": "Customer", "property": "customer_id"},
    "hub_outlier": {"label": "Customer", "rel_type": "TRANSFERS_TO"},
    "label_cooccurrence": {"label_a": "Person", "label_b": "Company"},
    "rel_direction": {"from_label": "Customer", "rel_type": "OWNS", "to_label": "Account"},
    "temporal_sanity": {
        "label": "Employment",
        "start_property": "start_at",
        "end_property": "end_at",
    },
}


def _load_pack(name: str) -> dict:
    return load_yaml_mapping(
        (PACKS / name).read_text(encoding="utf-8"),
        description="pack metadata",
    )


def _load_core_metadata() -> CorePackMetadata:
    metadata = load_pack_metadata_yaml((PACKS / "core.yml").read_text(encoding="utf-8"))
    assert isinstance(metadata, CorePackMetadata)
    return metadata


def _load_pii_metadata() -> PiiPackMetadata:
    metadata = load_pack_metadata_yaml((PACKS / "pii.yml").read_text(encoding="utf-8"))
    assert isinstance(metadata, PiiPackMetadata)
    return metadata


def _validate_pack_json_schema(raw: dict) -> None:
    validate_pack_metadata_schema(raw)


def _assert_rejected_by_typed_and_json_schema(raw: dict, model) -> None:
    with pytest.raises(ValidationError):
        model.model_validate(raw)
    with pytest.raises(jsonschema.ValidationError):
        _validate_pack_json_schema(raw)


@pytest.mark.parametrize(
    ("filename", "model"),
    [
        ("core.yml", CorePackMetadata),
        ("pii.yml", PiiPackMetadata),
    ],
)
def test_pack_metadata_files_validate_against_typed_and_json_schema_contract(filename, model):
    raw = _load_pack(filename)

    model.model_validate(raw)
    _validate_pack_json_schema(raw)


@pytest.mark.parametrize(
    ("original", "duplicate"),
    [
        pytest.param(
            "    email:\n      keys: [email, email_address, e_mail]\n",
            "    email:\n      keys: [alternate_email]\n",
            id="name-match-pattern-id",
        ),
        pytest.param(
            '    email:\n      regex: "^[^@\\\\s]+@[^@\\\\s]+\\\\.[^@\\\\s]+$"\n',
            '    email:\n      regex: "^alternate$"\n',
            id="value-match-pattern-id",
        ),
    ],
)
def test_pack_yaml_loader_rejects_duplicate_pattern_ids(original, duplicate):
    text = (PACKS / "pii.yml").read_text(encoding="utf-8")
    assert original in text

    with pytest.raises(DuplicateKeyError):
        load_pack_metadata_yaml(text.replace(original, original + duplicate, 1))


@pytest.mark.parametrize("check", CORE_CHECK_NAMES)
def test_each_core_template_mapping_has_typed_and_json_schema_parity(check):
    raw = _load_pack("core.yml")
    wrong_template = "cardinality" if check != "cardinality" else "completeness"
    raw["checks"][check]["template"] = wrong_template

    _assert_rejected_by_typed_and_json_schema(raw, CorePackMetadata)


@pytest.mark.parametrize(
    ("filename", "model", "path", "invalid_value"),
    [
        pytest.param(
            "core.yml",
            CorePackMetadata,
            ("checks", "completeness", "sampled"),
            0,
            id="unsampled-zero",
        ),
        pytest.param(
            "core.yml",
            CorePackMetadata,
            ("checks", "hub_outlier", "sampled"),
            1,
            id="sampled-one",
        ),
        pytest.param(
            "core.yml",
            CorePackMetadata,
            ("checks", "hub_outlier", "estimate", "required_when_sampled"),
            0,
            id="required-when-sampled-zero",
        ),
        pytest.param(
            "core.yml",
            CorePackMetadata,
            ("checks", "hub_outlier", "estimate", "required_when_sampled"),
            1,
            id="required-when-sampled-one",
        ),
        pytest.param(
            "pii.yml",
            PiiPackMetadata,
            ("value_match", "sample_required"),
            0,
            id="sample-required-zero",
        ),
        pytest.param(
            "pii.yml",
            PiiPackMetadata,
            ("value_match", "sample_required"),
            1,
            id="sample-required-one",
        ),
    ],
)
def test_boolean_literals_reject_integer_coercion_with_typed_and_schema_parity(
    filename, model, path, invalid_value
):
    raw = _load_pack(filename)
    target = raw
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = invalid_value

    _assert_rejected_by_typed_and_json_schema(raw, model)


@pytest.mark.parametrize(
    ("filename", "model", "path", "invalid_value"),
    [
        pytest.param(
            "core.yml",
            CorePackMetadata,
            ("checks", "completeness", "requires"),
            ["read", "read"],
            id="duplicate-capability",
        ),
        pytest.param(
            "core.yml",
            CorePackMetadata,
            ("checks", "completeness", "evidence", "elements"),
            ["node", "node"],
            id="duplicate-evidence-elements",
        ),
        pytest.param(
            "core.yml",
            CorePackMetadata,
            ("checks", "completeness", "evidence", "id_fields"),
            ["node_id", "node_id"],
            id="duplicate-evidence-id-fields",
        ),
        pytest.param(
            "pii.yml",
            PiiPackMetadata,
            ("name_match", "patterns", "email", "keys"),
            ["email", "email"],
            id="duplicate-name-match-keys",
        ),
        pytest.param(
            "pii.yml",
            PiiPackMetadata,
            ("value_match", "report", "fields"),
            ["location", "confidence", "confidence"],
            id="invalid-report-field-combination",
        ),
        pytest.param(
            "pii.yml",
            PiiPackMetadata,
            ("name_match", "patterns"),
            [
                {"id": "email", "keys": ["email"]},
                {"id": "email", "keys": ["e_mail"]},
            ],
            id="duplicate-name-match-pattern-id",
        ),
        pytest.param(
            "pii.yml",
            PiiPackMetadata,
            ("value_match", "patterns"),
            [
                {"id": "email", "regex": "^a$"},
                {"id": "email", "regex": "^b$"},
            ],
            id="duplicate-value-match-pattern-id",
        ),
        pytest.param(
            "pii.yml",
            PiiPackMetadata,
            ("name_match", "patterns"),
            {" ": {"keys": ["email"]}},
            id="blank-name-match-pattern-id",
        ),
        pytest.param(
            "pii.yml",
            PiiPackMetadata,
            ("value_match", "patterns"),
            {" ": {"regex": "^a$"}},
            id="blank-value-match-pattern-id",
        ),
        pytest.param(
            "pii.yml",
            PiiPackMetadata,
            ("value_match", "patterns"),
            {"broken": {"regex": "["}},
            id="invalid-pii-regex",
        ),
    ],
)
def test_shared_pack_invariants_have_typed_and_json_schema_parity(
    filename, model, path, invalid_value
):
    raw = _load_pack(filename)
    target = raw
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = invalid_value

    _assert_rejected_by_typed_and_json_schema(raw, model)


def test_pack_metadata_contract_rejects_unknown_fields_at_every_level():
    core = _load_pack("core.yml")
    core["checks"]["completeness"]["bogus"] = True
    pii = _load_pack("pii.yml")
    pii["value_match"]["report"]["bogus"] = True

    with pytest.raises(ValidationError):
        CorePackMetadata.model_validate(core)
    with pytest.raises(jsonschema.ValidationError):
        _validate_pack_json_schema(core)
    with pytest.raises(ValidationError):
        PiiPackMetadata.model_validate(pii)
    with pytest.raises(jsonschema.ValidationError):
        _validate_pack_json_schema(pii)


def test_core_metadata_contract_rejects_unknown_capability_values():
    core = _load_pack("core.yml")
    core["checks"]["completeness"]["requires"] = ["write"]

    with pytest.raises(ValidationError):
        CorePackMetadata.model_validate(core)
    with pytest.raises(jsonschema.ValidationError):
        _validate_pack_json_schema(core)


@pytest.mark.parametrize("capability", ["read", "show_procedures", "apoc", "count_store"])
def test_core_metadata_contract_accepts_runtime_capability_values(capability):
    core = _load_pack("core.yml")
    core["checks"]["completeness"]["requires"] = [capability]

    CorePackMetadata.model_validate(core)
    _validate_pack_json_schema(core)


def test_core_metadata_contract_requires_estimate_when_sampled():
    core = _load_pack("core.yml")
    del core["checks"]["hub_outlier"]["estimate"]

    with pytest.raises(ValidationError):
        CorePackMetadata.model_validate(core)
    with pytest.raises(jsonschema.ValidationError):
        _validate_pack_json_schema(core)


def test_core_metadata_contract_rejects_estimate_when_not_sampled():
    core = _load_pack("core.yml")
    core["checks"]["completeness"]["estimate"] = {"required_when_sampled": True}

    with pytest.raises(ValidationError):
        CorePackMetadata.model_validate(core)
    with pytest.raises(jsonschema.ValidationError):
        _validate_pack_json_schema(core)


def test_pack_metadata_contract_rejects_missing_and_invalid_pii_fields():
    missing = _load_pack("pii.yml")
    del missing["value_match"]["report"]
    invalid = _load_pack("pii.yml")
    invalid["name_match"]["confidence"] = "high"

    with pytest.raises(ValidationError):
        PiiPackMetadata.model_validate(missing)
    with pytest.raises(jsonschema.ValidationError):
        _validate_pack_json_schema(missing)
    with pytest.raises(ValidationError):
        PiiPackMetadata.model_validate(invalid)
    with pytest.raises(jsonschema.ValidationError):
        _validate_pack_json_schema(invalid)


def test_committed_pack_metadata_schema_is_current():
    committed = json.loads((SPECS_DIR / "pack.schema.json").read_text())

    assert committed == pack_metadata_schema()


def test_pack_metadata_schema_uses_standard_draft_without_custom_validation_keywords():
    schema = pack_metadata_schema()

    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert "x-unique-by" not in json.dumps(schema)


def test_all_core_conformance_checks_are_registered():
    assert set(REGISTRY) == set(CORE_CHECKS) == set(CORE_CHECK_NAMES)


def test_core_check_with_models_accept_representative_configs():
    for check, payload in CORE_CHECKS.items():
        normalized = REGISTRY[check].model_validate(payload).model_dump()

        assert payload.items() <= normalized.items()


def test_core_check_with_models_reject_unknown_keys():
    for check, payload in CORE_CHECKS.items():
        with_unknown = {**payload, "bogus": True}

        try:
            REGISTRY[check].model_validate(with_unknown)
        except ValidationError:
            continue
        raise AssertionError(f"{check} accepted an unknown key")


def test_load_suite_accepts_all_core_conformance_checks():
    items = []
    for check, payload in CORE_CHECKS.items():
        rendered = yaml.safe_dump(payload, default_flow_style=True).strip()
        items.append(f"  - id: {check}\n    check: {check}\n    with: {rendered}\n")
    suite = load_suite("suite: core\nconformance:\n" + "".join(items))

    assert len(suite.checks) == len(CORE_CHECKS)
    assert {check.id for check in suite.checks} == set(CORE_CHECKS)
    assert all(check.pattern is Pattern.CONFORMANCE for check in suite.checks)


def test_core_check_with_models_reject_invalid_enum_values():
    invalid = {
        **CORE_CHECKS["property_type"],
        "type": "object",
    }

    try:
        REGISTRY["property_type"].model_validate(invalid)
    except ValidationError:
        return
    raise AssertionError("property_type accepted an invalid type")


def test_core_check_with_models_reject_coerced_values():
    invalid_threshold = {
        **CORE_CHECKS["completeness"],
        "threshold": "0.5",
    }
    invalid_exactly = {
        **CORE_CHECKS["cardinality"],
        "exactly": True,
    }

    with pytest.raises(ValidationError):
        REGISTRY["completeness"].model_validate(invalid_threshold)
    with pytest.raises(ValidationError):
        REGISTRY["cardinality"].model_validate(invalid_exactly)


def test_property_format_rejects_invalid_regex():
    invalid = {
        **CORE_CHECKS["property_format"],
        "regex": "[",
    }

    with pytest.raises(ValidationError):
        REGISTRY["property_format"].model_validate(invalid)


@pytest.mark.parametrize(
    ("check", "payload"),
    [
        ("completeness", {"label": "", "property": "tax_id"}),
        ("completeness", {"label": "Customer", "property": "   "}),
        ("cardinality", {"from_label": "Customer", "rel_type": "", "to_label": "Account"}),
        ("no_orphans", {"label": "Account", "rel_type": "   "}),
        ("dangling_rels", {"rel_type": ""}),
        ("property_format", {"label": "Customer", "property": "tax_id", "regex": ""}),
    ],
)
def test_core_check_with_models_reject_blank_identifier_fields(check, payload):
    with pytest.raises(ValidationError):
        REGISTRY[check].model_validate(payload)


def test_whitespace_only_identifier_is_rejected_by_loader_and_generated_schema():
    raw = {
        "suite": "identifier-parity",
        "conformance": [
            {
                "id": "blank-label",
                "check": "completeness",
                "with": {"label": "   ", "property": "tax_id"},
            }
        ],
    }

    with pytest.raises(ValidationError):
        load_suite(yaml.safe_dump(raw))
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(raw, check_combined_schema())


@pytest.mark.parametrize(
    ("check", "payload"),
    [
        (
            "completeness",
            {"label": "Customer", "property": "tax_id", "threshold": 0.0},
        ),
        (
            "label_cooccurrence",
            {"label_a": "Person", "label_b": "Person"},
        ),
        (
            "rel_direction",
            {"from_label": "Account", "rel_type": "OWNS", "to_label": "Account"},
        ),
        (
            "temporal_sanity",
            {"label": "Employment", "start_property": "timestamp", "end_property": "timestamp"},
        ),
    ],
)
def test_core_check_with_models_reject_semantically_unusable_configs(check, payload):
    with pytest.raises(ValidationError):
        REGISTRY[check].model_validate(payload)


def test_core_pack_metadata_matches_registry_and_declares_evidence():
    core = _load_core_metadata()

    assert {check for check, _ in core.checks.items()} == set(REGISTRY)
    for check, metadata in core.checks.items():
        assert metadata.catches
        assert metadata.does_not_catch
        assert metadata.template == check
        assert tuple(metadata.requires) == PACK_REQUIREMENTS[check]
        assert metadata.evidence.elements
        assert metadata.evidence.id_fields


def test_sampled_core_checks_declare_estimate_contract():
    core = _load_core_metadata()

    sampled = {check: metadata for check, metadata in core.checks.items() if metadata.sampled}

    assert set(sampled) == {"hub_outlier"}
    assert sampled["hub_outlier"].estimate.required_when_sampled is True


def test_pii_pack_is_separate_and_declares_heuristic_limits():
    pii = _load_pii_metadata()

    assert pii.pack == "pii"
    assert "never claims complete PII discovery" in pii.completeness_notice
    assert pii.name_match.confidence == "name-match"
    assert pii.value_match.confidence == "value-match"


def test_pii_name_match_has_expected_pattern_coverage():
    pii = _load_pii_metadata()
    patterns = pii.name_match.patterns

    assert len(patterns) >= 15
    for required in {"ssn", "dob", "email", "phone", "nric", "aadhaar", "address", "passport"}:
        assert required in patterns
        assert patterns[required].keys


def test_pii_value_match_declares_required_patterns_and_checksums():
    pii = _load_pii_metadata()
    patterns = pii.value_match.patterns

    assert patterns["email"].regex
    assert patterns["e164_phone"].regex
    assert patterns["nric"].regex
    assert patterns["aadhaar"].checksum == "verhoeff"
    assert patterns["credit_card"].checksum == "luhn"


def test_combined_schema_contains_all_core_checks():
    branches = check_combined_schema()["$defs"]["WithByCheck"]["oneOf"]

    assert {branch["properties"]["check"]["const"] for branch in branches} == set(CORE_CHECKS)
