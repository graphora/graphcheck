import json
from pathlib import Path

import pytest

from graphcheck.contracts.profile import BaselineProfile, ProfileStatus
from graphcheck.diff import compare, diff, render_human, render_json
from graphcheck.errors import GraphCheckError


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
                        baseline.statistics.property_coverage[1].model_copy(
                            update={
                                "owner_name": "Airport",
                                "property": "code",
                                "coverage": 75.0,
                            }
                        ),
                        baseline.statistics.property_coverage[2],
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
        "drift_detected",
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
    coverage = payload["statistics"]["property_coverage"]

    assert coverage["changed"] == [
        {
            "owner": "node",
            "owner_name": "Account",
            "property": "id",
            "from": 100.0,
            "to": 92.0,
            "delta_pp": -8.0,
        }
    ]

    assert coverage["added"] == [
        {
            "owner": "node",
            "owner_name": "Airport",
            "property": "code",
            "coverage": 75.0,
        }
    ]

    assert coverage["removed"] == [
        {
            "owner": "node",
            "owner_name": "Customer",
            "property": "id",
            "coverage": 100.0,
        }
    ]
    assert "name" not in payload["statistics"]["node_count"]
    assert "name" not in payload["statistics"]["relationship_count"]
    assert set(payload["constraints"]) == {"added", "removed"}
    assert set(payload["indexes"]) == {"added", "removed"}


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


def _partial_profile() -> BaselineProfile:
    baseline = _profile()
    account, customer = baseline.graph_schema.labels
    return baseline.model_copy(
        update={
            "status": ProfileStatus.PARTIAL,
            "partial_reason": "degree probe timed out",
            "graph_schema": baseline.graph_schema.model_copy(
                update={
                    "labels": [
                        account.model_copy(update={"degree_distribution": None}),
                        customer,
                    ]
                }
            ),
        }
    )


@pytest.mark.parametrize(
    ("baseline_a", "baseline_b"),
    [
        (_partial_profile(), _profile()),
        (_profile(), _partial_profile()),
        (_partial_profile(), _partial_profile()),
    ],
)
def test_compare_rejects_partial_baselines(
    baseline_a: BaselineProfile, baseline_b: BaselineProfile
) -> None:
    with pytest.raises(GraphCheckError) as exc:
        compare(baseline_a, baseline_b)

    assert exc.value.error.code == "diff.partial_baseline"
    assert "comparison is inconclusive" in exc.value.error.message.lower()
    assert "complete baseline" in exc.value.error.fix.lower()


@pytest.mark.parametrize(
    ("current", "latest"),
    [
        (_partial_profile(), _profile()),
        (_profile(), _partial_profile()),
        (_partial_profile(), _partial_profile()),
    ],
)
def test_compatibility_diff_rejects_partial_baselines(
    current: BaselineProfile, latest: BaselineProfile
) -> None:
    with pytest.raises(GraphCheckError) as exc:
        diff(current, latest)

    assert exc.value.error.code == "diff.partial_baseline"


def test_human_count_formatting_and_statistics_labels() -> None:
    output = render_human(compare(_profile(), _changed_profile()))

    assert "Account 10 → 12 (+2, +20.0%)" in output
    assert "+ Airport 0 → 42 (new)" in output
    assert "- Customer 3 → 0 (removed)" in output
    assert "Nodes 13 → 54 (+41, +315.4%)" in output
    assert "Relationships 7 → 9 (+2, +28.6%)" in output
    assert "Account.id cover    100.0% → 92.0% (-8.0 pp)" in output
    assert "+ Airport.code cover    75.0% (new)" in output
    assert "- Customer.id cover    100.0% (removed)" in output
    assert "median: 1 → 1.8 (+0.8)" in output
    assert "Summary: 1 label changed, 1 added, 1 removed · 4 statistics changed" in output
    assert "Node count" not in output
    assert "Relationship count" not in output
    assert output.index("Nodes 13 → 54") < output.index("Relationships 7 → 9")
    assert output.index("Relationships 7 → 9") < output.index("Account.id cover")
    assert output.index("Account.id cover") < output.index("+ Airport.code cover")
    assert output.index("+ Airport.code cover") < output.index("- Customer.id cover")
    assert output.index("- Customer.id cover") < output.index("Account degree distribution")


def test_human_constraint_and_index_changes_use_single_line_contract() -> None:
    baseline = _profile()
    changed = baseline.model_copy(
        update={
            "fingerprint": "sha256:stored-different",
            "graph_schema": baseline.graph_schema.model_copy(
                update={"constraints": [], "indexes": []}
            ),
        }
    )

    assert render_human(compare(baseline, changed)) == (
        "diff  baseline_a → baseline_b\n"
        "fingerprint: CHANGED\n\n"
        "Constraints\n"
        "- customer_id_unique [Customer(id), UNIQUENESS] (removed)\n\n"
        "Indexes\n"
        "- customer_name_index [Customer(name), RANGE] (removed)\n\n"
        "Summary: 1 constraint removed · 1 index removed"
    )


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


def test_coverage_only_change_detects_drift_with_matching_fingerprint() -> None:
    baseline = _profile()
    coverage = baseline.statistics.property_coverage
    changed = baseline.model_copy(
        update={
            "statistics": baseline.statistics.model_copy(
                update={
                    "property_coverage": [
                        coverage[0].model_copy(update={"coverage": 92.0}),
                        *coverage[1:],
                    ]
                }
            )
        }
    )

    report = compare(baseline, changed)

    assert report.fingerprint_changed is False
    assert report.drift_detected is True
    assert "No drift detected." not in render_human(report)
    assert "Account.id cover    100.0% → 92.0% (-8.0 pp)" in render_human(report)
