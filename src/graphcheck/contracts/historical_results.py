"""Validate legacy input against the archived contract before upgrading its shape."""

import json
from functools import cache
from importlib.resources import files
from pathlib import Path

from jsonschema import Draft202012Validator
from pydantic import ValidationError


@cache
def _validator(version: str) -> Draft202012Validator:
    name = f"results-{version}.schema.json"
    schema = files("graphcheck").joinpath("_schemas", name)
    if not schema.is_file():  # Source checkout; wheels bundle the same archived files.
        schema = Path(__file__).resolve().parents[3] / "docs" / "schemas" / name
    return Draft202012Validator(json.loads(schema.read_text(encoding="utf-8")))


def validate_historical_results(payload: dict, version: str) -> None:
    if error := next(_validator(version).iter_errors(payload), None):
        # Preserve the loader's Pydantic/ValueError contract for CLI, history, and MCP callers.
        raise ValidationError.from_exception_data(
            "Results",
            [
                {
                    "type": "value_error",
                    "loc": tuple(error.absolute_path),
                    "input": error.instance,
                    "ctx": {"error": ValueError(f"results schema {version}: {error.message}")},
                }
            ],
        )
