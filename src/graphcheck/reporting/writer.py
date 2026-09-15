from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, TypeAdapter

from graphcheck.contracts.results import (
    DEPRECATED_SCHEMA_VERSIONS,
    SCHEMA_REMOVAL_RELEASE,
    SCHEMA_VERSION,
    Results,
)

_JSON_VALUE = TypeAdapter(Any, config=ConfigDict(ser_json_bytes="base64"))


def json_compatible(value: object) -> Any:
    """Return the same JSON-compatible value shape used by results.json."""

    historical_schema_version = (
        value._historical_schema_version if isinstance(value, Results) else None
    )
    if isinstance(value, BaseModel):
        value = value.model_dump(by_alias=True, exclude_none=False)
        if historical_schema_version is not None:
            value["schema_version"] = historical_schema_version
            run = value["run"]
            for key in ("previous_run_id", "baseline_ref", "config_hash"):
                run.pop(key, None)
            run["status"] = run.pop("run_status")
            if historical_schema_version in {"1.0", "1.1"}:
                target = run["target"]
                if target is not None:
                    target.pop("labels")
                    target.pop("relationship_types")
                    for field in ("nodes", "relationships"):
                        if historical_schema_version == "1.0" or target[field] is None:
                            target.pop(field)  # Counts were first introduced in schema 1.1.
    if isinstance(value, Mapping):
        return {str(key): json_compatible(item) for key, item in value.items()}
    if isinstance(value, (set, frozenset)):
        items = [json_compatible(item) for item in value]
        return sorted(
            items,
            key=lambda item: json.dumps(
                item,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ),
        )
    if isinstance(value, (list, tuple)):
        return [json_compatible(item) for item in value]
    return _JSON_VALUE.dump_python(value, mode="json")


def load_results(data: Results | dict[str, Any] | str | Path) -> Results:
    """Normalize supported artifacts, warning once per legacy read, not model revalidation."""

    historical_schema_version = None
    if isinstance(data, Results):
        # Pydantic models are mutable and model_copy(update=...) does not validate updates.
        # Rebuild from plain data so every public writer/renderer boundary rechecks the
        # semantic score, totals, exit-code, and suite invariants.
        historical_schema_version = data._historical_schema_version
        data = data.model_dump(mode="python", by_alias=True, exclude_none=False)
    if isinstance(data, Path):
        data = data.read_text(encoding="utf-8")
    payload = json.loads(data) if isinstance(data, str) else data
    legacy_read = (
        isinstance(payload, dict) and payload.get("schema_version") in DEPRECATED_SCHEMA_VERSIONS
    )
    if legacy_read:
        from graphcheck.contracts.historical_results import validate_historical_results

        historical_schema_version = str(payload["schema_version"])
        validate_historical_results(payload, historical_schema_version)
        run = payload.get("run")
        if isinstance(run, dict):
            run = {**run, "run_status": run.get("status")}
            run.pop("status", None)
            target = run.get("target")
            if historical_schema_version in {"1.0", "1.1"} and isinstance(target, dict):
                target = {**target}
                target.setdefault("labels", None)
                target.setdefault("relationship_types", None)
                run["target"] = target
        payload = {**payload, "schema_version": SCHEMA_VERSION, "run": run}
    context = (
        {"historical_schema_version": historical_schema_version}
        if historical_schema_version is not None
        else None
    )
    model = Results.model_validate(payload, context=context)
    model._historical_schema_version = historical_schema_version
    if legacy_read:
        print(
            f"results.schema_deprecated: Reading results schema {historical_schema_version} is "
            f"deprecated; removal is planned for GraphCheck {SCHEMA_REMOVAL_RELEASE}, postponed "
            "while required by the current/previous-schema guarantee. "
            "Use the transformer in docs/reference/artifact-compatibility.md to migrate to 2.0.",
            file=sys.stderr,
        )
    return model


def results_json(results: Results | dict[str, Any]) -> str:
    _, rendered = validated_results_json(results)
    return rendered


def validated_results_json(results: Results | dict[str, Any]) -> tuple[Results, str]:
    """Validate once and return both the canonical model and serialized JSON."""

    model = load_results(results)
    if model.run.redaction.policy.value == "mask" or model.run.redaction.applied:
        from graphcheck.reporting.redaction import verify_redacted_results

        verify_redacted_results(model)
    payload = json_compatible(model)
    return model, json.dumps(payload, indent=2, sort_keys=True) + "\n"


def write_results(results: Results | dict[str, Any], path: Path) -> Path:
    path.write_text(results_json(results), encoding="utf-8")
    return path
