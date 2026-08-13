import json
from pathlib import Path

import pytest

from graphcheck.cli import _print_run_summary
from graphcheck.reporting.html import render_html_report
from graphcheck.reporting.presentation import present_results
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
            "No checks were evaluated.",
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
    assert f"Result: {expected}" in stdout
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
