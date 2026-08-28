# SPEC-06 - Scorer + HTML Report

*Draft for C5.* This component writes `results.json` and renders a self-contained
HTML report from that file. `results.json` remains the single contract: the HTML
report, MCP responses, and any future cloud surface must consume SPEC-01 results
rather than bypassing them.

## Scope

Implemented in this slice:

- SPEC-01 `results.json` writer.
- Deterministic severity-weighted scorer.
- Offline HTML report renderer.
- Report history listing, selection, comparison, retention, and diagnostic filtering.
- Regression tests over the existing SPEC-01 fixture results.

Deferred:

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
schema 1.0 and 1.1 artifacts are upgraded in memory to the current 1.2 contract.
For a non-null historical target, the compatibility loader injects `labels:null`
and `relationship_types:null` to mean that the older schema did not record the
inventory. This compatibility read does not rewrite the source file. New runs
always use schema 1.2 and populate both fields with sorted, unique arrays; `[]`
means the probe completed and found no tokens.

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
- canonical target label and relationship-type inventory, including the distinction between a
  probed empty array and historical not-recorded null,
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
- inline JavaScript for report-local interactions,
- no CDN,
- no external fonts,
- no external images,
- no external links or asset references, and
- no runtime network API usage.

The current automated offline check is static: tests assert the rendered HTML
contains exactly one inline script, contains the expected interaction functions,
contains no `http://`, `https://`, `src="`, or `href="` references, and does not
use browser network APIs such as `fetch`, `XMLHttpRequest`, `WebSocket`, or
`EventSource`. A browser-level network-disabled test is deferred.

### Report Contents

The report shows:

- a navbar lifecycle summary headed by `Run Complete.`, `Partial Run.`, or `Run Failed.`, with the
  former banner color retained in a status pill, beside deterministic result language shared with
  the CLI: fully evaluated all-pass runs say `No failures. All N selected checks passed.`, empty or
  all-skipped runs say no checks were selected/evaluated, and incomplete clean runs state both the
  evaluated count and incomplete coverage; failures, warnings, and execution errors retain their
  exact stored counts and never use clean language,
- a `Troubleshoot.` action for failed runs that opens an offline `Troubleshooting Steps` dialog with
  the full stored problem and remediation steps; the overview does not duplicate that diagnostic,
- a concise header for `neo4j.credential_not_read_only` while its full role detail remains in
  the troubleshooting dialog,
- target database, version, edition, node count, and relationship count when available,
- a compact label and relationship-type summary sourced directly from `run.target`, with the exact
  names available in an offline disclosure; a probed empty inventory displays zero while migrated
  pre-1.2 null displays `Inventory not recorded`,
- GraphCheck version,
- pack version,
- per-suite executed/selected counts,
- per-suite outcome badges that distinguish failed, warning, errored, and skipped
  checks, use singular `WARNING` for a count of one, and show skipped counts in
  grey rather than treating them as failures,
- each suite's `SCORE: <value>` badge, or `SCORE: N/A` when that suite has no
  calculated score, always as the rightmost badge in its status card,
- a colored status marker for every rendered check,
- a permanent `Not Evaluated` section after the suite overview that distinguishes failed-before-
  evaluation, empty selection, complete evaluation, partial evaluation, and all-skipped runs,
- every skipped check's name as a compact control that opens and highlights its full Checks
  Explorer entry; the first five are visible and any remainder stays in the offline document
  behind a native disclosure,
- the stored `run.partial_reason` once as a coverage note when present, plus the exact stored suite
  and tag selection boundary and a permanent statement that unconfigured graph behavior was not
  evaluated,
- a complete selected-check ledger containing every `results.checks` identity exactly once, with
  the persisted verdict, `Not evaluated` state on skipped checks, pattern, expected value, and all
  available measurement, estimate, query, evidence, or structured-error detail; severity remains
  available in `results.json` but is not repeated in the explorer,
- a visible `Reason` block on every skipped card with its generic shared explanation but without
  the internal raw code; the renderer does not infer a more specific cause from run-level context,
- a combined right-hand panel with accessible `Checks Explorer` and `Next Steps` heading tabs; the
  Checks Explorer tab owns the complete ledger and the Next Steps tab always contains
  exactly the same two generic practices—adding competency checks and tracking drift over time—
  plus a statement that they are not recommendations derived from the run,
- checks sorted failures-first,
- report-history timestamps converted from stored UTC to the browser's local timezone and shown as
  `yyyy-mm-dd at hh:mm:ss`,
- compiled Cypher when present,
- expected and measured values,
- estimate details when present,
- check errors when present,
- evidence message and node/relationship or aggregate-scope IDs.

The target summary does not render property keys or property-coverage percentages and does not
derive recommendations from inventory. It never reconstructs labels or relationship types from a
fingerprint, checks, evidence, or baseline data.

The embedded script reveals the combined panel, navigates from suite status markers to checks,
filters checks by verdict or search text, provides an `Issues` union filter for `fail`, `warn`, and
`errored`, states when the selected verdict category is empty, focuses the `Not Evaluated` section
from partial-run navigation, toggles check details, and switches the inline CSS theme. `See issues`
opens Checks/Issues, while `Explore checks` opens Checks/All. The tab interface exposes the required
tablist, tab, and tabpanel relationships, uses roving tab stops, and supports Left/Right Arrow and
Home/End activation. The heading tabs are the sole navigation between these views. Switching tabs
within one report preserves the search, verdict filter, expanded details, and Checks scroll
position. Soft navigation atomically replaces the combined panel, resets it to Checks, clears
detail and scroll state from the prior run, retains a valid persisted filter preference, and keeps
the panel open when it was already open. Event handlers are registered with `addEventListener`;
check identities are read from escaped data attributes and matched directly rather than
interpolated into JavaScript or CSS selectors. These report-local interactions operate only on the
already-rendered document and do not load or transmit data.

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

`graphcheck run` names each report `<target-graph>_<YYYYMMDDTHHMMSSZ>`, publishes every
completed artifact below the correspondingly named `runs/<report-name>/` directory, and
then refreshes the consistently staged `runs/latest` convenience copy. This is
the history consumed by the commands below.

Its final summary prints the shared result sentence without a separate aggregate coverage line.
When checks were skipped, the Result line introduces a concise borderless table with Suite, Check,
and Reason columns. Suite names and the Check cell's human name are italicized, the stable check id
remains visible, and Reason contains the persisted code and shared generic explanation. A blank
line separates the table from the artifact path. Passing checks are not repeated after the
progress display. The final exit-code line uses green for 0, red for 1 and 3, and yellow for 2.
Borderless table header rules use a continuous `─` line, and interactive progress uses a green `━`
completed segment followed by a grey `─` remaining segment rather than ASCII equals and dashes.
Every run includes a borderless `Score breakdown by check suite:` table. The table shows suite
score, evaluated/selected coverage, and passed, failed, warning, errored, and skipped outcome
columns fixed to their header widths for compact terminals. Scores use green for 100, yellow for 50–99,
and red for 0–49. Check coverage is green when evaluated equals selected and yellow otherwise;
outcome colors apply only to non-zero values while headers remain white. Target metadata appears
before interactive progress. Suite names are italicized, and incomplete coverage names every suite
containing a skipped check in sorted order. Results and report paths collapse to one saved-directory
line, and blank lines separate the lifecycle, score, result/artifact, and exit code blocks. One final
blank line follows the exit-code line.

History operations load and validate each run's `results.json`, including the
schema 1.0/1.1 compatibility read described above. If `runs/latest` duplicates a
historical run id, it appears only once in history. History is ordered
chronologically by the validated UTC `run.finished_at`, newest first; ordering
never compares timestamp strings lexically.

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
- each named suite score as `suite-id=value`, using `n/a` for a suite with no
  calculated score or a run with no suites. The overall machine score is not
  shown in this human-facing view.

### Compare

`graphcheck report --compare <run1> <run2>` compares two existing result artifacts
in the stated direction. It reports status and per-suite score changes across the
union of suite ids, outcome regressions and improvements for matching
`(suite_id, check_id)` identities, other verdict changes, and added or removed
checks. It does not substitute the overall machine score for the suite scores
shown by the run command and HTML report.

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

The scorer lives in `src/graphcheck/scoring.py`. It is the single implementation
used by the engine and the `Results` consistency validator. The renderer consumes
the validated per-suite scores stored in `results.suites[].score`. The scorer
computes the SPEC-01 contract:

```text
round(100 * sum(weight(pass)) / sum(weight(pass|fail|warn|errored)))
```

with hard-coded weights:

```text
error = 3
warn = 1
```

For each selected check:

| Verdict | Possible weight | Earned weight |
| --- | ---: | ---: |
| `pass` | severity weight | severity weight |
| `fail` | severity weight | 0 |
| `warn` | severity weight | 0 |
| `errored` | severity weight | 0 |
| `skipped` | excluded | excluded |

An empty or all-skipped input has a `null` score.

### Determinism

Scoring uses integer arithmetic only. The exact rational percentage is rounded
half-to-even without an intermediate floating-point value. Check order does not
affect the calculation, weights are immutable, duplicate check identities are
rejected by `Results`, and invalid severities or verdict/execution combinations
fail loudly.

### Per-suite Breakdown

Each suite is scored from its own member checks with the same algorithm. The
overall score is computed directly from all checks; it is never an average of
rounded suite scores. This preserves check-level weighting and prevents a tiny
suite from receiving the same influence as a large suite.

The `Results` model validates the stored overall and per-suite score values against
fresh calculations. The HTML report presents each independently calculated suite
score as the rightmost badge in that suite's status card. The overall
`results.score` remains part of the machine-readable SPEC-01 contract but is not
repeated in Graph Health Overview. The report does not expose earned/possible
weights. Users drill into the failure-first check list to identify issues.

### Point Deduction Calculation

The scorer can attribute `100 - score` integer points to individual `fail`,
`warn`, and `errored` tests. Deductions are proportional to the same locked
severity weights used by the scorer. Integer remainders are assigned by largest
remainder, with suite id and check id as stable tie-breakers. Therefore:

- the deduction rows always sum exactly to `100 - score`,
- error-severity issues dock three times the points of warning-severity issues
  before integer rounding,
- input order cannot change a test's deduction, and
- passing and skipped tests dock no points.

Point deductions and earned/possible weight arithmetic are not presented in the
current HTML report.

## Tests

Reporting tests live in `tests/unit/reporting/test_reporting.py`; report-command tests live in
`tests/unit/cli/test_cli.py`.

The 1.2 test matrix uses these SPEC-01 fixtures:

- `results.clean.json`
- `results.complete.json`
- `results.partial.json`
- `results.generated-only.json`
- `results.failed.json`

The tests assert:

- writer round-trips all existing fixtures,
- writer output validates against `results_schema()`,
- malformed fail-without-evidence is rejected,
- renderer output is self-contained,
- rendered check-card identities and verdicts match the selected checks exactly for clean,
  findings, partial, and generated-only fixtures,
- evaluated state and all three persisted skip reasons render with shared human language,
- the `Issues` filter and `See issues` action target fail, warn, and errored cards only,
- failures render before warnings and passes,
- compiled Cypher is visible,
- evidence IDs are visible,
- failed-run errors are visible,
- failed runs remain failed in the header and history, expose troubleshooting in a dialog without
  a duplicate overview callout, and abbreviate verbose credential role details only in the
  header,
- `report --open` selects the newest HTML report,
- `report --open <id>` selects a historical report and a bare ID is rejected,
- schema 1.0 and 1.1 historical artifacts load with null inventory without being rewritten,
- the configured artifacts directory is honored,
- missing reports and browser-launch failures exit non-zero,
- report history is ordered and de-duplicates `runs/latest`,
- a historical run can be selected and opened by id,
- history and comparisons show named per-suite scores rather than the hidden
  overall machine score,
- pruning preserves the requested newest runs, `runs/latest`, and unknown directories,
- diagnostic reports contain failures, warnings, and errors but omit passing checks,
- clean, findings, failed, and diagnostic reports contain byte-identical generic Next Steps
  content; accessible tab markup, keyboard activation, same-report Checks
  state preservation, and report-history replacement/reset behavior are covered,
- scorer results are invariant to input order and use exact half-even rounding,
- per-suite calculations use the same locked weights as the overall score,
- reports show each suite score as the rightmost badge in its status card,
  distinguish execution errors from graph findings, project the same result sentence in the CLI
  and HTML for failed, empty-selection, all-skipped, findings, incomplete-clean, and fully clean
  runs, render every coverage state and skipped check name in `Not Evaluated`, preserve the stored
  selection boundary, link coverage entries to failure-first check details, and avoid a duplicate
  issue summary.
- new 1.2 reports render exact sorted label/type counts and names from the artifact, distinguish
  probed empty arrays from historical not-recorded null, and never infer inventory from other
  report data.

## Deferred Work

The following require C1, the fixture graph, or additional tooling:

- Convert raw C1 engine output into `Results`.
- Guarantee every real run writes `results.json`.
- Compute graph fingerprint, DB version, suite SHA, and timestamps at runtime.
- Full pipeline fixture graph coverage.
- Browser-level offline asset/network test.
- MCP transport implementation beyond the approved requirement that every result-returning tool
  and declared output schema consume the canonical SPEC-01 1.2 shape.
