# CR-1 — Trustworthy pass result

## Ticket

Make a clean result explicit without overstating what GraphCheck evaluated. The same validated
`Results` object must produce compatible outcome language in the final CLI summary and HTML report.

## Dependencies

No other clean-run ticket is required. CR-3 may land first because it changes fixture shape, but
CR-1 uses only existing result status, verdict, totals, target, and selection fields.

## User outcome

After a complete all-pass run, the user sees:

> No failures. All 12 selected checks passed.

The user must never see a clean celebration when nothing was evaluated or when coverage was
incomplete.

## Current behavior to replace

The HTML renderer currently derives variants of `No issues found` and `All clear!` locally. An
all-skipped run can therefore carry a positive-sounding headline before a later message explains
that no checks were evaluated. The CLI prints correct totals but leaves the user to infer the
conclusion.

## Required outcome states

Apply the first matching state:

| State | Required primary language |
| --- | --- |
| `run.status == failed` | `Run failed before checks could complete.` plus the stored error |
| `selected == 0` | `No checks were selected or evaluated.` |
| `evaluated == 0` | `No checks were evaluated.` |
| Any `fail`, `warn`, or `errored` | Exact failure, warning, and execution-error counts |
| No findings/errors, but `evaluated < selected` or run is partial | `No failures in the N checks evaluated. Coverage is incomplete.` |
| Fully clean | `No failures. All N selected checks passed.` |

Do not use `All clear`, celebration emoji, `healthy`, `safe`, `correct`, or equivalent claims. A
check pass means only that its configured assertion passed.

Warnings must not be summarized as clean. Errored checks must be reported as execution errors, not
as graph findings.

## CLI behavior

Keep the existing interactive progress display. Change only the stable final summary printed by
`_print_run_summary()`:

```text
GraphCheck run <id>: complete
Target: neo4j · Neo4j 5.18.0 community · 1,250 nodes · 3,480 relationships
Result: No failures. All 12 selected checks passed.
Coverage: 12/12 selected checks evaluated
Results: <path>
Report: <path>
```

For multi-suite runs, retain per-suite score/totals lines after Result and Coverage. Do not add
per-check pass lines. Preserve the exact process exit code and existing artifact paths.

For incomplete coverage without findings:

```text
Result: No failures in the 9 checks evaluated. Coverage is incomplete.
Coverage: 9/12 selected checks evaluated · 3 not evaluated
```

CR-2 owns the optional skipped-check detail printed after this summary.

## HTML behavior

Replace locally assembled clean/issue text in the navbar with the shared presentation result.
Lifecycle remains a separate status pill:

- `COMPLETE`, `PARTIAL`, or `FAILED` describes whether the run completed;
- the sentence beside it describes findings and evaluated coverage.

Examples:

```text
COMPLETE  Run complete. No failures. All 12 selected checks passed.
PARTIAL   Partial run. No failures in the 9 checks evaluated. Coverage is incomplete.
COMPLETE  Run complete. 1 failure and 2 warnings.
PARTIAL   Partial run. 1 execution error. Coverage is incomplete.
```

CR-4 supplies the detailed explanation of incomplete coverage.

## Implementation tasks

- Add a pure presentation helper under `src/graphcheck/reporting/` for selected/evaluated counts,
  fully-clean state, and primary result language.
- Make `src/graphcheck/cli.py::_print_run_summary()` consume that helper.
- Make `src/graphcheck/reporting/html.py::_run_title()` consume the same helper.
- Remove contradictory HTML branches that independently decide between `No issues found`, `No
  checks evaluated`, and `All clear`.
- Add `tests/contracts/fixtures/results.clean.json` with at least two passing checks and no skipped
  checks.
- Validate the new fixture through the contract/schema fixture parametrization.
- Add CLI and HTML regression tests for every state in the table above.
- Update SPEC-06's report contents and test list to use the new terminology.

## Acceptance tests

### CR-1.A — Clean artifact

Given `results.clean.json`, both surfaces contain the exact semantic statement:

```text
No failures. All <N> selected checks passed.
```

The HTML does not contain a fail/warn/errored check card, fabricated issue row, or clean-health
claim broader than the configured assertions.

### CR-1.B — Findings artifact

Given `results.complete.json`, the result sentence contains exactly the stored failure, warning, and
execution-error totals. It does not contain the clean statement.

### CR-1.C — Nothing evaluated

Given an empty selection and `results.generated-only.json`, the result says that no checks were
evaluated. It does not contain `No failures. All`, `All clear`, or a celebration.

### CR-1.D — Partial without findings

Given `results.partial.json`, the result states both that no failures were found in evaluated checks
and that coverage is incomplete.

### CR-1.E — CLI stability

An all-pass CLI run still prints progress while running, does not repeat individual passing checks
afterward, writes both artifacts, and exits 0. Findings, partial, and failed runs preserve their
contract-derived exit codes.

## Non-goals

- Deciding whether the configured checks adequately cover the user's business domain.
- Calculating a schema-coverage percentage.
- Generating findings, concerns, risks, or recommendations from a clean graph.
- Changing scoring or exit-code semantics.

## Definition of done

- All acceptance tests above pass.
- CLI and HTML wording comes from one tested semantic projection.
- No clean outcome can be reached solely because `fail == 0`; evaluated coverage is part of the
  decision.
