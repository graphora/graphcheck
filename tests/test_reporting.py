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


def test_html_renderer_exposes_cypher_and_evidence_ids():
    html = render_html_report(_fixture("complete"))

    assert "MATCH (c:Customer" in html
    assert "4:abc:12" in html
    assert "4:abc:88" in html
    assert "5000 rows exceeds max 200" in html


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
