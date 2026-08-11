from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, TypeAdapter

from graphcheck.contracts.results import SCHEMA_VERSION, Results

_JSON_VALUE = TypeAdapter(Any, config=ConfigDict(ser_json_bytes="base64"))


def json_compatible(value: object) -> Any:
    """Return the same JSON-compatible value shape used by results.json."""

    if isinstance(value, BaseModel):
        value = value.model_dump(by_alias=True, exclude_none=False)
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
    if isinstance(data, Results):
        # Pydantic models are mutable and model_copy(update=...) does not validate updates.
        # Rebuild from plain data so every public writer/renderer boundary rechecks the
        # semantic score, totals, exit-code, and suite invariants.
        data = data.model_dump(mode="python", by_alias=True, exclude_none=False)
    if isinstance(data, Path):
        data = data.read_text(encoding="utf-8")
    payload = json.loads(data) if isinstance(data, str) else data
    if isinstance(payload, dict) and payload.get("schema_version") == "1.0":
        payload = {**payload, "schema_version": SCHEMA_VERSION}
    return Results.model_validate(payload)


def results_json(results: Results | dict[str, Any]) -> str:
    _, rendered = validated_results_json(results)
    return rendered


def validated_results_json(results: Results | dict[str, Any]) -> tuple[Results, str]:
    """Validate once and return both the canonical model and serialized JSON."""

    model = load_results(results)
    payload = json_compatible(model)
    return model, json.dumps(payload, indent=2, sort_keys=True) + "\n"


def write_results(results: Results | dict[str, Any], path: Path) -> Path:
    path.write_text(results_json(results), encoding="utf-8")
    return path
