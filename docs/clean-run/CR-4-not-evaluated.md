# CR-4 — Known coverage limitations stated plainly

## Ticket

Replace the expandable Issue Summary in Graph Health Overview with a permanent **Not Evaluated**
section. It must make coverage gaps and the selected-universe boundary visible without implying
knowledge GraphCheck does not have.

## Dependencies

Depends on CR-1's coverage semantics and CR-2's skipped-check reason mapping and Issues filter. It
should land before CR-5 finalizes the combined panel navigation targets.

## Why this replaces Issue Summary

Issue Summary repeats information already available through:

- the run outcome sentence;
- per-suite verdict badges;
- per-check status markers;
- the Checks ledger and Issues filter.

Not Evaluated adds information that is otherwise easy to miss and is essential to interpreting a
clean result.

## Content states

Apply the first matching state:

### Failed before selection/evaluation

```text
Not Evaluated
The run failed before checks could be evaluated.
```

Show the stored run error in the existing diagnostic. Do not fabricate check rows when
`results.checks` is empty.

### No checks selected

```text
Not Evaluated
No checks were selected for this run.
```

### All selected checks evaluated

```text
Not Evaluated
None. All 12 selected checks were evaluated.
```

### One or more skipped checks

```text
Not Evaluated
3 of 12 selected checks were not evaluated.

customer-360/draft-check    Generated
Generated check awaiting review or approval.

core/dangling              Unsupported
A capability required by this check was unavailable.
```

Reuse CR-2's reason mapping and show the raw reason code. If `run.partial_reason` is present, render
it once as `Coverage note: <stored value>` above the skipped list. Do not copy it into every row.

Errored checks were attempted and therefore do not belong in Not Evaluated. They remain visible in
the Checks ledger as execution errors.

## Permanent scope statement

End the section with:

> This report covers checks selected for this run. GraphCheck did not evaluate graph behavior
> outside those configured checks.

This statement appears for clean and findings runs as well as partial runs. It describes the
contract boundary; it is not a calculated coverage percentage.

Optionally show the stored selection compactly:

```text
Suites: all configured suites | Tags: no tag filter
```

or the exact requested suite/tag values when lists are non-empty. Never enumerate omitted checks,
because the artifact does not contain them.

## Layout and behavior

- Place Not Evaluated after the suite overview and before the button that opens the right-hand
  **Checks Explorer & Next Steps** panel.
- Keep the heading and summary visible in all report states.
- With zero skipped checks, use a compact single-row success treatment rather than an expandable
  empty table.
- With up to five skipped checks, show all rows.
- With more than five, show the first five and use a native `<details>` disclosure for the
  remainder. All rows remain in the offline document.
- On mobile, stack identity, reason, and explanation without horizontal scrolling.

## Navigation changes

Remove:

- `Show Issue Summary` / `Hide Issue Summary`;
- the issue summary table and sorting behavior;
- JavaScript and CSS used only by that table;
- the navbar action's dependency on `summary-table-container`.

Replace navbar actions as follows:

- `See issues` opens the combined panel on the Checks tab with CR-2's Issues filter.
- Partial runs use `Review coverage` and scroll/focus the Not Evaluated section.
- `Explore checks` opens the combined panel on the Checks tab with the All filter.

## Implementation tasks

- Add a focused `_not_evaluated(results)` renderer in `src/graphcheck/reporting/html.py`.
- Derive its counts exclusively from `results.totals` and skipped records in `results.checks`.
- Reuse CR-2's skip-reason presentation mapping.
- Render `run.partial_reason`, when present, as stored text with HTML escaping.
- Render the selected suite/tag boundary from `run.selection` without reconstructing absent checks.
- Remove `_details_rows()`, `_empty_issue_summary()`, `_issue()`, summary-table markup, and
  issue-summary-only JavaScript/CSS after confirming they have no remaining callers.
- Rewire header and overview actions to the destinations above.
- Update SPEC-06 report contents and remove obsolete Issue Summary assertions.

## Acceptance tests

### CR-4.A — Clean complete run

The clean fixture contains a visible Not Evaluated section stating that none of the selected checks
were skipped and all were evaluated. The permanent scope statement is present. No empty issue table
is rendered.

### CR-4.B — Findings complete run

The findings fixture with complete coverage has the same accurate `None` coverage statement. Its
findings remain reachable through the Issues filter, not duplicated in Graph Health Overview.

### CR-4.C — Partial coverage

The partial fixture shows the exact evaluated/selected counts, each skipped identity, reason label,
raw reason code, and stored `partial_reason` once.

### CR-4.D — Generated-only run

The generated-only fixture says that no checks were evaluated, lists the generated skip, and does
not contain a clean/all-pass statement.

### CR-4.E — Failed and empty runs

A failed artifact states that the run failed before evaluation and does not invent skipped checks.
An empty successful selection says that no checks were selected.

### CR-4.F — Selected-universe honesty

For a run using suite/tag filters, the section displays the stored selection and renders no check
identity absent from `results.checks`.

### CR-4.G — Issue Summary removal

Rendered HTML contains no Issue Summary button/table or obsolete handlers. `See issues`, `Review
coverage`, and `Explore checks` target their new destinations and remain keyboard accessible.

## Non-goals

- Calculating an automated coverage percentage.
- Discovering checks that were not selected or do not exist.
- Suggesting which new checks the user should add.
- Treating execution errors as not evaluated.
- Repeating findings in the overview.

## Definition of done

- Every report has a truthful Not Evaluated section.
- Complete, partial, empty, all-skipped, and failed states are unambiguous.
- Issue Summary is fully removed without making findings harder to reach.
