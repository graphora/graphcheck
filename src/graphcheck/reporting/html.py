from __future__ import annotations

import html
import json
from collections.abc import Collection
from pathlib import Path
from typing import Any

from graphcheck.contracts.results import CheckResult, RedactionPolicy, Results, RunStatus, Verdict
from graphcheck.reporting.history import display_run_status
from graphcheck.reporting.writer import json_compatible, load_results

_VERDICT_ORDER = {
    Verdict.FAIL: 0,
    Verdict.WARN: 1,
    Verdict.ERRORED: 2,
    Verdict.SKIPPED: 3,
    Verdict.PASS: 4,
}
_SEVERITY_ORDER = {"error": 0, "warn": 1}


def render_html_report(
    results: Results | dict[str, Any] | str | Path,
    *,
    verdicts: Collection[Verdict] | None = None,
) -> str:
    model = load_results(results)
    return render_validated_html_report(model, verdicts=verdicts)


def render_validated_html_report(
    model: Results,
    *,
    verdicts: Collection[Verdict] | None = None,
    explorer_token: str | None = None,
) -> str:
    """Render a Results model already validated at an artifact boundary."""

    fragments = render_validated_html_report_fragments(model, verdicts=verdicts)
    return "\n".join(
        [
            "<!doctype html>",
            '<html lang="en">',
            "<head>",
            '<meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            (
                '<meta name="graphcheck-redaction" '
                f'content="{_escape(model.run.redaction.policy.value)}">'
            ),
            (
                f'<meta name="graphcheck-explorer-token" content="{_escape(explorer_token)}">'
                if explorer_token is not None
                else ""
            ),
            f"<title>GraphCheck Dashboard - {_escape(model.run.id)}</title>",
            "<style>",
            _CSS,
            "</style>",
            "</head>",
            "<body>",
            _header(fragments["run_title"]),
            '<main class="dashboard-body">',
            '<div class="dashboard-grid">',
            _report_explorer(model),
            fragments["overview"],
            fragments["checks"],
            "</div>",
            "</main>",
            "<script>",
            _JS,
            "</script>",
            "</body>",
            "</html>",
            "",
        ]
    )


def render_validated_html_report_fragments(
    model: Results,
    *,
    verdicts: Collection[Verdict] | None = None,
) -> dict[str, str]:
    """Render the report-specific roots used by full and soft navigation."""

    if model.run.redaction.policy is RedactionPolicy.MASK or model.run.redaction.applied:
        from graphcheck.reporting.redaction import verify_redacted_results

        verify_redacted_results(model)

    checks = sorted(
        (check for check in model.checks if verdicts is None or check.verdict in verdicts),
        key=lambda check: (
            _VERDICT_ORDER[check.verdict],
            _SEVERITY_ORDER[check.severity.value],
            check.suite_id,
            check.id,
        ),
    )
    return {
        "run_title": _run_title(model),
        "overview": _status_overview(model, checks, filtered=verdicts is not None),
        "checks": _checks(checks, redacted=model.run.redaction.applied),
    }


def write_html_report(
    results: Results | dict[str, Any] | str | Path,
    path: Path,
    *,
    verdicts: Collection[Verdict] | None = None,
) -> Path:
    path.write_text(render_html_report(results, verdicts=verdicts), encoding="utf-8")
    return path


def _report_explorer(results: Results) -> str:
    return (
        '<aside id="report-explorer" class="card panel-section report-explorer" '
        f'data-current-report="{_escape(results.run.id)}" aria-label="Report explorer">'
        '  <div class="explorer-header">'
        "    <h2>Report History</h2>"
        '    <input type="search" id="report-search-input" placeholder="🔍 Search reports..." '
        'aria-label="Search reports">'
        '    <div class="explorer-selection-actions">'
        '      <button id="clear-report-selection-btn" class="btn-secondary" '
        'type="button" disabled>Clear Selection</button>'
        '      <button id="delete-reports-btn" class="btn-danger" '
        'type="button" disabled>Delete</button>'
        "    </div>"
        "  </div>"
        '  <div class="scrollable-content explorer-scroll">'
        '    <details id="latest-report-group" class="report-group" open>'
        '      <summary class="report-group-heading">'
        '        <h3 id="latest-reports-heading">Latest report</h3>'
        "      </summary>"
        '      <div id="latest-report-list" class="report-list">'
        '        <p class="explorer-loading text-muted">Loading report history…</p>'
        "      </div>"
        "    </details>"
        '    <details id="last-five-report-group" class="report-group" open>'
        '      <summary class="report-group-heading">'
        '        <h3 id="last-five-reports-heading">Last 5 reports</h3>'
        "      </summary>"
        '      <div id="last-five-report-list" class="report-list">'
        '        <p class="explorer-loading text-muted">Loading…</p>'
        "      </div>"
        "    </details>"
        '    <details id="older-report-group" class="report-group">'
        '      <summary class="report-group-heading">'
        '        <h3 id="older-reports-heading">Older</h3>'
        "      </summary>"
        '      <div id="older-report-list" class="report-list">'
        '        <p class="explorer-loading text-muted">Loading…</p>'
        "      </div>"
        "    </details>"
        "  </div>"
        '  <div class="panel-footer explorer-footer">'
        '    <div id="report-explorer-status" class="explorer-status" aria-live="polite"></div>'
        '    <div class="explorer-comparison-actions">'
        '      <button id="compare-reports-btn" class="btn-secondary" '
        'type="button" disabled>Compare Selected</button>'
        '      <button id="compare-most-recent-btn" class="btn-primary" '
        'type="button" disabled>Compare Most Recent</button>'
        "    </div>"
        "  </div>"
        "</aside>"
        '<dialog id="report-comparison-dialog" class="comparison-dialog">'
        '  <div class="comparison-dialog-header">'
        "    <h2>Report Comparison</h2>"
        '    <button id="close-comparison-btn" class="dialog-close" type="button" aria-label="Close Comparison">×</button>'
        "  </div>"
        '  <pre id="report-comparison-content" tabindex="0"></pre>'
        "</dialog>"
        '<dialog id="report-missing-dialog" class="comparison-dialog report-missing-dialog">'
        '  <div class="comparison-dialog-header">'
        '    <div class="report-missing-title">'
        "      <h2>Report Not Found</h2>"
        '      <p id="report-missing-diagnostic"><span class="report-missing-name">Selected report</span> could not be found or opened.</p>'
        "    </div>"
        '    <button id="close-report-missing-btn" class="dialog-close" type="button" '
        'aria-label="Close Report Missing">×</button>'
        "  </div>"
        '  <div class="report-missing-content">'
        "    <p><strong>Next step:</strong> Select another report from history or regenerate the missing report.</p>"
        "  </div>"
        "</dialog>"
        '<dialog id="delete-confirmation-dialog" class="confirmation-dialog">'
        "  <h2>Are you sure?</h2>"
        '  <p id="delete-confirmation-message"></p>'
        '  <div class="confirmation-actions">'
        '    <button id="cancel-delete-btn" class="btn-secondary" type="button">Cancel</button>'
        '    <button id="confirm-delete-btn" class="btn-danger" type="button">Delete</button>'
        "  </div>"
        "</dialog>"
    )


def _run_title(results: Results) -> str:
    version_info = (
        f"[v{_escape(results.run.graphcheck_version)} / Pack v{_escape(results.run.pack_version)}]"
    )
    redaction_pill = (
        '    <span class="status-pill status-pill-redacted">DETAILS REDACTED</span>'
        if results.run.redaction.applied
        else ""
    )
    total_issues = results.totals.fail + results.totals.warn + results.totals.errored
    executed = results.totals.checks - results.totals.skipped

    if total_issues > 0:
        issue_counts = [
            (results.totals.fail, "failure", "failures"),
            (results.totals.warn, "warning", "warnings"),
            (results.totals.errored, "error", "errors"),
        ]
        status_text = ", ".join(
            f"{count} {singular if count == 1 else plural}"
            for count, singular, plural in issue_counts
            if count > 0
        )
    elif results.totals.skipped == 0:
        status_text = "No checks evaluated" if executed == 0 else "No issues found"
    else:
        status_text = "No issues found"

    skipped_text = ""
    if results.totals.skipped > 0:
        check_str = "check" if results.totals.skipped == 1 else "checks"
        skipped_text = f" ({results.totals.skipped} {check_str} skipped)"

    action = ""
    fix = ""
    status = display_run_status(results)
    if results.run.error is not None:
        kind, label, heading = (
            ("error", "FAILED", "Run Failed.")
            if status is RunStatus.FAILED
            else ("partial", "PARTIAL", "Partial Run.")
        )
        message = results.run.error.message
        action = (
            '<button id="run-error-fix-toggle" class="header-status-action" type="button" '
            'aria-expanded="false" aria-controls="run-error-fix">See fix.</button>'
        )
        fix = (
            '<span id="run-error-fix" class="header-status-fix hidden-status-fix">'
            f"💡 <strong>Fix:</strong> {_escape(results.run.error.fix)}</span>"
        )
    elif status is RunStatus.PARTIAL:
        kind, label, heading, message = "partial", "PARTIAL", "Partial Run.", f"{status_text}."
        action = (
            '<button id="run-summary-toggle" class="header-status-action" type="button" '
            'aria-expanded="false" aria-controls="summary-table-container">See more.</button>'
        )
    else:
        kind = "warning" if total_issues > 0 else "complete"
        label, heading = "COMPLETE", "Run Complete."
        celebration = " 🎉" if status_text == "No issues found" and executed > 0 else ""
        message = f"{status_text}{skipped_text}.{celebration}"
        if total_issues > 0:
            action = (
                '<button id="run-summary-toggle" class="header-status-action" type="button" '
                'aria-expanded="false" aria-controls="summary-table-container">See issues.</button>'
            )

    return (
        '<div id="report-run-title" class="brand-container">'
        f'  <span class="eyebrow">GraphCheck Dashboard {version_info}</span>'
        '  <div class="header-status">'
        f'    <span class="status-pill status-pill-{kind}">{label}</span>'
        f"{redaction_pill}"
        f'    <h1><strong>{heading}</strong> <span class="header-status-message">'
        f"{_escape(message)}</span>{action}{fix}</h1>"
        "  </div>"
        "</div>"
    )


def _header(run_title: str) -> str:
    return (
        '<header class="navbar">'
        f"{run_title}"
        '  <div class="navbar-actions">'
        '    <span id="report-navigation-status" class="report-navigation-status" '
        'role="status" aria-live="polite"></span>'
        '    <button id="theme-toggle" class="theme-toggle-btn" aria-label="Toggle Theme" '
        'title="Toggle Theme">🌙</button>'
        "  </div>"
        "</header>"
    )


def _status_overview(
    results: Results,
    checks: Collection[CheckResult],
    *,
    filtered: bool,
) -> str:
    details_rows = _details_rows(checks)
    target = results.run.target
    if target is None:
        target_html = '<span class="text-muted">Target unavailable</span>'
    else:
        target_html = (
            f"<strong>{_escape(target.database)}</strong> "
            f"(Neo4j version: {_escape(target.server_version)}, {_escape(target.edition)})"
        )
    overview_header = (
        '  <div class="summary-top-bar"><h2>Graph Health Overview</h2></div>'
        if results.run.redaction.applied
        else (
            '  <div class="summary-top-bar">'
            '    <div class="summary-meta-col">'
            "      <h2>Graph Health Overview</h2>"
            '      <div class="summary-meta-grid">'
            '        <div class="meta-item">'
            '          <span class="meta-label">Target Graph</span>'
            f"          <div>{target_html}</div>"
            "        </div>"
            '        <div class="meta-item graph-count-item">'
            '          <span class="meta-label">Nodes</span>'
            f"          <div>{_target_count(results, 'nodes')}</div>"
            "        </div>"
            '        <div class="meta-item graph-count-item">'
            '          <span class="meta-label">Relationships</span>'
            f"          <div>{_target_count(results, 'relationships')}</div>"
            "        </div>"
            "      </div>"
            "    </div>"
            "  </div>"
        )
    )

    # Group checks by suite
    checks_by_suite: dict[str, list[CheckResult]] = {}
    for check in checks:
        checks_by_suite.setdefault(check.suite_id, []).append(check)

    suite_blocks = []
    for suite in results.suites:
        suite_checks = checks_by_suite.get(suite.id, [])
        totals = suite.totals

        total_checks = totals.passed + totals.fail + totals.warn + totals.errored + totals.skipped
        run_checks = total_checks - totals.skipped

        # Right side status badges
        right_badges = []
        if totals.fail > 0:
            right_badges.append(f'<span class="badge badge-fail">{totals.fail} FAILED</span>')
        if totals.errored > 0:
            right_badges.append(
                f'<span class="badge badge-errored">{totals.errored} ERRORED</span>'
            )
        if totals.warn > 0:
            warning_label = "WARNING" if totals.warn == 1 else "WARNINGS"
            right_badges.append(
                f'<span class="badge badge-warn">{totals.warn} {warning_label}</span>'
            )
        if totals.skipped > 0:
            right_badges.append(
                f'<span class="badge badge-skipped">{totals.skipped} SKIPPED</span>'
            )

        if not right_badges and totals.passed == 0:
            right_badges.append('<span class="badge badge-skipped">NO CHECKS</span>')

        right_badges.append(_score_badge(suite.score))
        right_badges_html = f'<div class="suite-badges-row">{"".join(right_badges)}</div>'

        suite_stats_html = (
            f'<span class="suite-check-stats">{run_checks}/{total_checks} checks run</span>'
        )

        box_htmls = []
        for check in suite_checks:
            v_class = check.verdict.value.lower()
            tooltip_text = f"{_escape(check.name)} — {_escape(check.verdict.value)}"
            box_htmls.append(
                f'<div class="status-box status-box-{v_class}" '
                f'data-tooltip="{tooltip_text}" '
                f'data-suite-id="{_escape(check.suite_id)}" '
                f'data-check-id="{_escape(check.id)}" role="button" tabindex="0"></div>'
            )

        bars_content = (
            "".join(box_htmls)
            if box_htmls
            else (
                '<span class="text-muted">No matching issues</span>'
                if filtered
                else '<span class="text-muted">No checks selected</span>'
            )
        )

        suite_blocks.append(
            '<div class="suite-status-card">'
            '  <div class="suite-status-header">'
            '    <div class="suite-title-group">'
            f'      <span class="suite-title"><code>{_escape(suite.id)}</code></span>'
            f"      {suite_stats_html}"
            "    </div>"
            f"    {right_badges_html}"
            "  </div>"
            f'  <div class="status-bar-wrapper">{bars_content}</div>'
            "</div>"
        )

    suite_body = (
        "".join(suite_blocks)
        if suite_blocks
        else '<p class="empty-panel-message text-muted">No suites found.</p>'
    )

    details_body = (
        "".join(details_rows) if details_rows else _empty_issue_summary(results, filtered=filtered)
    )

    return (
        '<section id="report-overview" class="card panel-section">'
        f"{overview_header}"
        '  <div class="scrollable-content">'
        f'    <div class="suite-status-list">{suite_body}</div>'
        '    <div class="summary-toggle-wrapper">'
        '      <button id="toggle-summary-btn" class="btn-summary-toggle">'
        '        Show Issue Summary <span class="toggle-arrow">▼</span>'
        "      </button>"
        "    </div>"
        '    <div id="summary-table-container" class="table-container hidden-summary">'
        '      <table class="styled-table" id="summary-table"><thead><tr>'
        '        <th data-sort-column="0">Test <span class="sort-icon">↕</span></th>'
        '        <th data-sort-column="1">Suite <span class="sort-icon">↕</span></th>'
        '        <th data-sort-column="2">Result <span class="sort-icon">↕</span></th>'
        '        <th data-sort-column="3">Issue <span class="sort-icon">↕</span></th>'
        "      </tr></thead>"
        f"      <tbody>{details_body}</tbody>"
        "      </table>"
        "    </div>"
        "  </div>"
        '  <div class="panel-footer">'
        '    <button id="explore-checks-btn" class="btn-primary">Explore Checks &rarr;</button>'
        "  </div>"
        "</section>"
    )


def _details_rows(checks: Collection[CheckResult]) -> list[str]:
    issues = [check for check in checks if check.verdict not in (Verdict.PASS, Verdict.SKIPPED)]
    issues.sort(
        key=lambda check: (
            _VERDICT_ORDER[check.verdict],
            _SEVERITY_ORDER[check.severity.value],
            check.suite_id,
            check.id,
        )
    )
    rows = []
    for check in issues:
        v_class = check.verdict.value.lower()
        rows.append(
            "<tr>"
            f"<td><strong>{_escape(check.name)}</strong><br><code>{_escape(check.id)}</code></td>"
            f"<td><code>{_escape(check.suite_id)}</code></td>"
            f'<td><span class="badge badge-{v_class}">{_escape(check.verdict.value)}</span></td>'
            f"<td>{_escape(_issue(check))}</td>"
            "</tr>"
        )
    return rows


def _empty_issue_summary(results: Results, *, filtered: bool) -> str:
    if results.run.status is RunStatus.FAILED:
        message = "Run failed before any checks could be evaluated."
    elif results.totals.checks == results.totals.skipped:
        message = "No checks were evaluated."
    elif filtered:
        message = "No matching issues found."
    elif results.run.status is RunStatus.PARTIAL:
        message = "No issues found in the checks that were evaluated."
    else:
        message = "All clear! No issues found. 🎉"
    return f'<tr><td colspan="4" class="text-center text-muted">{message}</td></tr>'


def _issue(check: CheckResult) -> str:
    if check.evidence is not None:
        return check.evidence.message
    if check.error is not None:
        return check.error.message
    return "Check did not pass"


def _checks(checks: list[CheckResult], *, redacted: bool) -> str:
    items = "".join(_check(check, redacted=redacted) for check in checks)
    toggle_details = (
        ""
        if redacted
        else '      <button id="toggle-details-btn" class="btn-secondary">Toggle Details</button>'
    )
    return (
        '<section id="checks-panel" class="card panel-section hidden-panel">'
        '  <div class="checks-header">'
        "    <h2>Checks Explorer</h2>"
        '    <div class="checks-controls">'
        '      <input type="text" id="search-input" placeholder="🔍 Search checks...">'
        '      <div class="filter-group">'
        '        <button class="filter-btn active" data-filter="all">All</button>'
        '        <button class="filter-btn" data-filter="fail">Fail</button>'
        '        <button class="filter-btn" data-filter="warn">Warn</button>'
        '        <button class="filter-btn" data-filter="errored">Errored</button>'
        '        <button class="filter-btn" data-filter="pass">Pass</button>'
        '        <button class="filter-btn" data-filter="skipped">Skipped</button>'
        "      </div>"
        f"{toggle_details}"
        "    </div>"
        "  </div>"
        f'  <div id="checks-container" class="scrollable-content">{items}'
        '    <p id="checks-empty-message" class="empty-panel-message text-muted" hidden></p>'
        "  </div>"
        "</section>"
    )


def _check(check: CheckResult, *, redacted: bool) -> str:
    verdict_str = check.verdict.value.lower()
    classes = f"check-card check-{verdict_str}"
    suite_id_esc = _escape(check.suite_id)
    check_id_esc = _escape(check.id)
    key_esc = f"{suite_id_esc}::{check_id_esc}"
    name = _escape(check.name)
    pattern = _escape(check.pattern.value)
    name_html = (
        '<div class="check-name-group">'
        f"<h3>{name}</h3>"
        f'<span class="check-pattern">Pattern: <code>{pattern}</code></span>'
        "</div>"
        if redacted
        else f"<h3>{name}</h3>"
    )
    details = [f'<p class="meta-sub">Pattern: <code>{pattern}</code></p>']
    details.append(
        f"<p><strong>Expected:</strong> <code>{_escape(_json(check.expected))}</code></p>"
    )
    if check.measured is not None:
        details.append(
            f"<p><strong>Measured:</strong> <code>{_escape(_json(check.measured))}</code></p>"
        )
    if check.estimate is not False:
        details.append(
            f"<p><strong>Estimate:</strong> <code>{_escape(_json(check.estimate.model_dump()))}</code></p>"
        )
    if check.error is not None:
        details.append(
            '<div class="callout callout-error">'
            f"<strong>{_escape(check.error.code)}</strong>"
            f"<p>{_escape(check.error.message)}</p>"
            f"<p>💡 Fix: {_escape(check.error.fix)}</p>"
            "</div>"
        )
    if check.compiled_query is not None:
        details.append(
            f'<h4>Compiled Cypher</h4><pre class="code-block"><code>{_escape(check.compiled_query)}</code></pre>'
        )
    if check.evidence is not None:
        details.append(_evidence(check))
    details_html = (
        ""
        if redacted
        else (
            f'<details {_details_open(check)} class="check-details">'
            "<summary>View Details & Evidence</summary>"
            f'<div class="details-content">{"".join(details)}</div>'
            "</details>"
        )
    )

    return (
        f'<article class="{classes}" data-verdict="{verdict_str}" data-check-key="{key_esc}" '
        f'data-suite-id="{suite_id_esc}" data-check-id="{check_id_esc}">'
        '<div class="check-title-row">'
        f"{name_html}"
        f'<code class="check-id">{key_esc}</code>'
        "</div>"
        f"{details_html}"
        "</article>"
    )


def _evidence(check: CheckResult) -> str:
    assert check.evidence is not None
    rows = []
    for element in check.evidence.elements:
        if element.kind == "rel":
            descriptor = element.type
        elif element.kind == "node":
            descriptor = ", ".join(element.labels or [])
        else:
            descriptor = "aggregate measurement scope"
        rows.append(
            "<tr>"
            f'<td><span class="kind-tag">{_escape(element.kind)}</span></td>'
            f"<td><code>{_escape(element.id)}</code></td>"
            f"<td>{_escape(descriptor or '')}</td>"
            "</tr>"
        )
    return (
        "<h4>Evidence</h4>"
        f'<p class="text-muted">{_escape(check.evidence.message)} '
        f"({check.evidence.total_count} total, cap {check.evidence.cap})</p>"
        '<table class="styled-table compact"><thead><tr><th>Kind</th><th>ID</th><th>Labels / Type / Scope</th></tr></thead>'
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def _details_open(check: CheckResult) -> str:
    return ""


def _target_count(results: Results, field: str) -> str:
    value = getattr(results.run.target, field, None) if results.run.target is not None else None
    return '<span class="text-muted">Unavailable</span>' if value is None else f"{value:,}"


def _score_badge(score: int | None) -> str:
    value, color = (
        ("N/A", "na")
        if score is None
        else (str(score), "good" if score == 100 else "warn" if score >= 50 else "bad")
    )
    return f'<span class="badge badge-score badge-score-{color}">SCORE: {value}</span>'


def _json(value: object) -> str:
    return json.dumps(json_compatible(value), sort_keys=True)


def _escape(value: object) -> str:
    return html.escape(str(value), quote=True)


_CSS = """
:root {
  color-scheme: light;
  --bg-main: #f8fafc;
  --bg-card: #ffffff;
  --bg-subtle: #f8fafc;
  --bg-header: #0f172a;
  --text-main: #0f172a;
  --text-muted: #64748b;
  --border: #e2e8f0;
  --radius: 8px;
  --shadow: 0 1px 3px 0 rgba(0, 0, 0, 0.05);

  --code-bg: #f1f5f9;
  --code-text: #0f172a;

  --pass-color: #10b981;
  --pass-bg: #ecfdf5;
  --fail-color: #ef4444;
  --fail-bg: #fef2f2;
  --warn-color: #f59e0b;
  --warn-bg: #fffbeb;
  --errored-color: #8b5cf6;
  --errored-bg: #f5f3ff;
  --skipped-color: #64748b;
  --skipped-bg: #f8fafc;
}

[data-theme="dark"] {
  color-scheme: dark;
  --bg-main: #0f172a;
  --bg-card: #1e293b;
  --bg-subtle: #0f172a;
  --bg-header: #020617;
  --text-main: #f8fafc;
  --text-muted: #94a3b8;
  --border: #334155;
  --shadow: 0 1px 3px 0 rgba(0, 0, 0, 0.3);

  --code-bg: #0f172a;
  --code-text: #f8fafc;

  --pass-bg: #064e3b;
  --fail-bg: #7f1d1d;
  --warn-bg: #78350f;
  --errored-bg: #4c1d95;
  --skipped-bg: #1e293b;
}

::-webkit-scrollbar {
  width: 8px;
  height: 8px;
}
::-webkit-scrollbar-track {
  background: var(--bg-main);
}
::-webkit-scrollbar-thumb {
  background: var(--border);
  border-radius: 4px;
}
::-webkit-scrollbar-thumb:hover {
  background: var(--text-muted);
}
::-webkit-scrollbar-button { display: none; width: 0; height: 0; }

* { box-sizing: border-box; }
html, body {
  height: 100vh;
  margin: 0;
  overflow: hidden;
  font-size: 15px;
}
body {
  display: flex;
  flex-direction: column;
  font-family: system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  color: var(--text-main);
  background-color: var(--bg-main);
  line-height: 1.5;
  transition: background-color 0.2s ease, color 0.2s ease;
}

.navbar {
  flex-shrink: 0;
  background: var(--bg-header);
  color: #fff;
  padding: 14px 24px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 20px;
  border-bottom: 1px solid var(--border);
}
.brand-container { min-width: 0; }
.eyebrow {
  text-transform: uppercase;
  letter-spacing: 0.08em;
  font-size: 11px;
  color: #94a3b8;
  font-weight: 600;
}
.navbar h1 { margin: 0; font-weight: 400; }
.navbar h1, .panel-section h2 { font-size: 18px; }
.header-status { display: flex; align-items: center; gap: 9px; flex-wrap: wrap; }
.header-status-message { font-weight: 400; }
.status-pill { padding: 2px 7px; border-radius: 999px; color: #fff; font-size: 10px; font-weight: 800; letter-spacing: 0.06em; }
.status-pill-complete { background: var(--pass-color); }
.status-pill-warning { background: var(--warn-color); color: #422006; }
.status-pill-partial { background: var(--errored-color); }
.status-pill-error { background: var(--fail-color); }
.status-pill-redacted { background: #64748b; color: #fff; }
.header-status-action { margin-left: 6px; padding: 0; border: 0; background: transparent; color: inherit; font: inherit; font-weight: 700; text-decoration: underline; cursor: pointer; }
.header-status-fix { margin-left: 6px; color: #cbd5e1; font-size: 12px; font-weight: 400; }
.hidden-status-fix { display: none; }
.navbar-actions { display: flex; align-items: center; gap: 12px; }
.report-navigation-status { min-width: 94px; color: #cbd5e1; font-size: 12px; text-align: right; }

.theme-toggle-btn {
  background: transparent;
  color: #fff;
  border: none;
  outline: none;
  border-radius: 50%;
  width: 38px;
  height: 38px;
  font-size: 20px;
  line-height: 1;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: background-color 0.2s ease, transform 0.1s ease;
}
.theme-toggle-btn:hover {
  background: rgba(255, 255, 255, 0.1);
  transform: scale(1.05);
}

.exit-0 { color: var(--pass-color); font-weight: 600; }
.exit-1 { color: var(--fail-color); font-weight: 600; }
.exit-2 { color: var(--warn-color); font-weight: 600; }
.exit-3 { color: var(--skipped-color); font-weight: 600; }

.summary-top-bar {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 20px;
  height: 96px;
  min-height: 96px;
  padding-bottom: 14px;
  border-bottom: 1px solid var(--border);
  margin-bottom: 14px;
  flex-shrink: 0;
}
.summary-meta-col {
  display: flex;
  flex-direction: column;
  gap: 2px;
  flex: 1;
}
.summary-meta-grid {
  display: flex;
  align-items: flex-start;
  gap: 24px;
  font-size: 13px;
  margin-top: 6px;
  flex-wrap: wrap;
}
.meta-item { display: flex; flex-direction: column; }
.graph-count-item { min-width: 92px; }
.meta-item .meta-label {
  font-size: 11px;
  text-transform: uppercase;
  color: var(--text-muted);
  font-weight: 700;
  letter-spacing: 0.05em;
  margin-bottom: 2px;
}

/* Status Graph / Suite Status Layout */
.suite-status-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.empty-panel-message { margin: 0; }

.suite-status-card {
  background: var(--bg-subtle);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 14px;
}

.suite-status-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
  gap: 12px;
}

.suite-title-group {
  display: flex;
  align-items: baseline;
  gap: 10px;
  flex-wrap: wrap;
}

.suite-title {
  font-weight: 600;
  font-size: 14px;
}

.suite-check-stats {
  font-size: 12px;
  color: var(--text-muted);
}

.suite-badges-row {
  display: flex;
  flex-direction: row;
  align-items: center;
  gap: 6px;
  flex-shrink: 0;
  flex-wrap: wrap;
  justify-content: flex-end;
}

.status-bar-wrapper {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  align-items: center;
}

.status-box {
  width: 14px;
  height: 30px;
  border-radius: 3px;
  cursor: pointer;
  position: relative;
  transition: transform 0.15s ease, filter 0.15s ease;
  flex-shrink: 0;
}

.status-box:hover {
  transform: scaleY(1.15) scaleX(1.1);
  filter: brightness(1.1);
  z-index: 10;
}

.status-box-pass { background-color: var(--pass-color); }
.status-box-fail { background-color: var(--fail-color); }
.status-box-warn { background-color: var(--warn-color); }
.status-box-errored { background-color: var(--errored-color); }
.status-box-skipped { background-color: var(--skipped-color); }

/* Toggleable Issue Summary */
.summary-toggle-wrapper {
  margin-top: 16px;
  margin-bottom: 8px;
}

.btn-summary-toggle {
  background: transparent;
  border: 1px solid var(--border);
  color: var(--text-main);
  padding: 8px 14px;
  border-radius: 6px;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  gap: 8px;
  transition: background-color 0.2s ease, border-color 0.2s ease;
}

.btn-summary-toggle:hover {
  background: var(--bg-subtle);
  border-color: #2563eb;
  color: #2563eb;
}

.toggle-arrow {
  font-size: 10px;
  transition: transform 0.2s ease;
}

.hidden-summary {
  display: none !important;
}

/* Global Floating Tooltip */
.floating-tooltip {
  position: fixed;
  transform: translate(-50%, -100%);
  background: #0f172a;
  color: #ffffff;
  padding: 6px 12px;
  border-radius: 6px;
  font-size: 12px;
  font-weight: 500;
  pointer-events: none;
  z-index: 9999;
  opacity: 0;
  transition: opacity 0.15s ease;
  box-shadow: 0 4px 14px rgba(0, 0, 0, 0.25);
  white-space: nowrap;
}

[data-theme="dark"] .floating-tooltip {
  background: #f8fafc;
  color: #0f172a;
}

.floating-tooltip.visible {
  opacity: 1;
}

.dashboard-body {
  flex: 1;
  display: flex;
  flex-direction: column;
  max-width: 1600px;
  width: 100%;
  margin: 0 auto;
  padding: 16px 20px;
  gap: 12px;
  overflow: hidden;
}

.dashboard-grid {
  flex: 1;
  display: grid;
  grid-template-columns: 260px minmax(0, 900px);
  justify-content: center;
  gap: 16px;
  min-height: 0;
  transition: all 0.4s cubic-bezier(0.16, 1, 0.3, 1);
}

.dashboard-grid.has-checks {
  grid-template-columns: 260px minmax(0, 1fr) minmax(0, 1fr);
  justify-content: stretch;
}

#report-run-title, #report-overview, #checks-panel { transition: opacity 0.15s ease; }
body.report-navigation-loading #report-run-title,
body.report-navigation-loading #report-overview,
body.report-navigation-loading #checks-panel { opacity: 0.55; }

.card {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 18px;
  box-shadow: var(--shadow);
}

.panel-section {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  margin-bottom: 0;
  overflow: hidden;
}
.panel-header, .checks-header { flex-shrink: 0; }
.panel-section h2 { margin: 0 0 4px 0; }
.panel-section h3 { margin: 14px 0 8px 0; font-size: 14px; }

.scrollable-content {
  flex: 1;
  overflow-y: auto;
  padding-right: 6px;
}

.report-explorer { padding: 18px; }
.explorer-header {
  display: flex;
  flex-direction: column;
  justify-content: flex-start;
  flex-shrink: 0;
  height: 96px;
  min-height: 96px;
  gap: 10px;
  padding-bottom: 14px;
  border-bottom: 1px solid var(--border);
}
#report-search-input {
  width: 100%;
  padding: 6px 12px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--bg-card);
  color: var(--text-main);
  font-size: 13px;
  outline: none;
}
.explorer-scroll { margin-top: 20px; }
.report-group + .report-group { margin-top: 18px; }
.report-group-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  list-style: none;
  cursor: pointer;
  user-select: none;
}
.report-group[open] > .report-group-heading { margin-bottom: 7px; }
.report-group-heading::-webkit-details-marker { display: none; }
.report-group-heading::after { content: "▾"; color: var(--text-muted); font-size: 18px; line-height: 1; transition: transform 0.15s ease; }
.report-group:not([open]) > .report-group-heading::after { transform: rotate(-90deg); }
.report-group-heading h3 { flex: 1; }
.report-group-heading h3 { margin: 0; font-size: 12px; text-transform: uppercase; letter-spacing: 0.04em; color: var(--text-muted); }
.report-list { display: flex; flex-direction: column; gap: 7px; }
.explorer-loading, .empty-report-list { margin: 4px 0; font-size: 12px; }
.report-row {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  align-items: start;
  gap: 8px;
  padding: 9px;
  border: 1px solid var(--border);
  border-radius: 7px;
  background: var(--bg-card);
}
.report-row.current { border-color: #2563eb; background: #eff6ff; }
[data-theme="dark"] .report-row.current { background: #172554; }
.report-select { margin: 3px 0 0; accent-color: #2563eb; cursor: pointer; }
.report-link { min-width: 0; color: var(--text-main); text-decoration: none; }
.report-link:hover .report-id { color: #2563eb; }
.report-id { display: block; overflow: hidden; font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; font-size: 12px; font-weight: 700; text-overflow: ellipsis; white-space: nowrap; }
.report-meta { display: block; margin-top: 3px; color: var(--text-muted); font-size: 10px; line-height: 1.35; }
.report-status { text-transform: capitalize; }
.explorer-footer { flex-direction: column; align-items: stretch; gap: 6px; margin-top: 5px; margin-bottom: 0; padding-top: 5px; padding-bottom: 0; border-bottom: 0; }
.explorer-status { display: flex; align-items: flex-start; justify-content: space-between; gap: 8px; min-height: 16px; margin: 0; color: var(--text-muted); font-size: 11px; line-height: 1.35; }
.explorer-status:empty { display: none; }
.explorer-status.error { color: var(--fail-color); }
.explorer-status-message { min-width: 0; flex: 1; }
.explorer-status-dismiss { flex: 0 0 auto; padding: 0; border: 0; background: transparent; color: inherit; font-size: 16px; line-height: 1; cursor: pointer; }
.explorer-selection-actions { position: relative; z-index: 1; display: grid; grid-template-columns: 1fr 1fr; gap: 7px; }
.explorer-comparison-actions { display: grid; grid-template-columns: 1fr; gap: 7px; }
.explorer-selection-actions button, .explorer-comparison-actions button { width: 100%; min-width: 0; }
.btn-danger { padding: 6px 12px; border: 1px solid rgba(239, 68, 68, 0.35); border-radius: 6px; background: var(--fail-bg); color: var(--fail-color); font-size: 12px; font-weight: 600; cursor: pointer; }
.comparison-dialog { width: min(760px, calc(100vw - 32px)); max-height: min(720px, calc(100vh - 32px)); padding: 0; overflow: hidden; border: 1px solid var(--border); border-radius: 10px; background: var(--bg-card); color: var(--text-main); box-shadow: 0 24px 80px rgba(15, 23, 42, 0.35); }
.comparison-dialog::backdrop, .confirmation-dialog::backdrop { background: rgba(15, 23, 42, 0.62); }
.comparison-dialog-header { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 14px 18px; border-bottom: 1px solid var(--border); }
.comparison-dialog-header h2 { margin: 0; font-size: 17px; }
.dialog-close { width: 32px; height: 32px; padding: 0; border: 0; border-radius: 6px; background: transparent; color: var(--text-muted); font-size: 24px; line-height: 1; cursor: pointer; }
.dialog-close:hover { background: var(--bg-subtle); color: var(--text-main); }
#report-comparison-content { max-height: calc(100vh - 130px); margin: 0; padding: 18px; overflow: auto; background: var(--bg-card); color: var(--text-main); font-size: 12px; line-height: 1.55; white-space: pre-wrap; }
.report-missing-dialog { width: min(520px, calc(100vw - 32px)); }
.report-missing-title { min-width: 0; }
.report-missing-title p { margin: 4px 0 0; color: var(--text-muted); font-size: 12px; line-height: 1.4; }
.report-missing-name { color: var(--fail-color); font-weight: 400; }
.report-missing-content { padding: 18px; border-top: 1px solid var(--border); }
.report-missing-content p { margin: 0; color: var(--text-main); font-size: 13px; line-height: 1.5; }
.comparison-status-complete, .comparison-delta-positive { color: var(--pass-color); font-weight: 700; }
.comparison-status-partial { color: var(--errored-color); font-weight: 700; }
.comparison-status-failed, .comparison-delta-negative { color: var(--fail-color); font-weight: 700; }
.confirmation-dialog { width: min(420px, calc(100vw - 32px)); padding: 20px; border: 1px solid var(--border); border-radius: 10px; background: var(--bg-card); color: var(--text-main); box-shadow: 0 24px 80px rgba(15, 23, 42, 0.35); }
.confirmation-dialog h2 { margin: 0; font-size: 18px; }
.confirmation-dialog p { margin: 8px 0 20px; color: var(--text-muted); font-size: 13px; }
.confirmation-actions { display: flex; justify-content: flex-end; gap: 8px; }

.panel-footer {
  flex-shrink: 0;
  margin-top: 12px;
  padding-top: 12px;
  display: flex;
  justify-content: flex-end;
}

.btn-primary {
  background: #2563eb;
  color: #ffffff;
  border: none;
  padding: 8px 16px;
  border-radius: 6px;
  font-size: 12px;
  cursor: pointer;
  transition: background-color 0.2s ease, transform 0.1s ease;
}
.btn-primary:hover { background: #1d4ed8; transform: translateY(-1px); }

.hidden-panel {
  display: none !important;
}

.visible-panel {
  animation: slideInRight 0.4s cubic-bezier(0.16, 1, 0.3, 1) forwards;
}

@keyframes slideInRight {
  from {
    opacity: 0;
    transform: translateX(30px);
  }
  to {
    opacity: 1;
    transform: translateX(0);
  }
}

.badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-height: 22px;
  padding: 3px 9px;
  border: 0;
  border-radius: 999px;
  color: #fff;
  font-size: 11px;
  font-weight: 700;
  line-height: 1;
  text-transform: uppercase;
  white-space: nowrap;
}
.badge-score-good { background: #059669; }
.badge-fail, .badge-score-bad { background: #dc2626; }
.badge-warn, .badge-score-warn { background: #d97706; }
.badge-errored { background: #7c3aed; }
.badge-skipped, .badge-score-na { background: #64748b; }
.badge-score { order: 999; }

.table-container { overflow-x: auto; margin-top: 8px; }
.styled-table { width: 100%; border-collapse: collapse; font-size: 13px; text-align: left; }
.styled-table th {
  background: var(--bg-subtle);
  padding: 10px 12px;
  border-bottom: 2px solid var(--border);
  color: var(--text-muted);
  font-weight: 600;
  cursor: pointer;
  user-select: none;
  transition: background-color 0.15s ease;
}
.styled-table th:hover { background: var(--border); }
.styled-table td { padding: 10px 12px; border-bottom: 1px solid var(--border); }

.sort-icon {
  font-size: 11px;
  opacity: 0.5;
  margin-left: 4px;
}

.checks-header {
  display: flex;
  flex-direction: column;
  justify-content: flex-start;
  height: 96px;
  min-height: 96px;
  gap: 10px;
  padding-bottom: 14px;
  border-bottom: 1px solid var(--border);
  margin-bottom: 14px;
  flex-shrink: 0;
}
.checks-controls { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
#search-input {
  padding: 6px 12px;
  border: 1px solid var(--border);
  border-radius: 6px;
  font-size: 13px;
  outline: none;
  background: var(--bg-card);
  color: var(--text-main);
}
.filter-group { display: flex; border: 1px solid var(--border); border-radius: 6px; overflow: hidden; }
.filter-btn {
  background: var(--bg-card);
  color: var(--text-muted);
  border: none;
  padding: 6px 12px;
  font-size: 12px;
  cursor: pointer;
  border-right: 1px solid var(--border);
}
.filter-btn:last-child { border-right: none; }
.filter-btn.active { background: var(--bg-header); color: #fff; font-weight: 600; }

.btn-secondary {
  background: var(--bg-subtle);
  color: var(--text-main);
  border: 1px solid var(--border);
  padding: 6px 12px;
  border-radius: 6px;
  font-size: 12px;
  cursor: pointer;
}
.btn-secondary:disabled, .btn-danger:disabled, .btn-primary:disabled, .btn-primary:disabled:hover {
  cursor: not-allowed;
  opacity: 1;
  border-color: var(--border);
  background: var(--bg-subtle);
  color: var(--text-muted);
  transform: none;
}

.check-card {
  border: 1px solid var(--border);
  border-radius: 6px;
  margin-bottom: 12px;
  padding: 14px;
  background: var(--bg-card);
  transition: border-color 0.3s ease, box-shadow 0.3s ease;
}
.check-card.check-fail { border-left: 4px solid var(--fail-color); }
.check-card.check-warn { border-left: 4px solid var(--warn-color); }
.check-card.check-errored { border-left: 4px solid var(--errored-color); }
.check-card.check-pass { border-left: 4px solid var(--pass-color); }
.check-card.check-skipped { border-left: 4px solid var(--skipped-color); }

.check-card.card-highlight {
  animation: highlightPulse 1.5s ease;
}

@keyframes highlightPulse {
  0% { outline: 2px solid #2563eb; box-shadow: 0 0 12px rgba(37, 99, 235, 0.5); }
  100% { outline: 0px solid transparent; box-shadow: var(--shadow); }
}

.check-title-row { display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: start; gap: 8px; }
.check-title-row h3 { margin: 0; overflow-wrap: anywhere; font-size: 15px; font-weight: 600; line-height: 1.45; }
.check-name-group { min-width: 0; }
.check-pattern { display: block; margin-top: 2px; color: var(--text-muted); font-size: 12px; }
.check-id { max-width: 100%; overflow-wrap: anywhere; color: var(--text-muted); font-size: 12px; }

.check-details { margin-top: 8px; font-size: 13px; }
.check-details summary { cursor: pointer; font-weight: 600; color: #000000; font-size: 13px; }
[data-theme="dark"] .check-details summary { color: #ffffff; }

.details-content { margin-top: 8px; padding-top: 8px; border-top: 1px solid var(--border); }

code, pre { font-family: 'ui-monospace', SFMono-Regular, Menlo, Monaco, Consolas, monospace; }
code { background: var(--code-bg); color: var(--code-text); padding: 2px 5px; border-radius: 4px; font-size: 88%; }

pre.code-block { background: #020617; color: #f8fafc; padding: 12px; border-radius: 6px; overflow-x: auto; font-size: 12px; margin: 8px 0; }
pre.code-block code { background: transparent; color: inherit; padding: 0; border-radius: 0; font-size: 100%; }

.text-muted { color: var(--text-muted); }
p.text-muted { margin-top: 0; }
.text-danger { color: var(--fail-color); }
.text-center { text-align: center; }
.meta-sub { font-size: 12px; color: var(--text-muted); }
.callout { padding: 10px; border-radius: 6px; margin: 8px 0; font-size: 13px; }
.callout-error { background: var(--fail-bg); border: 1px solid rgba(239, 68, 68, 0.3); }
.kind-tag { background: var(--bg-subtle); color: var(--text-muted); border: 1px solid var(--border); padding: 2px 5px; border-radius: 3px; font-size: 11px; }

@media (max-width: 1024px) {
  html, body { height: auto; overflow: auto; }
  .dashboard-body { overflow: visible; }
  .navbar { flex-direction: row; justify-content: space-between; }
  .summary-top-bar, .checks-header { height: auto; min-height: 0; }
  .summary-top-bar { flex-direction: column; align-items: flex-start; }
  .summary-meta-grid { flex-direction: column; gap: 8px; }
  .dashboard-grid, .dashboard-grid.has-checks { grid-template-columns: 1fr; }
  .panel-section { max-height: 500px; }
}
""".strip()


_JS = """
let activeVerdictFilter = 'all';
let globalTooltip = null;
let sortDirections = {};
let reportHistory = [];
let pendingDeleteIds = [];
let reportGroupStateBeforeSearch = null;
let reportNavigationController = null;
let reportNavigationSequence = 0;
let checkDetailsOpenPreference = null;
const selectedReportIds = new Set();
const CHECK_FILTERS_STORAGE_KEY = 'graphcheck.checksExplorerFilters';
const CHECKS_EXPLORER_STORAGE_KEY = 'graphcheck.checksExplorerOpen';
const REPORT_EXPLORER_STATE_STORAGE_KEY = 'graphcheck.reportExplorerNavigation';
const THEME_STORAGE_KEY = 'graphcheck.theme';
const REPORT_EXPLORER_TOKEN = document.querySelector('meta[name="graphcheck-explorer-token"]')?.content || '';

function setReportExplorerStatus(message, error = false) {
  const status = document.getElementById('report-explorer-status');
  if (!status) return;
  status.replaceChildren();
  status.classList.toggle('error', error);
  if (!message) return;
  const text = document.createElement('span');
  text.className = 'explorer-status-message';
  text.textContent = message;
  status.appendChild(text);
  if (!error) return;
  const dismiss = document.createElement('button');
  dismiss.className = 'explorer-status-dismiss';
  dismiss.type = 'button';
  dismiss.setAttribute('aria-label', 'Dismiss report error');
  dismiss.textContent = '×';
  dismiss.addEventListener('click', () => setReportExplorerStatus(''));
  status.appendChild(dismiss);
}

function reportHref(runId) {
  return `/report?id=${encodeURIComponent(runId)}`;
}

function setReportNavigationLoading(loading) {
  document.body.classList.toggle('report-navigation-loading', loading);
  document.querySelectorAll('#report-run-title, #report-overview, #checks-panel').forEach(
    fragment => fragment.setAttribute('aria-busy', String(loading))
  );
  const status = document.getElementById('report-navigation-status');
  if (status) status.textContent = loading ? 'Loading report…' : '';
}

function reportFragment(markup, expectedId) {
  const template = document.createElement('template');
  template.innerHTML = String(markup || '').trim();
  const fragment = template.content.firstElementChild;
  if (!fragment || fragment.id !== expectedId) throw new Error(`Invalid ${expectedId} report fragment.`);
  return fragment;
}

function updateCurrentReportRow(runId) {
  document.querySelectorAll('.report-row').forEach(
    row => row.classList.toggle('current', row.dataset.reportId === runId)
  );
}

function applyReport(report, historyMode = 'push') {
  const fragmentIds = {
    run_title: 'report-run-title',
    overview: 'report-overview',
    checks: 'checks-panel',
  };
  const replacements = Object.entries(fragmentIds).map(([name, id]) => {
    const current = document.getElementById(id);
    if (!current) throw new Error(`Missing ${id} report container.`);
    return [current, reportFragment(report.fragments?.[name], id)];
  });
  const checksOpen = !document.getElementById('checks-panel').classList.contains('hidden-panel');
  const issueSummaryExpanded = !document.getElementById('summary-table-container').classList.contains('hidden-summary');
  const scrollPosition = { x: window.scrollX, y: window.scrollY };
  replacements.forEach(([current, replacement]) => current.replaceWith(replacement));
  sortDirections = {};
  initReportSpecificInteractions();
  setSummaryTableExpanded(issueSummaryExpanded);
  applyCheckDetailsPreference();
  if (checksOpen) showChecksExplorer(false);
  restoreCheckFilters();
  document.title = report.title;
  if (historyMode === 'push') history.pushState({ reportId: report.id }, '', report.href);
  else if (historyMode === 'replace') history.replaceState({ reportId: report.id }, '', report.href);
  const explorer = document.getElementById('report-explorer');
  if (explorer) explorer.dataset.currentReport = report.id;
  updateCurrentReportRow(report.id);
  closeReportMissingDialog();
  requestAnimationFrame(() => window.scrollTo(scrollPosition.x, scrollPosition.y));
}

async function navigateReport(href, historyMode = 'push') {
  const target = new URL(href, window.location.href);
  const runId = target.searchParams.get('id');
  if (!runId) throw new Error('The report link is missing an ID.');
  const requestSequence = ++reportNavigationSequence;
  reportNavigationController?.abort();
  reportNavigationController = new AbortController();
  setReportNavigationLoading(true);
  try {
    const payload = await reportExplorerRequest(`/api/report?id=${encodeURIComponent(runId)}`, {
      signal: reportNavigationController.signal,
    });
    if (requestSequence !== reportNavigationSequence) return;
    applyReport(payload.report, historyMode);
  } catch (error) {
    if (error.name !== 'AbortError' && requestSequence === reportNavigationSequence) {
      openReportMissingDialog(runId);
    }
  } finally {
    if (requestSequence === reportNavigationSequence) setReportNavigationLoading(false);
  }
}

function handleReportLinkClick(event) {
  const link = event.target.closest?.('.report-link');
  if (!link || event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
  if (!REPORT_EXPLORER_TOKEN) {
    persistReportExplorerNavigation(link.closest('.report-row')?.dataset.reportId || '');
    return;
  }
  event.preventDefault();
  navigateReport(link.getAttribute('href'));
}

function reportRow(report) {
  const row = document.createElement('div');
  const checkbox = document.createElement('input');
  const link = document.createElement('a');
  const id = document.createElement('span');
  const meta = document.createElement('span');
  const current = new URLSearchParams(window.location.search).get('id') === report.id;
  row.className = `report-row${current ? ' current' : ''}`;
  row.dataset.reportId = report.id;
  checkbox.className = 'report-select';
  checkbox.type = 'checkbox';
  checkbox.checked = selectedReportIds.has(report.id);
  checkbox.setAttribute('aria-label', `Select report ${report.id}`);
  checkbox.addEventListener('change', () => {
    checkbox.checked ? selectedReportIds.add(report.id) : selectedReportIds.delete(report.id);
    updateReportActions();
  });
  link.className = 'report-link';
  link.href = report.href || reportHref(report.id);
  link.title = `Open report ${report.id}`;
  id.className = 'report-id';
  id.textContent = report.id;
  meta.className = 'report-meta';
  meta.textContent = `${formatReportFinishedAt(report.finished_at)} · ${report.status}`;
  link.append(id, meta);
  row.append(checkbox, link);
  return row;
}

function formatReportFinishedAt(value) {
  const finishedAt = new Date(value);
  if (Number.isNaN(finishedAt.getTime())) return String(value || '');
  const pad = part => String(part).padStart(2, '0');
  return `${finishedAt.getFullYear()}-${pad(finishedAt.getMonth() + 1)}-${pad(finishedAt.getDate())} at ${pad(finishedAt.getHours())}:${pad(finishedAt.getMinutes())}:${pad(finishedAt.getSeconds())}`;
}

function appendReportRows(list, reports, emptyMessage) {
  list.replaceChildren();
  if (reports.length) reports.forEach(report => list.appendChild(reportRow(report)));
  else {
    const empty = document.createElement('p');
    empty.className = 'empty-report-list text-muted';
    empty.textContent = emptyMessage;
    list.appendChild(empty);
  }
}

function reportMatchesSearch(report, query) {
  return !query || `${report.id} ${report.finished_at} ${report.status}`.toLowerCase().includes(query);
}

function reportGroupState() {
  return Object.fromEntries(Array.from(document.querySelectorAll('.report-group[id]')).map(group => [group.id, group.open]));
}

function applyReportGroupState(state) {
  Object.entries(state || {}).forEach(([id, open]) => {
    const group = document.getElementById(id);
    if (group) group.open = Boolean(open);
  });
}

function persistReportExplorerNavigation(reportId) {
  const scroll = document.querySelector('.explorer-scroll');
  try {
    sessionStorage.setItem(REPORT_EXPLORER_STATE_STORAGE_KEY, JSON.stringify({
      reportId,
      scrollTop: scroll?.scrollTop || 0,
      groups: reportGroupState(),
    }));
  } catch {}
}

function restoreReportExplorerNavigation() {
  let state = null;
  try {
    state = JSON.parse(sessionStorage.getItem(REPORT_EXPLORER_STATE_STORAGE_KEY) || 'null');
    sessionStorage.removeItem(REPORT_EXPLORER_STATE_STORAGE_KEY);
  } catch {}
  if (!state) return;
  applyReportGroupState(state.groups);
  const reportIndex = reportHistory.findIndex(report => report.id === state.reportId);
  if (reportIndex > 5) document.getElementById('older-report-group').open = true;
  else if (reportIndex > 0) document.getElementById('last-five-report-group').open = true;
  const scroll = document.querySelector('.explorer-scroll');
  const row = Array.from(document.querySelectorAll('.report-row')).find(item => item.dataset.reportId === state.reportId);
  requestAnimationFrame(() => {
    if (!scroll) return;
    scroll.scrollTop = Number(state.scrollTop) || 0;
    if (!row) return;
    const scrollBounds = scroll.getBoundingClientRect();
    const rowBounds = row.getBoundingClientRect();
    if (rowBounds.top < scrollBounds.top || rowBounds.bottom > scrollBounds.bottom) row.scrollIntoView({ block: 'nearest' });
  });
}

function renderReportLists() {
  const latestList = document.getElementById('latest-report-list');
  const recentList = document.getElementById('last-five-report-list');
  const olderList = document.getElementById('older-report-list');
  if (!latestList || !recentList || !olderList) return;
  const query = (document.getElementById('report-search-input')?.value || '').trim().toLowerCase();
  const latest = reportHistory.slice(0, 1).filter(report => reportMatchesSearch(report, query));
  const recent = reportHistory.slice(1, 6).filter(report => reportMatchesSearch(report, query));
  const older = reportHistory.slice(6).filter(report => reportMatchesSearch(report, query));
  appendReportRows(latestList, latest, query ? 'No matching latest report.' : 'No reports found.');
  appendReportRows(recentList, recent, query ? 'No matching reports.' : 'No recent reports.');
  appendReportRows(olderList, older, query ? 'No matching reports.' : 'No older reports.');
  if (query) document.querySelectorAll('.report-group').forEach(group => group.open = Boolean(group.querySelector('.report-row')));
}

function renderReportHistory(reports, restoreNavigation = false) {
  reportHistory = reports;
  const availableIds = new Set(reports.map(report => report.id));
  Array.from(selectedReportIds).forEach(runId => {
    if (!availableIds.has(runId)) selectedReportIds.delete(runId);
  });
  renderReportLists();
  updateReportActions();
  if (restoreNavigation) restoreReportExplorerNavigation();
}

function filterReportHistory() {
  const query = (document.getElementById('report-search-input')?.value || '').trim();
  if (query && !reportGroupStateBeforeSearch) reportGroupStateBeforeSearch = reportGroupState();
  if (!query && reportGroupStateBeforeSearch) {
    const state = reportGroupStateBeforeSearch;
    reportGroupStateBeforeSearch = null;
    renderReportLists();
    applyReportGroupState(state);
    return;
  }
  renderReportLists();
}

function updateReportActions() {
  const count = selectedReportIds.size;
  const clear = document.getElementById('clear-report-selection-btn');
  const compare = document.getElementById('compare-reports-btn');
  const compareRecent = document.getElementById('compare-most-recent-btn');
  const remove = document.getElementById('delete-reports-btn');
  if (clear) clear.disabled = count === 0;
  if (compare) compare.disabled = count !== 2;
  if (compareRecent) compareRecent.disabled = reportHistory.length < 2;
  if (remove) remove.disabled = count === 0;
  setReportExplorerStatus(count ? `${count} report${count === 1 ? '' : 's'} selected` : '');
}

async function reportExplorerRequest(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    credentials: 'same-origin',
    headers: {
      'Content-Type': 'application/json',
      'X-GraphCheck-Token': REPORT_EXPLORER_TOKEN,
      ...(options.headers || {}),
    },
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || `Report explorer request failed (${response.status})`);
  return payload;
}

function renderComparisonMessage(content, message) {
  const lines = String(message).split('\\n');
  content.replaceChildren();
  lines.forEach((line, index) => {
    const status = line.match(/^Status: (complete|partial|failed) -> (complete|partial|failed)$/);
    const delta = line.match(/^(  .+: .* )(\\([+-]\\d+\\))$/);
    if (status) {
      content.append('Status: ');
      [status[1], status[2]].forEach((value, statusIndex) => {
        if (statusIndex) content.append(' -> ');
        const span = document.createElement('span');
        span.className = `comparison-status-${value}`;
        span.textContent = value;
        content.append(span);
      });
    } else if (delta && Number.parseInt(delta[2].slice(1, -1), 10) !== 0) {
      const value = Number.parseInt(delta[2].slice(1, -1), 10);
      const span = document.createElement('span');
      span.className = value > 0 ? 'comparison-delta-positive' : 'comparison-delta-negative';
      span.textContent = delta[2];
      content.append(delta[1], span);
    } else {
      content.append(line);
    }
    if (index < lines.length - 1) content.append('\\n');
  });
}

function openComparisonDialog(message) {
  const dialog = document.getElementById('report-comparison-dialog');
  const content = document.getElementById('report-comparison-content');
  if (!dialog || !content) return;
  renderComparisonMessage(content, message);
  if (typeof dialog.showModal === 'function' && !dialog.open) dialog.showModal();
  else dialog.setAttribute('open', '');
  content.focus();
}

function openReportMissingDialog(reportName) {
  const dialog = document.getElementById('report-missing-dialog');
  const diagnostic = document.getElementById('report-missing-diagnostic');
  if (!dialog || !diagnostic) return;
  const name = document.createElement('span');
  name.className = 'report-missing-name';
  name.textContent = reportName || 'Selected report';
  diagnostic.replaceChildren(name, ' could not be found or opened.');
  if (typeof dialog.showModal === 'function' && !dialog.open) dialog.showModal();
  else dialog.setAttribute('open', '');
}

function closeReportMissingDialog() {
  const dialog = document.getElementById('report-missing-dialog');
  if (typeof dialog?.close === 'function' && dialog.open) dialog.close();
  else dialog?.removeAttribute('open');
}

function clearReportSelection() {
  selectedReportIds.clear();
  document.querySelectorAll('.report-select').forEach(checkbox => checkbox.checked = false);
  updateReportActions();
}

async function compareReports(ids) {
  if (ids.length !== 2) return;
  openComparisonDialog('Comparing Reports…');
  try {
    const payload = await reportExplorerRequest('/api/compare', {
      method: 'POST',
      body: JSON.stringify({ ids }),
    });
    openComparisonDialog(payload.comparison);
  } catch (error) {
    openComparisonDialog(error.message);
  }
}

function compareSelectedReports() {
  const ids = reportHistory.filter(report => selectedReportIds.has(report.id)).map(report => report.id).reverse();
  return compareReports(ids);
}

function compareMostRecentReports() {
  return compareReports(reportHistory.slice(0, 2).map(report => report.id).reverse());
}

function deleteSelectedReports() {
  const ids = reportHistory.filter(report => selectedReportIds.has(report.id)).map(report => report.id);
  const dialog = document.getElementById('delete-confirmation-dialog');
  const message = document.getElementById('delete-confirmation-message');
  if (!ids.length || !dialog || !message) return;
  pendingDeleteIds = ids;
  message.textContent = `Permanently delete ${ids.length} selected report${ids.length === 1 ? '' : 's'}?`;
  if (typeof dialog.showModal === 'function' && !dialog.open) dialog.showModal();
  else dialog.setAttribute('open', '');
}

function closeDeleteConfirmation() {
  const dialog = document.getElementById('delete-confirmation-dialog');
  pendingDeleteIds = [];
  if (typeof dialog?.close === 'function') dialog.close();
  else dialog?.removeAttribute('open');
}

async function confirmDeleteSelectedReports() {
  const ids = pendingDeleteIds.slice();
  closeDeleteConfirmation();
  if (!ids.length) return;
  setReportExplorerStatus('Deleting selected reports…');
  try {
    const payload = await reportExplorerRequest('/api/delete', {
      method: 'POST',
      body: JSON.stringify({
        ids,
        current: new URLSearchParams(window.location.search).get('id'),
      }),
    });
    selectedReportIds.clear();
    if (payload.replacement) {
      applyReport(payload.replacement, 'replace');
      renderReportHistory(payload.reports || []);
      setReportExplorerStatus(`Deleted ${payload.deleted.length} report${payload.deleted.length === 1 ? '' : 's'}.`);
      return;
    }
    if (payload.redirect) {
      window.location.replace(payload.redirect);
      return;
    }
    renderReportHistory(payload.reports || []);
    setReportExplorerStatus(`Deleted ${payload.deleted.length} report${payload.deleted.length === 1 ? '' : 's'}.`);
  } catch (error) {
    setReportExplorerStatus(error.message, true);
  }
}

async function initReportExplorer() {
  if (!REPORT_EXPLORER_TOKEN) {
    document.getElementById('latest-report-list')?.replaceChildren();
    document.getElementById('last-five-report-list')?.replaceChildren();
    document.getElementById('older-report-list')?.replaceChildren();
    setReportExplorerStatus('Run `graphcheck report --open` to browse, compare, or delete report history.');
    return;
  }
  try {
    const payload = await reportExplorerRequest('/api/reports');
    renderReportHistory(payload.reports || [], true);
    window.setInterval(() => reportExplorerRequest('/api/ping').catch(() => {}), 30000);
  } catch (error) {
    const current = new URLSearchParams(window.location.search).get('id');
    openReportMissingDialog(current || document.getElementById('report-explorer')?.dataset.currentReport);
  }
}

function initTooltips() {
  globalTooltip = document.createElement('div');
  globalTooltip.id = 'floating-tooltip';
  globalTooltip.className = 'floating-tooltip';
  document.body.appendChild(globalTooltip);

  document.addEventListener('mouseover', function(e) {
    const box = e.target.closest('.status-box');
    if (box && box.dataset.tooltip) {
      globalTooltip.textContent = box.dataset.tooltip;
      globalTooltip.classList.add('visible');
      updateTooltipPos(e);
    }
  });

  document.addEventListener('mousemove', function(e) {
    if (globalTooltip && globalTooltip.classList.contains('visible')) {
      updateTooltipPos(e);
    }
  });

  document.addEventListener('mouseout', function(e) {
    const box = e.target.closest('.status-box');
    if (box && globalTooltip) {
      globalTooltip.classList.remove('visible');
    }
  });
}

function updateTooltipPos(e) {
  if (!globalTooltip) return;
  globalTooltip.style.left = e.clientX + 'px';
  globalTooltip.style.top = (e.clientY - 12) + 'px';
}

function setVerdictFilter(verdict, btn) {
  activeVerdictFilter = verdict;
  document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  filterChecks();
}

function filterChecks() {
  const query = document.getElementById('search-input').value.toLowerCase();
  const cards = document.querySelectorAll('.check-card');
  let visible = 0;

  cards.forEach(card => {
    const verdict = card.getAttribute('data-verdict');
    const text = card.textContent.toLowerCase();

    const matchesVerdict = (activeVerdictFilter === 'all' || verdict === activeVerdictFilter);
    const matchesSearch = !query || text.includes(query);

    const matches = matchesVerdict && matchesSearch;
    card.style.display = matches ? 'block' : 'none';
    if (matches) visible += 1;
  });
  const empty = document.getElementById('checks-empty-message');
  if (empty) {
    const categoryMessages = {
      fail: 'No checks failed.',
      warn: 'No checks with warnings.',
      errored: 'No checks with errors.',
      pass: 'No checks passed.',
      skipped: 'No checks skipped.',
    };
    const categoryHasChecks = Array.from(cards).some(
      card => activeVerdictFilter === 'all' || card.dataset.verdict === activeVerdictFilter
    );
    empty.textContent = !categoryHasChecks
      ? (categoryMessages[activeVerdictFilter] || 'No checks to explore.')
      : 'No matching checks.';
    empty.hidden = visible > 0;
  }
  persistCheckFilters();
}

function persistCheckFilters() {
  const query = document.getElementById('search-input')?.value || '';
  try {
    localStorage.setItem(
      CHECK_FILTERS_STORAGE_KEY,
      JSON.stringify({ verdict: activeVerdictFilter, query })
    );
  } catch {}
}

function restoreCheckFilters() {
  let saved;
  try {
    saved = JSON.parse(localStorage.getItem(CHECK_FILTERS_STORAGE_KEY) || '{}');
  } catch {
    return;
  }

  const filterButton = Array.from(document.querySelectorAll('.filter-btn')).find(
    button => button.dataset.filter === saved.verdict
  );
  if (filterButton) {
    activeVerdictFilter = filterButton.dataset.filter;
    document.querySelectorAll('.filter-btn').forEach(button => {
      button.classList.toggle('active', button === filterButton);
    });
  }

  const searchInput = document.getElementById('search-input');
  if (searchInput && typeof saved.query === 'string') searchInput.value = saved.query;
  filterChecks();
}

function toggleAllDetails() {
  const details = document.querySelectorAll('.check-details');
  checkDetailsOpenPreference = Array.from(details).some(detail => !detail.open);
  applyCheckDetailsPreference();
}

function applyCheckDetailsPreference() {
  if (checkDetailsOpenPreference === null) return;
  document.querySelectorAll('.check-details').forEach(
    detail => detail.open = checkDetailsOpenPreference
  );
}

function applyTheme(theme, persist = false) {
  const newTheme = theme === 'dark' ? 'dark' : 'light';
  document.documentElement.setAttribute('data-theme', newTheme);
  const btn = document.getElementById('theme-toggle');
  if (btn) btn.textContent = newTheme === 'dark' ? '☀️' : '🌙';
  if (persist) {
    try {
      localStorage.setItem(THEME_STORAGE_KEY, newTheme);
    } catch {}
  }
}

function restoreTheme() {
  try {
    applyTheme(localStorage.getItem(THEME_STORAGE_KEY) || 'light');
  } catch {
    applyTheme('light');
  }
}

function toggleTheme() {
  applyTheme(document.documentElement.getAttribute('data-theme') === 'dark' ? 'light' : 'dark', true);
}

function setSummaryTableExpanded(expanded) {
  const container = document.getElementById('summary-table-container');
  const btn = document.getElementById('toggle-summary-btn');
  const bannerBtn = document.getElementById('run-summary-toggle');
  if (!container || !btn) return;
  container.classList.toggle('hidden-summary', !expanded);
  btn.innerHTML = `${expanded ? 'Hide' : 'Show'} Issue Summary <span class="toggle-arrow">${expanded ? '▲' : '▼'}</span>`;
  bannerBtn?.setAttribute('aria-expanded', String(expanded));
}

function toggleSummaryTable() {
  const container = document.getElementById('summary-table-container');
  if (container) setSummaryTableExpanded(container.classList.contains('hidden-summary'));
}

function toggleRunErrorFix() {
  const fix = document.getElementById('run-error-fix');
  const btn = document.getElementById('run-error-fix-toggle');
  if (!fix || !btn) return;
  const isHidden = fix.classList.toggle('hidden-status-fix');
  btn.setAttribute('aria-expanded', String(!isHidden));
  btn.textContent = isHidden ? 'See fix.' : 'Hide fix.';
}

function showIssueSummary() {
  const container = document.getElementById('summary-table-container');
  if (!container) return;
  setSummaryTableExpanded(true);
  container.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

function sortTable(columnIndex) {
  const table = document.getElementById('summary-table');
  if (!table) return;
  const tbody = table.querySelector('tbody');
  const rows = Array.from(tbody.querySelectorAll('tr'));

  if (rows.length === 1 && rows[0].cells.length === 1) return;

  const currentDir = sortDirections[columnIndex] === 'asc' ? 'desc' : 'asc';
  sortDirections[columnIndex] = currentDir;

  rows.sort((a, b) => {
    const cellA = a.cells[columnIndex]?.textContent.trim().toLowerCase() || '';
    const cellB = b.cells[columnIndex]?.textContent.trim().toLowerCase() || '';
    return currentDir === 'asc'
      ? cellA.localeCompare(cellB)
      : cellB.localeCompare(cellA);
  });

  rows.forEach(row => tbody.appendChild(row));
}

function showChecksExplorer(animate = true) {
  const grid = document.querySelector('.dashboard-grid');
  const checksPanel = document.getElementById('checks-panel');
  const btn = document.getElementById('explore-checks-btn');

  if (checksPanel.classList.contains('hidden-panel')) {
    grid.classList.add('has-checks');
    checksPanel.classList.remove('hidden-panel');
    checksPanel.classList.toggle('visible-panel', animate);
    if (btn) btn.style.display = 'none';
  }
  try {
    localStorage.setItem(CHECKS_EXPLORER_STORAGE_KEY, 'true');
  } catch {}
}

function restoreChecksExplorerState() {
  try {
    if (localStorage.getItem(CHECKS_EXPLORER_STORAGE_KEY) === 'true') showChecksExplorer();
  } catch {}
}

function navigateToCheck(suiteId, checkId) {
  showChecksExplorer();

  const targetCard = Array.from(document.querySelectorAll('.check-card')).find(card =>
    card.dataset.suiteId === suiteId && card.dataset.checkId === checkId
  );

  if (targetCard) {
    targetCard.style.display = 'block';

    const details = targetCard.querySelector('.check-details');
    if (details) {
      details.open = true;
    }

    targetCard.scrollIntoView({ behavior: 'smooth', block: 'start' });

    targetCard.classList.remove('card-highlight');
    void targetCard.offsetWidth;
    targetCard.classList.add('card-highlight');
  }
}

function initReportSpecificInteractions() {
  document.getElementById('run-error-fix-toggle')?.addEventListener('click', toggleRunErrorFix);
  document.getElementById('run-summary-toggle')?.addEventListener('click', showIssueSummary);
  document.getElementById('toggle-summary-btn')?.addEventListener('click', toggleSummaryTable);
  document.getElementById('explore-checks-btn')?.addEventListener('click', showChecksExplorer);
  document.getElementById('toggle-details-btn')?.addEventListener('click', toggleAllDetails);
  document.getElementById('search-input')?.addEventListener('input', filterChecks);

  document.querySelectorAll('.filter-btn').forEach(button => {
    button.addEventListener('click', () => setVerdictFilter(button.dataset.filter, button));
  });

  document.querySelectorAll('[data-sort-column]').forEach(header => {
    header.addEventListener('click', () => sortTable(Number(header.dataset.sortColumn)));
  });

  document.querySelectorAll('.status-box').forEach(box => {
    const navigate = () => navigateToCheck(box.dataset.suiteId, box.dataset.checkId);
    box.addEventListener('click', navigate);
    box.addEventListener('keydown', event => {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        navigate();
      }
    });
  });
}

function initInteractions() {
  restoreTheme();
  initTooltips();

  document.getElementById('theme-toggle')?.addEventListener('click', toggleTheme);
  document.getElementById('report-search-input')?.addEventListener('input', filterReportHistory);
  document.getElementById('clear-report-selection-btn')?.addEventListener('click', clearReportSelection);
  document.getElementById('compare-reports-btn')?.addEventListener('click', compareSelectedReports);
  document.getElementById('compare-most-recent-btn')?.addEventListener('click', compareMostRecentReports);
  document.getElementById('delete-reports-btn')?.addEventListener('click', deleteSelectedReports);
  document.getElementById('cancel-delete-btn')?.addEventListener('click', closeDeleteConfirmation);
  document.getElementById('confirm-delete-btn')?.addEventListener('click', confirmDeleteSelectedReports);
  document.getElementById('delete-confirmation-dialog')?.addEventListener('close', () => {
    pendingDeleteIds = [];
  });
  document.getElementById('close-comparison-btn')?.addEventListener('click', () => {
    const dialog = document.getElementById('report-comparison-dialog');
    if (typeof dialog?.close === 'function') dialog.close();
    else dialog?.removeAttribute('open');
  });
  document.getElementById('close-report-missing-btn')?.addEventListener('click', closeReportMissingDialog);

  document.addEventListener('click', handleReportLinkClick);
  window.addEventListener('popstate', () => navigateReport(window.location.href, 'none'));
  initReportSpecificInteractions();
  restoreChecksExplorerState();
  restoreCheckFilters();
  initReportExplorer();
}

document.addEventListener('DOMContentLoaded', initInteractions);
""".strip()
