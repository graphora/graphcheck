import json
from pathlib import Path

import pytest

from graphcheck.contracts.profile import BaselineProfile, ProfileStatus
from graphcheck.diff import compare, diff, render_human, render_json


def _profile() -> BaselineProfile:
    path = Path(__file__).parent / "contracts" / "fixtures" / "baseline.json"
    return BaselineProfile.model_validate_json(path.read_text(encoding="utf-8"))


def _changed_profile(*, degree_missing: bool = False) -> BaselineProfile:
    baseline = _profile()
    account, customer = baseline.graph_schema.labels
    degree = account.degree_distribution
    assert degree is not None
    changed_degree = (
        None
        if degree_missing
        else degree.model_copy(update={"median": 1.8, "p95": 3.0, "p99": 4.0, "maximum": 5})
    )
    changed_account = account.model_copy(
        update={"count": 12, "degree_distribution": changed_degree}
    )
    airport = customer.model_copy(
        update={"name": "Airport", "count": 42, "degree_distribution": changed_degree}
    )
    return baseline.model_copy(
        update={
            "status": ProfileStatus.PARTIAL if degree_missing else ProfileStatus.COMPLETE,
            "partial_reason": "degree probe timed out" if degree_missing else None,
            "fingerprint": "sha256:stored-different",
            "graph_schema": baseline.graph_schema.model_copy(
                update={"labels": [changed_account, airport]}
            ),
            "statistics": baseline.statistics.model_copy(
                update={
                    "node_count": 54,
                    "relationship_count": 9,
                    "property_coverage": [
                        baseline.statistics.property_coverage[0].model_copy(
                            update={"coverage": 92.0}
                        ),
                        *baseline.statistics.property_coverage[1:],
                    ],
                }
            ),
        }
    )


def test_identical_profiles_return_no_messages() -> None:
    baseline = _profile()
    assert diff(baseline, baseline) == []


def test_json_uses_finalized_schema_and_nested_summary() -> None:
    payload = json.loads(render_json(compare(_profile(), _changed_profile())))

    assert list(payload) == [
        "schema_version",
        "baseline_a",
        "baseline_b",
        "a_status",
        "b_status",
        "fingerprint_changed",
        "labels",
        "relationship_types",
        "constraints",
        "indexes",
        "statistics",
        "summary",
    ]
    assert payload["summary"] == {
        "labels": {"changed": 1, "added": 1, "removed": 1},
        "relationship_types": {"changed": 0, "added": 0, "removed": 0},
        "constraints": {"added": 0, "removed": 0},
        "indexes": {"added": 0, "removed": 0},
        "statistics": {"changed": 4},
    }
    assert payload["statistics"]["node_count"] == {
        "from": 13,
        "to": 54,
        "delta": 41,
        "pct": 315.4,
    }
    assert payload["statistics"]["relationship_count"] == {
        "from": 7,
        "to": 9,
        "delta": 2,
        "pct": 28.6,
    }
    assert "name" not in payload["statistics"]["node_count"]
    assert "name" not in payload["statistics"]["relationship_count"]


def test_degree_distribution_json_uses_model_shape_and_not_a_list() -> None:
    degree = json.loads(render_json(compare(_profile(), _changed_profile())))["statistics"][
        "degree_distribution"
    ]

    assert isinstance(degree, dict)
    assert degree == {
        "Account": {
            "from": {"median": 1.0, "p95": 3.0, "p99": 4.0, "maximum": 4},
            "to": {"median": 1.8, "p95": 3.0, "p99": 4.0, "maximum": 5},
        }
    }


def test_degree_distribution_json_is_null_when_either_side_is_missing() -> None:
    degree = json.loads(render_json(compare(_profile(), _changed_profile(degree_missing=True))))[
        "statistics"
    ]["degree_distribution"]

    assert degree["Account"] is None


def test_partial_warning_explains_suppressed_removals() -> None:
    output = render_human(compare(_profile(), _changed_profile(degree_missing=True)))

    assert "warning: baseline_b is PARTIAL" in output
    assert "Collections missing due to partial status are not reported as removed." in output


def test_human_count_formatting_and_statistics_labels() -> None:
    output = render_human(compare(_profile(), _changed_profile()))

    assert "Account\n10 → 12 (+2, +20.0%)" in output
    assert "+ Airport\n0 → 42 (new)" in output
    assert "- Customer\n3 → 0 (removed)" in output
    assert "Nodes    13 → 54 (+41, +315.4%)" in output
    assert "Relationships    7 → 9 (+2, +28.6%)" in output
    assert "Account.id cover    100.0% → 92.0% (-8.0 pp)" in output
    assert "median: 1 → 1.8 (+0.8)" in output
    assert "Summary: 1 label changed, 1 added, 1 removed · 4 statistics changed" in output
    assert "Node count" not in output
    assert "Relationship count" not in output


def test_human_output_omits_summary_when_there_is_no_drift() -> None:
    output = render_human(compare(_profile(), _profile()))

    assert "Summary:" not in output


def test_json_lists_and_degree_keys_are_deterministically_ordered() -> None:
    payload = json.loads(render_json(compare(_profile(), _changed_profile())))

    for collection in ("labels", "relationship_types", "constraints", "indexes"):
        for category in ("changed", "added", "removed"):
            if category in payload[collection]:
                names = [item["name"] for item in payload[collection][category]]
                assert names == sorted(names)
    assert list(payload["statistics"]["degree_distribution"]) == ["Account"]


def test_fingerprint_uses_stored_values_and_is_never_recomputed(monkeypatch) -> None:
    baseline = _profile()
    changed = baseline.model_copy(update={"fingerprint": "sha256:stored-different"})

    def fail_recompute(*args, **kwargs):
        pytest.fail("fingerprint must not be recomputed during diff")

    monkeypatch.setattr("graphcheck.contracts.profile.profile_fingerprint", fail_recompute)
    assert compare(baseline, changed).fingerprint_changed is True
