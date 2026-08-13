# CR-5 — Generic next steps

## Ticket

Turn the existing right-hand check panel into a combined panel with heading-level accessible tabs
for **Checks Explorer** and **Next Steps**. Users must be able to switch views without losing their
prior Checks context.

## Dependencies

Depends on CR-2's final check-ledger controls and CR-4's replacement navigation targets. It should
be the last clean-run HTML ticket so report-history fragment behavior is updated once.

## User outcome

The report ends with a clear, bounded continuation:

1. review what GraphCheck evaluated;
2. understand the result and coverage;
3. optionally read generic ways to improve future assurance;
4. return to the exact check view they were using.

No next step is generated from graph contents or presented as a finding.

## Panel structure

Use one right-hand panel rather than adding a fourth dashboard column:

```text
[ Checks Explorer ] [ Next Steps ]

<active tab panel>
```

The tabs occupy the former panel-heading position and are the sole navigation between views.

## Checks tab

Move the existing Checks Explorer header, search, verdict filters, check cards, empty state, and
detail controls into the Checks tab without losing CR-2 behavior.

When the user switches to Next Steps and back during the same report:

- preserve the search query;
- preserve the active verdict filter;
- preserve expanded check details;
- preserve the checks scroll position;
- return focus to the control that initiated the switch where appropriate.

The Overview actions behave as follows:

- `Explore checks` opens the panel and selects Checks/All.
- `See issues` opens the panel and selects Checks/Issues.
- clicking a suite marker opens Checks and focuses the exact check.

## Next Steps tab

Use fixed, identical guidance for clean and findings runs:

### Add competency checks

> Add competency checks for the core business questions your graph must answer.

### Track drift over time

> Set a baseline and rerun GraphCheck to track structural drift.

Add one boundary note:

> These are general practices, not recommendations derived from this run.

Do not reorder, add, remove, or personalize these items based on target metadata, scores, findings,
suite names, check patterns, evidence, or skipped reasons. Existing structured `fix` fields attached
to run/check errors remain in their diagnostic locations and are not Next Steps content.

## Accessibility contract

Implement the switcher as an accessible tab interface:

- the container has `role="tablist"` and an accessible label;
- each tab is a real `<button role="tab">`;
- tabs expose `aria-selected`, `aria-controls`, and roving `tabindex`;
- panels use `role="tabpanel"`, `aria-labelledby`, and the `hidden` attribute;
- Left/Right Arrow changes tabs;
- Home/End selects the first/last tab;
- Enter/Space behavior follows native buttons;
- switching tabs moves focus predictably without forcing page scroll.

## Report history and diagnostic behavior

The full report renderer and report-history fragment renderer must emit the same combined-panel
root. Soft navigation between historical reports must replace that root atomically.

When a different historical report is loaded:

- reset the active tab to Checks;
- retain the existing persisted check-filter preference only where it remains valid;
- do not carry check-detail expansion or scroll position across different run IDs;
- keep the panel open if it was already open.

`report --failures-only` opens on the Checks tab and exposes its diagnostic cards immediately when
the panel is opened. Next Steps remains the same generic content.

## Offline and layout constraints

- Guidance is rendered into the HTML artifact; it is not fetched at runtime.
- Tabs operate only on existing DOM nodes.
- Do not add external links, assets, fonts, or network APIs.
- Desktop uses the existing right-hand column footprint.
- Mobile uses the same single panel in the stacked layout; inactive tab content remains hidden.
- The panel height should remain stable when switching; each tab panel owns its scrolling region.

## Implementation tasks

- Replace the standalone `checks-panel` root with a combined report fragment such as
  `checks-next-steps-panel`.
- Add the heading-level tablist, Checks tab panel, and Next Steps tab panel.
- Move current check controls/cards into the Checks tab without changing their identities.
- Add pure static HTML generation for the two guidance items and boundary note.
- Add tab activation, keyboard navigation, focus management, and same-report Checks-state
  restoration to the inline script.
- Update `render_validated_html_report_fragments()` and report explorer's fragment replacement map
  to use the new root consistently.
- Update navigation-loading selectors and checks-open detection for the new root.
- Reset tab-local state correctly during report-history soft navigation.
- Update CSS for selected tabs, focus-visible states, stable panel sizing, and mobile stacking.
- Update SPEC-06's report content and interaction documentation.

## Acceptance tests

### CR-5.A — Content on clean and findings reports

Both fixtures render the combined panel, both tab buttons, exactly the two approved guidance items,
and the generic-boundary note. The content is byte-for-byte equivalent between fixtures apart from
surrounding report data.

### CR-5.B — Bidirectional tab flow

Checks Explorer initially owns the active state. The heading tabs switch directly between Checks
Explorer and Next Steps in both directions.

### CR-5.C — Checks-state preservation

After setting a search query, verdict filter, expanded detail, and nonzero scroll position,
switching to Next Steps and back restores all four values for the same report.

### CR-5.D — Accessible tabs

Assert the required roles and ARIA relationships. A browser interaction test verifies Arrow,
Home/End, Enter/Space, focus, selected state, and hidden-panel behavior.

### CR-5.E — Existing navigation

Overview actions and suite markers open the combined panel on Checks with the correct filter or
focused identity. No action opens Next Steps accidentally.

### CR-5.F — Report-history replacement

Soft navigation replaces the combined fragment without duplicate IDs or stale event handlers,
keeps an open panel open, and resets the active tab/check-local state for the new run.

### CR-5.G — Diagnostic and offline reports

Failures-only reports expose their check cards through the Checks tab and retain Next Steps. Static
offline assertions continue to find one inline script, no external assets, and no browser network
API usage.

### CR-5.H — Generic-only guarantee

Changing findings, target metadata, score, suites, checks, or skip reasons does not change the Next
Steps content. No check identity, label, relationship type, evidence text, or target-specific noun
appears in that content.

## Non-goals

- Automated or contextual check recommendations.
- Links to generated checks, cloud services, or external documentation.
- A fourth dashboard column or carousel of multiple recommendation pages.
- Persisting the active Next Steps tab across different reports or browser sessions.
- Changing check verdicts, score, exit code, or report data.

## Definition of done

- Users can switch between Checks and Next Steps in both directions without losing same-report
  Checks context.
- Guidance is visibly generic and identical across outcomes.
- Existing report history, diagnostic filtering, offline behavior, and mobile layout remain intact.
