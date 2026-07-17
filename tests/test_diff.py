from pathlib import Path

from graphcheck.contracts.profile import BaselineProfile
from graphcheck.diff import diff


def _profile() -> BaselineProfile:
    path = Path(__file__).parent / "contracts" / "fixtures" / "baseline.json"
    return BaselineProfile.model_validate_json(path.read_text(encoding="utf-8"))


def test_identical_profiles_return_no_messages() -> None:
    baseline = _profile()
    assert diff(baseline, baseline) == []


def test_diff_reports_all_sections_in_deterministic_order() -> None:
    current = _profile()
    current_schema = current.graph_schema
    current_statistics = current.statistics
    retained_label = current_schema.labels[0]
    current_degree = retained_label.degree_distribution
    assert current_degree is not None
    latest_label = retained_label.model_copy(
        update={
            "degree_distribution": current_degree.model_copy(
                update={
                    "median": current_degree.median + 1,
                    "p95": current_degree.p95 + 1,
                    "p99": current_degree.p99 + 1,
                    "maximum": current_degree.maximum + 1,
                }
            )
        }
    )
    current_coverage = current_statistics.property_coverage
    latest = current.model_copy(
        update={
            "graph_schema": current_schema.model_copy(
                update={
                    "labels": [latest_label],
                    "relationship_types": [],
                    "constraints": [],
                    "indexes": [],
                }
            ),
            "statistics": current_statistics.model_copy(
                update={
                    "node_count": current_statistics.node_count + 1,
                    "relationship_count": current_statistics.relationship_count + 1,
                    "property_coverage": [
                        current_coverage[0].model_copy(
                            update={"coverage": current_coverage[0].coverage - 1}
                        )
                    ],
                }
            ),
        }
    )

    messages = diff(current, latest)

    assert messages.index("Schema") < messages.index("Statistics")
    assert messages.index("Statistics") < messages.index("Property Coverage")
    assert messages.index("Property Coverage") < messages.index("Degree Distribution")
    for expected in (
        "- Label Customer",
        "- Relationship Type CONTROLS",
        "- Constraint customer_id_unique",
        "- Index customer_name_index",
        "Node count changed",
        "Relationship count changed",
        "- Customer.id",
        "Property coverage changed",
        "Account.id",
        "median",
        "p95",
        "p99",
        "maximum",
    ):
        assert expected in messages
