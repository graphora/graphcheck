"""Regenerate current public schemas without changing archived results contracts."""

import argparse
import json
import logging
from pathlib import Path

from graphcheck.contracts.schemas import (
    check_combined_schema,
    check_envelope_schema,
    pack_metadata_schema,
    profile_schema,
    results_schema,
)

SCHEMAS_DIR = Path(__file__).resolve().parents[1] / "docs" / "schemas"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Check schemas without writing.")
    args = parser.parse_args()
    schemas = {
        "check.schema.json": check_combined_schema(),
        "check.envelope.schema.json": check_envelope_schema(),
        "pack.schema.json": pack_metadata_schema(),
        "profile.schema.json": profile_schema(),
        "results.schema.json": results_schema(),
    }
    stale = False
    for name, schema in schemas.items():
        path = SCHEMAS_DIR / name
        text = json.dumps(schema, indent=2, sort_keys=True) + "\n"
        if args.check:
            if not path.is_file() or path.read_text(encoding="utf-8") != text:
                logging.error("Schema is stale: %s; run tools/generate_schemas.py", path)
                stale = True
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
    return int(stale)


if __name__ == "__main__":
    raise SystemExit(main())
