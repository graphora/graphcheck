# Artifact compatibility

Schema versions describe saved data, independently of GraphCheck CLI versions. For example,
`schema_version: "2.0"` identifies the results format, while `run.graphcheck_version` identifies
the producer. Updating the CLI does not necessarily change the schema, and migrating an artifact
does not change its recorded producer version.

## Compatibility promise

- The current and previous published schema revisions are always readable. For `results.json`,
  these are currently 2.0 and 1.2. A CLI release alone cannot end that guarantee.
- Changes are additive by default: new optional fields have defaults when absent. Renaming or
  removing a field, making an optional field required, or changing its meaning needs a breaking
  schema revision. Strict readers may reject unfamiliar fields and the promise is that newer readers
  can read supported older artifacts, not that older binaries understand every future artifact.
- A deprecation warning must ship in at least one released CLI version before a reader is removed.
  It identifies the schema, a stable diagnostic code, and the planned CLI removal release. A date
  or an unreleased changelog entry alone does not satisfy that notice period.
- The [JSON Schemas in `docs/schemas/`](../schemas/) are the public structural contract. The
  [current results schema](../schemas/results.schema.json) describes newly written results;
  archived schemas below describe historical input. Model and schema changes must ship together.
- Every breaking revision ships either a migration command or a documented, tested transformer.
  Historical fixtures stay committed and are exercised through the public loader; supported
  inputs must normalize to the current model without losing recorded findings.

GraphCheck also validates semantics beyond JSON Schema: totals tally the checks, run and suite
scores match their weighted outcomes, exit codes match the run's outcomes, check identities and
suite IDs are unique, and verdict-specific evidence/error fields are consistent. Run timestamps
must be UTC and ordered. Use `graphcheck.reporting.writer.load_results()` to apply those checks
when reading supported artifacts.

## Results support and deprecation

| Schema | Recorded shape | Reader status |
| --- | --- | --- |
| [1.0](../schemas/results-1.0.schema.json) | Original `run.status`; node/relationship evidence; no target inventory | Readable, deprecated |
| [1.1](../schemas/results-1.1.schema.json) | Adds `aggregate` measurement-scope evidence; no target inventory | Readable, deprecated |
| [1.2](../schemas/results-1.2.schema.json) | Adds `run.target.labels` and `relationship_types` | Readable, deprecated; protected as the previous schema |
| [2.0](../schemas/results.schema.json) | Renames `run.status` to `run.run_status`; optional lineage fields default to null | Current, readable without a warning |

Reading a valid 1.x artifact produces exactly one stderr line with diagnostic code
`results.schema_deprecated`, the schema found, and planned removal release **GraphCheck 0.5.0**.
The warning ships in the next CLI release before 0.5.0. Removal is postponed if that would leave
less than one released version of notice, or if the schema is still current or previous.
In particular, **1.2 cannot be removed while 2.0 remains the current schema**. Release notes must
announce any revised removal release and the warning must be updated to match. Nothing is
removed by this change; 1.0, 1.1, 1.2, and 2.0 remain readable.

The warning is emitted for each artifact read from a path, JSON string, or dictionary. Passing
the resulting `Results` model through another writer or renderer revalidates it without another
warning. Reading a separate legacy artifact emits its own warning. Schema 2.0 reads are silent.
Unknown schema versions and malformed artifacts fail validation rather than being guessed at.
Before normalization, legacy input is validated against its declared archived JSON Schema,
including in installed wheels. Fields and enum values introduced by later schemas are rejected
even when the current model understands them.

`load_results()` returns the normalized 2.0 model: `run_status` replaces `status`, missing lineage
(`previous_run_id`, `baseline_ref`, `config_hash`) becomes null, and unrecorded counts/inventory
remain null. It does not modify the source. Normal exports through `results_json()` or
`write_results()` preserve a loaded artifact's historical schema; use the transformer below to
explicitly upgrade it.
Redaction through `redact_results()` or `graphcheck redact` also preserves the source schema,
including unknown inventory in schemas 1.0 and 1.1.
Schema 1.0 exports always omit graph counts, including counts subsequently set on a mutable
model. Schema 1.1 exports preserve recorded counts and omit unknown (null) counts.
Before serialization, the final historical payload is validated against its archived schema.
Mutations that cannot be represented in that schema, such as aggregate evidence in 1.0, raise a
validation error before any output file is written.

## Migrate results to 2.0

We provide a documented transformer because the format conversion is small and can reuse the
existing loader and validators. It also makes the missing inventory in 1.0/1.1 explicit before
writing an artifact. The exact Python block below is tested against the same four committed
historical fixtures used by the loader tests.

For 1.2 and 2.0, no extra input is needed. For a non-null 1.0/1.1 target, supply both inventory
arrays from a trusted snapshot of the graph **at the time of the original run**. For example,
an `inventory.json` file might contain:

```json
{"labels": ["Account", "Customer"], "relationship_types": ["CONTROLS", "OWNS"]}
```

The arrays must be sorted by Unicode code point and contain no duplicates. `[]` means the
inventory was measured and was empty; null means it was not recorded. Never substitute empty
arrays for unknown inventory or infer it from checks, evidence, fingerprints, or today's graph.
If no trustworthy historical inventory exists, retain the original artifact and use the legacy
reader during its support window. A fully valid standalone 2.0 result requires that information.
Failed runs with `target: null` need no inventory. Missing graph counts and lineage remain null.

Save the following as `migrate_results.py` and run it in an environment with GraphCheck installed.
It preserves the original file and refuses to overwrite an existing destination. Validation
finishes before the destination is opened. Keep the original until the converted copy is verified;
if a filesystem write fails, discard the incomplete destination and retry.

```python
import json
import sys
from pathlib import Path

from graphcheck.contracts.results import Results
from graphcheck.reporting.writer import load_results


def migrate(source: Path, destination: Path, inventory: dict | None = None) -> None:
    payload = load_results(source).model_dump(mode="json", by_alias=True)
    target = payload["run"]["target"]
    if target is not None:
        for field in ("labels", "relationship_types"):
            if target[field] is None:
                if inventory is None or field not in inventory:
                    raise ValueError(f"Supply historical inventory.{field}; unknown is not empty")
                target[field] = inventory[field]
    rendered = Results.model_validate(payload).model_dump_json(by_alias=True, indent=2) + "\n"
    with destination.open("x", encoding="utf-8") as output:
        output.write(rendered)


if __name__ == "__main__":
    if len(sys.argv) not in (3, 4):
        raise SystemExit("Usage: python migrate_results.py INPUT OUTPUT [INVENTORY.json]")
    inventory = (
        json.loads(Path(sys.argv[3]).read_text(encoding="utf-8")) if len(sys.argv) == 4 else None
    )
    migrate(Path(sys.argv[1]), Path(sys.argv[2]), inventory)
```

For schema 1.2, or an already-current 2.0 file:

```console
python migrate_results.py results.json results-2.0.json
```

For schemas 1.0 and 1.1 with a recorded target:

```console
python migrate_results.py results.json results-2.0.json inventory.json
```

The converted file declares schema 2.0 and loads without a deprecation warning. Its checks,
evidence (including aggregate pointers), scores, totals, run identity, producer version, and
timestamps are preserved. Migration adds no lineage or graph counts that the input did not
record. Transforming the converted file again to another destination gives the same JSON data.
This transformer handles standalone `results.json` files; it does not rebuild report-history
indexes or related HTML/summary artifacts.
