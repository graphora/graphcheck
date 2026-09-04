import json
import math

import pytest

from graphcheck.contracts.results import EvidenceElement
from graphcheck.engine.baseline import (
    BaselineValue,
    DirectoryBaselineProvider,
    MappingBaselineProvider,
    require_baseline,
)
from graphcheck.errors import GraphCheckError


def test_compact_reference_can_be_a_numeric_value():
    provider = MappingBaselineProvider({"latest": 42})

    assert provider.resolve("latest", "node_count", {"label": "Customer"}) == BaselineValue(
        value=42.0
    )


def test_directory_provider_resolves_pinned_and_latest_c4_snapshots(tmp_path):
    older = _c4_profile()
    newer = _c4_profile()
    older["statistics"]["node_count"] = 10
    newer["statistics"]["node_count"] = 20
    (tmp_path / "2026-01-01.json").write_text(json.dumps(older), encoding="utf-8")
    (tmp_path / "2026-02-01.json").write_text(json.dumps(newer), encoding="utf-8")
    provider = DirectoryBaselineProvider(tmp_path)

    assert provider.resolve("2026-01-01", "node_count", {}) == BaselineValue(value=10)
    assert provider.resolve("latest", "node_count", {}) == BaselineValue(value=20)


def test_directory_provider_reports_invalid_referenced_snapshot(tmp_path):
    (tmp_path / "broken.json").write_text("{", encoding="utf-8")
    provider = DirectoryBaselineProvider(tmp_path)

    with pytest.raises(GraphCheckError) as caught:
        provider.resolve("latest", "node_count", {})

    assert caught.value.error.code == "engine.baseline_invalid"


def test_compact_reference_can_map_metric_directly():
    provider = MappingBaselineProvider({"release-1": {"node_count": 81}})

    assert provider.resolve("release-1", "node_count", {}) == BaselineValue(value=81.0)


def test_compact_metric_supports_canonical_target_keys():
    provider = MappingBaselineProvider(
        {
            "latest": {
                "property_coverage": {
                    "label=Customer|property=tax_id": 97.5,
                }
            }
        }
    )

    value = provider.resolve(
        "latest",
        "property_coverage",
        {"property": "tax_id", "label": "Customer"},
    )

    assert value == BaselineValue(value=97.5)


def test_profile_statistics_support_nested_label_and_property_targets():
    provider = MappingBaselineProvider(
        {
            "latest": {
                "statistics": {
                    "property_coverage": {
                        "Customer": {"tax_id": 96.0},
                    }
                }
            }
        }
    )

    value = provider.resolve(
        "latest",
        "property_coverage",
        {"label": "Customer", "property": "tax_id"},
    )

    assert value == BaselineValue(value=96.0)


def _c4_profile(*, status: str = "complete") -> dict[str, object]:
    return {
        "status": status,
        "statistics": {
            "node_count": 1_000,
            "relationship_count": 2_000,
        },
        "schema": {
            "labels": [
                {"name": "Customer", "count": 125},
                {"name": "Account", "count": 300},
            ],
            "relationship_types": [
                {"name": "CONTROLS", "count": 175},
                {"name": "TRANSFERRED_TO", "count": 900},
            ],
        },
    }


def test_c4_profile_resolves_total_node_statistic_without_a_target():
    provider = MappingBaselineProvider({"latest": _c4_profile()})

    assert provider.resolve("latest", "node_count", {}) == BaselineValue(value=1_000.0)


def test_c4_profile_resolves_label_count_from_schema():
    provider = MappingBaselineProvider({"latest": _c4_profile()})

    assert provider.resolve("latest", "node_count", {"label": "Customer"}) == BaselineValue(
        value=125.0
    )


def test_c4_profile_resolves_relationship_count_from_schema():
    provider = MappingBaselineProvider({"latest": _c4_profile()})

    value = provider.resolve("latest", "relationship_count", {"type": "CONTROLS"})

    assert value == BaselineValue(value=175.0)


def test_partial_profile_marks_resolved_value_partial():
    provider = MappingBaselineProvider({"latest": _c4_profile(status="partial")})

    value = provider.resolve("latest", "node_count", {"label": "Customer"})

    assert value == BaselineValue(value=125.0, partial=True)


def test_non_partial_status_does_not_mark_value_partial():
    provider = MappingBaselineProvider({"latest": _c4_profile(status="failed")})

    value = provider.resolve("latest", "node_count", {"label": "Customer"})

    assert value is not None
    assert value.partial is False


def test_targeted_value_preserves_validated_node_and_relationship_evidence():
    provider = MappingBaselineProvider(
        {
            "latest": {
                "status": "partial",
                "node_count": {
                    "label=Customer": {
                        "value": 2,
                        "evidence": [
                            {
                                "kind": "node",
                                "id": "4:graph:12",
                                "labels": ["Customer"],
                                "type": None,
                            },
                            {
                                "kind": "rel",
                                "id": "5:graph:8",
                                "labels": None,
                                "type": "CONTROLS",
                            },
                        ],
                    }
                },
            }
        }
    )

    value = provider.resolve("latest", "node_count", {"label": "Customer"})

    assert value == BaselineValue(
        value=2.0,
        evidence=(
            EvidenceElement(kind="node", id="4:graph:12", labels=["Customer"], type=None),
            EvidenceElement(kind="rel", id="5:graph:8", labels=None, type="CONTROLS"),
        ),
        partial=True,
    )


def test_compact_value_wrapper_preserves_evidence_without_a_target():
    provider = MappingBaselineProvider(
        {
            "latest": {
                "node_count": {
                    "value": 10,
                    "evidence": [
                        {
                            "kind": "node",
                            "id": "4:graph:1",
                            "labels": ["Customer"],
                            "type": None,
                        }
                    ],
                }
            }
        }
    )

    value = provider.resolve("latest", "node_count", {})

    assert value == BaselineValue(
        value=10.0,
        evidence=(EvidenceElement(kind="node", id="4:graph:1", labels=["Customer"], type=None),),
    )


@pytest.mark.parametrize(
    ("reference", "metric", "target"),
    [
        ("missing", "node_count", {}),
        ("latest", "missing_metric", {}),
        ("latest", "node_count", {"label": "MissingLabel"}),
        ("latest", "relationship_count", {"type": "MISSING_TYPE"}),
    ],
)
def test_missing_reference_metric_or_profile_target_returns_none(reference, metric, target):
    provider = MappingBaselineProvider({"latest": _c4_profile()})

    assert provider.resolve(reference, metric, target) is None


@pytest.mark.parametrize("raw", ["not-a-profile", object(), [1, 2]])
def test_unrecognized_reference_shapes_return_none(raw):
    provider = MappingBaselineProvider({"latest": raw})

    assert provider.resolve("latest", "node_count", {}) is None


def test_require_baseline_errors_when_no_provider_is_configured():
    with pytest.raises(GraphCheckError) as caught:
        require_baseline(None, "latest", "node_count", {"label": "Customer"})

    assert caught.value.error.code == "engine.baseline_missing"
    assert "no baseline provider" in caught.value.error.message
    assert caught.value.error.fix


def test_require_baseline_errors_when_value_is_missing():
    provider = MappingBaselineProvider({"latest": {"node_count": {}}})

    with pytest.raises(GraphCheckError) as caught:
        require_baseline(provider, "latest", "node_count", {"label": "Customer"})

    assert caught.value.error.code == "engine.baseline_missing"
    assert "Customer" in caught.value.error.message
    assert caught.value.error.fix


@pytest.mark.parametrize("invalid", [True, False, "12", [], object()])
def test_non_numeric_metric_values_are_invalid(invalid):
    provider = MappingBaselineProvider({"latest": {"node_count": invalid}})

    with pytest.raises(GraphCheckError) as caught:
        provider.resolve("latest", "node_count", {})

    assert caught.value.error.code == "engine.baseline_invalid"
    assert caught.value.error.fix


@pytest.mark.parametrize("missing", [{}, None])
def test_empty_target_map_or_null_metric_value_is_missing(missing):
    provider = MappingBaselineProvider({"latest": {"node_count": missing}})

    assert provider.resolve("latest", "node_count", {}) is None


@pytest.mark.parametrize("invalid", [-1, math.nan, math.inf, -math.inf])
def test_non_finite_metric_values_are_invalid(invalid):
    provider = MappingBaselineProvider({"latest": {"node_count": invalid}})

    with pytest.raises(GraphCheckError) as caught:
        provider.resolve("latest", "node_count", {})

    assert caught.value.error.code == "engine.baseline_invalid"
    assert "finite" in caught.value.error.message


@pytest.mark.parametrize("invalid", [-1, math.nan, math.inf, -math.inf])
def test_baseline_value_rejects_invalid_custom_provider_measurements(invalid):
    with pytest.raises(ValueError, match="finite and non-negative"):
        BaselineValue(value=invalid)


def test_missing_measurement_in_partial_profile_is_a_distinct_partial_error():
    provider = MappingBaselineProvider({"latest": _c4_profile(status="partial")})

    with pytest.raises(GraphCheckError) as caught:
        provider.resolve(
            "latest",
            "property_coverage",
            {"label": "Customer", "property": "tax_id"},
        )

    assert caught.value.error.code == "engine.baseline_partial_missing"


@pytest.mark.parametrize("invalid", [True, False, math.nan, math.inf, -math.inf])
def test_invalid_compact_scalar_references_are_not_treated_as_missing(invalid):
    provider = MappingBaselineProvider({"latest": invalid})

    with pytest.raises(GraphCheckError) as caught:
        provider.resolve("latest", "node_count", {})

    assert caught.value.error.code == "engine.baseline_invalid"


def test_model_dump_compatible_c4_profile_is_supported():
    class Profile:
        def model_dump(self, *, mode):
            assert mode == "python"
            return _c4_profile()

    provider = MappingBaselineProvider({"latest": Profile()})

    assert provider.resolve("latest", "node_count", {"label": "Account"}) == BaselineValue(
        value=300.0
    )


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        ({"label": "Customer", "property": "tax_id"}, 97.5),
        ({"type": "CONTROLS", "property": "since"}, 82.0),
    ],
)
def test_c4_property_coverage_list_resolves_node_and_relationship_targets(target, expected):
    profile = _c4_profile()
    profile["statistics"]["property_coverage"] = [
        {
            "owner": "node",
            "owner_name": "Customer",
            "property": "tax_id",
            "coverage": 97.5,
        },
        {
            "owner": "relationship",
            "owner_name": "CONTROLS",
            "property": "since",
            "coverage": 82.0,
        },
    ]

    value = MappingBaselineProvider({"latest": profile}).resolve(
        "latest", "property_coverage", target
    )

    assert value == BaselineValue(value=expected)
