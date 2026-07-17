import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
from pydantic import TypeAdapter

from graphcheck.contracts.check import _SuiteFile
from graphcheck.contracts.results import Results
from graphcheck.packs import PACK_VERSION, REGISTRY
from graphcheck.packs.metadata import PackMetadata

SPECS_DIR = Path(__file__).resolve().parents[3] / "docs" / "specs"
JSON_SCHEMA_2020_12 = "https://json-schema.org/draft/2020-12/schema"
_FORMAT_CHECKER = FormatChecker()
_CHECK_SCHEMA_COMMENT = (
    "Portable structural contract. Consumers must additionally enforce the semantic "
    "invariants in SPEC-02: globally unique check ids, distinct label_cooccurrence "
    "labels, distinct rel_direction endpoint labels, and distinct temporal_sanity "
    "properties. GraphCheck enforces them in load_suite()."
)


def results_schema() -> dict:
    return Results.model_json_schema(by_alias=True)


def write_results_schema() -> Path:
    SPECS_DIR.mkdir(parents=True, exist_ok=True)
    path = SPECS_DIR / "results.schema.json"
    path.write_text(json.dumps(results_schema(), indent=2, sort_keys=True) + "\n")
    return path


def pack_metadata_schema() -> dict:
    schema = TypeAdapter(PackMetadata).json_schema()
    schema["$schema"] = JSON_SCHEMA_2020_12
    schema["x-pack-version"] = PACK_VERSION
    return schema


def validate_pack_metadata_schema(instance: object) -> None:
    """Validate pack metadata, including standard JSON Schema format assertions."""
    schema = pack_metadata_schema()
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(
        schema,
        format_checker=_FORMAT_CHECKER,
    )
    validator.validate(instance)


def write_pack_metadata_schema() -> Path:
    SPECS_DIR.mkdir(parents=True, exist_ok=True)
    path = SPECS_DIR / "pack.schema.json"
    path.write_text(json.dumps(pack_metadata_schema(), indent=2, sort_keys=True) + "\n")
    return path


def check_envelope_schema() -> dict:
    schema = _SuiteFile.model_json_schema(by_alias=True)
    schema["$schema"] = JSON_SCHEMA_2020_12
    schema["$comment"] = (
        "Frozen SPEC-02 envelope only; use check.schema.json for pack-owned `with` validation."
    )
    return schema


def check_combined_schema() -> dict:
    schema = _SuiteFile.model_json_schema(by_alias=True)
    schema["$schema"] = JSON_SCHEMA_2020_12
    schema["$comment"] = _CHECK_SCHEMA_COMMENT
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


def validate_check_schema(instance: object) -> None:
    """Validate the portable structural schema, including format assertions.

    Use ``load_suite`` for the additional semantic invariants defined by SPEC-02.
    """
    schema = check_combined_schema()
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(
        schema,
        format_checker=_FORMAT_CHECKER,
    )
    validator.validate(instance)


def write_check_schemas() -> None:
    SPECS_DIR.mkdir(parents=True, exist_ok=True)
    (SPECS_DIR / "check.envelope.schema.json").write_text(
        json.dumps(check_envelope_schema(), indent=2, sort_keys=True) + "\n"
    )
    (SPECS_DIR / "check.schema.json").write_text(
        json.dumps(check_combined_schema(), indent=2, sort_keys=True) + "\n"
    )
