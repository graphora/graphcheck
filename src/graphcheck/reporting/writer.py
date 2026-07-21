from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import jsonschema
from pydantic import BaseModel, ConfigDict, TypeAdapter

from graphcheck.contracts.results import Results
from graphcheck.contracts.schemas import results_schema

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
        return data
    if isinstance(data, Path):
        return Results.model_validate_json(data.read_text(encoding="utf-8"))
    if isinstance(data, str):
        return Results.model_validate_json(data)
    return Results.model_validate(data)


def results_json(results: Results | dict[str, Any]) -> str:
    model = load_results(results)
    payload = json.loads(model.model_dump_json(by_alias=True, exclude_none=False))
    jsonschema.validate(payload, results_schema())
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def write_results(results: Results | dict[str, Any], path: Path) -> Path:
    path.write_text(results_json(results), encoding="utf-8")
    return path
