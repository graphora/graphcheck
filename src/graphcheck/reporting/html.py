from __future__ import annotations

import html
import json
from collections.abc import Collection
from pathlib import Path
from typing import Any

from graphcheck.contracts.results import CheckResult, Results, Verdict
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
            f"<title>GraphCheck report - {_escape(model.run.id)}</title>",
            "<style>",
            _CSS,
            "</style>",
            "</head>",
            "<body>",
            _header(model),
            _run_summary(model),
            _suite_breakdown(model),
            _checks(checks),
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
    score = "n/a" if results.score is None else str(results.score.value)
    return (
        "<header>"
        "<div>"
        '<p class="eyebrow">GraphCheck</p>'
        f"<h1>Run id: {_escape(results.run.id)}</h1>"
        f"<p>Status: <strong>{_escape(results.run.status.value)}</strong></p>"
        "</div>"
        f'<div class="score"><span>{_escape(score)}</span><small>score</small></div>'
        "</header>"
    )


def _run_summary(results: Results) -> str:
    target = results.run.target
    target_html = (
        "<p>Target unavailable</p>"
        if target is None
        else (
            f"<p>Database: <strong>{_escape(target.database)}</strong> "
            f"({_escape(target.edition)}, {_escape(target.server_version)})</p>"
            f"<p>Fingerprint: <code>{_escape(target.fingerprint)}</code></p>"
        )
    )
    error_html = ""
    if results.run.error is not None:
        error_html = (
            '<section class="banner error">'
            f"<strong>{_escape(results.run.error.code)}</strong>"
            f"<p>{_escape(results.run.error.message)}</p>"
            f"<p>Fix: {_escape(results.run.error.fix)}</p>"
            "</section>"
        )
    partial_html = ""
    if results.run.partial_reason is not None:
        partial_html = (
            '<section class="banner partial">'
            f"Partial run: {_escape(results.run.partial_reason)}"
            "</section>"
        )
    return (
        '<section class="panel">'
        "<h2>Summary</h2>"
        f"{error_html}{partial_html}"
        f"{target_html}"
        f"<p>Started: {_escape(results.run.started_at)}; "
        f"finished: {_escape(results.run.finished_at)}</p>"
        f"<p>GraphCheck version: {_escape(results.run.graphcheck_version)}; "
        f"Check-pack version: {_escape(results.run.pack_version)}</p>"
        f"<p>Total {_totals(results.totals.model_dump(by_alias=True))}</p>"
        "</section>"
    )


def _suite_breakdown(results: Results) -> str:
    items = []
    for suite in results.suites:
        score = "n/a" if suite.score is None else str(suite.score)
        items.append(
            "<tr>"
            f"<td>{_escape(suite.id)}</td>"
            f"<td>{_escape(score)}</td>"
            f"<td><code>{_escape(suite.source_sha)}</code></td>"
            f"<td>{_totals(suite.totals.model_dump(by_alias=True))}</td>"
            "</tr>"
        )
    body = "".join(items) if items else '<tr><td colspan="4">No suites</td></tr>'
    return (
        '<section class="panel">'
        "<h2>Suite Breakdown</h2>"
        "<table><thead><tr><th>Suite</th><th>Score</th>"
        "<th>Source SHA</th><th>Totals</th></tr></thead>"
        f"<tbody>{body}</tbody></table>"
        "</section>"
    )


def _checks(checks: list[CheckResult]) -> str:
    items = "".join(_check(check) for check in checks)
    return f'<section class="panel"><h2>Checks</h2>{items}</section>'


def _check(check: CheckResult) -> str:
    classes = f"check {check.verdict.value}"
    details = [
        f"<p>{_escape(check.pattern.value)} / {_escape(check.severity.value)}</p>",
        f"<p>Expected: <code>{_escape(_json(check.expected))}</code></p>",
    ]
    if check.measured is not None:
        details.append(f"<p>Measured: <code>{_escape(_json(check.measured))}</code></p>")
    if check.estimate is not False:
        details.append(
            f"<p>Estimate: <code>{_escape(_json(check.estimate.model_dump()))}</code></p>"
        )
    if check.error is not None:
        details.append(
            '<div class="callout">'
            f"<strong>{_escape(check.error.code)}</strong>"
            f"<p>{_escape(check.error.message)}</p>"
            f"<p>Fix: {_escape(check.error.fix)}</p>"
            "</div>"
        )
    if check.compiled_query is not None:
        details.append(f"<h4>Compiled Cypher</h4><pre>{_escape(check.compiled_query)}</pre>")
    if check.evidence is not None:
        details.append(_evidence(check))
    return (
        f'<article class="{classes}">'
        '<div class="check-title">'
        f'<span class="badge">{_escape(check.verdict.value)}</span>'
        f"<h3>{_escape(check.name)}</h3>"
        f"<code>{_escape(check.suite_id)}::{_escape(check.id)}</code>"
        "</div>"
        f"<details {_details_open(check)}>"
        "<summary>Details</summary>"
        f"{''.join(details)}"
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
            f"<td>{_escape(element.kind)}</td>"
            f"<td><code>{_escape(element.id)}</code></td>"
            f"<td>{_escape(descriptor or '')}</td>"
            "</tr>"
        )
    return (
        "<h4>Evidence</h4>"
        f"<p>{_escape(check.evidence.message)} "
        f"({check.evidence.total_count} total, cap {check.evidence.cap})</p>"
        "<table><thead><tr><th>Kind</th><th>ID</th><th>Labels/Type/Scope</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def _totals(totals: dict[str, int]) -> str:
    return ", ".join(f"{key}: {value}" for key, value in totals.items())


def _details_open(check: CheckResult) -> str:
    return "open" if check.verdict in (Verdict.FAIL, Verdict.WARN, Verdict.ERRORED) else ""


def _json(value: object) -> str:
    return json.dumps(json_compatible(value), sort_keys=True)


def _escape(value: object) -> str:
    return html.escape(str(value), quote=True)


_CSS = """
:root {
  color-scheme: light;
  font-family: Arial, sans-serif;
  color: #172026;
  background: #f6f7f9;
}
body { margin: 0; }
header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 28px 36px;
  background: #172026;
  color: white;
}
h1, h2, h3, h4, p { margin-top: 0; }
.eyebrow { text-transform: uppercase; letter-spacing: .08em; font-size: 12px; color: #b8c3cc; }
.score { width: 112px; height: 112px; border: 2px solid #fff; display: grid; place-items: center; }
.score span { font-size: 34px; font-weight: 700; }
.score small { display: block; text-transform: uppercase; font-size: 11px; }
.panel {
  margin: 24px auto;
  max-width: 1080px;
  background: #fff;
  border: 1px solid #d9e0e6;
  padding: 22px;
}
.banner { padding: 14px; margin-bottom: 16px; border-left: 4px solid #7d8790; background: #f2f4f6; }
.banner.error { border-color: #b42318; }
.banner.partial { border-color: #b7791f; }
table { width: 100%; border-collapse: collapse; margin: 12px 0; }
th, td { text-align: left; border-bottom: 1px solid #e4e8ec; padding: 8px; vertical-align: top; }
code, pre { background: #eef2f5; border: 1px solid #d9e0e6; }
code { padding: 1px 4px; }
pre { padding: 12px; white-space: pre-wrap; overflow-wrap: anywhere; }
.check { border: 1px solid #d9e0e6; margin: 14px 0; padding: 16px; }
.check.fail { border-left: 5px solid #b42318; }
.check.warn { border-left: 5px solid #b7791f; }
.check.errored { border-left: 5px solid #6f42c1; }
.check.skipped { border-left: 5px solid #7d8790; }
.check.pass { border-left: 5px solid #1f7a4d; }
.check-title { display: grid; grid-template-columns: auto 1fr; gap: 6px 12px; align-items: center; }
.check-title code { grid-column: 2; width: fit-content; }
.badge {
  display: inline-block;
  padding: 4px 8px;
  color: #fff;
  background: #172026;
  text-transform: uppercase;
  font-size: 12px;
}
.callout { padding: 12px; background: #f8f0f0; border: 1px solid #edc8c8; }
details { margin-top: 12px; }
summary { cursor: pointer; font-weight: 700; }
""".strip()
