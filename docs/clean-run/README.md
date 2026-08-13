# Clean-run epic

## Permanent principle

GraphCheck never manufactures findings. A clean graph returns a trustworthy clean result. Value
comes from demonstrated coverage, not invented concern.

This epic belongs to C5 (results and report). It is split into five implementation tickets so that
each requirement has an independently reviewable behavior and acceptance test:

| Ticket | Outcome | Primary surface |
| --- | --- | --- |
| [CR-1](CR-1-trustworthy-pass-result.md) | Trustworthy pass result | CLI and HTML |
| [CR-2](CR-2-check-ledger.md) | Every selected check and skip reason is visible | HTML, concise CLI coverage |
| [CR-3](CR-3-target-header.md) | Target facts are visible in the report header | Results contract, MCP, and HTML |
| [CR-4](CR-4-not-evaluated.md) | Coverage limitations are stated plainly | HTML |
| [CR-5](CR-5-next-steps.md) | Generic next steps are available after the check ledger | HTML |

## Product contract

The implementation must preserve these distinctions:

- A **finding** is a check with verdict `fail` or `warn`.
- An **execution error** is a check with verdict `errored`; it is not a graph finding.
- A check is **evaluated** when its verdict is `pass`, `fail`, `warn`, or `errored`.
- A check is **not evaluated** when its verdict is `skipped`.
- A **fully clean run** is complete, has at least one selected check, evaluates every selected
  check, and every verdict is `pass`.
- A run with no findings but incomplete coverage must say both facts. It must not be presented as
  fully clean.
- An empty selection or an all-skipped run must say that no checks were evaluated.
- `checks[]` is the selected check universe. The report must not claim that unselected or
  unconfigured graph behavior was evaluated.

All human-facing statements must be deterministic projections of a validated `Results` object.
Renderers may humanize contract values, but they must not add inferred findings, inferred skip
causes, graph-specific advice, or facts absent from `results.json`.

## Shared presentation model

CR-1 and CR-2 should introduce a small pure presentation layer under
`src/graphcheck/reporting/`, shared by the CLI and HTML renderer. It should calculate, without
mutating `Results`:

```text
selected = totals.checks
evaluated = totals.checks - totals.skipped
findings = totals.fail + totals.warn
execution_errors = totals.errored
fully_clean = run.status == complete
              and selected > 0
              and evaluated == selected
              and totals.pass == selected
```

The layer should also own human labels for verdicts and skip reasons. This prevents the CLI and
HTML report from drifting into different interpretations of the same artifact.

## Agreed HTML information architecture

The report should read in this order:

1. Run lifecycle and outcome.
2. Target metadata and coverage.
3. Per-suite overview.
4. Not Evaluated.
5. Checks Explorer & Next Steps.

The existing expandable **Issue Summary** is removed. Findings already appear in suite outcome
badges, status markers, and the full check ledger. Its space is used for the new permanent **Not
Evaluated** section.

The right-hand panel is titled **Checks Explorer & Next Steps**. It has two tabs:

- **Checks** — the complete selected-check ledger, filters, search, and details.
- **Next Steps** — static generic guidance only.

The panel footer provides the forward/back reading flow with one contextual action at a time:
`Next steps →` from Checks and `← Back to checks` from Next Steps.

## Agreed CLI boundary

The interactive progress display remains unchanged. The final CLI summary remains concise:

- do not print every passing check again;
- print one explicit result sentence;
- for multiple suites, print evaluated/selected coverage and outcome totals in a compact score
  table with aligned check-state columns;
- list individual checks only when they were not evaluated, because their reasons explain a
  coverage gap;
- leave evidence and the complete check ledger to the HTML report and `results.json`.

CR-3 and CR-5 are primarily HTML requirements. The CLI does not need label/type inventory or the
generic next-steps panel.

## Fixture and acceptance matrix

Add `tests/contracts/fixtures/results.clean.json`: a complete, non-empty, all-pass result with full
target metadata. Continue using `results.complete.json` as the findings fixture. The partial,
generated-only, and failed fixtures provide supplemental boundary coverage.

| Requirement | Clean fixture | Findings fixture | Supplemental boundary fixture |
| --- | --- | --- | --- |
| CR-1 | Explicit `No failures`; no finding rows | Exact stored finding counts and rows | Empty/all-skipped never called clean |
| CR-2 | Every selected passing check is in the ledger | Every selected check and verdict is in the ledger | Every skipped check shows its reason |
| CR-3 | Persisted target facts render exactly | Persisted target facts render exactly | Probed-empty and historical not-recorded inventory remain distinct |
| CR-4 | `Not Evaluated` says none | `Not Evaluated` says none when coverage is complete | Partial/generated skipped check names link to their ledger entries |
| CR-5 | Both tabs and generic guidance work | Same generic guidance, unaffected by findings | Failed/diagnostic reports remain navigable |

At minimum, acceptance runs at three boundaries:

1. Contract fixtures validate and round-trip through the writer.
2. `render_html_report()` is asserted structurally for the clean and findings fixtures.
3. `graphcheck run` tests exercise one all-pass and one findings run through the CLI artifact
   boundary.

CR-3 additionally runs the same contract through each MCP result-returning tool and its declared
output schema. MCP must expose the canonical SPEC-01 1.2 target shape rather than a separately
interpreted inventory.

When the repository's real fixture graph is available, repeat the same matrix end to end against a
findings seed and a clean seed. Artifact-level tests are not a substitute for that final fixture
graph acceptance.

## Recommended delivery order

1. CR-3 contract work, because it changes the canonical fixture shape.
2. CR-1 shared result language and clean fixture.
3. CR-2 complete check-ledger presentation.
4. CR-4 replacement of Issue Summary with Not Evaluated.
5. CR-5 combined tabbed panel and generic guidance.

CR-1, CR-2, CR-4, and CR-5 must not add fields to `results.json`. CR-3 is the sole contract change
in this epic.

## Implementation changelog

### CR-1 — Trustworthy pass result

Implemented on 2026-08-13:

- added a pure immutable reporting projection for selected/evaluated coverage, finding and
  execution-error counts, fully-clean state, and deterministic primary result language;
- made the final CLI summary and HTML lifecycle header consume the same projection, including an
  explicit `Result` line in the CLI and exact target formatting;
- added a borderless Rich score-breakdown table for multi-suite runs, with evaluated/selected
  coverage, fixed-width outcome columns, threshold-colored scores, and colors only on non-zero
  outcome values;
- moved target metadata before interactive check progress, italicized suite names in the score
  table and skipped-suite coverage explanation, and added deterministic attribution when skips
  span multiple suites;
- reorganized the final summary into spaced lifecycle, score, result/artifact, and exit-code blocks
  and collapsed the duplicate artifact paths into one saved-directory line;
- replaced `No issues found`, `All clear`, and celebration branches with precise language for
  failed, empty-selection, all-skipped, findings, incomplete-clean, and fully clean runs;
- expanded the clean contract fixture to two passing checks and added shared CLI/HTML regression
  coverage for every CR-1 outcome state plus end-to-end CLI assertions for passing and findings
  runs;
- updated SPEC-06 to document the shared terminology and acceptance matrix.

### CR-2 — Complete selected-check ledger

Implemented on 2026-08-13:

- extended the shared immutable reporting projection with human verdict labels,
  evaluated/not-evaluated state, and the canonical labels and generic explanations for all three
  persisted skip reasons;
- made every HTML check card show its stable suite/check identity, verdict, skipped state when not
  evaluated, pattern, expected value, and every available measured value, estimate, compiled query,
  evidence record, or structured execution error;
- added a prominent skipped-check `Reason` block with the shared generic explanation but without
  its internal raw code, while keeping non-skipped cards free of skip-reason language;
- renamed the card disclosure to `View details` and added the `Issues` union filter for fail, warn,
  and errored checks, including its precise empty state and the same visual treatment as `All`;
- changed the navbar `See issues` action to open the Checks Explorer with the Issues filter active,
  without interpolating check identities into JavaScript or CSS selectors;
- aligned the header lifecycle-status message with the 18px section-heading type scale while
  retaining the compact 10px status pill;
- added a concise borderless CLI table for unevaluated checks, with italicized Suite and Check name
  plus stable id, and Reason columns containing persisted codes and shared generic explanations;
- separated that table from the artifact line with a blank line and added semantic color to the
  final exit-code line;
- replaced ASCII table-header and progress-bar characters with continuous Unicode line glyphs;
- preserved failures-only diagnostic reports as exact fail/warn/errored ledgers and added fixture,
  renderer, shared-presentation, and end-to-end CLI acceptance coverage;
- updated SPEC-06 to document the complete ledger, skip-reason presentation, Issues filter, and
  CLI boundary.

### CR-3 — Target information in the run-report header

Implemented on 2026-08-13:

- bumped the canonical results contract from schema 1.1 to 1.2 and regenerated
  `docs/specs/results.schema.json`;
- added required, sorted, unique `labels` and `relationship_types` arrays to newly produced target
  results, populated from the connector's existing schema-token probe without another database
  round trip;
- added explicit engine failure when read visibility prevents a trustworthy inventory and rejected
  programmatically supplied targets without canonical inventories;
- added compatibility loading for schema 1.0 and 1.1 artifacts, preserving missing inventory as
  in-memory null values without rewriting historical files;
- added the compact HTML target block with database/version/edition, combined graph size, offline
  expandable schema inventory, escaped names, and available/unavailable capability pills;
- added the clean 1.2 fixture, upgraded all current result fixtures and the rendered writer
  snapshot, and added contract, writer, history, renderer, engine, and connector acceptance tests;
- confirmed that this repository does not yet contain an MCP transport implementation; the
  canonical generated results schema now exposes the 1.2 target shape for future MCP consumers.

### CR-4 — Known coverage limitations stated plainly

Implemented on 2026-08-13:

- replaced the expandable Issue Summary with a permanent Not Evaluated section after the suite
  overview, with distinct language for failed, empty-selection, fully evaluated, partially
  evaluated, and all-skipped runs;
- listed every skipped check by name, showing the first five directly and retaining any remainder
  in a native offline disclosure; each name is a keyboard-accessible control that opens and
  highlights the matching Checks Explorer entry;
- rendered `run.partial_reason` once as an escaped coverage note and displayed the exact stored
  suite/tag selection boundary without reconstructing omitted checks;
- added the permanent selected-universe scope statement to every report and kept errored checks in
  the evaluated Checks ledger rather than treating them as coverage gaps;
- rewired `See issues` to the Issues filter, `Review coverage` to focus and scroll to Not Evaluated,
  and `Explore checks` to reset the explorer to the All filter;
- fitted the coverage controls within the overview scroll area, aligned their typography with the
  surrounding report, italicized suite and suite/check identities, and removed Severity metadata
  from Checks Explorer cards;
- added a blank line after the final CLI exit-code line;
- colored complete suite coverage green and incomplete suite coverage yellow in the CLI score
  breakdown;
- removed the obsolete issue-summary table, renderer helpers, sorting/toggle JavaScript, and
  summary-only styles while preserving findings in suite markers and the complete check ledger;
- added fixture acceptance coverage for clean, findings, partial, generated-only, failed, empty,
  filtered-selection, HTML-escaping, and more-than-five-skips states;
- updated SPEC-06 to document the permanent coverage section and its navigation behavior.

## Epic acceptance

- All five ticket acceptance suites pass for both the clean and findings fixtures.
- Existing partial, generated-only, failed-run, diagnostic-report, report-history, and offline HTML
  tests remain green.
- Every new non-failed 1.2 run—and every MCP response representing that run—carries sorted, unique
  `labels` and `relationship_types` arrays; only compatibility-loaded pre-1.2 artifacts and MCP
  responses representing them use null to mean `not recorded`.
- The clean run contains no rendered finding that is absent from `results.json`.
- The report remains a self-contained offline document with no external assets or network calls.
- The CLI preserves current exit-code semantics and interactive progress behavior.

