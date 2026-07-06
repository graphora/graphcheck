import json
from pathlib import Path

from graphcheck.contracts.results import Results

SPECS_DIR = Path(__file__).resolve().parents[3] / "docs" / "specs"


def results_schema() -> dict:
    return Results.model_json_schema(by_alias=True)


def write_results_schema() -> Path:
    SPECS_DIR.mkdir(parents=True, exist_ok=True)
    path = SPECS_DIR / "results.schema.json"
    path.write_text(json.dumps(results_schema(), indent=2, sort_keys=True) + "\n")
    return path
