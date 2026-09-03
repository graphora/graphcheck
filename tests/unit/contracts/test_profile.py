import json
from pathlib import Path

import jsonschema
import pytest
from pydantic import ValidationError

from graphcheck.contracts.profile import (
    BaselineProfile,
    DegreeDistribution,
    GraphSchema,
    ProfileStatistics,
    ProfileStatus,
    profile_fingerprint,
)
from graphcheck.contracts.schemas import SCHEMAS_DIR, profile_schema

FIXTURES = Path(__file__).parent / "fixtures"


def _baseline(**over):
    data = json.loads((FIXTURES / "baseline.json").read_text())
    data.update(over)
    return data


def _validate(raw):
    return BaselineProfile.model_validate_json(json.dumps(raw))


def _refresh_fingerprint(raw):
    raw["fingerprint"] = profile_fingerprint(
        GraphSchema.model_validate(raw["schema"]),
        ProfileStatistics.model_validate(raw["statistics"]),
    )


def _assert_duplicate_rejected(raw):
    _refresh_fingerprint(raw)
    with pytest.raises(ValidationError, match="unique values"):
        _validate(raw)


def test_baseline_fixture_validates_against_schema_and_round_trips():
    raw = _baseline()
    jsonschema.validate(raw, profile_schema())
    model = _validate(raw)
    assert json.loads(model.model_dump_json(by_alias=True)) == raw


def test_complete_baseline_requires_null_partial_reason():
    with pytest.raises(ValidationError):
        _validate(_baseline(partial_reason="timed out"))


def test_complete_baseline_requires_degree_distribution():
    raw = _baseline()
    raw["schema"]["labels"][0]["degree_distribution"] = None

    with pytest.raises(ValidationError):
        _validate(raw)


def test_partial_baseline_requires_non_empty_partial_reason():
    with pytest.raises(ValidationError):
        _validate(_baseline(status=ProfileStatus.PARTIAL))


def test_partial_baseline_allows_null_degree_distribution():
    raw = _baseline(status=ProfileStatus.PARTIAL, partial_reason="degree probe timed out")
    raw["schema"]["labels"][0]["degree_distribution"] = None
    _refresh_fingerprint(raw)

    model = _validate(raw)

    assert model.status is ProfileStatus.PARTIAL


def test_counts_must_be_non_negative():
    raw = _baseline()
    raw["schema"]["labels"][0]["count"] = -1

    with pytest.raises(ValidationError):
        _validate(raw)


def test_label_count_cannot_exceed_node_count():
    raw = _baseline()
    raw["schema"]["labels"][0]["count"] = 14
    _refresh_fingerprint(raw)

    with pytest.raises(
        ValidationError,
        match=(
            r"schema\.labels\['Account'\]\.count \(14\) "
            r"exceeds statistics\.node_count \(13\)"
        ),
    ):
        _validate(raw)


def test_complete_relationship_type_count_cannot_exceed_relationship_count():
    raw = _baseline()
    raw["schema"]["relationship_types"][0]["count"] = 8
    _refresh_fingerprint(raw)

    with pytest.raises(
        ValidationError,
        match=(
            r"schema\.relationship_types\['CONTROLS'\]\.count \(8\) "
            r"exceeds statistics\.relationship_count \(7\)"
        ),
    ):
        _validate(raw)


def test_label_count_sum_may_exceed_node_count():
    raw = _baseline()
    raw["statistics"]["node_count"] = 10
    _refresh_fingerprint(raw)

    model = _validate(raw)

    assert sum(label.count for label in model.graph_schema.labels) > model.statistics.node_count


def test_partial_relationship_type_count_may_exceed_relationship_count():
    raw = _baseline(status=ProfileStatus.PARTIAL, partial_reason="relationship probe incomplete")
    raw["schema"]["relationship_types"][0]["count"] = 8
    _refresh_fingerprint(raw)

    model = _validate(raw)

    assert model.graph_schema.relationship_types[0].count > model.statistics.relationship_count


def test_coverage_must_be_percentage():
    raw = _baseline()
    raw["statistics"]["property_coverage"][0]["coverage"] = 101

    with pytest.raises(ValidationError):
        _validate(raw)


def test_collections_must_be_canonically_sorted():
    raw = _baseline()
    raw["schema"]["labels"] = list(reversed(raw["schema"]["labels"]))
    _refresh_fingerprint(raw)

    with pytest.raises(ValidationError, match="canonically sorted"):
        _validate(raw)


def test_constraint_and_index_nested_arrays_must_be_canonically_sorted():
    raw = _baseline()
    raw["schema"]["constraints"][0]["labels_or_types"] = ["Person", "Customer"]
    _refresh_fingerprint(raw)

    with pytest.raises(ValidationError, match="labels_or_types"):
        _validate(raw)

    raw = _baseline()
    raw["schema"]["indexes"][0]["properties"] = ["name", "id"]
    _refresh_fingerprint(raw)

    with pytest.raises(ValidationError, match="properties"):
        _validate(raw)


def test_duplicate_labels_are_rejected():
    raw = _baseline()
    raw["schema"]["labels"].insert(1, raw["schema"]["labels"][0])
    _assert_duplicate_rejected(raw)


def test_duplicate_properties_within_label_are_rejected():
    raw = _baseline()
    properties = raw["schema"]["labels"][0]["properties"]
    properties.append(properties[0])
    _assert_duplicate_rejected(raw)


def test_duplicate_relationship_types_are_rejected():
    raw = _baseline()
    relationship_types = raw["schema"]["relationship_types"]
    relationship_types.append(relationship_types[0])
    _assert_duplicate_rejected(raw)


def test_duplicate_constraints_are_rejected():
    raw = _baseline()
    constraints = raw["schema"]["constraints"]
    constraints.append(constraints[0])
    _assert_duplicate_rejected(raw)


def test_duplicate_indexes_are_rejected():
    raw = _baseline()
    indexes = raw["schema"]["indexes"]
    indexes.append(indexes[0])
    _assert_duplicate_rejected(raw)


def test_duplicate_constraint_labels_or_types_are_rejected():
    raw = _baseline()
    labels_or_types = raw["schema"]["constraints"][0]["labels_or_types"]
    labels_or_types.append(labels_or_types[0])
    _assert_duplicate_rejected(raw)


def test_duplicate_constraint_properties_are_rejected():
    raw = _baseline()
    properties = raw["schema"]["constraints"][0]["properties"]
    properties.append(properties[0])
    _assert_duplicate_rejected(raw)


def test_duplicate_index_labels_or_types_are_rejected():
    raw = _baseline()
    labels_or_types = raw["schema"]["indexes"][0]["labels_or_types"]
    labels_or_types.append(labels_or_types[0])
    _assert_duplicate_rejected(raw)


def test_duplicate_index_properties_are_rejected():
    raw = _baseline()
    properties = raw["schema"]["indexes"][0]["properties"]
    properties.append(properties[0])
    _assert_duplicate_rejected(raw)


def test_duplicate_property_coverage_identities_are_rejected():
    raw = _baseline()
    property_coverage = raw["statistics"]["property_coverage"]
    property_coverage.insert(1, property_coverage[0])
    _assert_duplicate_rejected(raw)


def test_degree_distribution_percentiles_must_be_ordered():
    with pytest.raises(ValidationError, match="median <= p95 <= p99 <= maximum"):
        DegreeDistribution.model_validate({"median": 10, "p95": 2, "p99": 1, "maximum": 0})


def test_fingerprint_must_match_v0_hash_input():
    raw = _baseline(fingerprint="sha256:abc")

    with pytest.raises(ValidationError):
        _validate(raw)


def test_fingerprint_uses_labels_relationship_types_and_core_counts():
    raw = _baseline()
    expected = profile_fingerprint(
        GraphSchema.model_validate(raw["schema"]),
        ProfileStatistics.model_validate(raw["statistics"]),
    )

    assert _validate(raw).fingerprint == expected

    changed = _baseline()
    changed["statistics"]["node_count"] += 1
    assert (
        profile_fingerprint(
            GraphSchema.model_validate(changed["schema"]),
            ProfileStatistics.model_validate(changed["statistics"]),
        )
        != expected
    )


def test_fingerprint_excludes_metadata_constraints_and_indexes_for_v0():
    raw = _baseline()
    expected = profile_fingerprint(
        GraphSchema.model_validate(raw["schema"]),
        ProfileStatistics.model_validate(raw["statistics"]),
    )
    raw["metadata"]["generated_at"] = "2026-07-08T00:00:00Z"
    raw["schema"]["constraints"][0]["type"] = "NODE_KEY"
    raw["schema"]["indexes"][0]["type"] = "TEXT"

    assert (
        profile_fingerprint(
            GraphSchema.model_validate(raw["schema"]),
            ProfileStatistics.model_validate(raw["statistics"]),
        )
        == expected
    )


def test_unknown_keys_are_rejected():
    with pytest.raises(ValidationError):
        _validate(_baseline(bogus=True))


def test_committed_profile_schema_is_current():
    committed = json.loads((SCHEMAS_DIR / "profile.schema.json").read_text())
    assert committed == profile_schema()


def test_scalar_types_are_strict():
    raw = _baseline()
    raw["statistics"]["node_count"] = "13"

    with pytest.raises(ValidationError):
        _validate(raw)
