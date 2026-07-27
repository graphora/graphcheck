from __future__ import annotations

import html
import json
from collections.abc import Collection
from datetime import datetime
from pathlib import Path
from typing import Any

from graphcheck.contracts.results import CheckResult, Results, RunStatus, Verdict
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
) -> str:
    """Render a Results model already validated at an artifact boundary."""

    checks = sorted(
        (check for check in model.checks if verdicts is None or check.verdict in verdicts),
        key=lambda check: (
            _VERDICT_ORDER[check.verdict],
            _SEVERITY_ORDER[check.severity.value],
            check.suite_id,
            check.id,
        ),
    )
    return "\n".join(
        [
            "<!doctype html>",
            '<html lang="en">',
            "<head>",
            '<meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            f"<title>GraphCheck Dashboard - {_escape(model.run.id)}</title>",
            "<style>",
            _CSS,
            "</style>",
            "</head>",
            "<body>",
            _header(model),
            '<main class="dashboard-body">',
            _banners(model),
            '<div class="dashboard-grid">',
            _status_overview(model, checks, filtered=verdicts is not None),
            _checks(checks),
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


def write_html_report(
    results: Results | dict[str, Any] | str | Path,
    path: Path,
    *,
    verdicts: Collection[Verdict] | None = None,
) -> Path:
    path.write_text(render_html_report(results, verdicts=verdicts), encoding="utf-8")
    return path


def _header(results: Results) -> str:
    version_info = (
        f"[v{_escape(results.run.graphcheck_version)} / Pack v{_escape(results.run.pack_version)}]"
    )

    return (
        '<header class="navbar">'
        '  <div class="brand-container">'
        f'    <span class="eyebrow">GraphCheck Dashboard {version_info}</span>'
        f"    <h1>Run: <code>{_escape(results.run.id)}</code></h1>"
        "  </div>"
        '  <button id="theme-toggle" class="theme-toggle-btn" aria-label="Toggle Theme" title="Toggle Theme">🌙</button>'
        "</header>"
    )


def _banners(results: Results) -> str:
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

    if results.run.error is not None:
        return (
            '<div class="banner-stack">'
            '<section class="banner banner-error" role="alert">'
            '<span aria-hidden="true">⚠️</span>'
            "<strong>Run Failed.</strong>"
            f'<span class="banner-message">{_escape(results.run.error.message)}</span>'
            '<button id="run-error-fix-toggle" class="banner-action" type="button" '
            'aria-expanded="false" aria-controls="run-error-fix">See fix.</button>'
            "</section>"
            '<section id="run-error-fix" class="banner-fix hidden-banner-fix">'
            f"<span>💡 <strong>Fix:</strong> {_escape(results.run.error.fix)}</span>"
            "</section>"
            "</div>"
        )

    if results.run.status is RunStatus.PARTIAL:
        return (
            '<div class="banner-stack">'
            '<section class="banner banner-partial">'
            "<strong>Partial Run.</strong>"
            f'<span class="banner-message">{_escape(status_text)}.</span>'
            '<button id="run-summary-toggle" class="banner-action" type="button" '
            'aria-expanded="false" aria-controls="summary-table-container">See more.</button>'
            "</section>"
            "</div>"
        )

    celebration = " 🎉" if status_text == "No issues found" and executed > 0 else ""
    banner_class = "banner-warning" if total_issues > 0 else "banner-complete"
    issues_action = (
        '<button id="run-summary-toggle" class="banner-action" type="button" '
        'aria-expanded="false" aria-controls="summary-table-container">See issues.</button>'
        if total_issues > 0
        else ""
    )
    return (
        '<div class="banner-stack">'
        f'<section class="banner {banner_class}">'
        "<strong>Run Complete.</strong>"
        f'<span class="banner-message">{_escape(status_text)}'
        f"{_escape(skipped_text)}.{celebration}</span>{issues_action}"
        "</section>"
        "</div>"
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

    time_info_html = _format_time_info(
        results.run.started_at,
        results.run.finished_at,
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

        if not right_badges:
            if totals.passed > 0:
                right_badges.append('<span class="badge badge-pass">OPERATIONAL</span>')
            else:
                right_badges.append('<span class="badge badge-skipped">NO CHECKS</span>')

        score = "N/A" if suite.score is None else str(suite.score)
        right_badges.append(f'<span class="badge badge-score">SCORE: {score}</span>')
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
        '<section class="card panel-section">'
        '  <div class="summary-top-bar">'
        '    <div class="summary-meta-col">'
        "      <h2>Graph Health Overview</h2>"
        '      <div class="summary-meta-grid">'
        '        <div class="meta-item">'
        '          <span class="meta-label">CHECKED ON</span>'
        f"          <div>{_escape(time_info_html)}</div>"
        "        </div>"
        '        <div class="meta-item">'
        '          <span class="meta-label">Target Graph</span>'
        f"          <div>{target_html}</div>"
        "        </div>"
        "      </div>"
        "    </div>"
        "  </div>"
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


def _checks(checks: list[CheckResult]) -> str:
    items = "".join(_check(check) for check in checks) or (
        '<p class="empty-panel-message text-muted">No checks to explore.</p>'
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
        '      <button id="toggle-details-btn" class="btn-secondary">Toggle Details</button>'
        "    </div>"
        "  </div>"
        f'  <div id="checks-container" class="scrollable-content">{items}</div>'
        "</section>"
    )


def _check(check: CheckResult) -> str:
    verdict_str = check.verdict.value.lower()
    classes = f"check-card check-{verdict_str}"
    suite_id_esc = _escape(check.suite_id)
    check_id_esc = _escape(check.id)
    key_esc = f"{suite_id_esc}::{check_id_esc}"

    details = [
        f'<p class="meta-sub">Pattern: <code>{_escape(check.pattern.value)}</code> | Severity: <code>{_escape(check.severity.value)}</code></p>',
        f"<p><strong>Expected:</strong> <code>{_escape(_json(check.expected))}</code></p>",
    ]
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

    return (
        f'<article class="{classes}" data-verdict="{verdict_str}" data-check-key="{key_esc}" '
        f'data-suite-id="{suite_id_esc}" data-check-id="{check_id_esc}">'
        '<div class="check-title-row">'
        f'<span class="badge badge-{verdict_str}">{_escape(check.verdict.value)}</span>'
        f"<h3>{_escape(check.name)}</h3>"
        f'<code class="check-id">{key_esc}</code>'
        "</div>"
        f'<details {_details_open(check)} class="check-details">'
        "<summary>View Details & Evidence</summary>"
        f'<div class="details-content">{"".join(details)}</div>'
        "</details>"
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


def _format_time_info(
    started: Any,
    finished: Any,
) -> str:
    s_dt = _parse_datetime(started)
    f_dt = _parse_datetime(finished)

    if s_dt is None:
        return "n/a"

    formatted_date = s_dt.strftime("%d-%m-%Y at %H:%M:%S")
    if f_dt is not None:
        duration = max(0, int((f_dt - s_dt).total_seconds()))
        unit = "second" if duration == 1 else "seconds"
        return f"{formatted_date} (in {duration} {unit})"
    return formatted_date


def _parse_datetime(val: Any) -> datetime | None:
    if not val:
        return None
    if isinstance(val, datetime):
        return val
    s = str(val).replace("Z", "").replace("z", "").strip()
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


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

[data-theme="dark"] ::-webkit-scrollbar {
  width: 8px;
  height: 8px;
}
[data-theme="dark"] ::-webkit-scrollbar-track {
  background: var(--bg-main);
}
[data-theme="dark"] ::-webkit-scrollbar-thumb {
  background: var(--border);
  border-radius: 4px;
}
[data-theme="dark"] ::-webkit-scrollbar-thumb:hover {
  background: var(--text-muted);
}

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
.brand-container { flex-shrink: 0; }
.eyebrow {
  text-transform: uppercase;
  letter-spacing: 0.08em;
  font-size: 11px;
  color: #94a3b8;
  font-weight: 600;
}
.navbar h1 { margin: 0; font-size: 18px; font-weight: 600; }
.navbar code { background: #1e293b; color: #f8fafc; padding: 2px 8px; border-radius: 4px; font-size: 14px; }

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
  grid-template-columns: minmax(0, 900px);
  justify-content: center;
  gap: 16px;
  min-height: 0;
  transition: all 0.4s cubic-bezier(0.16, 1, 0.3, 1);
}

.dashboard-grid.has-checks {
  grid-template-columns: 1fr 1fr;
  justify-content: stretch;
}

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
.panel-section h2 { margin: 0 0 4px 0; font-size: 18px; }
.panel-section h3 { margin: 14px 0 8px 0; font-size: 14px; }

.scrollable-content {
  flex: 1;
  overflow-y: auto;
  padding-right: 6px;
}

.panel-footer {
  flex-shrink: 0;
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px solid var(--border);
  display: flex;
  justify-content: flex-end;
}

.btn-primary {
  background: #2563eb;
  color: #ffffff;
  border: none;
  padding: 8px 16px;
  border-radius: 6px;
  font-size: 13px;
  font-weight: 600;
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
  display: inline-block;
  padding: 3px 7px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 700;
  text-transform: uppercase;
}
.badge-pass { background: var(--pass-bg); color: var(--pass-color); border: 1px solid rgba(16, 185, 129, 0.3); }
.badge-fail { background: var(--fail-bg); color: var(--fail-color); border: 1px solid rgba(239, 68, 68, 0.3); }
.badge-warn { background: var(--warn-bg); color: var(--warn-color); border: 1px solid rgba(245, 158, 11, 0.3); }
.badge-errored { background: var(--errored-bg); color: var(--errored-color); border: 1px solid rgba(139, 92, 246, 0.3); }
.badge-skipped { background: var(--skipped-bg); color: var(--skipped-color); border: 1px solid var(--border); }
.badge-score { background: #eff6ff; color: #2563eb; border: 1px solid rgba(37, 99, 235, 0.3); order: 999; }
[data-theme="dark"] .badge-score { background: #1e3a8a; color: #bfdbfe; }

.banner-stack {
  display: flex;
  flex-direction: column;
  gap: 8px;
  flex-shrink: 0;
  max-width: 900px;
  width: 100%;
  margin: 0 auto;
  transition: max-width 0.4s cubic-bezier(0.16, 1, 0.3, 1);
}
.banner-stack.has-checks {
  max-width: 100%;
}
.banner { display: flex; align-items: center; gap: 6px; padding: 10px 14px; border-radius: var(--radius); font-size: 13px; flex-shrink: 0; }
.banner-message { min-width: 0; }
.banner-error { background: var(--fail-color); color: #fff; }
.banner-partial { background: var(--errored-color); color: #fff; }
.banner-complete { background: var(--pass-color); color: #fff; }
.banner-warning { background: var(--warn-bg); color: var(--warn-color); border: 1px solid rgba(245, 158, 11, 0.3); }
.banner-action { padding: 0; border: 0; background: transparent; color: inherit; font: inherit; font-weight: 700; text-decoration: underline; white-space: nowrap; cursor: pointer; }
.banner-fix { display: flex; flex-direction: column; gap: 4px; padding: 10px 14px; border: 1px solid var(--fail-color); border-radius: var(--radius); background: var(--bg-card); color: var(--text-main); font-size: 13px; }
.hidden-banner-fix { display: none; }

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
  justify-content: space-between;
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

.check-title-row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.check-title-row h3 { margin: 0; font-size: 15px; font-weight: 600; }
.check-id { color: var(--text-muted); font-size: 12px; margin-left: auto; }

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
const CHECK_FILTERS_STORAGE_KEY = 'graphcheck.checksExplorerFilters';

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

  cards.forEach(card => {
    const verdict = card.getAttribute('data-verdict');
    const text = card.textContent.toLowerCase();

    const matchesVerdict = (activeVerdictFilter === 'all' || verdict === activeVerdictFilter);
    const matchesSearch = !query || text.includes(query);

    card.style.display = (matchesVerdict && matchesSearch) ? 'block' : 'none';
  });
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
  const anyClosed = Array.from(details).some(d => !d.open);
  details.forEach(d => d.open = anyClosed);
}

function toggleTheme() {
  const body = document.documentElement;
  const isDark = body.getAttribute('data-theme') === 'dark';
  const newTheme = isDark ? 'light' : 'dark';
  body.setAttribute('data-theme', newTheme);
  
  const btn = document.getElementById('theme-toggle');
  btn.innerHTML = newTheme === 'dark' ? '☀️' : '🌙';
}

function toggleSummaryTable() {
  const container = document.getElementById('summary-table-container');
  const btn = document.getElementById('toggle-summary-btn');
  const bannerBtn = document.getElementById('run-summary-toggle');
  if (container.classList.contains('hidden-summary')) {
    container.classList.remove('hidden-summary');
    btn.innerHTML = 'Hide Issue Summary <span class="toggle-arrow">▲</span>';
    bannerBtn?.setAttribute('aria-expanded', 'true');
  } else {
    container.classList.add('hidden-summary');
    btn.innerHTML = 'Show Issue Summary <span class="toggle-arrow">▼</span>';
    bannerBtn?.setAttribute('aria-expanded', 'false');
  }
}

function toggleRunErrorFix() {
  const fix = document.getElementById('run-error-fix');
  const btn = document.getElementById('run-error-fix-toggle');
  if (!fix || !btn) return;
  const isHidden = fix.classList.toggle('hidden-banner-fix');
  btn.setAttribute('aria-expanded', String(!isHidden));
  btn.textContent = isHidden ? 'See fix.' : 'Hide fix.';
}

function showIssueSummary() {
  const container = document.getElementById('summary-table-container');
  const btn = document.getElementById('toggle-summary-btn');
  if (!container || !btn) return;
  container.classList.remove('hidden-summary');
  btn.innerHTML = 'Hide Issue Summary <span class="toggle-arrow">▲</span>';
  document.getElementById('run-summary-toggle')?.setAttribute('aria-expanded', 'true');
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

function showChecksExplorer() {
  const grid = document.querySelector('.dashboard-grid');
  const checksPanel = document.getElementById('checks-panel');
  const btn = document.getElementById('explore-checks-btn');
  const bannerStack = document.querySelector('.banner-stack');

  if (checksPanel.classList.contains('hidden-panel')) {
    grid.classList.add('has-checks');
    if (bannerStack) bannerStack.classList.add('has-checks');
    checksPanel.classList.remove('hidden-panel');
    checksPanel.classList.add('visible-panel');
    if (btn) btn.style.display = 'none';
  }
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

function initInteractions() {
  initTooltips();

  document.getElementById('theme-toggle')?.addEventListener('click', toggleTheme);
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

  restoreCheckFilters();
}

document.addEventListener('DOMContentLoaded', initInteractions);
""".strip()
