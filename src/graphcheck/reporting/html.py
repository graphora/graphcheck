from __future__ import annotations

import html
import json
from collections.abc import Collection
from pathlib import Path
from typing import Any

from graphcheck.contracts.results import CheckResult, Results, Verdict
from graphcheck.reporting.writer import json_compatible, load_results
from graphcheck.scoring import (
    ScoreCalculation,
    calculate_score,
    calculate_score_deductions,
    calculate_suite_scores,
)

_VERDICT_ORDER = {
    Verdict.FAIL: 0,
    Verdict.WARN: 1,
    Verdict.ERRORED: 2,
    Verdict.SKIPPED: 3,
    Verdict.PASS: 4,
}
_SEVERITY_ORDER = {"error": 0, "warn": 1}

_EXIT_CODES = {
    0: "0: Run complete. No errors found.",
    1: "1: Run complete. Errors found.",
    2: "2: Run interrupted. Please try again.",
    3: "3: Run incomplete. Please check configured connections.",
}


def render_html_report(
    results: Results | dict[str, Any] | str | Path,
    *,
    verdicts: Collection[Verdict] | None = None,
) -> str:
    model = load_results(results)
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
            _score_breakdown(model),
            _checks(checks),
            "</div>",
            "</main>",
            '<button id="theme-toggle" class="theme-toggle-btn" onclick="toggleTheme()" aria-label="Toggle Theme">🌙 <span>Dark</span></button>',
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
    if results.score is None:
        score = "n/a"
        score_value = 0
        score_class = " score-ring-empty"
        score_label = "Overall score unavailable"
    else:
        score = str(results.score.value)
        score_value = results.score.value
        score_class = ""
        score_label = f"Overall score: {results.score.value} out of 100"

    status_val = results.run.status.value.lower()
    target = results.run.target

    target_html = (
        '<span class="text-muted">Target unavailable</span>'
        if target is None
        else f"<strong>{_escape(target.database)}</strong> ({_escape(target.edition)}, {_escape(target.server_version)})"
    )

    times_html = _format_run_times(results.run.started_at, results.run.finished_at)
    exit_desc = _exit_code_desc(results.run.exit_code)
    version_info = (
        f"[v{_escape(results.run.graphcheck_version)} / Pack v{_escape(results.run.pack_version)}]"
    )

    return (
        '<header class="navbar">'
        '  <div class="brand-container">'
        f'   <span class="eyebrow">GraphCheck Dashboard {version_info}</span>'
        f"   <h1>Run: <code>{_escape(results.run.id)}</code> "
        f'   <span class="status-pill status-{_escape(status_val)}">{_escape(results.run.status.value)}</span></h1>'
        "  </div>"
        '  <div class="header-meta-inline">'
        '    <div class="meta-item">'
        '      <span class="meta-label">Target</span>'
        f"     <div>{target_html}</div>"
        "    </div>"
        '    <div class="meta-item">'
        '      <span class="meta-label">Run Info</span>'
        f"     <div>{times_html} | Exit: <strong>{_escape(exit_desc)}</strong></div>"
        "    </div>"
        "  </div>"
        f' <div class="score-ring{score_class}" style="--score-value: {score_value}" '
        f' role="img" aria-label="{_escape(score_label)}">'
        '   <div class="score-ring-inner">'
        f"     <span>{_escape(score)}</span><small>Score</small>"
        "   </div>"
        " </div>"
        "</header>"
    )


def _banners(results: Results) -> str:
    error_html = ""
    if results.run.error is not None:
        error_html = (
            '<section class="banner banner-error">'
            f"<div><strong>{_escape(results.run.error.code)}</strong></div>"
            f"<div>{_escape(results.run.error.message)}</div>"
            f'<div class="fix-tip">💡 <strong>Fix:</strong> {_escape(results.run.error.fix)}</div>'
            "</section>"
        )

    partial_html = ""
    if results.run.partial_reason is not None:
        partial_html = (
            '<section class="banner banner-partial">'
            f"<strong>Partial run:</strong> {_escape(results.run.partial_reason)}"
            "</section>"
        )

    return f"{error_html}{partial_html}"


def _score_breakdown(results: Results) -> str:
    deduction_rows, total_deducted = _deduction_rows(results)
    scores = calculate_suite_scores(results.checks)
    suite_rows = []

    for suite in results.suites:
        calculation = scores.get(suite.id, calculate_score(()))
        totals = suite.totals
        suite_rows.append(
            "<tr>"
            f"<td><code>{_escape(suite.id)}</code></td>"
            f'<td><span class="badge badge-pass">{totals.passed}</span></td>'
            f'<td><span class="badge badge-fail">{totals.fail}</span></td>'
            f'<td><span class="badge badge-warn">{totals.warn}</span></td>'
            f'<td><span class="badge badge-errored">{totals.errored}</span></td>'
            f'<td><span class="badge badge-skipped">{totals.skipped}</span></td>'
            f"<td>{_escape(_coverage(calculation))}</td>"
            f"<td><code>{_escape(suite.source_sha)}</code></td>"
            "</tr>"
        )

    suite_body = (
        "".join(suite_rows)
        if suite_rows
        else '<tr><td colspan="8" class="text-center">No suites</td></tr>'
    )
    deduction_body = (
        "".join(deduction_rows)
        if deduction_rows
        else '<tr><td colspan="5" class="text-center text-muted">No points docked. All clear! 🎉</td></tr>'
    )
    deducted = "n/a" if total_deducted is None else str(total_deducted)

    return (
        '<section class="card panel-section">'
        ' <div class="panel-header">'
        "   <h2>Graph Health Summary</h2>"
        '   <p class="text-muted">Each row shows points docked by a test. Open matching checks for evidence.</p>'
        " </div>"
        ' <div class="scrollable-content">'
        "   <h3>Check Suite Overview</h3>"
        '   <div class="table-container">'
        '     <table class="styled-table"><thead><tr><th>Suite</th><th>Pass</th><th>Fail</th><th>Warn</th>'
        "     <th>Errored</th><th>Skipped</th><th>Coverage</th><th>Source SHA</th></tr></thead>"
        f"     <tbody>{suite_body}</tbody>"
        "     </table>"
        "   </div>"
        '   <h3 style="margin-top: 20px;">Details</h3>'
        '   <div class="table-container">'
        '     <table class="styled-table"><thead><tr><th>Test</th><th>Suite</th><th>Result</th>'
        "     <th>Issue</th><th>Verdict</th></tr></thead>"
        f"     <tbody>{deduction_body}</tbody>"
        f'     <tfoot><tr><th colspan="4" style="text-align:right">Total Points Docked:</th>'
        f'     <th><span class="points-deducted-total">{deducted}</span></th></tr></tfoot>'
        "     </table>"
        "   </div>"
        " </div>"
        ' <div class="panel-footer">'
        '   <button id="explore-checks-btn" class="btn-primary" onclick="showChecksExplorer()">Explore Checks &rarr;</button>'
        " </div>"
        "</section>"
    )


def _deduction_rows(results: Results) -> tuple[list[str], int | None]:
    if results.score is None:
        return [], None
    points = {
        (deduction.suite_id, deduction.check_id): deduction.points
        for deduction in calculate_score_deductions(results.checks)
    }
    checks = sorted(
        (check for check in results.checks if (check.suite_id, check.id) in points),
        key=lambda check: (
            -points[(check.suite_id, check.id)],
            _VERDICT_ORDER[check.verdict],
            check.suite_id,
            check.id,
        ),
    )
    rows = []
    for check in checks:
        deducted = points[(check.suite_id, check.id)]
        v_class = check.verdict.value.lower()
        rows.append(
            "<tr>"
            f"<td><strong>{_escape(check.name)}</strong><br><code>{_escape(check.id)}</code></td>"
            f"<td><code>{_escape(check.suite_id)}</code></td>"
            f'<td><span class="badge badge-{v_class}">{_escape(check.verdict.value)}</span></td>'
            f"<td>{_escape(_issue(check))}</td>"
            f'<td><strong class="text-danger">-{deducted}</strong></td>'
            "</tr>"
        )
    return rows, sum(points.values())


def _issue(check: CheckResult) -> str:
    if check.evidence is not None:
        return check.evidence.message
    if check.error is not None:
        return check.error.message
    return "Check did not pass"


def _checks(checks: list[CheckResult]) -> str:
    items = "".join(_check(check) for check in checks)
    return (
        '<section id="checks-panel" class="card panel-section hidden-panel">'
        ' <div class="checks-header">'
        "   <h2>Checks Explorer</h2>"
        '   <div class="checks-controls">'
        '     <input type="text" id="search-input" placeholder="🔍 Search checks..." onkeyup="filterChecks()">'
        '     <div class="filter-group">'
        '       <button class="filter-btn active" data-filter="all" onclick="setVerdictFilter(\'all\', this)">All</button>'
        '       <button class="filter-btn" data-filter="fail" onclick="setVerdictFilter(\'fail\', this)">Fail</button>'
        '       <button class="filter-btn" data-filter="warn" onclick="setVerdictFilter(\'warn\', this)">Warn</button>'
        '       <button class="filter-btn" data-filter="errored" onclick="setVerdictFilter(\'errored\', this)">Errored</button>'
        '       <button class="filter-btn" data-filter="pass" onclick="setVerdictFilter(\'pass\', this)">Pass</button>'
        '       <button class="filter-btn" data-filter="skipped" onclick="setVerdictFilter(\'skipped\', this)">Skipped</button>'
        "     </div>"
        '     <button class="btn-secondary" onclick="toggleAllDetails()">Toggle Details</button>'
        "   </div>"
        " </div>"
        f' <div id="checks-container" class="scrollable-content">{items}</div>'
        "</section>"
    )


def _check(check: CheckResult) -> str:
    verdict_str = check.verdict.value.lower()
    classes = f"check-card check-{verdict_str}"
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
        f'<article class="{classes}" data-verdict="{verdict_str}">'
        '<div class="check-title-row">'
        f'<span class="badge badge-{verdict_str}">{_escape(check.verdict.value)}</span>'
        f"<h3>{_escape(check.name)}</h3>"
        f'<code class="check-id">{_escape(check.suite_id)}::{_escape(check.id)}</code>'
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


def _exit_code_desc(code: int) -> str:
    return _EXIT_CODES.get(code, f"{code}: Unknown exit code")


def _coverage(score: ScoreCalculation) -> str:
    if score.coverage_percent is None:
        return "n/a (no checks selected)"
    return f"{score.executed}/{score.selected} checks"


def _details_open(check: CheckResult) -> str:
    return ""


def _format_run_times(started: Any, finished: Any) -> str:
    s_str = _format_datetime(started)
    f_str = _format_datetime(finished)

    if s_str == "n/a" or f_str == "n/a":
        return f"{s_str} &rarr; {f_str}"

    s_parts = s_str.split(" ")
    f_parts = f_str.split(" ")

    if len(s_parts) == 2 and len(f_parts) == 2 and s_parts[0] == f_parts[0]:
        return f"{s_parts[0]} {s_parts[1]} &rarr; {f_parts[1]}"

    return f"{s_str} &rarr; {f_str}"


def _format_datetime(val: Any) -> str:
    if not val:
        return "n/a"
    s = str(val).replace("T", " ")
    s = s.split(".")[0].split("+")[0].replace("Z", "").replace("z", "").strip()
    return _escape(s)


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

/* Dark mode scrollbars fix */
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
  padding: 12px 24px;
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
  font-size: 10px;
  color: #94a3b8;
  font-weight: 600;
}
.navbar h1 { margin: 0; font-size: 16px; font-weight: 600; }
.navbar code { background: #1e293b; color: #f8fafc; padding: 2px 6px; border-radius: 4px; font-size: 13px; }

.header-meta-inline {
  display: flex;
  align-items: center;
  gap: 24px;
  font-size: 11px;
  color: #cbd5e1;
  flex-wrap: wrap;
}
.meta-item { display: flex; flex-direction: column; }
.meta-item .meta-label {
  font-size: 9px;
  text-transform: uppercase;
  color: #94a3b8;
  font-weight: 700;
  letter-spacing: 0.05em;
  margin-bottom: 1px;
}

.status-pill {
  font-size: 10px;
  padding: 2px 6px;
  border-radius: 12px;
  text-transform: uppercase;
  font-weight: 700;
  vertical-align: middle;
}
.status-success, .status-passed { background: var(--pass-bg); color: var(--pass-color); }
.status-failed, .status-error { background: var(--fail-bg); color: var(--fail-color); }

.score-ring {
  flex-shrink: 0;
  width: 52px;
  height: 52px;
  border-radius: 50%;
  display: grid;
  place-items: center;
  background: conic-gradient(var(--pass-color) calc(var(--score-value) * 1%), #334155 0);
}
.score-ring-inner {
  width: 40px;
  height: 40px;
  border-radius: 50%;
  display: grid;
  place-content: center;
  text-align: center;
  background: var(--bg-header);
}
.score-ring span { font-size: 14px; font-weight: 700; line-height: 1; color: #fff; }
.score-ring small { font-size: 7px; text-transform: uppercase; color: #94a3b8; }

.dashboard-body {
  flex: 1;
  display: flex;
  flex-direction: column;
  max-width: 1600px;
  width: 100%;
  margin: 0 auto;
  padding: 14px 20px;
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
  padding: 16px;
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
.panel-section h2 { margin: 0 0 4px 0; font-size: 17px; }
.panel-section h3 { margin: 12px 0 6px 0; font-size: 13px; }

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
  font-size: 12px;
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
  padding: 2px 6px;
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

.banner { padding: 8px 12px; border-radius: var(--radius); font-size: 12px; flex-shrink: 0; }
.banner-error { background: var(--fail-bg); border-left: 4px solid var(--fail-color); color: var(--fail-color); }
.banner-partial { background: var(--warn-bg); border-left: 4px solid var(--warn-color); color: var(--warn-color); }

.table-container { overflow-x: auto; }
.styled-table { width: 100%; border-collapse: collapse; font-size: 12px; text-align: left; }
.styled-table th { background: var(--bg-subtle); padding: 8px 10px; border-bottom: 2px solid var(--border); color: var(--text-muted); font-weight: 600; }
.styled-table td { padding: 8px 10px; border-bottom: 1px solid var(--border); }

.checks-header {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-bottom: 10px;
}
.checks-controls { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
#search-input {
  padding: 5px 10px;
  border: 1px solid var(--border);
  border-radius: 6px;
  font-size: 12px;
  outline: none;
  background: var(--bg-card);
  color: var(--text-main);
}
.filter-group { display: flex; border: 1px solid var(--border); border-radius: 6px; overflow: hidden; }
.filter-btn {
  background: var(--bg-card);
  color: var(--text-muted);
  border: none;
  padding: 5px 10px;
  font-size: 11px;
  cursor: pointer;
  border-right: 1px solid var(--border);
}
.filter-btn:last-child { border-right: none; }
.filter-btn.active { background: var(--bg-header); color: #fff; font-weight: 600; }

.btn-secondary {
  background: var(--bg-subtle);
  color: var(--text-main);
  border: 1px solid var(--border);
  padding: 5px 10px;
  border-radius: 6px;
  font-size: 11px;
  cursor: pointer;
}

.check-card {
  border: 1px solid var(--border);
  border-radius: 6px;
  margin-bottom: 10px;
  padding: 12px;
  background: var(--bg-card);
}
.check-card.check-fail { border-left: 4px solid var(--fail-color); }
.check-card.check-warn { border-left: 4px solid var(--warn-color); }
.check-card.check-errored { border-left: 4px solid var(--errored-color); }
.check-card.check-pass { border-left: 4px solid var(--pass-color); }
.check-card.check-skipped { border-left: 4px solid var(--skipped-color); }

.check-title-row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.check-title-row h3 { margin: 0; font-size: 14px; font-weight: 600; }
.check-id { color: var(--text-muted); font-size: 11px; margin-left: auto; }

.check-details { margin-top: 8px; font-size: 12px; }
.check-details summary { cursor: pointer; font-weight: 600; color: #3b82f6; font-size: 12px; }
.details-content { margin-top: 8px; padding-top: 8px; border-top: 1px solid var(--border); }

code, pre { font-family: 'ui-monospace', SFMono-Regular, Menlo, Monaco, Consolas, monospace; }
code { background: var(--code-bg); color: var(--code-text); padding: 2px 4px; border-radius: 4px; font-size: 85%; }

pre.code-block { background: #020617; color: #f8fafc; padding: 10px; border-radius: 6px; overflow-x: auto; font-size: 11px; margin: 6px 0; }
pre.code-block code { background: transparent; color: inherit; padding: 0; border-radius: 0; font-size: 100%; }

.text-muted { color: var(--text-muted); }
.text-danger { color: var(--fail-color); }
.text-center { text-align: center; }
.meta-sub { font-size: 11px; color: var(--text-muted); }
.callout { padding: 8px; border-radius: 6px; margin: 8px 0; font-size: 12px; }
.callout-error { background: var(--fail-bg); border: 1px solid rgba(239, 68, 68, 0.3); }
.kind-tag { background: var(--bg-subtle); color: var(--text-muted); border: 1px solid var(--border); padding: 1px 4px; border-radius: 3px; font-size: 10px; }

/* Dark mode toggle button */
.theme-toggle-btn {
  position: fixed;
  bottom: 16px;
  right: 16px;
  z-index: 1000;
  background: var(--bg-card);
  color: var(--text-main);
  border: 1px solid var(--border);
  border-radius: 20px;
  padding: 6px 12px;
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
  display: flex;
  align-items: center;
  gap: 6px;
  transition: all 0.2s ease;
}
.theme-toggle-btn:hover {
  transform: translateY(-2px);
}

@media (max-width: 1024px) {
  html, body { height: auto; overflow: auto; }
  .dashboard-body { overflow: visible; }
  .navbar { flex-direction: column; align-items: flex-start; }
  .header-meta-inline { flex-direction: column; gap: 8px; align-items: flex-start; }
  .dashboard-grid, .dashboard-grid.has-checks { grid-template-columns: 1fr; }
  .panel-section { max-height: 500px; }
}
""".strip()


_JS = """
let activeVerdictFilter = 'all';

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
  btn.innerHTML = newTheme === 'dark' ? '☀️ <span>Light</span>' : '🌙 <span>Dark</span>';
}

function showChecksExplorer() {
  const grid = document.querySelector('.dashboard-grid');
  const checksPanel = document.getElementById('checks-panel');
  const btn = document.getElementById('explore-checks-btn');

  if (checksPanel.classList.contains('hidden-panel')) {
    grid.classList.add('has-checks');
    checksPanel.classList.remove('hidden-panel');
    checksPanel.classList.add('visible-panel');
    if (btn) btn.style.display = 'none';
  }
}
""".strip()
