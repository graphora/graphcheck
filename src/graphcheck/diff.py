from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from graphcheck.contracts.profile import BaselineProfile, ProfileStatus


class SchemaVersionMismatch(ValueError):
    """Raised when snapshots use incompatible contracts."""


@dataclass(frozen=True)
class DiffReport:
    schema_version: str
    baseline_a: str
    baseline_b: str
    a_status: str
    b_status: str
    fingerprint_changed: bool
    labels: dict[str, Any]
    relationship_types: dict[str, Any]
    constraints: dict[str, Any]
    indexes: dict[str, Any]
    statistics: dict[str, Any]
    summary: dict[str, Any]
    a_partial_reason: str | None = None
    b_partial_reason: str | None = None


def _change(name: str, before: int | float, after: int | float) -> dict[str, Any]:
    delta = after - before
    return {
        "name": name,
        "from": before,
        "to": after,
        "delta": delta,
        "pct": None if before == 0 else round(delta / before * 100, 1),
    }


def _count_collection(
    a_items: list[Any], b_items: list[Any], suppress_removed: bool
) -> dict[str, Any]:
    a = {item.name: item for item in a_items}
    b = {item.name: item for item in b_items}
    shared = sorted(a.keys() & b.keys())
    return {
        "changed": [
            _change(name, a[name].count, b[name].count)
            for name in shared
            if a[name].count != b[name].count
        ],
        "added": [{"name": name, "to": b[name].count} for name in sorted(b.keys() - a.keys())],
        "removed": []
        if suppress_removed
        else [{"name": name, "from": a[name].count} for name in sorted(a.keys() - b.keys())],
        "unchanged": sum(a[name].count == b[name].count for name in shared),
    }


def _definition(item: Any) -> dict[str, Any]:
    return {
        "name": item.name,
        "labels_or_types": list(item.labels_or_types),
        "properties": list(item.properties),
        "type": item.type,
    }


def _definition_collection(
    a_items: list[Any], b_items: list[Any], suppress_removed: bool
) -> dict[str, Any]:
    a = {item.name: item for item in a_items}
    b = {item.name: item for item in b_items}
    added, removed, unchanged = set(b) - set(a), set(a) - set(b), 0
    for name in set(a) & set(b):
        if a[name] == b[name]:
            unchanged += 1
        else:
            added.add(name)
            removed.add(name)
    return {
        "added": [_definition(b[name]) for name in sorted(added)],
        "removed": [] if suppress_removed else [_definition(a[name]) for name in sorted(removed)],
        "unchanged": unchanged,
    }


def compare(
    baseline_a: BaselineProfile,
    baseline_b: BaselineProfile,
    baseline_a_name: str = "baseline_a",
    baseline_b_name: str = "baseline_b",
) -> DiffReport:
    """Compare profiles without formatting or recomputing their fingerprints."""
    if baseline_a.schema_version != baseline_b.schema_version:
        raise SchemaVersionMismatch(
            "cannot diff baselines with different schema_version "
            f"(a={baseline_a.schema_version}, b={baseline_b.schema_version})"
        )
    b_partial = baseline_b.status is ProfileStatus.PARTIAL
    labels = _count_collection(
        baseline_a.graph_schema.labels,
        baseline_b.graph_schema.labels,
        b_partial and not baseline_b.graph_schema.labels,
    )
    relationships = _count_collection(
        baseline_a.graph_schema.relationship_types,
        baseline_b.graph_schema.relationship_types,
        b_partial and not baseline_b.graph_schema.relationship_types,
    )
    constraints = _definition_collection(
        baseline_a.graph_schema.constraints,
        baseline_b.graph_schema.constraints,
        b_partial and not baseline_b.graph_schema.constraints,
    )
    indexes = _definition_collection(
        baseline_a.graph_schema.indexes,
        baseline_b.graph_schema.indexes,
        b_partial and not baseline_b.graph_schema.indexes,
    )
    a_cov = {
        (x.owner, x.owner_name, x.property): x for x in baseline_a.statistics.property_coverage
    }
    b_cov = {
        (x.owner, x.owner_name, x.property): x for x in baseline_b.statistics.property_coverage
    }
    cov_changed = []

    def order(key: tuple[str, str, str]) -> tuple[str, str, str]:
        return (key[1], key[2], key[0])

    for key in sorted(a_cov.keys() & b_cov.keys(), key=order):
        old, new = a_cov[key], b_cov[key]
        if old.coverage != new.coverage:
            cov_changed.append(
                {
                    "owner": old.owner,
                    "owner_name": old.owner_name,
                    "property": old.property,
                    "from": old.coverage,
                    "to": new.coverage,
                    "delta_pp": round(new.coverage - old.coverage, 1),
                }
            )
    coverage = {
        "changed": cov_changed,
        "added": [b_cov[k].model_dump() for k in sorted(b_cov.keys() - a_cov.keys(), key=order)],
        "removed": []
        if b_partial and not b_cov
        else [a_cov[k].model_dump() for k in sorted(a_cov.keys() - b_cov.keys(), key=order)],
        "unchanged": len(a_cov.keys() & b_cov.keys()) - len(cov_changed),
    }
    a_labels = {x.name: x for x in baseline_a.graph_schema.labels}
    b_labels = {x.name: x for x in baseline_b.graph_schema.labels}
    degree: dict[str, Any] = {}
    for name in sorted(a_labels.keys() & b_labels.keys()):
        old, new = a_labels[name].degree_distribution, b_labels[name].degree_distribution
        if old is None or new is None:
            degree[name] = None
        elif old != new:
            degree[name] = {"from": old.model_dump(), "to": new.model_dump()}
    statistics = {
        "node_count": None
        if baseline_a.statistics.node_count == baseline_b.statistics.node_count
        else _change(
            "node_count", baseline_a.statistics.node_count, baseline_b.statistics.node_count
        ),
        "relationship_count": None
        if baseline_a.statistics.relationship_count == baseline_b.statistics.relationship_count
        else _change(
            "relationship_count",
            baseline_a.statistics.relationship_count,
            baseline_b.statistics.relationship_count,
        ),
        "property_coverage": coverage,
        "degree_distribution": degree,
    }
    summary = {
        "labels": {
            "changed": len(labels["changed"]),
            "added": len(labels["added"]),
            "removed": len(labels["removed"]),
        },
        "relationship_types": {
            "changed": len(relationships["changed"]),
            "added": len(relationships["added"]),
            "removed": len(relationships["removed"]),
        },
        "constraints": {"added": len(constraints["added"]), "removed": len(constraints["removed"])},
        "indexes": {"added": len(indexes["added"]), "removed": len(indexes["removed"])},
        "statistics": {
            "changed": sum(
                (
                    statistics["node_count"] is not None,
                    statistics["relationship_count"] is not None,
                    bool(cov_changed or coverage["added"] or coverage["removed"]),
                    bool(degree),
                )
            )
        },
    }
    return DiffReport(
        baseline_a.schema_version,
        Path(baseline_a_name).name,
        Path(baseline_b_name).name,
        str(baseline_a.status),
        str(baseline_b.status),
        baseline_a.fingerprint != baseline_b.fingerprint,
        labels,
        relationships,
        constraints,
        indexes,
        statistics,
        summary,
        baseline_a.partial_reason,
        baseline_b.partial_reason,
    )


def _number(value: int | float) -> str:
    return f"{value:,.0f}" if float(value).is_integer() else f"{value:,}"


def _count_lines(item: dict[str, Any], prefix: str = "") -> list[str]:
    before, after, delta = item.get("from", 0), item.get("to", 0), item.get("delta")
    if "from" not in item:
        note = "new"
    elif "to" not in item:
        after, note = 0, "removed"
    else:
        pct = item["pct"]
        note = f"{delta:+,}, n/a" if pct is None else f"{delta:+,}, {pct:+.1f}%"
    return [f"{prefix}{item['name']}", f"{_number(before)} → {_number(after)} ({note})"]


def _render_counts(title: str, section: dict[str, Any]) -> list[str]:
    body: list[str] = []
    for item in section["changed"]:
        body.extend(_count_lines(item))
    for item in section["added"]:
        body.extend(_count_lines(item, "+ "))
    for item in section["removed"]:
        body.extend(_count_lines(item, "- "))
    if section["unchanged"]:
        body.append(f"({section['unchanged']} unchanged)")
    return [title, *body] if body else []


def render_human(report: DiffReport) -> str:
    lines = [
        f"diff  {report.baseline_a} → {report.baseline_b}",
        f"fingerprint: {'CHANGED' if report.fingerprint_changed else 'MATCH'}",
    ]
    partial = False
    if report.a_status == str(ProfileStatus.PARTIAL):
        lines.extend(("", f"warning: baseline_a is PARTIAL — {report.a_partial_reason}"))
        partial = True
    if report.b_status == str(ProfileStatus.PARTIAL):
        lines.extend(("", f"warning: baseline_b is PARTIAL — {report.b_partial_reason}"))
        partial = True
    if partial:
        lines.append("Collections missing due to partial status are not reported as removed.")
    if not report.fingerprint_changed:
        return "\n".join((*lines, "", "No drift detected."))
    sections = [
        _render_counts("Labels", report.labels),
        _render_counts("Relationships", report.relationship_types),
    ]
    for title, collection in (("Constraints", report.constraints), ("Indexes", report.indexes)):
        body = []
        for sign, key in (("+", "added"), ("-", "removed")):
            for item in collection[key]:
                target, props = ", ".join(item["labels_or_types"]), ", ".join(item["properties"])
                body.extend((f"{sign} {item['name']}", f"[{target}({props}), {item['type']}]"))
        sections.append([title, *body] if body else [])
    stats: list[str] = []
    for label, key in (("Nodes", "node_count"), ("Relationships", "relationship_count")):
        if report.statistics[key]:
            count_line = _count_lines({**report.statistics[key], "name": label})[1]
            stats.append(f"{label}    {count_line}")
    for item in report.statistics["property_coverage"]["changed"]:
        stats.append(
            f"{item['owner_name']}.{item['property']} cover    "
            f"{item['from']:.1f}% → {item['to']:.1f}% ({item['delta_pp']:+.1f} pp)"
        )
    for name, item in report.statistics["degree_distribution"].items():
        if item is not None:
            stats.append(f"{name} degree distribution")
            for field, before in item["from"].items():
                after = item["to"][field]
                delta = round(after - before, 1)
                stats.append(f"{field}: {_number(before)} → {_number(after)} ({delta:+g})")
    sections.append(["Statistics", *stats] if stats else [])
    for section in sections:
        if section:
            lines.extend(("", *section))
    summary = report.summary
    parts: list[str] = []
    labels = summary["labels"]
    if any(labels.values()):
        noun = "label" if labels["changed"] == 1 else "labels"
        parts.append(
            f"{labels['changed']} {noun} changed, {labels['added']} added, "
            f"{labels['removed']} removed"
        )
    relationships = summary["relationship_types"]
    if any(relationships.values()):
        noun = "relationship" if relationships["changed"] == 1 else "relationships"
        parts.append(
            f"{relationships['changed']} {noun} changed, {relationships['added']} added, "
            f"{relationships['removed']} removed"
        )
    for key, singular in (("constraints", "constraint"), ("indexes", "index")):
        values = summary[key]
        details = []
        for action in ("added", "removed"):
            count = values[action]
            if count:
                if singular == "index":
                    noun = "index" if count == 1 else "indexes"
                else:
                    noun = singular if count == 1 else f"{singular}s"
                details.append(f"{count} {noun} {action}")
        if details:
            parts.append(", ".join(details))
    changed = summary["statistics"]["changed"]
    if changed:
        noun = "statistic" if changed == 1 else "statistics"
        parts.append(f"{changed} {noun} changed")
    if parts:
        lines.extend(("", f"Summary: {' · '.join(parts)}"))
    return "\n".join(lines)


def render_json(report: DiffReport) -> str:
    statistics = {
        **report.statistics,
        "node_count": (
            None
            if report.statistics["node_count"] is None
            else {
                key: value
                for key, value in report.statistics["node_count"].items()
                if key != "name"
            }
        ),
        "relationship_count": (
            None
            if report.statistics["relationship_count"] is None
            else {
                key: value
                for key, value in report.statistics["relationship_count"].items()
                if key != "name"
            }
        ),
    }
    payload = {
        "schema_version": report.schema_version,
        "baseline_a": report.baseline_a,
        "baseline_b": report.baseline_b,
        "a_status": report.a_status,
        "b_status": report.b_status,
        "fingerprint_changed": report.fingerprint_changed,
        "labels": report.labels,
        "relationship_types": report.relationship_types,
        "constraints": report.constraints,
        "indexes": report.indexes,
        "statistics": statistics,
        "summary": report.summary,
    }
    return json.dumps(payload, indent=2)


def diff(current_baseline: BaselineProfile, latest_baseline: BaselineProfile) -> list[str]:
    """Compatibility wrapper for the original line-oriented API."""
    report = compare(current_baseline, latest_baseline)
    return [] if not report.fingerprint_changed else render_human(report).splitlines()[3:]
