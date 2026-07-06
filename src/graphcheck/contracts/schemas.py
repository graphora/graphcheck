import json
from pathlib import Path

from graphcheck.contracts.check import _SuiteFile
from graphcheck.contracts.results import Results
from graphcheck.packs import PACK_VERSION, REGISTRY

SPECS_DIR = Path(__file__).resolve().parents[3] / "docs" / "specs"


def results_schema() -> dict:
    return Results.model_json_schema(by_alias=True)


def write_results_schema() -> Path:
    SPECS_DIR.mkdir(parents=True, exist_ok=True)
    path = SPECS_DIR / "results.schema.json"
    path.write_text(json.dumps(results_schema(), indent=2, sort_keys=True) + "\n")
    return path


def check_envelope_schema() -> dict:
    return _SuiteFile.model_json_schema(by_alias=True)


def check_combined_schema() -> dict:
    schema = _SuiteFile.model_json_schema(by_alias=True)
    schema["x-pack-version"] = PACK_VERSION
    defs = schema["$defs"]
    # Constrain each conformance item's `with` by its `check`: move the auto-generated
    # ConformanceCheck def aside, then redefine its $ref target as an allOf of the base
    # plus a oneOf over the registry. The conformance array already $refs ConformanceCheck,
    # so it picks up the constraint without touching the array schema.
    defs["ConformanceCheckBase"] = defs["ConformanceCheck"]
    branches = []
    for name, model in sorted(REGISTRY.items()):
        with_schema = model.model_json_schema()
        if "$defs" in with_schema:
            # A nested pack model emits internal #/$defs/... refs that dangle once inlined here.
            # v0 pack `with` models must be flat/ref-free; hoisting $defs is C3's job when needed.
            raise ValueError(
                f"pack `with` model {name!r} emits $defs; v0 requires flat, ref-free pack "
                f"schemas so they can be inlined. Hoist its $defs before registering it."
            )
        branches.append(
            {
                "properties": {"check": {"const": name}, "with": with_schema},
                "required": ["check", "with"],
            }
        )
    defs["WithByCheck"] = {"oneOf": branches}
    defs["ConformanceCheck"] = {
        "allOf": [
            {"$ref": "#/$defs/ConformanceCheckBase"},
            {"$ref": "#/$defs/WithByCheck"},
        ]
    }
    return schema


def write_check_schemas() -> None:
    SPECS_DIR.mkdir(parents=True, exist_ok=True)
    (SPECS_DIR / "check.envelope.schema.json").write_text(
        json.dumps(check_envelope_schema(), indent=2, sort_keys=True) + "\n"
    )
    (SPECS_DIR / "check.schema.json").write_text(
        json.dumps(check_combined_schema(), indent=2, sort_keys=True) + "\n"
    )
