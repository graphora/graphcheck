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
    assert payload["run"]["id"] == "redacted_20260706T090241000000Z"
    assert payload["totals"] == source_payload["totals"]
    assert payload["score"] == source_payload["score"]
    assert [suite["totals"] for suite in payload["suites"]] == [
        suite["totals"] for suite in source_payload["suites"]
    ]
    assert payload["run"]["selection"]["suites"] == ["suite-1"]
    assert payload["run"]["target"]["labels"] == ["label-1", "label-2"]
    assert payload["run"]["target"]["relationship_types"] == [
        "relationship-type-1",
        "relationship-type-2",
    ]
    assert payload["suites"][0]["id"] == "suite-1"
    assert payload["suites"][0]["source_sha"] == REDACTION_MASK
    assert {check["suite_id"] for check in payload["checks"]} == {"suite-1"}
    assert [check["id"] for check in payload["checks"]] == ["check-1", "check-2", "check-3"]
    assert [check["verdict"] for check in payload["checks"]] == [
        check["verdict"] for check in source_payload["checks"]
    ]
    assert payload["checks"][0]["name"] == REDACTION_MASK
    assert payload["checks"][0]["provenance"] == REDACTION_MASK
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


def test_redaction_aliases_sensitive_identifier_and_scans_the_final_artifact():
    payload = json.loads((FIXTURES / "results.complete.json").read_text(encoding="utf-8"))
    payload["checks"][0]["id"] = "CUST-1042"

    redacted = redact_results(payload)
    exported = results_json(redacted)
    html = render_html_report(redacted)

    assert redacted.checks[0].id == "check-1"
    assert redacted.checks[0].suite_id == redacted.suites[0].id
    assert redacted.suites[0].id == redacted.run.selection.suites[0]
    assert verify_redacted_results(redacted) == redacted
    assert "CUST-1042" not in exported
    assert "CUST-1042" not in html


def test_redaction_avoids_deterministic_run_id_collision_in_json_and_html():
    payload = json.loads((FIXTURES / "results.complete.json").read_text(encoding="utf-8"))
    sensitive = "redacted_20260706T090241000000Z"
    payload["checks"][0]["params"]["customer_id"] = sensitive

    redacted = redact_results(payload)
    exported = results_json(redacted)
    html = render_html_report(redacted)

    assert redacted.run.id == "redacted_collision1_20260706T090241000000Z"
    assert redact_results(payload).run.id == redacted.run.id
    assert verify_redacted_results(redacted) == redacted
    assert sensitive not in exported
    assert sensitive not in html


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


@pytest.mark.parametrize(
    ("path", "value", "match"),
    [
        (("checks", 0, "id"), "CUST-1042", r"checks\[0\]\.id"),
        (("checks", 0, "name"), "CUST-1042", r"checks\[0\]\.name"),
        (("checks", 0, "provenance"), "CUST-1042", r"checks\[0\]\.provenance"),
        (("run", "id"), "patient-prod_20260706T090241000000Z", r"run\.id"),
    ],
)
def test_redaction_verifier_rejects_sensitive_preserved_metadata(path, value, match):
    payload = redact_results(FIXTURES / "results.complete.json").model_dump(
        mode="json", by_alias=True
    )
    target = payload
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value

    with pytest.raises(ValueError, match=match):
        verify_redacted_results(payload)
    with pytest.raises(ValueError, match=match):
        results_json(payload)
    with pytest.raises(ValueError, match=match):
        render_html_report(payload)


def test_redaction_masks_run_error_diagnostics_before_json_and_html_export():
    payload = json.loads((FIXTURES / "results.failed.json").read_text(encoding="utf-8"))
    payload["run"]["error"]["message"] = "Driver failed for SECRET-SSN-123"
    payload["run"]["error"]["fix"] = "Remove SECRET-SSN-123 and retry"

    redacted = redact_results(payload)
    exported = results_json(redacted)
    html = render_html_report(redacted)

    assert redacted.run.error.message == REDACTION_MASK
    assert redacted.run.error.fix == REDACTION_MASK
    assert "SECRET-SSN-123" not in exported
    assert "SECRET-SSN-123" not in html
    tampered = redacted.model_dump(mode="json", by_alias=True)
    tampered["run"]["error"]["message"] = "SECRET-SSN-123"
    with pytest.raises(ValueError, match=r"run\.error\.message"):
        verify_redacted_results(tampered)


def test_redaction_masks_partial_reason_and_check_error_diagnostics():
    partial = json.loads((FIXTURES / "results.partial.json").read_text(encoding="utf-8"))
    partial["run"]["partial_reason"] = "Stopped after SECRET-SSN-123"
    redacted_partial = redact_results(partial)

    assert redacted_partial.run.partial_reason == REDACTION_MASK
    assert "SECRET-SSN-123" not in results_json(redacted_partial)
    tampered_partial = redacted_partial.model_dump(mode="json", by_alias=True)
    tampered_partial["run"]["partial_reason"] = "SECRET-SSN-123"
    with pytest.raises(ValueError, match=r"run\.partial_reason"):
        verify_redacted_results(tampered_partial)

    errored = json.loads((FIXTURES / "results.generated-only.json").read_text(encoding="utf-8"))
    errored["run"]["exit_code"] = 1
    errored["score"] = {
        "value": 0,
        "method": "weighted-by-severity",
        "weights": {"error": 3, "warn": 1},
    }
    errored["totals"].update({"errored": 1, "skipped": 0})
    errored["suites"][0]["score"] = 0
    errored["suites"][0]["totals"].update({"errored": 1, "skipped": 0})
    check = errored["checks"][0]
    check.update(
        verdict="errored",
        skip_reason=None,
        started_at="2026-07-06T09:01:13Z",
        duration_ms=61,
        error={
            "code": "neo4j.query_failed",
            "message": "Driver exposed SECRET-SSN-123",
            "fix": "Retry without SECRET-SSN-123",
        },
    )
    redacted_error = redact_results(errored)

    assert redacted_error.checks[0].error.message == REDACTION_MASK
    assert redacted_error.checks[0].error.fix == REDACTION_MASK
    assert "SECRET-SSN-123" not in results_json(redacted_error)
    tampered_error = redacted_error.model_dump(mode="json", by_alias=True)
    tampered_error["checks"][0]["error"]["fix"] = "SECRET-SSN-123"
    with pytest.raises(ValueError, match=r"checks\[0\]\.error\.fix"):
        verify_redacted_results(tampered_error)


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
    check_name = f"<h3>{REDACTION_MASK}</h3>"
    pattern = '<span class="check-pattern">Pattern: <code>competency-shape</code></span>'
    check_id = '<code class="check-id">suite-1::check-1</code>'
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
