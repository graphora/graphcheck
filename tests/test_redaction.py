import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from graphcheck.cli import app
from graphcheck.reporting.html import render_html_report
from graphcheck.reporting.redaction import REDACTION_MASK, redact_results, verify_redacted_results
from graphcheck.reporting.writer import load_results, results_json

FIXTURES = Path(__file__).parent / "contracts" / "fixtures"
runner = CliRunner()


def test_redaction_masks_every_literal_surface_and_preserves_contract_shape():
    source = load_results(FIXTURES / "results.complete.json")
    source_payload = source.model_dump(mode="json", by_alias=True)

    redacted = redact_results(source)
    payload = json.loads(results_json(redacted))

    assert payload["run"]["redaction"] == {"policy": "mask", "applied": True}
    assert payload["totals"] == source_payload["totals"]
    assert payload["score"] == source_payload["score"]
    assert payload["suites"] == source_payload["suites"]
    assert [check["verdict"] for check in payload["checks"]] == [
        check["verdict"] for check in source_payload["checks"]
    ]
    assert payload["checks"][0]["params"] == {"customer_id": REDACTION_MASK}
    assert payload["checks"][0]["measured"] == {"rows": REDACTION_MASK}
    assert payload["checks"][0]["compiled_query"] == REDACTION_MASK
    assert payload["checks"][0]["expected"] == {
        "rows": {"min": REDACTION_MASK, "max": REDACTION_MASK},
        "unique": REDACTION_MASK,
    }
    assert payload["checks"][0]["evidence"]["message"] == REDACTION_MASK
    assert payload["checks"][0]["evidence"]["elements"][0] == {
        "kind": "node",
        "id": REDACTION_MASK,
        "labels": [REDACTION_MASK],
        "type": None,
    }
    assert "CUST-1042" not in results_json(redacted)
    assert "4:abc:12" not in results_json(redacted)
    assert source.checks[0].params == {"customer_id": "CUST-1042"}
    assert verify_redacted_results(payload) == redacted


def test_redaction_verifier_rejects_an_unmasked_value():
    payload = redact_results(load_results(FIXTURES / "results.complete.json")).model_dump(
        mode="json", by_alias=True
    )
    payload["checks"][0]["params"]["customer_id"] = "CUST-1042"

    with pytest.raises(ValueError, match=r"checks\[0\]\.params\.customer_id"):
        verify_redacted_results(payload)

    with pytest.raises(ValueError, match=r"checks\[0\]\.params\.customer_id"):
        results_json(payload)

    with pytest.raises(ValueError, match=r"checks\[0\]\.params\.customer_id"):
        render_html_report(payload)

    payload = redact_results(load_results(FIXTURES / "results.complete.json")).model_dump(
        mode="json", by_alias=True
    )
    payload["checks"][0]["measured"]["rows"] = 5000
    with pytest.raises(ValueError, match=r"checks\[0\]\.measured\.rows"):
        verify_redacted_results(payload)


def test_html_report_reflects_verified_redaction_without_raw_literals():
    html = render_html_report(redact_results(FIXTURES / "results.complete.json"))

    assert '<meta name="graphcheck-redaction" content="mask">' in html
    main_pill = '<span class="status-pill status-pill-warning">COMPLETE</span>'
    redaction_pill = '<span class="status-pill status-pill-redacted">DETAILS REDACTED</span>'
    assert html.index(main_pill) < html.index(redaction_pill) < html.index("<h1>")
    assert "<h2>Graph Health Overview</h2>" in html
    assert '<span class="meta-label">Target Graph</span>' not in html
    assert "5.18.0" not in html
    assert "1,250" not in html
    assert "3,480" not in html
    assert '<span class="meta-label">Redaction</span>' not in html
    assert '<span class="meta-label">Nodes</span>' not in html
    assert '<span class="meta-label">Relationships</span>' not in html
    assert "<strong>Expected:</strong>" not in html
    assert "<strong>Measured:</strong>" not in html
    assert "<h4>Compiled Cypher</h4>" not in html
    assert "View Details & Evidence" not in html
    assert 'id="toggle-details-btn"' not in html
    check_name = "<h3>Which accounts does a customer control</h3>"
    pattern = '<span class="check-pattern">Pattern: <code>competency-shape</code></span>'
    check_id = '<code class="check-id">customer-360::cq-001</code>'
    assert html.index(check_name) < html.index(pattern) < html.index(check_id)
    assert "CUST-1042" not in html
    assert "4:abc:12" not in html
    assert REDACTION_MASK in html


def test_redact_command_writes_verified_sidecar(tmp_path):
    source = tmp_path / "results.json"
    fixture = (FIXTURES / "results.complete.json").read_text(encoding="utf-8")
    source.write_text(fixture, encoding="utf-8")

    result = runner.invoke(app, ["redact", str(source)])

    destination = tmp_path / "results.redacted.json"
    assert result.exit_code == 0
    assert destination.is_file()
    exported = destination.read_text(encoding="utf-8")
    assert "CUST-1042" not in exported
    assert verify_redacted_results(exported).run.redaction.applied is True
