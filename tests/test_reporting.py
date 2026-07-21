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
def test_html_renderer_outputs_self_contained_report(name: str):
    html = render_html_report(_fixture(name))

    assert "<!doctype html>" in html
    assert "<style>" in html
    assert "GraphCheck" in html
    assert "http://" not in html
    assert "https://" not in html
    assert "<script" not in html
    assert ' src="' not in html
    assert ' href="' not in html


def test_html_renderer_orders_failures_before_passes():
    html = render_html_report(_fixture("complete"))

    fail_pos = html.index("Which accounts does a customer control")
    warn_pos = html.index("Accounts are connected to a Customer")
    pass_pos = html.index("Customer.tax_id is present")

    assert fail_pos < warn_pos < pass_pos


def test_html_renderer_shows_one_score_and_outcome_breakdown():
    html = render_html_report(_fixture("complete"))

    assert (
        '<div class="score-ring" style="--score-value: 43" role="img" '
        'aria-label="Overall score: 43 out of 100">'
        '<div class="score-ring-inner"><span>43</span><small>overall score</small>' in html
    )
    assert "Execution coverage: 3 of 3 selected checks executed (100%)" in html
    assert "CI exit code: <strong>1</strong>" in html
    assert "<h2>Score Breakdown</h2>" in html
    assert "<th>Issue</th><th>Points docked</th>" in html
    assert "5000 rows exceeds max 200</td><td><strong>43</strong>" in html
    assert "2 Account nodes have no controlling Customer</td><td><strong>14</strong>" in html
    assert '<th colspan="5">Total points docked</th><th>57</th>' in html
    assert "<h3>Suite Results</h3>" in html
    assert "<th>Suite</th><th>Pass</th><th>Fail</th><th>Warn</th>" in html
    assert "<td>customer-360</td><td>1</td><td>1</td><td>1</td>" in html
    assert "passed weight" not in html.lower()
    assert "weighted score" not in html.lower()
    assert "<th>Score</th>" not in html
    assert html.count('class="score-ring"') == 1


def test_html_renderer_keeps_score_separate_from_partial_coverage():
    html = render_html_report(_fixture("partial"))

    assert "<span>100</span><small>overall score</small>" in html
    assert 'style="--score-value: 100"' in html
    assert "Execution coverage: 1 of 2 selected checks executed (50%)" in html


def test_html_renderer_uses_null_score_when_every_check_is_skipped():
    html = render_html_report(_fixture("generated-only"))

    assert 'class="score-ring score-ring-empty"' in html
    assert 'aria-label="Overall score unavailable"' in html
    assert "<span>n/a</span><small>overall score</small>" in html
    assert "Execution coverage: 0 of 1 selected checks executed (0%)" in html


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

    assert "Labels/Type/Scope" in html
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
