import json
from pathlib import Path

import jsonschema
import pytest
from pydantic import ValidationError

from graphcheck.contracts.profile import (
    BaselineProfile,
    GraphSchema,
    ProfileStatistics,
    ProfileStatus,
    profile_fingerprint,
)
from graphcheck.contracts.schemas import SPECS_DIR, profile_schema

FIXTURES = Path(__file__).parent / "fixtures"


def _baseline(**over):
    data = json.loads((FIXTURES / "baseline.json").read_text())
    data.update(over)
    return data


def test_baseline_fixture_validates_against_schema_and_round_trips():
    raw = _baseline()
    jsonschema.validate(raw, profile_schema())
    model = BaselineProfile.model_validate(raw)
    assert json.loads(model.model_dump_json(by_alias=True)) == raw


def test_complete_baseline_requires_null_partial_reason():
    with pytest.raises(ValidationError):
        BaselineProfile.model_validate(_baseline(partial_reason="timed out"))


def test_complete_baseline_requires_degree_distribution():
    raw = _baseline()
    raw["statistics"]["degree_distribution"] = None

    with pytest.raises(ValidationError):
        BaselineProfile.model_validate(raw)


def test_partial_baseline_requires_non_empty_partial_reason():
    with pytest.raises(ValidationError):
        BaselineProfile.model_validate(_baseline(status=ProfileStatus.PARTIAL))


def test_partial_baseline_allows_null_degree_distribution():
    raw = _baseline(status=ProfileStatus.PARTIAL, partial_reason="degree probe timed out")
    raw["statistics"]["degree_distribution"] = None

    model = BaselineProfile.model_validate(raw)

    assert model.status is ProfileStatus.PARTIAL


def test_counts_must_be_non_negative():
    raw = _baseline()
    raw["schema"]["labels"][0]["count"] = -1

    with pytest.raises(ValidationError):
        BaselineProfile.model_validate(raw)


def test_coverage_must_be_percentage():
    raw = _baseline()
    raw["schema"]["labels"][0]["properties"][0]["coverage"] = 101

    with pytest.raises(ValidationError):
        BaselineProfile.model_validate(raw)


def test_collections_must_be_canonically_sorted():
    raw = _baseline()
    raw["schema"]["labels"] = list(reversed(raw["schema"]["labels"]))

    with pytest.raises(ValidationError):
        BaselineProfile.model_validate(raw)


def test_fingerprint_must_match_v0_hash_input():
    raw = _baseline(fingerprint="sha256:abc")

    with pytest.raises(ValidationError):
        BaselineProfile.model_validate(raw)


def test_fingerprint_uses_labels_relationship_types_and_core_counts():
    raw = _baseline()
    expected = profile_fingerprint(
        GraphSchema.model_validate(raw["schema"]),
        ProfileStatistics.model_validate(raw["statistics"]),
    )

    assert BaselineProfile.model_validate(raw).fingerprint == expected

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
        BaselineProfile.model_validate(_baseline(bogus=True))


def test_committed_profile_schema_is_current():
    committed = json.loads((SPECS_DIR / "profile.schema.json").read_text())
    assert committed == profile_schema()
