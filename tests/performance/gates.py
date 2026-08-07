from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from pathlib import Path

from tests.performance.helpers import BenchmarkRecord


def load_reference_budgets(path: Path, reference: str) -> dict[str, dict[str, object]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1:
        raise ValueError(f"{path} must use performance budget schema version 1")
    try:
        selected = payload["references"][reference]
    except KeyError as exc:
        raise ValueError(f"performance reference {reference!r} is not defined in {path}") from exc
    if not isinstance(selected, dict) or not isinstance(selected.get("budgets"), dict):
        raise ValueError(f"performance reference {reference!r} must define budgets")
    return selected["budgets"]


def evaluate_record(
    record: BenchmarkRecord,
    policy: Mapping[str, object],
    *,
    gate: str = "timing",
    details: Mapping[str, object] | None = None,
) -> dict[str, object]:
    baseline = _number(policy, "baseline_median_ms")
    regression_pct = _number(policy, "median_regression_pct")
    limit = round(baseline * (1 + regression_pct / 100), 3)
    return {
        "gate": gate,
        "benchmark": record.benchmark,
        "mode": policy.get("mode", "required"),
        "reference_environment": policy.get("reference_environment"),
        "passed": record.median_ms <= limit,
        "observed": {
            "samples": record.samples,
            "median_ms": record.median_ms,
            "p95_ms": record.p95_ms,
            "maximum_ms": record.maximum_ms,
        },
        "baseline": {
            "median_ms": baseline,
            "p95_ms": policy.get("baseline_p95_ms"),
            "observation_runs": policy.get("observation_runs"),
            "observation_medians_ms": policy.get("observation_medians_ms"),
            "observation_p95_ms": policy.get("observation_p95_ms"),
        },
        "limit": {"median_ms": limit, "regression_pct": regression_pct},
        "metadata": {**record.as_dict(), **dict(details or {})},
    }


def assert_required_gates(results: Iterable[Mapping[str, object]]) -> None:
    failures = [
        dict(result)
        for result in results
        if result.get("mode") == "required" and result.get("passed") is not True
    ]
    if failures:
        raise AssertionError(json.dumps({"performance_regressions": failures}, indent=2))


def write_gate_results(results: Iterable[Mapping[str, object]], path: Path) -> Path:
    payload = list(results)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def assert_plan_gate(
    *,
    name: str,
    query: str,
    operators: list[dict[str, object]],
    server: str | None,
    cypher: str | None,
    required_any: Iterable[str] = (),
    forbidden: Iterable[str] = (),
) -> None:
    names = {str(operator["operator"]) for operator in operators}
    required = set(required_any)
    rejected = names & set(forbidden)
    if (required and names.isdisjoint(required)) or rejected:
        raise AssertionError(
            json.dumps(
                {
                    "performance_regressions": [
                        {
                            "gate": "query-plan",
                            "benchmark": name,
                            "passed": False,
                            "query": query,
                            "operators": operators,
                            "server": server,
                            "cypher": cypher,
                            "required_any": sorted(required),
                            "missing_required": sorted(required)
                            if names.isdisjoint(required)
                            else [],
                            "forbidden": sorted(set(forbidden)),
                            "present_forbidden": sorted(rejected),
                        }
                    ]
                },
                indent=2,
            )
        )


def _number(policy: Mapping[str, object], name: str) -> float:
    value = policy.get(name)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise ValueError(f"{name} must be a non-negative number")
    return float(value)
