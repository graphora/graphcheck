import json
import re
import shutil
import subprocess
from collections import Counter
from copy import deepcopy
from html import escape
from html.parser import HTMLParser
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


class _CheckCardParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.cards = []
        self._card = None

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "article" and "check-card" in attributes.get("class", "").split():
            self._card = {"attrs": attributes, "text": []}

    def handle_data(self, data):
        if self._card is not None:
            self._card["text"].append(data)

    def handle_endtag(self, tag):
        if tag == "article" and self._card is not None:
            self._card["text"] = " ".join("".join(self._card["text"]).split())
            self.cards.append(self._card)
            self._card = None


def _check_cards(rendered: str):
    parser = _CheckCardParser()
    parser.feed(rendered)
    return parser.cards


def _next_steps_fragment(rendered: str) -> str:
    start = rendered.index('  <div id="next-steps-tab-panel"')
    end = rendered.index("</section>", start)
    return rendered[start:end]


@pytest.mark.parametrize("name", ["clean", "complete", "partial", "generated-only", "failed"])
def test_writer_round_trips_existing_results_fixtures(name: str):
    source = _fixture(name)
    model = load_results(source)
    output_dir = Path.cwd() / ".test-tmp"
    output_dir.mkdir(exist_ok=True)
    output = write_results(model, output_dir / f"results-{name}.json")
    raw = json.loads(output.read_text(encoding="utf-8"))

    jsonschema.validate(raw, results_schema())
    assert Results.model_validate(raw) == model


@pytest.mark.parametrize("historical_version", ["1.0", "1.1"])
def test_writer_preserves_historical_provenance_in_output(historical_version):
    raw = json.loads(_fixture("complete").read_text(encoding="utf-8"))
    raw["schema_version"] = historical_version
    raw["run"]["target"].pop("labels")
    raw["run"]["target"].pop("relationship_types")

    output = json.loads(results_json(raw))

    assert output["schema_version"] == historical_version
    assert output["run"]["target"]["labels"] is None
    assert output["run"]["target"]["relationship_types"] is None


def test_writer_reloads_serialized_historical_results(tmp_path):
    raw = json.loads(_fixture("complete").read_text(encoding="utf-8"))
    raw["schema_version"] = "1.1"
    raw["run"]["target"].pop("labels")
    raw["run"]["target"].pop("relationship_types")

    from_json = load_results(results_json(raw))
    from_file = load_results(write_results(raw, tmp_path / "results.json"))

    for model in (from_json, from_file):
        assert model._historical_schema_version == "1.1"
        assert model.run.target is not None
        assert model.run.target.labels is None
        assert model.run.target.relationship_types is None


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


@pytest.mark.parametrize("name", ["clean", "complete", "partial", "generated-only", "failed"])
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


def test_html_renderer_adds_identical_generic_next_steps_to_clean_and_findings_reports():
    clean = render_html_report(_fixture("clean"))
    findings = render_html_report(_fixture("complete"))
    next_steps = _next_steps_fragment(clean)

    assert next_steps == _next_steps_fragment(findings)
    assert next_steps.count("<h3>Add competency checks</h3>") == 1
    assert next_steps.count("<h3>Track drift over time</h3>") == 1
    assert (
        "Add competency checks for the core business questions your graph must answer."
        in next_steps
    )
    assert "Set a baseline and rerun GraphCheck to track structural drift." in next_steps
    assert "These are general practices, not recommendations derived from this run." in next_steps
    for graph_specific_value in (
        "Customer",
        "Account",
        "customer-360",
        "cq-001",
        "2 Account nodes",
    ):
        assert graph_specific_value not in next_steps


def test_html_renderer_exposes_accessible_tabs_without_redundant_flow_actions():
    html = render_html_report(_fixture("clean"))

    assert html.count('id="checks-next-steps-panel"') == 1
    assert (
        'class="checks-next-steps-header report-tabs" role="tablist" '
        'aria-label="Checks Explorer and Next Steps"' in html
    )
    assert 'id="checks-next-steps-heading"' not in html
    assert (
        'id="checks-tab" class="report-tab active" type="button" role="tab" '
        'aria-selected="true" aria-controls="checks-tab-panel" tabindex="0"' in html
    )
    assert 'data-tab="checks">Checks Explorer</button>' in html
    assert (
        'id="next-steps-tab" class="report-tab" type="button" role="tab" '
        'aria-selected="false" aria-controls="next-steps-tab-panel" tabindex="-1"' in html
    )
    assert (
        'id="checks-tab-panel" class="report-tab-panel" role="tabpanel" '
        'aria-labelledby="checks-tab"' in html
    )
    assert (
        'id="next-steps-tab-panel" class="report-tab-panel next-steps-content scrollable-content" '
        'role="tabpanel" aria-labelledby="next-steps-tab" data-tab-panel="next-steps" hidden'
        in html
    )
    assert 'id="next-steps-action"' not in html
    assert 'id="back-to-checks-action"' not in html
    assert 'class="checks-next-steps-footer"' not in html
    assert 'class="panel-flow-action"' not in html
    assert 'aria-hidden="true">→' not in html
    assert 'aria-hidden="true">←' not in html
    assert ".report-tab { margin: 0; padding: 0 0 5px;" in html
    assert "font-size: 18px; font-weight: 600;" in html
    assert "height: auto;" in html
    assert "padding-bottom: 0;" in html
    assert "margin-bottom: 4px;" in html
    assert ".next-steps-content { padding: 0 6px 4px 0; font-size: 13px; }" in html
    assert ".next-step h3 { margin: 0 0 5px; font-size: 13px; }" in html
    assert "#checks-next-steps-panel { height: 500px; }" in html
    assert "function activateReportTab(tabName, focusTab = false)" in html
    assert "function handleReportTabKeydown(event)" in html
    assert "event.key === 'ArrowRight'" in html
    assert "event.key === 'ArrowLeft'" in html
    assert "event.key === 'Home'" in html
    assert "event.key === 'End'" in html
    assert "selectedTab.focus({ preventScroll: true });" in html


def test_html_renderer_resets_tab_local_state_when_history_replaces_the_combined_panel():
    html = render_html_report(_fixture("complete"))

    assert "checks_next_steps: 'checks-next-steps-panel'" in html
    assert "checkDetailsOpenPreference = null;" in html
    assert "activateReportTab('checks');" in html
    assert "if (checksOpen) showChecksExplorer(false);" in html
    assert "|| filterButtons.find(button => button.dataset.filter === 'all');" in html
    assert "restoreCheckFilters();" in html


def test_html_renderer_orders_failures_before_passes():
    html = render_html_report(_fixture("complete"))

    fail_pos = html.index('data-check-key="customer-360::cq-001"')
    warn_pos = html.index('data-check-key="customer-360::account-no-orphans"')
    pass_pos = html.index('data-check-key="customer-360::cust-tax-id-present"')

    assert fail_pos < warn_pos < pass_pos


@pytest.mark.parametrize("name", ["clean", "complete", "partial", "generated-only"])
def test_html_check_ledger_matches_selected_checks_exactly(name):
    results = load_results(_fixture(name))
    cards = _check_cards(render_html_report(results))

    assert Counter(
        (card["attrs"]["data-suite-id"], card["attrs"]["data-check-id"]) for card in cards
    ) == Counter((check.suite_id, check.id) for check in results.checks)
    expected = {(check.suite_id, check.id): check for check in results.checks}
    for card in cards:
        attrs, text = card["attrs"], card["text"]
        check = expected[(attrs["data-suite-id"], attrs["data-check-id"])]
        assert attrs["data-verdict"] == check.verdict.value
        if check.executed:
            assert "Evaluated" not in text
        else:
            assert "Not evaluated" in text
        assert f"Pattern: {check.pattern.value}" in text
        assert "Severity:" not in text


@pytest.mark.parametrize(
    ("reason", "explanation"),
    [
        ("generated", "Generated check awaiting review or approval."),
        ("unsupported", "A capability required by this check was unavailable."),
        ("not_run", "The run ended before this check started."),
    ],
)
def test_html_skipped_cards_show_generic_reason_without_internal_code(reason, explanation):
    raw = json.loads(_fixture("generated-only").read_text(encoding="utf-8"))
    raw["checks"][0]["skip_reason"] = reason
    if reason != "generated":
        raw["run"].update(status="partial", partial_reason="coverage unavailable")
    card = _check_cards(render_html_report(raw))[0]

    assert "Reason" in card["text"]
    assert explanation in card["text"]
    assert "Reason code:" not in card["text"]
    assert "View details" in card["text"]
    assert "View Details & Evidence" not in card["text"]


def test_html_non_skipped_cards_do_not_show_skip_reasons():
    cards = _check_cards(render_html_report(_fixture("clean")))

    assert all("Reason" not in card["text"] for card in cards)


def test_html_renderer_shows_health_overview_and_outcome_breakdown():
    html = render_html_report(_fixture("complete"))

    assert "<h2>Graph Health Overview</h2>" in html
    assert "CHECKED ON" not in html
    assert '<span class="status-pill status-pill-warning">COMPLETE</span>' in html
    assert "<strong>Run Complete.</strong>" in html
    assert '<span class="header-status-message">1 failure and 1 warning.</span>' in html
    assert 'aria-controls="checks-next-steps-panel">See issues.</button>' in html
    assert 'data-action="issues"' in html
    assert "localStorage.setItem(" in html
    assert "restoreCheckFilters();" in html
    assert "<dt>Target Graph</dt><dd><strong>neo4j</strong>" in html
    assert "<dt>Database</dt><dd>Neo4j 5.18.0 community" in html
    assert "<dt>Size</dt><dd>1,250 nodes · 3,480 relationships" in html
    assert "<summary>2 labels · 2 relationship types</summary>" in html
    assert "<strong>Labels:</strong> Account, Customer" in html
    assert "<strong>Relationship types:</strong> CONTROLS, OWNS" in html
    assert 'class="capability-pill capability-available"' in html
    assert 'role="tooltip">Available</span>' in html
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
    assert "<h2>Not Evaluated</h2>" in html
    assert "None. All 3 selected checks were evaluated." in html
    assert "5000 rows exceeds max 200" in html
    assert "2 Account nodes have no controlling Customer" in html


def test_html_renderer_reports_partial_coverage():
    html = render_html_report(_fixture("partial"))

    assert "<strong>Partial Run.</strong>" in html
    assert (
        '<span class="header-status-message">No failures in the 1 check evaluated. '
        "Coverage is incomplete due to skipped check(s) from "
        "<em>customer-360</em>.</span>" in html
    )
    assert 'data-action="coverage" aria-controls="not-evaluated">Review coverage.</button>' in html
    assert "run-summary-toggle')?.addEventListener('click', handleRunSummaryAction)" in html
    assert '<span class="suite-check-stats">1/2 checks run</span>' in html
    assert '<span class="badge badge-skipped">1 SKIPPED</span>' in html
    assert 'class="status-box status-box-skipped"' in html
    assert 'class="status-box status-box-pass"' in html
    assert "CHECKED ON" not in html
    assert '<span class="exit-2">1 check skipped</span>' not in html
    assert '<span class="badge badge-score">SCORE: 100</span>' in html
    assert "Check did not pass" not in html
    assert (
        "No failures in the 1 check evaluated. Coverage is incomplete due to skipped check(s) "
        "from <em>customer-360</em>." in html
    )
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
    assert '<span class="header-status-message">No checks were evaluated.</span>' in html
    assert '<span class="exit-2">1 check skipped</span>' not in html
    assert 'data-tooltip="draft competency check awaiting approval — skipped"' in html
    assert "Check did not pass" not in html
    assert "No checks were evaluated." in html
    assert "All clear! No issues found." not in html


def test_not_evaluated_is_truthful_for_clean_findings_failed_and_empty_runs():
    scope = (
        "This report covers checks selected for this run. GraphCheck did not evaluate graph "
        "behavior outside those configured checks."
    )
    clean = render_html_report(_fixture("clean"))
    findings = render_html_report(_fixture("complete"))
    failed = render_html_report(_fixture("failed"))
    empty = json.loads(_fixture("generated-only").read_text(encoding="utf-8"))
    empty_totals = {"checks": 0, "pass": 0, "fail": 0, "warn": 0, "errored": 0, "skipped": 0}
    empty.update(score=None, checks=[])
    empty["totals"] = empty_totals
    empty["suites"][0].update(score=None, totals=empty_totals.copy())

    assert "None. All 2 selected checks were evaluated." in clean
    assert 'class="not-evaluated-summary not-evaluated-complete"' in clean
    assert "None. All 3 selected checks were evaluated." in findings
    assert "The run failed before checks could be evaluated." in failed
    assert "No checks were selected for this run." in render_html_report(empty)
    assert all(scope in report for report in (clean, findings, failed))
    assert all("summary-table" not in report for report in (clean, findings, failed))


def test_not_evaluated_lists_partial_and_generated_coverage_from_stored_values():
    partial = render_html_report(_fixture("partial"))
    generated = render_html_report(_fixture("generated-only"))
    partial_start = partial.index('<section id="not-evaluated"')
    generated_start = generated.index('<section id="not-evaluated"')
    partial_coverage = partial[partial_start : partial.index("</section>", partial_start)]
    generated_coverage = generated[generated_start : generated.index("</section>", generated_start)]

    assert "1 of 2 selected checks were not evaluated." in partial_coverage
    assert partial_coverage.count("Coverage note:</strong> time budget exceeded (30s)") == 1
    assert (
        '<button class="not-evaluated-row" type="button" '
        'data-suite-id="customer-360" data-check-id="account-no-orphans">'
        "Accounts are connected to a Customer</button>"
    ) in partial_coverage
    assert "Not run" not in partial_coverage
    assert "not_run" not in partial_coverage
    assert "The run ended before this check started." not in partial_coverage
    assert "No checks were evaluated." in generated_coverage
    assert (
        'data-suite-id="customer-360" data-check-id="cq-draft">'
        "draft competency check awaiting approval</button>"
    ) in generated_coverage
    assert "Reason code:" not in generated_coverage
    assert "Generated check awaiting review or approval." not in generated_coverage
    assert "All 1 selected check passed." not in generated


def test_not_evaluated_discloses_more_than_five_skips_without_omitting_rows():
    raw = json.loads(_fixture("generated-only").read_text(encoding="utf-8"))
    template = raw["checks"][0]
    raw["checks"] = [dict(template, id=f"generated-{index}") for index in range(6)]
    raw["totals"].update(checks=6, skipped=6)
    raw["suites"][0]["totals"].update(checks=6, skipped=6)

    html = render_html_report(raw)

    assert "Show 1 more not evaluated checks" in html
    assert '<details class="not-evaluated-more">' in html
    assert all(f'data-check-id="generated-{index}"' in html for index in range(6))


def test_not_evaluated_uses_only_stored_selection_and_escapes_it():
    raw = json.loads(_fixture("clean").read_text(encoding="utf-8"))
    raw["run"]["selection"].update(suites=["customer-360", "<suite>"], tags=["core", "<tag>"])

    html = render_html_report(raw)

    assert "<strong>Suites:</strong> customer-360, &lt;suite&gt;" in html
    assert "<strong>Tags:</strong> core, &lt;tag&gt;" in html
    assert "&lt;suite&gt;/" not in html


def test_issue_summary_is_removed_and_coverage_navigation_is_accessible():
    complete = render_html_report(_fixture("complete"))
    partial = render_html_report(_fixture("partial"))

    for report in (complete, partial):
        assert "Issue Summary" not in report
        assert "summary-table" not in report
        assert "toggleSummaryTable" not in report
        assert "showIssueSummary" not in report
        assert "sortTable" not in report
        assert '<section id="not-evaluated" class="not-evaluated" tabindex="-1">' in report
        assert (
            'aria-controls="checks-next-steps-panel" aria-expanded="false">Explore checks' in report
        )
        assert "showAllChecks" in report
        assert "setVerdictFilter('all', allButton);" in report
        assert "button.addEventListener('click', () => navigateToCheck(" in report
        assert all(card["attrs"]["tabindex"] == "-1" for card in _check_cards(report))
        assert report.index('class="suite-status-list"') < report.index('id="not-evaluated"')
        assert report.index('id="not-evaluated"') < report.index('id="explore-checks-btn"')
    assert 'aria-controls="checks-next-steps-panel">See issues.</button>' in complete
    assert "run-summary-toggle')?.setAttribute('aria-expanded', 'true');" in complete
    assert "setVerdictFilter('issues', issuesButton);" in complete
    assert 'aria-controls="not-evaluated">Review coverage.</button>' in partial
    assert "section.focus({ preventScroll: true });" in partial


def test_check_navigation_moves_dom_focus_to_target_card():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for the report DOM behavior test")
    report = render_html_report(_fixture("complete"))
    navigate = re.search(
        r"function navigateToCheck\(suiteId, checkId\) \{.*?\n\}", report, re.DOTALL
    )
    assert navigate is not None
    script = f"""
const document = {{
  activeElement: null,
  querySelectorAll: () => [targetCard],
}};
const targetCard = {{
  dataset: {{ suiteId: 'customer-360', checkId: 'email-coverage' }},
  style: {{}},
  querySelector: () => details,
  scrollIntoView: () => {{}},
  focus: options => {{ document.activeElement = targetCard; targetCard.focusOptions = options; }},
  classList: {{ remove: () => {{}}, add: () => {{}} }},
  offsetWidth: 1,
}};
const details = {{ open: false }};
const showChecksExplorer = () => {{}};
const activateReportTab = () => {{}};
{navigate.group(0)}
navigateToCheck('customer-360', 'email-coverage');
if (document.activeElement !== targetCard) throw new Error('target card did not receive focus');
if (!targetCard.focusOptions?.preventScroll) throw new Error('focus did not prevent scrolling');
if (!details.open) throw new Error('target details were not opened');
"""
    subprocess.run([node, "--input-type=module", "--eval", script], check=True)


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

    assert "No checks were selected or evaluated." in html
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

    assert (
        "No failures in the 1 check evaluated. Coverage is incomplete due to skipped check(s) "
        "from <em>customer-360</em>." in html
    )
    assert '<span class="exit-0">2 checks skipped</span>' not in html
    assert '<span class="suite-check-stats">1/3 checks run</span>' in html
    assert '<span class="badge badge-skipped">2 SKIPPED</span>' in html
    assert " FAILED</span>" not in html
    assert "Check did not pass" not in html
    assert "All clear" not in html


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
        '<span class="header-status-message">1 failure and 1 warning. '
        "Coverage is incomplete due to skipped check(s) from "
        "<em>customer-360</em>.</span>" in html
    )


def test_html_renderer_escapes_skipped_suite_names_before_italicizing():
    raw = json.loads(_fixture("partial").read_text(encoding="utf-8"))
    raw["suites"][0]["id"] = "<unsafe-suite>"
    for check in raw["checks"]:
        check["suite_id"] = "<unsafe-suite>"

    html = render_html_report(raw)

    assert "<em>&lt;unsafe-suite&gt;</em>" in html
    assert "<em><unsafe-suite></em>" not in html


def test_html_renderer_distinguishes_empty_and_historical_inventory(tmp_path):
    empty = json.loads(_fixture("clean").read_text(encoding="utf-8"))
    empty["run"]["target"]["labels"] = []
    empty["run"]["target"]["relationship_types"] = []
    assert "<summary>0 labels · 0 relationship types</summary>" in render_html_report(empty)

    historical = deepcopy(empty)
    historical["schema_version"] = "1.1"
    historical["run"]["target"].pop("labels")
    historical["run"]["target"].pop("relationship_types")
    path = tmp_path / "results.json"
    original = json.dumps(historical)
    path.write_text(original, encoding="utf-8")

    model = load_results(path)

    assert model.run.target is not None
    assert model.run.target.labels is None
    assert model.run.target.relationship_types is None
    assert "Inventory not recorded" in render_html_report(model)
    assert path.read_text(encoding="utf-8") == original


def test_html_renderer_escapes_full_inventory_and_shows_capability_states():
    raw = json.loads(_fixture("clean").read_text(encoding="utf-8"))
    raw["run"]["target"]["labels"] = ["<Customer&>"]
    raw["run"]["target"]["relationship_types"] = ["OWNS<script>"]
    raw["run"]["target"]["capabilities"]["count_store"] = False

    html = render_html_report(raw)

    assert "&lt;Customer&amp;&gt;" in html
    assert "OWNS&lt;script&gt;" in html
    assert "<Customer&>" not in html
    assert 'class="capability-pill capability-unavailable"' in html
    assert 'role="tooltip">Unavailable</span>' in html


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
    assert (
        '<span class="header-status-message">1 warning and 3 execution errors. '
        "Coverage is incomplete.</span>" in html
    )
    assert (
        '<div class="suite-badges-row">'
        '<span class="badge badge-errored">3 ERRORED</span>'
        '<span class="badge badge-warn">1 WARNING</span>'
        '<span class="badge badge-score">SCORE: 23</span></div>'
    ) in html
    assert " FAILED</span>" not in html
    errored_cards = [
        card for card in _check_cards(html) if card["attrs"]["data-verdict"] == "errored"
    ]
    assert len(errored_cards) == 3
    assert all(
        "Errored" in card["text"] and "Evaluated" not in card["text"] for card in errored_cards
    )
    assert "None. All 5 selected checks were evaluated." in html
    coverage_start = html.index('<section id="not-evaluated"')
    assert (
        "not-evaluated-row" not in html[coverage_start : html.index("</section>", coverage_start)]
    )


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


def test_html_renderer_italicizes_suite_and_check_identities_and_fits_coverage_controls():
    html = render_html_report(_fixture("partial"))

    assert "<em><code>customer-360</code></em>" in html
    assert '<em><code class="check-id">customer-360::account-no-orphans</code></em>' in html
    assert ".not-evaluated { width: 100%; min-width: 0; box-sizing: border-box;" in html
    assert ".not-evaluated:focus { outline: 0; box-shadow: inset" in html
    assert ".not-evaluated-row { display: block; width: 100%; min-width: 0;" in html
    assert "font-size: 13px" in html


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
    assert '<span class="status-pill status-pill-error">FAILED</span>' in html
    assert "<strong>Run Failed.</strong>" in html
    assert 'aria-controls="troubleshooting-dialog">Troubleshoot.</button>' in html
    assert '<dialog id="troubleshooting-dialog"' in html
    assert "<h2>Troubleshooting Steps</h2>" in html
    assert "troubleshoot-btn')?.addEventListener('click', openTroubleshootingDialog)" in html
    assert (
        "close-troubleshooting-btn')?.addEventListener('click', closeTroubleshootingDialog)" in html
    )
    assert "CHECKED ON" not in html
    assert "connection.auth" not in html
    assert "Neo4j rejected the credentials" in html
    assert "Check the password in profiles.yml" in html
    assert "Target unavailable" in html
    assert "Run failed before checks could complete." in html
    assert "Action required" not in html
    assert "run-diagnostic" not in html
    assert "See fix" not in html
    assert "All clear! No issues found." not in html


def test_html_renderer_keeps_target_labels_and_values_on_aligned_rows():
    html = render_html_report(_fixture("clean"))

    assert ".target-summary { display: flex; flex-direction: column;" in html
    assert (
        ".target-summary > div { display: grid; grid-template-columns: 132px minmax(0, 1fr);"
        in html
    )
    assert ".target-summary > div { display: contents; }" not in html
    assert ".target-summary { grid-template-columns: 1fr;" not in html


def test_html_renderer_colors_the_active_verdict_filter():
    html = render_html_report(_fixture("complete"))

    assert '.filter-btn.active[data-filter="issues"]' not in html
    assert (
        ".filter-btn.active { background: var(--bg-header); color: #fff; font-weight: 600; }"
        in html
    )
    assert 'data-filter="issues" aria-pressed="false">Issues</button>' in html
    for verdict, color in (
        ("fail", "--fail-color"),
        ("warn", "--warn-color"),
        ("errored", "--errored-color"),
        ("pass", "--pass-color"),
        ("skipped", "--skipped-color"),
    ):
        assert (
            f'.filter-btn.active[data-filter="{verdict}"] '
            f"{{ background: var({color}); color: #fff; }}" in html
        )
        assert f'data-filter="{verdict}" aria-pressed="false"' in html
    assert 'data-filter="all" aria-pressed="true"' in html
    assert "b.setAttribute('aria-pressed', 'false');" in html
    assert "btn.setAttribute('aria-pressed', 'true');" in html


def test_see_issues_opens_checks_with_union_filter_and_precise_empty_state():
    html = render_html_report(_fixture("complete"))

    assert "function showIssues()" in html
    assert "['fail', 'warn', 'errored'].includes(verdict)" in html
    assert "setVerdictFilter('issues', issuesButton);" in html
    assert "No checks with findings or execution errors." in html
    assert "dataset.action === 'issues'" in html


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
def test_html_renderer_shows_connection_troubleshooting_dialog(code, message, fix):
    raw = json.loads(_fixture("failed").read_text(encoding="utf-8"))
    raw["run"]["error"] = {"code": code, "message": message, "fix": fix}

    html = render_html_report(raw)

    assert '<span class="status-pill status-pill-error">FAILED</span>' in html
    assert '<dialog id="troubleshooting-dialog"' in html
    assert "<h2>Troubleshooting Steps</h2>" in html
    assert "<h3>Problem</h3>" in html
    assert message in html
    assert "<h3>Steps</h3>" in html
    if code == "neo4j.credential_not_read_only":
        assert "Create a dedicated Neo4j user for GraphCheck." in html
    else:
        assert f"<li>{fix}</li>" in html
    assert "Action required" not in html


def test_html_renderer_shortens_read_only_error_header_and_keeps_detail_in_dialog():
    raw = json.loads(_fixture("failed").read_text(encoding="utf-8"))
    detail = (
        "The configured Neo4j credential has privileges outside the allowed read-only model "
        "(WRITE NODE(*), ROLE ADMIN) and is not server-enforced read-only."
    )
    raw["run"]["error"] = {
        "code": "neo4j.credential_not_read_only",
        "message": detail,
        "fix": "Create a dedicated read-only user.",
    }

    html = render_html_report(raw)
    header = html[
        html.index('<div id="report-run-title"') : html.index(
            "</div>", html.index('<div id="report-run-title"')
        )
    ]

    assert (
        "The configured Neo4j credential has privileges outside the allowed read-only model."
        in header
    )
    assert "WRITE NODE(*)" not in header
    assert detail in html
    assert "Update user and password/password_env in profiles.yml" in html


def test_html_renderer_can_limit_checks_to_diagnostic_verdicts():
    html = render_html_report(
        _fixture("complete"),
        verdicts={Verdict.FAIL, Verdict.WARN, Verdict.ERRORED},
    )
    full_html = render_html_report(_fixture("complete"))

    assert "Which accounts does a customer control" in html
    assert "Accounts are connected to a Customer" in html
    assert "Customer.tax_id is present" not in html
    assert _next_steps_fragment(html) == _next_steps_fragment(full_html)
    assert {
        (card["attrs"]["data-suite-id"], card["attrs"]["data-check-id"])
        for card in _check_cards(html)
    } == {
        ("customer-360", "cq-001"),
        ("customer-360", "account-no-orphans"),
    }


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
    assert html.count('id="checks-next-steps-panel"') == 1
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
    assert ".header-status-message { font-size: 18px; font-weight: 400; }" in html
    assert (
        ".status-pill { padding: 2px 7px; border-radius: 999px; color: #fff; font-size: 10px;"
        in html
    )
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
    assert "setSummaryTableExpanded(issueSummaryExpanded);" not in html
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

    assert set(fragments) == {"run_title", "overview", "checks_next_steps"}
    assert fragments["run_title"].startswith('<div id="report-run-title"')
    assert '<section id="report-overview"' in fragments["overview"]
    assert '<section id="checks-next-steps-panel"' in fragments["checks_next_steps"]
    assert "run_01HXATZ" not in fragments["run_title"]
    assert "<strong>Partial Run.</strong>" in fragments["run_title"]
    assert 'id="report-explorer"' not in "".join(fragments.values())
    assert 'id="theme-toggle"' not in "".join(fragments.values())
