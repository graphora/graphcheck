import json
from pathlib import Path

import jsonschema
import pytest
from pydantic import ValidationError

from graphcheck.contracts.results import Results, Verdict
from graphcheck.contracts.schemas import results_schema
from graphcheck.reporting.html import render_html_report
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


def test_writer_rejects_invalid_results():
    raw = json.loads(_fixture("complete").read_text(encoding="utf-8"))
    raw["checks"][0]["evidence"] = None

    with pytest.raises(ValidationError):
        results_json(raw)


@pytest.mark.parametrize("name", ["complete", "partial", "generated-only", "failed"])
def test_html_renderer_outputs_self_contained_interactive_report(name: str):
    html = render_html_report(_fixture(name))

    assert "<!doctype html>" in html
    assert "<style>" in html
    assert html.count("<script>") == 1
    assert "function filterChecks()" in html
    assert "function toggleTheme()" in html
    assert "GraphCheck" in html
    assert "http://" not in html
    assert "https://" not in html
    assert ' src="' not in html
    assert ' href="' not in html
    assert "fetch(" not in html
    assert "XMLHttpRequest" not in html
    assert "WebSocket(" not in html
    assert "EventSource(" not in html


def test_html_renderer_orders_failures_before_passes():
    html = render_html_report(_fixture("complete"))

    fail_pos = html.index('data-check-key="customer-360::cq-001"')
    warn_pos = html.index('data-check-key="customer-360::account-no-orphans"')
    pass_pos = html.index('data-check-key="customer-360::cust-tax-id-present"')

    assert fail_pos < warn_pos < pass_pos


def test_html_renderer_shows_health_overview_and_outcome_breakdown():
    html = render_html_report(_fixture("complete"))

    assert "<h2>Graph Health Overview</h2>" in html
    assert '<span class="exit-1">Run complete. 2 issues found</span>' in html
    assert "<strong>neo4j</strong> (Neo4j version: 5.18.0, community)" in html
    assert "<code>customer-360</code>" in html
    assert '<span class="suite-check-stats">3/3 checks run</span>' in html
    assert '<span class="badge badge-fail">1 FAILED</span>' in html
    assert '<span class="badge badge-warn">1 WARNINGS</span>' in html
    assert 'data-tooltip="Which accounts does a customer control — fail"' in html
    assert 'data-tooltip="Accounts are connected to a Customer — warn"' in html
    assert 'data-tooltip="Customer.tax_id is present — pass"' in html
    assert "Show Issue Summary" in html
    assert "5000 rows exceeds max 200" in html
    assert "2 Account nodes have no controlling Customer" in html


def test_html_renderer_reports_partial_coverage():
    html = render_html_report(_fixture("partial"))

    assert "<strong>Partial run:</strong>" in html
    assert '<span class="suite-check-stats">1/2 checks run (1 skipped)</span>' in html
    assert '<span class="badge badge-pass">OPERATIONAL</span>' in html
    assert 'class="status-box status-box-skipped"' in html
    assert 'class="status-box status-box-pass"' in html


def test_html_renderer_reports_all_checks_skipped():
    html = render_html_report(_fixture("generated-only"))

    assert '<span class="suite-check-stats">0/1 checks run (1 skipped)</span>' in html
    assert '<span class="badge badge-skipped">SKIPPED</span>' in html
    assert 'data-tooltip="draft competency check awaiting approval — skipped"' in html


def test_html_renderer_exposes_cypher_and_evidence_ids():
    html = render_html_report(_fixture("complete"))

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

    assert "connection.auth" in html
    assert "Neo4j rejected the credentials" in html
    assert "Target unavailable" in html


def test_html_renderer_can_limit_checks_to_diagnostic_verdicts():
    html = render_html_report(
        _fixture("complete"),
        verdicts={Verdict.FAIL, Verdict.WARN, Verdict.ERRORED},
    )

    assert "Which accounts does a customer control" in html
    assert "Accounts are connected to a Customer" in html
    assert "Customer.tax_id is present" not in html
