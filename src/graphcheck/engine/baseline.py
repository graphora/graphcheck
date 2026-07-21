from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from graphcheck.contracts.results import EvidenceElement
from graphcheck.errors import GraphCheckError


@dataclass(frozen=True)
class BaselineValue:
    value: float
    evidence: tuple[EvidenceElement, ...] = ()
    partial: bool = False

    def __post_init__(self) -> None:
        if isinstance(self.value, bool) or not isinstance(self.value, (int, float)):
            raise ValueError("baseline value must be numeric")
        numeric = float(self.value)
        if not math.isfinite(numeric) or numeric < 0:
            raise ValueError("baseline value must be finite and non-negative")
        object.__setattr__(self, "value", numeric)


class BaselineProvider(Protocol):
    def resolve(
        self, reference: str, metric: str, target: Mapping[str, object]
    ) -> BaselineValue | None: ...


class MappingBaselineProvider:
    """Resolve drift values from simple mappings or a C4 baseline-shaped mapping."""

    def __init__(self, baselines: Mapping[str, object]) -> None:
        self._baselines = baselines

    def resolve(
        self, reference: str, metric: str, target: Mapping[str, object]
    ) -> BaselineValue | None:
        raw = self._baselines.get(reference)
        if raw is None:
            return None
        if hasattr(raw, "model_dump"):
            try:
                raw = raw.model_dump(mode="python", by_alias=True)
            except TypeError:
                # Lightweight provider doubles may implement only the Pydantic v1-style subset.
                raw = raw.model_dump(mode="python")
        if isinstance(raw, (int, float)):
            return _baseline_value(raw)
        if not isinstance(raw, Mapping):
            return None

        partial = raw.get("status") == "partial"
        candidate = _resolve_candidate(raw, metric, target)
        if candidate is None:
            if partial:
                raise GraphCheckError(
                    "engine.baseline_partial_missing",
                    f"Partial baseline {reference!r} did not collect {metric!r} for "
                    f"target {dict(target)!r}.",
                    "Regenerate a complete baseline or choose one containing this measurement.",
                )
            return None
        if isinstance(candidate, Mapping) and "value" in candidate:
            evidence = tuple(
                EvidenceElement.model_validate(item) for item in candidate.get("evidence", [])
            )
            return _baseline_value(candidate["value"], evidence=evidence, partial=partial)
        return _baseline_value(candidate, partial=partial)


class DirectoryBaselineProvider:
    """Resolve C4 baseline snapshots by filename, including the newest as ``latest``."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def resolve(
        self, reference: str, metric: str, target: Mapping[str, object]
    ) -> BaselineValue | None:
        path = self._path_for(reference)
        if path is None:
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise GraphCheckError(
                "engine.baseline_invalid",
                f"Baseline {path.name!r} could not be loaded: {exc}",
                "Regenerate the baseline with `graphcheck profile` or select another snapshot.",
            ) from exc
        if not isinstance(raw, Mapping):
            raise GraphCheckError(
                "engine.baseline_invalid",
                f"Baseline {path.name!r} must contain a JSON object.",
                "Regenerate the baseline with a compatible C4 profiler.",
            )
        return MappingBaselineProvider({reference: raw}).resolve(reference, metric, target)

    def _path_for(self, reference: str) -> Path | None:
        if not self.directory.is_dir():
            return None
        paths = sorted(path for path in self.directory.glob("*.json") if path.is_file())
        if reference == "latest":
            return paths[-1] if paths else None
        return next((path for path in paths if path.stem == reference), None)


def require_baseline(
    provider: BaselineProvider | None,
    reference: str,
    metric: str,
    target: Mapping[str, object],
) -> BaselineValue:
    if provider is None:
        raise GraphCheckError(
            "engine.baseline_missing",
            f"Drift check requires baseline {reference!r}, but no baseline provider is configured.",
            "Pin a baseline with `graphcheck baseline set`, then run the suite again.",
        )
    value = provider.resolve(reference, metric, target)
    if value is None:
        raise GraphCheckError(
            "engine.baseline_missing",
            f"Baseline {reference!r} has no {metric!r} value for target {dict(target)!r}.",
            "Create or select a baseline containing this metric and target.",
        )
    return value


def _resolve_candidate(
    raw: Mapping[str, object], metric: str, target: Mapping[str, object]
) -> object | None:
    # A compact test/user mapping can put metric values directly under the reference.
    if metric in raw:
        candidate = raw[metric]
        if isinstance(candidate, Mapping):
            if "value" in candidate:
                return candidate
            target_key = _target_key(target)
            if target_key in candidate:
                return candidate[target_key]
        else:
            return candidate

    statistics = raw.get("statistics")
    if isinstance(statistics, Mapping) and metric in statistics:
        candidate = statistics[metric]
        if metric == "property_coverage" and isinstance(candidate, list):
            return _property_coverage(candidate, target)
        if metric == "node_count" and target.get("label") is not None:
            label_count = _label_count(raw, str(target["label"]))
            if label_count is not None:
                return label_count
            return _nested(candidate, target) if isinstance(candidate, Mapping) else None
        if metric == "relationship_count" and target.get("type") is not None:
            rel_count = _relationship_count(raw, str(target["type"]))
            if rel_count is not None:
                return rel_count
            return _nested(candidate, target) if isinstance(candidate, Mapping) else None
        return _nested(candidate, target)

    if metric == "node_count" and target.get("label") is not None:
        return _label_count(raw, str(target["label"]))
    if metric == "relationship_count" and target.get("type") is not None:
        return _relationship_count(raw, str(target["type"]))
    return None


def _nested(candidate: object, target: Mapping[str, object]) -> object | None:
    if not isinstance(candidate, Mapping) or not target:
        return candidate
    target_key = _target_key(target)
    if target_key in candidate:
        return candidate[target_key]
    current: object = candidate
    for key in (target.get("label"), target.get("type"), target.get("property")):
        if key is None:
            continue
        if not isinstance(current, Mapping) or key not in current:
            return None
        current = current[key]
    return current


def _label_count(raw: Mapping[str, object], label: str) -> object | None:
    schema = raw.get("schema", raw.get("graph_schema"))
    if not isinstance(schema, Mapping):
        return None
    labels = schema.get("labels")
    if not isinstance(labels, list):
        return None
    for item in labels:
        if isinstance(item, Mapping) and item.get("name") == label:
            return item.get("count")
    return None


def _relationship_count(raw: Mapping[str, object], rel_type: str) -> object | None:
    schema = raw.get("schema", raw.get("graph_schema"))
    if not isinstance(schema, Mapping):
        return None
    relationships = schema.get("relationship_types")
    if not isinstance(relationships, list):
        return None
    for item in relationships:
        if isinstance(item, Mapping) and item.get("name") == rel_type:
            return item.get("count")
    return None


def _property_coverage(values: list[object], target: Mapping[str, object]) -> object | None:
    if target.get("label") is not None:
        owner = "node"
        owner_name = target["label"]
    elif target.get("type") is not None:
        owner = "relationship"
        owner_name = target["type"]
    else:
        return None
    property_name = target.get("property")
    for item in values:
        if not isinstance(item, Mapping):
            continue
        if (
            item.get("owner") == owner
            and item.get("owner_name") == owner_name
            and item.get("property") == property_name
        ):
            return item.get("coverage")
    return None


def _target_key(target: Mapping[str, object]) -> str:
    return "|".join(f"{key}={target[key]}" for key in sorted(target))


def _baseline_value(
    value: object,
    *,
    evidence: tuple[EvidenceElement, ...] = (),
    partial: bool = False,
) -> BaselineValue:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise GraphCheckError(
            "engine.baseline_invalid",
            f"Baseline measurement must be numeric, got {value!r}.",
            "Regenerate the baseline with a compatible C4 profiler.",
        )
    numeric = float(value)
    if not math.isfinite(numeric) or numeric < 0:
        raise GraphCheckError(
            "engine.baseline_invalid",
            "Baseline measurement must be finite and non-negative.",
            "Regenerate the baseline after fixing the invalid measurement.",
        )
    return BaselineValue(value=numeric, evidence=evidence, partial=partial)
