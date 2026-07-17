from __future__ import annotations

from graphcheck.contracts.profile import BaselineProfile


def diff(
    current_baseline: BaselineProfile,
    latest_baseline: BaselineProfile,
) -> list[str]:
    """Return deterministic human-readable drift between two baseline snapshots."""
    sections = (
        _schema_diff(current_baseline, latest_baseline),
        _statistics_diff(current_baseline, latest_baseline),
        _property_coverage_diff(current_baseline, latest_baseline),
        _degree_distribution_diff(current_baseline, latest_baseline),
    )
    messages: list[str] = []
    for heading, changes in sections:
        if changes:
            if messages:
                messages.append("")
            messages.extend((heading, *changes))
    return messages


def _schema_diff(
    current_baseline: BaselineProfile,
    latest_baseline: BaselineProfile,
) -> tuple[str, list[str]]:
    changes: list[str] = []
    collections = (
        (current_baseline.graph_schema.labels, latest_baseline.graph_schema.labels, "Label"),
        (
            current_baseline.graph_schema.relationship_types,
            latest_baseline.graph_schema.relationship_types,
            "Relationship Type",
        ),
        (
            current_baseline.graph_schema.constraints,
            latest_baseline.graph_schema.constraints,
            "Constraint",
        ),
        (current_baseline.graph_schema.indexes, latest_baseline.graph_schema.indexes, "Index"),
    )
    for current_items, latest_items, label in collections:
        current_by_name = {item.name: item for item in current_items}
        latest_by_name = {item.name: item for item in latest_items}
        changes.extend(
            f"- {label} {name}" for name in current_by_name if name not in latest_by_name
        )
        changes.extend(
            f"+ {label} {name}" for name in latest_by_name if name not in current_by_name
        )
    return "Schema", changes


def _statistics_diff(
    current_baseline: BaselineProfile,
    latest_baseline: BaselineProfile,
) -> tuple[str, list[str]]:
    changes: list[str] = []
    comparisons = (
        (
            "Node count changed",
            current_baseline.statistics.node_count,
            latest_baseline.statistics.node_count,
        ),
        (
            "Relationship count changed",
            current_baseline.statistics.relationship_count,
            latest_baseline.statistics.relationship_count,
        ),
    )
    for description, current_value, latest_value in comparisons:
        if current_value != latest_value:
            _append_change(changes, description, current_value, latest_value)
    return "Statistics", changes


def _property_coverage_diff(
    current_baseline: BaselineProfile,
    latest_baseline: BaselineProfile,
) -> tuple[str, list[str]]:
    def key(item: object) -> tuple[object, object, object]:
        return (item.owner, item.owner_name, item.property)  # type: ignore[attr-defined]

    current_by_key = {key(item): item for item in current_baseline.statistics.property_coverage}
    latest_by_key = {key(item): item for item in latest_baseline.statistics.property_coverage}
    changes: list[str] = []
    for identity, item in current_by_key.items():
        if identity not in latest_by_key:
            changes.append(f"- {item.owner_name}.{item.property}")
    for identity, item in latest_by_key.items():
        if identity not in current_by_key:
            changes.append(f"+ {item.owner_name}.{item.property}")
    for identity, current_item in current_by_key.items():
        latest_item = latest_by_key.get(identity)
        if latest_item is not None and current_item.coverage != latest_item.coverage:
            if changes:
                changes.append("")
            changes.extend(
                (
                    "Property coverage changed",
                    "",
                    f"{current_item.owner_name}.{current_item.property}",
                    "",
                    f"{current_item.coverage:.2f}% -> {latest_item.coverage:.2f}%",
                )
            )
    return "Property Coverage", changes


def _degree_distribution_diff(
    current_baseline: BaselineProfile,
    latest_baseline: BaselineProfile,
) -> tuple[str, list[str]]:
    current_by_name = {item.name: item for item in current_baseline.graph_schema.labels}
    latest_by_name = {item.name: item for item in latest_baseline.graph_schema.labels}
    changes: list[str] = []
    for name, current_label in current_by_name.items():
        latest_label = latest_by_name.get(name)
        if (
            latest_label is None
            or current_label.degree_distribution is None
            or latest_label.degree_distribution is None
        ):
            continue
        label_changes: list[str] = []
        for field in ("median", "p95", "p99", "maximum"):
            current_value = getattr(current_label.degree_distribution, field)
            latest_value = getattr(latest_label.degree_distribution, field)
            if current_value != latest_value:
                _append_change(
                    label_changes,
                    field,
                    _format_number(current_value),
                    _format_number(latest_value),
                )
        if label_changes:
            if changes:
                changes.append("")
            changes.extend(("Degree distribution changed", "", name, *label_changes))
    return "Degree Distribution", changes


def _append_change(changes: list[str], description: str, current: object, latest: object) -> None:
    if changes:
        changes.append("")
    changes.extend((description, "", f"{current} -> {latest}"))


def _format_number(value: float | int) -> str:
    return str(int(value)) if isinstance(value, float) and value.is_integer() else str(value)
