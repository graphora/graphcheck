import json
from pathlib import Path

import pytest

from graphcheck.cli import _print_run_summary
from graphcheck.contracts.results import CoverageStatus
from graphcheck.reporting.history import calculate_coverage_status
from graphcheck.reporting.html import render_html_report
from graphcheck.reporting.presentation import present_check, present_results
from graphcheck.reporting.writer import load_results

FIXTURES = Path(__file__).parent / "contracts" / "fixtures"


def _fixture(name):
    return load_results(FIXTURES / f"results.{name}.json")


def _empty_selection():
    raw = json.loads((FIXTURES / "results.generated-only.json").read_text(encoding="utf-8"))
    totals = {"checks": 0, "pass": 0, "fail": 0, "warn": 0, "errored": 0, "skipped": 0}
    raw.update(score=None, checks=[], totals=totals)
    raw["suites"][0].update(score=None, totals=totals.copy())
    return load_results(raw)


def _pass_with_generated_skip():
    raw = json.loads((FIXTURES / "results.clean.json").read_text(encoding="utf-8"))
    generated = json.loads((FIXTURES / "results.generated-only.json").read_text(encoding="utf-8"))[
        "checks"
    ][0]
    generated["suite_id"] = raw["suites"][0]["id"]
    raw["checks"].append(generated)
    raw["totals"].update(checks=3, skipped=1)
    raw["suites"][0]["totals"].update(checks=3, skipped=1)
    return load_results(raw)


@pytest.mark.parametrize(
    ("factory", "expected", "coverage", "fully_clean"),
    [
        (
            lambda: _fixture("failed"),
            "Run failed before checks could complete.",
            "0/0 selected checks evaluated",
            False,
        ),
        (
            lambda: _empty_selection(),
            "No checks were selected or evaluated.",
            "0/0 selected checks evaluated",
            False,
        ),
        (
            lambda: _fixture("generated-only"),
            "No checks were evaluated. Coverage is incomplete due to skipped check(s) from "
            "customer-360.",
            "0/1 selected checks evaluated · 1 not evaluated",
            False,
        ),
        (
            lambda: _fixture("complete"),
            "1 failure and 1 warning.",
            "3/3 selected checks evaluated",
            False,
        ),
        (
            lambda: _fixture("partial"),
            "No failures in the 1 check evaluated. Coverage is incomplete due to skipped "
            "check(s) from customer-360.",
            "1/2 selected checks evaluated · 1 not evaluated",
            False,
        ),
        (
            lambda: _fixture("clean"),
            "No failures. All 2 selected checks passed.",
            "2/2 selected checks evaluated",
            True,
        ),
    ],
)
def test_result_language_is_shared_by_cli_and_html(
    factory, expected, coverage, fully_clean, capsys
):
    results = factory()
    presentation = present_results(results)

    _print_run_summary(results, Path("results.json"), Path("report.html"))
    stdout = capsys.readouterr().out
    html = render_html_report(results)

    assert presentation.result_sentence == expected
    assert presentation.fully_clean is fully_clean
    assert presentation.coverage_sentence == coverage
    cli_expected = (
        f"{presentation.primary_sentence} Coverage is incomplete due to the following check(s) "
        "which have not been evaluated:"
        if results.totals.skipped
        else expected
    )
    assert f"Result: {cli_expected}" in stdout
    assert stdout.endswith("\n\n")
    assert "Coverage:" not in stdout
    assert presentation.primary_sentence in html
    if presentation.coverage_incomplete:
        for suite in presentation.skipped_suites:
            assert f"<em>{suite}</em>" in html


def test_incomplete_coverage_names_all_skipped_suites_in_sorted_order():
    raw = json.loads((FIXTURES / "results.partial.json").read_text(encoding="utf-8"))
    skipped = raw["checks"][1].copy()
    skipped.update(id="second-skip", suite_id="other-suite")
    raw["checks"].append(skipped)
    raw["totals"].update(checks=3, skipped=2)
    raw["suites"].append(
        {
            "id": "other-suite",
            "source_sha": "sha256:other",
            "score": None,
            "totals": {
                "checks": 1,
                "pass": 0,
                "fail": 0,
                "warn": 0,
                "errored": 0,
                "skipped": 1,
            },
        }
    )

    presentation = present_results(load_results(raw))

    assert presentation.skipped_suites == ("customer-360", "other-suite")
    assert presentation.result_sentence.endswith(
        "Coverage is incomplete due to skipped check(s) from customer-360, other-suite."
    )


def test_clean_report_has_no_broader_health_claim_or_finding_card():
    html = render_html_report(_fixture("clean"))

    assert "No failures. All 2 selected checks passed." in html
    assert 'class="check-card check-fail"' not in html
    assert 'class="check-card check-warn"' not in html
    assert 'class="check-card check-errored"' not in html
    assert "All clear" not in html
    assert "🎉" not in html


@pytest.mark.parametrize(
    ("reason", "label", "explanation"),
    [
        ("generated", "Generated", "Generated check awaiting review or approval."),
        ("unsupported", "Unsupported", "A capability required by this check was unavailable."),
        ("not_run", "Not run", "The run ended before this check started."),
    ],
)
def test_check_presentation_owns_skip_reason_language(reason, label, explanation):
    raw = json.loads((FIXTURES / "results.generated-only.json").read_text(encoding="utf-8"))
    raw["checks"][0]["skip_reason"] = reason
    if reason != "generated":
        raw["run"].update(run_status="partial", partial_reason="coverage unavailable")

    presentation = present_check(load_results(raw).checks[0])

    assert presentation.verdict == "skipped"
    assert presentation.verdict_label == "Skipped"
    assert presentation.evaluated is False
    assert presentation.evaluation_label == "Not evaluated"
    assert presentation.skip_reason is not None
    assert (presentation.skip_reason.code, presentation.skip_reason.label) == (reason, label)
    assert presentation.skip_reason.explanation == explanation


def test_cli_lists_only_skipped_checks_with_stored_reason_codes(capsys):
    raw = json.loads((FIXTURES / "results.generated-only.json").read_text(encoding="utf-8"))
    checks = []
    for reason in ("generated", "unsupported", "not_run"):
        check = raw["checks"][0].copy()
        check.update(id=f"{reason}-check", skip_reason=reason)
        checks.append(check)
    raw["checks"] = checks
    raw["run"].update(run_status="partial", partial_reason="coverage unavailable")
    raw["totals"].update(checks=3, skipped=3)
    raw["suites"][0]["totals"].update(checks=3, skipped=3)

    _print_run_summary(load_results(raw), Path("results.json"), Path("report.html"))
    stdout = capsys.readouterr().out

    normalized = " ".join(stdout.split())
    assert "Not evaluated:" not in stdout
    assert "Suite" in stdout and "Check" in stdout and "Reason" in stdout
    for reason, explanation in (
        ("generated", "Generated check awaiting review or approval."),
        ("unsupported", "A capability required by this check was unavailable."),
        ("not_run", "The run ended before this check started."),
    ):
        assert f"{reason}-check" in stdout
        assert f"{reason}:" in normalized
        assert all(word in normalized for word in explanation.split())


def test_cli_distinguishes_complete_run_from_partial_coverage(capsys):
    raw = json.loads((FIXTURES / "results.complete.json").read_text(encoding="utf-8"))
    raw["checks"][0].update(
        verdict="errored",
        measured=None,
        evidence=None,
        error={
            "code": "query.execution",
            "message": "Query execution failed",
            "fix": "Check the generated Cypher",
        },
    )
    raw["totals"].update(fail=0, errored=1)
    raw["suites"][0]["totals"].update(fail=0, errored=1)
    results = load_results(raw)

    _print_run_summary(results, Path("results.json"), Path("report.html"))

    stdout = capsys.readouterr().out
    assert results.run.run_status.value == "complete"
    assert "Run status: complete" in stdout
    assert "Coverage status: partial" in stdout


@pytest.mark.parametrize("factory", [_pass_with_generated_skip, lambda: _fixture("generated-only")])
def test_generated_skips_use_partial_coverage_in_cli_html_and_presentation(factory, capsys):
    results = factory()
    presentation = present_results(results)

    _print_run_summary(results, Path("results.json"), Path("report.html"))
    stdout = capsys.readouterr().out
    html = render_html_report(results)

    assert results.run.run_status.value == "complete"
    assert calculate_coverage_status(results) is CoverageStatus.PARTIAL
    assert presentation.coverage_incomplete is True
    assert "Run status: complete" in stdout
    assert "Coverage status: partial" in stdout
    assert "Coverage status: complete" not in stdout
    assert '<span class="status-pill status-pill-partial">PARTIAL</span>' in html
    assert '<span class="status-pill status-pill-complete">COMPLETE</span>' not in html
