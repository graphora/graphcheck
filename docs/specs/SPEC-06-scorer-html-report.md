# SPEC-06 - Scorer + HTML Report

*Draft for C5.* This component writes `results.json` and renders a self-contained
HTML report from that file. `results.json` remains the single contract: the HTML
report, MCP responses, and any future cloud surface must consume SPEC-01 results
rather than bypassing them.

## Scope

Implemented in this slice:

- SPEC-01 `results.json` writer.
- Offline HTML report renderer.
- Report history listing, selection, comparison, retention, and diagnostic filtering.
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

All inputs are normalized through the SPEC-01 source-of-truth model. Historical
schema 1.0 artifacts are upgraded in memory by changing their version marker to
the current 1.1 contract before validation; this compatibility read does not
rewrite the source file. Newly written artifacts always use schema 1.1.

Current inputs are validated through:

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

Evidence elements carry node/relationship IDs with labels/types, or an aggregate
measurement-scope ID for aggregate drift findings. Property values must not be
placed in evidence pointers.

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
- evidence message and node/relationship or aggregate-scope IDs.

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

History operations load and validate each run's `results.json`, including the
schema 1.0 compatibility read described above. If `runs/latest` duplicates a
historical run id, it appears only once in history. History is ordered by
`run.finished_at`, newest first.

### Open and Select

`graphcheck report --open [<id>]`:

1. discovers the project root and configured artifacts directory,
2. when `<id>` is omitted, finds `report.html` files below `<artifacts>/runs/`
   and selects the most recently modified report,
3. when `<id>` is present, resolves it against `results.json.run.id` or the run
   directory name and selects that run's `report.html`, and
4. opens its local file URI in the default browser.

If no report exists or the browser cannot be launched, the command exits non-zero
with an actionable error.

A missing id or HTML artifact is an error that points the user to
`graphcheck report --list`. A positional report ID without `--open` is a usage
error. The former `--run <id>` selector is not part of the command surface.

Running `graphcheck report` without an action prints a concise command guide;
`graphcheck report --help` provides argument and option details.

### List

`graphcheck report --list` prints every unique historical run with:

- run id,
- completion timestamp,
- run status, and
- score or `n/a`.

### Compare

`graphcheck report --compare <run1> <run2>` compares two existing result artifacts
in the stated direction. It reports status and score changes, outcome regressions
and improvements for matching `(suite_id, check_id)` identities, other verdict
changes, and added or removed checks.

A regression is a move toward a worse outcome in this order:

```text
pass < skipped < warn < errored(warn) < fail/errored(error)
```

The comparison reads artifacts only and does not connect to Neo4j.

### Retention

`graphcheck report --prune --keep <count>` retains the newest `<count>` historical
run directories and removes older ones. `<count>` must be at least 1. The
`runs/latest` convenience artifact is always preserved and is not counted against
the retention limit. Directories that do not contain a valid immediate
`results.json` run artifact are not removed.

### Diagnostic Report

`graphcheck report --failures-only` reads the newest result and writes
`report.failures.html` beside it. The diagnostic contains only `fail`, `warn`, and
`errored` checks while preserving the original run summary; it does not modify
`results.json` or `report.html`.

Combining `--failures-only` with `--open` generates the diagnostic for the newest
run and opens it. `--open <id> --failures-only` selects a historical run, generates
its diagnostic, and opens the generated file.

`--list`, `--compare`, and `--prune` are standalone actions. Invalid combinations
exit 2 without changing artifacts.

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
- `report --open <id>` selects a historical report and a bare ID is rejected,
- schema 1.0 historical artifacts load without being rewritten,
- the configured artifacts directory is honored,
- missing reports and browser-launch failures exit non-zero,
- report history is ordered and de-duplicates `runs/latest`,
- a historical run can be selected and opened by id,
- report comparisons classify outcome changes and show score movement,
- pruning preserves the requested newest runs, `runs/latest`, and unknown directories,
- diagnostic reports contain failures, warnings, and errors but omit passing checks.

## Deferred Work

The following require C1, the fixture graph, or additional tooling:

- Convert raw C1 engine output into `Results`.
- Guarantee every real run writes `results.json`.
- Compute graph fingerprint, DB version, suite SHA, and timestamps at runtime.
- Full pipeline fixture graph coverage.
- Browser-level offline asset/network test.
- Dedicated scorer API.
- MCP/C7 consumption tests.
