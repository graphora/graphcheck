# SPEC-06 - Scorer + HTML Report

*Draft for C5.* This component writes `results.json` and renders a self-contained
HTML report from that file. `results.json` remains the single contract: the HTML
report, MCP responses, and any future cloud surface must consume SPEC-01 results
rather than bypassing them.

## Scope

Implemented in this slice:

- SPEC-01 `results.json` writer.
- Offline HTML report renderer.
- `graphcheck report --open` for opening the most recent HTML report.
- Regression tests over the existing SPEC-01 fixture results.

Deferred:

- Scorer module.
- C1 engine-output adapter.
- Fixture graph full-pipeline coverage.
- Browser-level network interception test.

## Results Writer

The writer lives in `src/graphcheck/reporting/writer.py`.

### Inputs

The writer accepts:

- a `Results` model,
- a plain `dict`,
- a JSON string,
- or a `Path` to a `results.json` file.

All inputs are normalized through the SPEC-01 source-of-truth model:

```python
Results.model_validate(...)
Results.model_validate_json(...)
```

### Validation

Before writing, the writer validates twice:

1. Pydantic validation through `Results`, which enforces SPEC-01 semantic
   invariants such as evidence presence, verdict field presence, status shape,
   totals, score consistency, exit code, suite identity, and partial-run rules.
2. Structural JSON Schema validation through `results_schema()`.

The writer serializes with all frozen nullable keys present. It uses
`exclude_none=False` and deterministic JSON formatting.

### Responsibilities

The writer is responsible for refusing malformed results. It does not compute
engine metadata itself.

It preserves:

- run status and `partial_reason`,
- `pass`, `fail`, `warn`, `errored`, and `skipped` verdicts as distinct states,
- graph target metadata including fingerprint and database version,
- suite `source_sha`,
- run timestamps,
- check `compiled_query`,
- check `params`, `measured`, `expected`, and `estimate`,
- evidence pointers for failing/warning checks,
- structured run/check errors.

### Evidence Pointers

SPEC-01 requires failing and warning checks to carry evidence. The model also
requires `evidence.elements` to contain at least one pointer, so a fail/warn
cannot be written with an empty evidence list.

Evidence elements carry node/relationship IDs and labels/types only. Property
values must not be placed in evidence pointers.

## HTML Renderer

The renderer lives in `src/graphcheck/reporting/html.py`.

### Inputs

The renderer accepts the same input shapes as the writer and normalizes them
through `load_results()`. It renders from a validated SPEC-01 `Results` object
only.

### Output

The renderer emits one self-contained HTML document:

- inline CSS,
- no JavaScript,
- no CDN,
- no external fonts,
- no external images,
- no external links or asset references.

The current automated offline check is static: tests assert the rendered HTML
contains no `http://`, `https://`, `<script`, `src="`, or `href="` references.
A browser-level network-disabled test is deferred.

### Report Contents

The report shows:

- run id,
- run status,
- top-level score or `n/a`,
- target database/version/fingerprint when available,
- run error banner for failed runs,
- partial-run banner when `partial_reason` is present,
- run timestamps,
- GraphCheck version,
- pack version,
- total counts,
- per-suite score/totals/source SHA,
- checks sorted failures-first,
- compiled Cypher when present,
- expected and measured values,
- estimate details when present,
- check errors when present,
- evidence message and node/relationship IDs.

### Ordering

Checks are rendered in this order:

1. `fail`
2. `warn`
3. `errored`
4. `skipped`
5. `pass`

Within the same verdict, checks sort by severity, suite id, then check id.

## CLI Command Boundary

`graphcheck report` operates on report artifacts that already exist. It does not
connect to Neo4j or create new run data.

`graphcheck report --open`:

1. discovers the project root and configured artifacts directory,
2. finds `report.html` files below `<artifacts>/runs/`,
3. selects the most recently modified report, and
4. opens its local file URI in the default browser.

If no report exists or the browser cannot be launched, the command exits non-zero
with an actionable error.

## Scorer

TODO.

SPEC-01 already defines the scoring contract:

```text
round(100 * sum(weight(pass)) / sum(weight(pass|fail|warn|errored)))
```

with hard-coded weights:

```text
error = 3
warn = 1
```

The current `Results` model validates score consistency, totals, per-suite score,
and exit code. C5 does not yet expose a separate scorer module. When added, it
must reuse SPEC-01 rules rather than introducing a second scoring contract.

## Tests

Reporting tests live in `tests/test_reporting.py`; report-command tests live in
`tests/test_cli.py`.

They use the existing SPEC-01 fixtures:

- `results.complete.json`
- `results.partial.json`
- `results.generated-only.json`
- `results.failed.json`

The tests assert:

- writer round-trips all existing fixtures,
- writer output validates against `results_schema()`,
- malformed fail-without-evidence is rejected,
- renderer output is self-contained,
- failures render before warnings and passes,
- compiled Cypher is visible,
- evidence IDs are visible,
- failed-run errors are visible,
- `report --open` selects the newest HTML report,
- the configured artifacts directory is honored,
- missing reports and browser-launch failures exit non-zero.

## Deferred Work

The following require C1, the fixture graph, or additional tooling:

- Convert raw C1 engine output into `Results`.
- Guarantee every real run writes `results.json`.
- Compute graph fingerprint, DB version, suite SHA, and timestamps at runtime.
- Full pipeline fixture graph coverage.
- Browser-level offline asset/network test.
- Dedicated scorer API.
- MCP/C7 consumption tests.
