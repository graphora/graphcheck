import json
from copy import deepcopy
from html import escape
from pathlib import Path

import jsonschema
import pytest
from pydantic import ValidationError

from graphcheck.contracts.results import Results, Verdict
from graphcheck.contracts.schemas import results_schema
from graphcheck.reporting.html import (
    render_html_report,
    render_validated_html_report_fragments,
)
from graphcheck.reporting.writer import load_results, results_json, write_results

FIXTURES = Path(__file__).parent / "contracts" / "fixtures"


def _fixture(name: str) -> Path:
    return FIXTURES / f"results.{name}.json"


@pytest.mark.parametrize("name", ["complete", "partial", "generated-only", "failed"])
def test_writer_round_trips_existing_results_fixtures(name: str):
    source = _fixture(name)
    model = load_results(source)
    output_dir = Path.cwd() / ".test-tmp"
    output_dir.mkdir(exist_ok=True)
    output = write_results(model, output_dir / f"results-{name}.json")
    raw = json.loads(output.read_text(encoding="utf-8"))

    jsonschema.validate(raw, results_schema())
    assert Results.model_validate(raw) == model


def test_writer_upgrades_schema_1_0_input_to_current_output():
    raw = json.loads(_fixture("complete").read_text(encoding="utf-8"))
    raw["schema_version"] = "1.0"

    output = json.loads(results_json(raw))

    assert output["schema_version"] == "1.1"


def test_writer_output_bytes_match_regression_fixture():
    expected = (FIXTURES / "results.complete.rendered.json").read_text(encoding="utf-8")

    assert results_json(_fixture("complete")) == expected


def test_writer_rejects_invalid_results():
    raw = json.loads(_fixture("complete").read_text(encoding="utf-8"))
    raw["checks"][0]["evidence"] = None

    with pytest.raises(ValidationError):
        results_json(raw)


def test_writer_revalidates_mutated_results_instances():
    model = load_results(_fixture("complete"))
    assert model.score is not None
    model.score.value = 99

    with pytest.raises(ValidationError, match="score.value must be 43"):
        results_json(model)


@pytest.mark.parametrize("name", ["complete", "partial", "generated-only", "failed"])
def test_html_renderer_outputs_self_contained_interactive_report(name: str):
    report = render_html_report(_fixture(name))

    assert "<!doctype html>" in report
    assert "<style>" in report
    assert report.count("<script>") == 1
    assert "function filterChecks()" in report
    assert "function toggleTheme()" in report
    assert "GraphCheck" in report
    assert "http://" not in report
    assert "https://" not in report
    assert ' src="' not in report
    assert ' href="' not in report
    assert 'meta name="graphcheck-explorer-token"' not in report
    assert "graphcheck report --open" in report
    assert "XMLHttpRequest" not in report
    assert "WebSocket(" not in report
    assert "EventSource(" not in report
    assert " onclick=" not in report
    assert " onkeyup=" not in report


def test_html_renderer_orders_failures_before_passes():
    html = render_html_report(_fixture("complete"))

    fail_pos = html.index('data-check-key="customer-360::cq-001"')
    warn_pos = html.index('data-check-key="customer-360::account-no-orphans"')
    pass_pos = html.index('data-check-key="customer-360::cust-tax-id-present"')

    assert fail_pos < warn_pos < pass_pos


def test_html_renderer_shows_health_overview_and_outcome_breakdown():
    html = render_html_report(_fixture("complete"))

    assert "<h2>Graph Health Overview</h2>" in html
    assert "CHECKED ON" not in html
    assert '<span class="status-pill status-pill-warning">COMPLETE</span>' in html
    assert "<strong>Run Complete.</strong>" in html
    assert '<span class="header-status-message">1 failure, 1 warning.</span>' in html
    assert 'aria-controls="summary-table-container">See issues.</button>' in html
    assert "localStorage.setItem(" in html
    assert "restoreCheckFilters();" in html
    assert "<strong>neo4j</strong> (Neo4j version: 5.18.0, community)" in html
    assert '<span class="meta-label">Nodes</span>' in html
    assert '<span class="meta-label">Relationships</span>' in html
    assert "1,250" in html
    assert "3,480" in html
    assert "<code>customer-360</code>" in html
    assert '<span class="suite-check-stats">3/3 checks run</span>' in html
    assert '<span class="badge badge-fail">1 FAILED</span>' in html
    assert '<span class="badge badge-warn">1 WARNING</span>' in html
    assert (
        '<div class="suite-badges-row"><span class="badge badge-fail">1 FAILED</span>'
        '<span class="badge badge-warn">1 WARNING</span>'
        '<span class="badge badge-score">SCORE: 43</span></div>'
    ) in html
    assert "overall-badges-row" not in html
    assert 'data-tooltip="Which accounts does a customer control — fail"' in html
    assert 'data-tooltip="Accounts are connected to a Customer — warn"' in html
    assert 'data-tooltip="Customer.tax_id is present — pass"' in html
    assert "Show Issue Summary" in html
    assert "5000 rows exceeds max 200" in html
    assert "2 Account nodes have no controlling Customer" in html


def test_html_renderer_reports_partial_coverage():
    html = render_html_report(_fixture("partial"))

    assert "<strong>Partial Run.</strong>" in html
    assert '<span class="header-status-message">No issues found.</span>' in html
    assert 'aria-controls="summary-table-container">See more.</button>' in html
    assert "run-summary-toggle')?.addEventListener('click', showIssueSummary)" in html
    assert '<span class="suite-check-stats">1/2 checks run</span>' in html
    assert '<span class="badge badge-skipped">1 SKIPPED</span>' in html
    assert 'class="status-box status-box-skipped"' in html
    assert 'class="status-box status-box-pass"' in html
    assert "CHECKED ON" not in html
    assert '<span class="exit-2">1 check skipped</span>' not in html
    assert '<span class="badge badge-score">SCORE: 100</span>' in html
    assert "Check did not pass" not in html
    assert "No issues found in the checks that were evaluated." in html
    assert "No checks failed." in html
    assert "No checks with warnings." in html
    assert "No checks with errors." in html
    assert "No checks passed." in html
    assert "No checks skipped." in html


def test_html_renderer_reports_all_checks_skipped():
    html = render_html_report(_fixture("generated-only"))

    assert '<span class="suite-check-stats">0/1 checks run</span>' in html
    assert (
        '<div class="suite-badges-row">'
        '<span class="badge badge-skipped">1 SKIPPED</span>'
        '<span class="badge badge-score">SCORE: N/A</span></div>'
    ) in html
    assert '<span class="badge badge-score">SCORE: N/A</span>' in html
    assert "CHECKED ON" not in html
    assert "No issues found (1 check skipped)" in html
    assert '<span class="exit-2">1 check skipped</span>' not in html
    assert 'data-tooltip="draft competency check awaiting approval — skipped"' in html
    assert "Check did not pass" not in html
    assert "No checks were evaluated." in html
    assert "All clear! No issues found." not in html


def test_html_renderer_does_not_call_an_empty_selection_all_clear():
    raw = json.loads(_fixture("generated-only").read_text(encoding="utf-8"))
    empty_totals = {
        "checks": 0,
        "pass": 0,
        "fail": 0,
        "warn": 0,
        "errored": 0,
        "skipped": 0,
    }
    raw["score"] = None
    raw["checks"] = []
    raw["totals"] = empty_totals
    raw["suites"][0]["score"] = None
    raw["suites"][0]["totals"] = empty_totals.copy()

    html = render_html_report(raw)

    assert "No checks evaluated" in html
    assert "No checks were evaluated." in html
    assert "All clear! No issues found." not in html


def test_html_renderer_does_not_count_intentional_skips_as_issues():
    raw = json.loads(_fixture("partial").read_text(encoding="utf-8"))
    raw["run"]["status"] = "complete"
    raw["run"]["partial_reason"] = None
    raw["run"]["exit_code"] = 0
    raw["checks"][1]["skip_reason"] = "generated"
    second_skip = deepcopy(raw["checks"][1])
    second_skip["id"] = "second-generated-check"
    raw["checks"].append(second_skip)
    raw["totals"]["checks"] = 3
    raw["totals"]["skipped"] = 2
    raw["suites"][0]["totals"]["checks"] = 3
    raw["suites"][0]["totals"]["skipped"] = 2

    html = render_html_report(raw)

    assert "No issues found (2 checks skipped)" in html
    assert '<span class="exit-0">2 checks skipped</span>' not in html
    assert '<span class="suite-check-stats">1/3 checks run</span>' in html
    assert '<span class="badge badge-skipped">2 SKIPPED</span>' in html
    assert " FAILED</span>" not in html
    assert "Check did not pass" not in html
    assert "All clear! No issues found." in html


def test_html_renderer_appends_skips_to_issue_status_text():
    raw = json.loads(_fixture("complete").read_text(encoding="utf-8"))
    skipped_fixture = json.loads(_fixture("generated-only").read_text(encoding="utf-8"))
    first_skip = skipped_fixture["checks"][0]
    second_skip = deepcopy(first_skip)
    second_skip["id"] = "second-generated-check"
    raw["checks"].extend((first_skip, second_skip))
    raw["totals"]["checks"] = 5
    raw["totals"]["skipped"] = 2
    raw["suites"][0]["totals"]["checks"] = 5
    raw["suites"][0]["totals"]["skipped"] = 2

    html = render_html_report(raw)

    assert (
        '<span class="header-status-message">1 failure, 1 warning (2 checks skipped).</span>'
        in html
    )


def test_html_renderer_describes_completed_warning_only_exit_two_as_complete():
    raw = json.loads(_fixture("complete").read_text(encoding="utf-8"))
    raw["checks"] = raw["checks"][1:]
    raw["run"]["exit_code"] = 2
    raw["score"]["value"] = 75
    raw["totals"] = {
        "checks": 2,
        "pass": 1,
        "fail": 0,
        "warn": 1,
        "errored": 0,
        "skipped": 0,
    }
    raw["suites"][0]["score"] = 75
    raw["suites"][0]["totals"] = raw["totals"].copy()

    html = render_html_report(raw)

    assert '<span class="header-status-message">1 warning.</span>' in html
    assert "Run interrupted" not in html
    assert (
        '<div class="suite-badges-row"><span class="badge badge-warn">1 WARNING</span>'
        '<span class="badge badge-score">SCORE: 75</span></div>'
    ) in html


def test_html_renderer_reports_errored_checks_separately_from_failures():
    raw = json.loads(_fixture("complete").read_text(encoding="utf-8"))
    errored = raw["checks"][0]
    errored["verdict"] = "errored"
    errored["measured"] = None
    errored["evidence"] = None
    errored["error"] = {
        "code": "query.execution",
        "message": "Query execution failed",
        "fix": "Check the generated Cypher",
    }
    for suffix in ("two", "three"):
        extra = deepcopy(errored)
        extra["id"] = f"errored-{suffix}"
        raw["checks"].append(extra)
    raw["score"]["value"] = 23
    raw["totals"] = {
        "checks": 5,
        "pass": 1,
        "fail": 0,
        "warn": 1,
        "errored": 3,
        "skipped": 0,
    }
    raw["suites"][0]["score"] = 23
    raw["suites"][0]["totals"] = raw["totals"].copy()

    html = render_html_report(raw)

    assert '<span class="status-pill status-pill-partial">PARTIAL</span>' in html
    assert "<strong>Partial Run.</strong>" in html
    assert '<span class="header-status-message">1 warning, 3 errors.</span>' in html
    assert (
        '<div class="suite-badges-row">'
        '<span class="badge badge-errored">3 ERRORED</span>'
        '<span class="badge badge-warn">1 WARNING</span>'
        '<span class="badge badge-score">SCORE: 23</span></div>'
    ) in html
    assert " FAILED</span>" not in html


def test_html_renderer_keeps_check_identity_out_of_javascript_contexts():
    raw = json.loads(_fixture("complete").read_text(encoding="utf-8"))
    payload = "x');window.injected=true;//"
    raw["suites"][0]["id"] = payload
    for check in raw["checks"]:
        check["suite_id"] = payload
    raw["checks"][0]["id"] = payload

    report = render_html_report(raw)
    escaped_payload = escape(payload, quote=True)

    assert f'data-suite-id="{escaped_payload}"' in report
    assert f'data-check-id="{escaped_payload}"' in report
    assert " onclick=" not in report
    assert " onkeyup=" not in report
    assert "box.addEventListener('click', navigate)" in report
    assert "card.dataset.suiteId === suiteId" in report
    assert "card.dataset.checkId === checkId" in report
    assert "document.querySelector(`[data-check-key=" not in report


def test_html_renderer_exposes_cypher_and_evidence_ids():
    html = render_html_report(_fixture("complete"))

    assert "Severity:" not in html
    assert "MATCH (c:Customer" in html
    assert "4:abc:12" in html
    assert "4:abc:88" in html
    assert "5000 rows exceeds max 200" in html


def test_html_renderer_labels_aggregate_measurement_scope():
    raw = json.loads(_fixture("complete").read_text(encoding="utf-8"))
    raw["checks"][0]["evidence"]["elements"][0] = {
        "kind": "aggregate",
        "id": "node_count:label=Customer",
        "labels": None,
        "type": None,
    }

    html = render_html_report(raw)

    assert "Labels / Type / Scope" in html
    assert "aggregate measurement scope" in html


def test_html_renderer_displays_failed_run_error():
    html = render_html_report(_fixture("failed"))

    assert '<p class="empty-panel-message text-muted">No suites found.</p>' in html
    assert 'id="checks-empty-message"' in html
    assert "No checks to explore." in html
    assert '<span class="status-pill status-pill-partial">PARTIAL</span>' in html
    assert "<strong>Partial Run.</strong>" in html
    assert 'aria-controls="run-error-fix">See fix.</button>' in html
    assert '<span id="run-error-fix" class="header-status-fix hidden-status-fix">' in html
    assert (
        html.index('aria-controls="run-error-fix">See fix.</button>')
        < html.index('<span id="run-error-fix"')
        < html.index("</h1>", html.index('<span id="run-error-fix"'))
    )
    assert "run-error-fix-toggle')?.addEventListener('click', toggleRunErrorFix)" in html
    assert "CHECKED ON" not in html
    assert "connection.auth" not in html
    assert "Neo4j rejected the credentials" in html
    assert "Target unavailable" in html
    assert "Run failed before any checks could be evaluated." in html
    assert "All clear! No issues found." not in html


def test_html_renderer_displays_unreachable_neo4j_as_failed():
    raw = json.loads(_fixture("failed").read_text(encoding="utf-8"))
    raw["run"]["error"] = {
        "code": "neo4j.unreachable",
        "message": "Neo4j is unreachable at the configured Bolt URI.",
        "fix": "Start Neo4j and verify the configured URI.",
    }

    html = render_html_report(raw)

    assert '<span class="status-pill status-pill-error">FAILED</span>' in html
    assert "<strong>Run Failed.</strong>" in html
    assert "Neo4j is unreachable at the configured Bolt URI." in html
    assert "Start Neo4j and verify the configured URI." in html


@pytest.mark.parametrize(
    ("code", "message", "fix"),
    [
        ("neo4j.auth_failed", "Credentials rejected.", "Update the password."),
        ("neo4j.database_not_found", "Database missing.", "Select an online database."),
        ("profile.password_missing", "Password missing.", "Set password_env."),
        ("profile.uri_invalid", "URI scheme invalid.", "Use bolt:// or neo4j+s://."),
        ("neo4j.tls_mismatch", "TLS mode mismatch.", "Match the URI scheme to TLS."),
        (
            "neo4j.credential_not_read_only",
            "Credential is write-capable.",
            "Use a server-enforced read-only user.",
        ),
    ],
)
def test_html_renderer_shows_actionable_connection_diagnostic(code, message, fix):
    raw = json.loads(_fixture("failed").read_text(encoding="utf-8"))
    raw["run"]["error"] = {"code": code, "message": message, "fix": fix}

    html = render_html_report(raw)

    assert '<section class="callout callout-error run-diagnostic"' in html
    assert "Action required" in html
    assert message in html
    assert f"<strong>Fix:</strong> {fix}" in html


def test_html_renderer_can_limit_checks_to_diagnostic_verdicts():
    html = render_html_report(
        _fixture("complete"),
        verdicts={Verdict.FAIL, Verdict.WARN, Verdict.ERRORED},
    )

    assert "Which accounts does a customer control" in html
    assert "Accounts are connected to a Customer" in html
    assert "Customer.tax_id is present" not in html


def test_html_renderer_describes_empty_diagnostic_as_no_matching_issues():
    html = render_html_report(
        _fixture("partial"),
        verdicts={Verdict.FAIL, Verdict.WARN, Verdict.ERRORED},
    )

    assert '<span class="suite-check-stats">1/2 checks run</span>' in html
    assert '<span class="badge badge-score">SCORE: 100</span>' in html
    assert "No matching issues" in html
    assert "No checks executed" not in html


def test_html_renderer_places_report_explorer_left_of_graph_health_overview():
    html = render_html_report(_fixture("complete"))

    assert html.count('id="report-run-title"') == 1
    assert 'id="report-banners"' not in html
    assert html.count('id="report-overview"') == 1
    assert html.count('id="checks-panel"') == 1
    assert 'id="report-explorer"' in html
    assert "<h2>Report History</h2>" in html
    assert '<span class="eyebrow explorer-eyebrow">' not in html
    assert "Latest report" in html
    assert "Last 5 reports" in html
    assert ">Older</h3>" in html
    assert "Open a run, or select reports to compare or delete." not in html
    assert 'id="report-search-input"' in html
    assert '<details id="latest-report-group" class="report-group" open>' in html
    assert '<details id="last-five-report-group" class="report-group" open>' in html
    assert '<details id="older-report-group" class="report-group">' in html
    assert 'class="latest-pill"' not in html
    assert "font-size: 18px; line-height: 1;" in html
    assert html.count("justify-content: flex-start;") >= 2
    assert 'id="clear-report-selection-btn"' in html
    assert 'id="compare-most-recent-btn"' in html
    assert html.index('id="report-search-input"') < html.index('id="clear-report-selection-btn"')
    explorer_scroll_position = html.index('class="scrollable-content explorer-scroll"')
    assert html.index('id="delete-reports-btn"') < explorer_scroll_position
    assert explorer_scroll_position < html.index('id="compare-most-recent-btn"')
    assert html.index('id="compare-reports-btn"') < html.index('id="compare-most-recent-btn"')
    assert 'id="compare-most-recent-btn" class="btn-primary"' in html
    assert ">Compare Selected</button>" in html
    assert 'class="explorer-selection-actions"' in html
    assert 'class="explorer-comparison-actions"' in html
    assert "#delete-reports-btn {" not in html
    assert ".explorer-scroll { margin-top: 20px; }" in html
    assert ".explorer-status:empty { display: none; }" in html
    comparison_dialog_start = html.index(".comparison-dialog {")
    comparison_dialog_css = html[comparison_dialog_start : html.index("}", comparison_dialog_start)]
    assert "overflow: hidden;" in comparison_dialog_css
    assert ".navbar h1, .panel-section h2 { font-size: 18px; }" in html
    assert "opacity: 1;" in html
    assert "function clearReportSelection()" in html
    assert "function compareMostRecentReports()" in html
    assert "function renderComparisonMessage(content, message)" in html
    assert "comparison-status-${value}" in html
    assert "comparison-delta-positive" in html
    assert "comparison-delta-negative" in html
    assert "content.replaceChildren();" in html
    assert "content.innerHTML" not in html
    assert "reportHistory.slice(0, 2)" in html
    assert "reportHistory.slice(1, 6)" in html
    assert "reportHistory.slice(6)" in html
    assert html.index('id="report-explorer"') < html.index("Graph Health Overview")
    assert "compare-reports-btn" in html
    assert "delete-reports-btn" in html
    assert "<h2>Are you sure?</h2>" in html
    assert "window.confirm" not in html
    assert "graphcheck.checksExplorerOpen" in html
    assert "restoreChecksExplorerState();" in html
    assert "graphcheck.theme" in html
    assert "restoreTheme();" in html
    assert "graphcheck.reportExplorerNavigation" in html
    assert "restoreReportExplorerNavigation()" in html
    assert "handleReportLinkClick" in html
    assert "fetch(path" in html
    assert "/api/report?id=" in html
    assert "history.pushState" in html
    assert "history.replaceState" in html
    assert "window.addEventListener('popstate'" in html
    assert "new AbortController()" in html
    assert "requestSequence !== reportNavigationSequence" in html
    assert "setSummaryTableExpanded(issueSummaryExpanded);" in html
    assert "checkDetailsOpenPreference = Array.from(details).some" in html
    assert "applyCheckDetailsPreference();" in html
    assert "showChecksExplorer(false);" in html
    assert "Loading report…" in html
    assert "formatReportFinishedAt(report.finished_at)" in html
    assert "const finishedAt = new Date(value);" in html
    assert "finishedAt.getFullYear()" in html
    assert "finishedAt.getHours()" in html
    assert "`${match[1]} at ${match[2]}`" not in html
    assert "window.location.assign" not in html
    panel_footer_start = html.index(".panel-footer {")
    panel_footer_css = html[panel_footer_start : html.index("}", panel_footer_start)]
    assert "border-top" not in panel_footer_css
    assert "::-webkit-scrollbar-button { display: none; width: 0; height: 0; }" in html


def test_html_renderer_exposes_report_specific_fragments_without_the_permanent_shell():
    fragments = render_validated_html_report_fragments(load_results(_fixture("partial")))

    assert set(fragments) == {"run_title", "overview", "checks"}
    assert fragments["run_title"].startswith('<div id="report-run-title"')
    assert '<section id="report-overview"' in fragments["overview"]
    assert '<section id="checks-panel"' in fragments["checks"]
    assert "run_01HXATZ" not in fragments["run_title"]
    assert "<strong>Partial Run.</strong>" in fragments["run_title"]
    assert 'id="report-explorer"' not in "".join(fragments.values())
    assert 'id="theme-toggle"' not in "".join(fragments.values())
