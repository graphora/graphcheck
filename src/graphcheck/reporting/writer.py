from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema

from graphcheck.contracts.results import Results
from graphcheck.contracts.schemas import results_schema


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
