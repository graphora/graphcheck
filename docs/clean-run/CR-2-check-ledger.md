# CR-2 — Checks executed and skipped, with reasons

## Ticket

Make the HTML check ledger an auditable rendering of every check in `results.checks`. Each check
must show its verdict and whether it was evaluated. Every skipped check must show the persisted skip
reason in human language without inventing a more specific cause.

## Dependencies

Depends on CR-1's shared presentation helper. CR-4 consumes this ticket's skip-reason presentation,
and CR-5 moves the resulting ledger into the combined tabbed panel.

## User outcome

The user can answer all of these questions from the report:

- Which checks were selected for this run?
- Which of those checks were evaluated?
- What was each verdict?
- Which checks were not evaluated, and what reason code did GraphCheck record?

## Source-of-truth boundary

`results.checks` is exactly the selected universe. A report must render a one-to-one ledger:

```text
rendered identities == {(check.suite_id, check.id) for check in results.checks}
```

Checks excluded by suite or tag selection are absent, not skipped. The report must not synthesize
entries for them.

The `CheckResult.executed` property defines evaluation status:

- `pass`, `fail`, `warn`, `errored` → evaluated/attempted;
- `skipped` → not evaluated.

## HTML check-card contract

Every card in the Checks tab displays:

- check name;
- stable `(suite_id, check_id)` identity;
- verdict badge;
- `Not evaluated` label for skipped checks only;
- pattern;
- expected value;
- existing measured value, estimate, compiled query, evidence, or structured error when present.

A skipped card additionally displays a prominent block headed `Reason`. Use the shared generic
explanation mapping:

| Stored value | Explanation |
| --- | --- |
| `generated` | `Generated check awaiting review or approval.` |
| `unsupported` | `A capability required by this check was unavailable.` |
| `not_run` | `The run ended before this check started.` |

Do not repeat the internal raw code in the HTML card; it remains available in `results.json` and the
CLI skipped-check table. The explanation may not claim which capability was missing unless that
specific fact is persisted. The run-level `partial_reason` belongs in CR-4 and may provide
additional context.

Rename the card disclosure from `View Details & Evidence` to `View details`; passing and skipped
checks do not necessarily contain evidence.

## Checks controls

Retain search and verdict filters. Add an **Issues** filter representing the union of `fail`, `warn`,
and `errored`, so the navbar's `See issues` action has a non-duplicative destination after CR-4
removes Issue Summary.

Recommended filter order:

```text
All | Issues | Fail | Warn | Errored | Pass | Skipped
```

`Issues` is a presentation grouping only. It must not change verdicts or call an errored check a
finding.

## CLI behavior

Do not repeat all passing checks after the progress display. When skips exist, end the Result line
with `Coverage is incomplete due to the following check(s) which have not been evaluated:` and
follow it with the same borderless table style as the score breakdown:

```text
Suite          Check                              Reason
customer-360   Draft check (draft-check)          generated: Generated check awaiting review or approval.
core           Dangling relationship (dangling)   unsupported: A capability required by this check was unavailable.
```

The suite and check's human name are italicized, and the stable check id remains visible in
parentheses. A blank line separates the table from the artifact path.

This is deliberately narrower than the HTML ledger. The CLI remains a concise run summary; the
artifact remains the complete audit surface.

## Implementation tasks

- Put verdict, evaluated-state, and skip-reason humanization in the shared reporting presentation
  helper introduced by CR-1.
- Extend `src/graphcheck/reporting/html.py::_check()` to render skipped state and generic reasons.
- Add the Issues filter to the existing filter state and empty-state messages.
- Change the navbar `See issues` action to open **Checks Explorer & Next Steps** on the Checks tab
  with the Issues filter active.
- Keep suite marker navigation opening the exact matching check card.
- Extend the final CLI summary with the conditional skipped-check table.
- Update the renderer's filtered diagnostic path so `--failures-only` still contains exactly
  `fail`, `warn`, and `errored` cards.
- Update SPEC-06 and existing report tests that refer to issue-summary navigation.

## Acceptance tests

### CR-2.A — Ledger identity

For clean, findings, partial, and generated-only fixtures, parse rendered check-card data
attributes and assert that their `(suite_id, check_id)` multiset equals `results.checks` exactly.
There are no missing, duplicate, or synthetic cards.

### CR-2.B — Verdict visibility

Every rendered card contains the exact persisted verdict and correct evaluated/not-evaluated
label. Findings-run assertions cover pass, fail, and warn; supplemental fixtures cover errored and
skipped.

### CR-2.C — Skip reasons

Each skipped card shows a `Reason` heading and generic explanation without its raw `skip_reason`.
Non-skipped cards do not show a skip reason. Tests cover all three enum values.

### CR-2.D — CLI concision

The clean CLI run contains no final per-check pass listing. A run containing skips prints exactly
the skipped checks and stored reason codes in the table after the coverage summary.

### CR-2.E — Issue navigation

`See issues` opens the Checks tab with the Issues filter. Only fail/warn/errored cards remain
visible. When none exist, the panel says `No checks with findings or execution errors.`

### CR-2.F — Safe rendering

Check names, identities, and reason text remain HTML-escaped and are never interpolated into inline
event handlers, JavaScript string literals, or CSS selectors.

## Non-goals

- Displaying checks outside the selected suite/tag universe.
- Guessing an exact unsupported capability or exact `not_run` cause from the enum alone.
- Printing the full check ledger in the CLI.
- Treating skipped checks as findings or including them in the score.

## Definition of done

- The report is a lossless human index of `results.checks` identities and verdicts.
- Every skipped check has a visible persisted reason.
- The CLI remains concise and the full ledger remains in HTML/results JSON.
